use super::{static_server::StaticServer, store::Installed};
use crate::{diagnostics::Diagnostics, process_job::OwnedJob};
use serde_json::{json, Value};
use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    process::Stdio,
    sync::Arc,
    time::Duration,
};
use tokio::{
    io::{AsyncBufRead, AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    time::timeout,
};

const REQUEST_LIMIT: usize = 65_536;
const RESPONSE_LIMIT: usize = 2 * 1024 * 1024;
const WAIT: Duration = Duration::from_secs(8);
const WEB_APP_START_WAIT: Duration = Duration::from_secs(45);

pub struct Session {
    pub installed: Installed,
    pub root: PathBuf,
    pub data: PathBuf,
    pub generation: String,
    pub token: String,
    pub worker: Option<ModuleWorker>,
    pub error: Option<String>,
    pub ui_revision: u64,
    web_app_origin: Option<String>,
    web_app_initialized: bool,
    server: StaticServer,
}

pub struct CallError {
    pub message: String,
    pub fatal: bool,
}
impl From<String> for CallError {
    fn from(message: String) -> Self {
        Self {
            message,
            fatal: true,
        }
    }
}

pub struct ModuleWorker {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    _job: OwnedJob,
    diagnostics: Arc<Diagnostics>,
    module_id: String,
    web_app: bool,
    pending_response: Option<String>,
    response_buffer: Vec<u8>,
    shutdown_sent: bool,
}

impl Session {
    pub async fn start(
        installed: Installed,
        root: PathBuf,
        data: PathBuf,
        diagnostics: Arc<Diagnostics>,
    ) -> Result<Self, String> {
        std::fs::create_dir_all(&data).map_err(|e| e.to_string())?;
        let entry = installed
            .manifest
            .frontend
            .strip_prefix("frontend/")
            .ok_or("MODULE_FRONTEND_INVALID")?;
        let server = StaticServer::start(root.join("frontend"), entry)?;
        let worker = if installed.manifest.worker.is_some() {
            Some(ModuleWorker::spawn(&installed, &root, &data, diagnostics).await?)
        } else {
            None
        };
        Ok(Self {
            installed,
            root,
            data,
            generation: uuid::Uuid::new_v4().to_string(),
            token: uuid::Uuid::new_v4().to_string(),
            worker,
            error: None,
            ui_revision: 0,
            web_app_origin: None,
            web_app_initialized: false,
            server,
        })
    }
    pub fn describe(&self) -> Value {
        json!({"moduleId":self.installed.manifest.id,"version":self.installed.manifest.version,"digest":self.installed.digest,"generation":self.generation,"url":self.server.url(),"webApp":self.installed.manifest.web_app,"token":self.token,"workerPid":self.worker.as_ref().and_then(|worker| worker.child.id())})
    }
    pub async fn initialize_web_app(&mut self) -> Result<(), String> {
        if !self.installed.manifest.web_app || self.web_app_origin.is_some() {
            return Ok(());
        }
        // The manager owns this session before HTTP startup. If a control request
        // fails, its live worker remains available for a retry or safe shutdown.
        let result = async {
            let worker = self.worker.as_mut().ok_or("MODULE_WORKER_UNAVAILABLE")?;
            let result = worker
                .request_with_wait("module.webapp", json!({}), WEB_APP_START_WAIT)
                .await
                .map_err(|error| error.message)?;
            let url = result
                .get("url")
                .and_then(Value::as_str)
                .ok_or("MODULE_WEB_APP_URL_INVALID")?;
            let origin = validate_web_app_url(url, self.server.url())?;
            let entry = self
                .installed
                .manifest
                .frontend
                .strip_prefix("frontend/")
                .ok_or("MODULE_FRONTEND_INVALID")?;
            let server =
                StaticServer::start_with_web_app(self.root.join("frontend"), entry, Some(&origin))?;
            self.server = server;
            self.web_app_origin = Some(origin);
            if self.web_app_initialized {
                self.ui_revision += 1;
                self.generation = uuid::Uuid::new_v4().to_string();
                self.token = uuid::Uuid::new_v4().to_string();
            }
            self.web_app_initialized = true;
            Ok::<(), String>(())
        }
        .await;
        self.error = result.as_ref().err().cloned();
        result
    }
    pub async fn stop_worker(&mut self) -> Result<(), String> {
        if let Some(worker) = self.worker.as_mut() {
            if let Err(error) = worker.stop().await {
                self.error = Some(error.clone());
                return Err(error);
            }
        }
        self.worker.take();
        self.web_app_origin = None;
        self.error = None;
        Ok(())
    }
    pub fn check_health(&mut self) {
        let ended = self
            .worker
            .as_mut()
            .is_some_and(|worker| matches!(worker.child.try_wait(), Ok(Some(_))));
        if ended {
            self.worker.take();
            self.error = Some("MODULE_WORKER_EXITED: reopen this module to retry".into());
        }
    }
    pub async fn restore_worker(&mut self, diagnostics: Arc<Diagnostics>) -> Result<(), String> {
        self.check_health();
        if self.installed.manifest.worker.is_some() && self.worker.is_none() {
            match ModuleWorker::spawn(&self.installed, &self.root, &self.data, diagnostics).await {
                Ok(worker) => {
                    self.worker = Some(worker);
                    self.error = None;
                    self.web_app_origin = None;
                }
                Err(error) => {
                    self.error = Some(error.clone());
                    return Err(error);
                }
            }
        }
        self.initialize_web_app().await?;
        Ok(())
    }
    pub async fn call(&mut self, method: &str, params: Value) -> Result<Value, String> {
        if !self
            .installed
            .manifest
            .methods
            .iter()
            .any(|entry| entry == method)
        {
            return Err("MODULE_METHOD_NOT_ALLOWED".into());
        }
        if !params.is_object() {
            return Err("INVALID_PARAMS: expected object".into());
        }
        let worker = self.worker.as_mut().ok_or("MODULE_WORKER_UNAVAILABLE")?;
        match worker.request(method, params).await {
            Ok(value) => {
                self.error = None;
                Ok(value)
            }
            Err(error) => {
                if error.fatal && !self.installed.manifest.web_app {
                    if let Some(mut worker) = self.worker.take() {
                        let _ = worker.child.kill().await;
                    }
                    self.error = Some(error.message.clone());
                }
                if self.installed.manifest.web_app {
                    self.error = Some(error.message.clone());
                }
                Err(error.message)
            }
        }
    }
}

impl ModuleWorker {
    async fn spawn(
        installed: &Installed,
        root: &Path,
        data: &Path,
        diagnostics: Arc<Diagnostics>,
    ) -> Result<Self, String> {
        let entry = installed
            .manifest
            .worker
            .as_ref()
            .ok_or("MODULE_WORKER_MISSING")?;
        let mut command = Command::new(root.join(entry));
        command
            .current_dir(data)
            .env("OPENDESK_MODULE_DATA_DIR", data)
            // hostApi 1 compatibility alias; shares the new data path without creating an old directory.
            .env("NIMDADESK_MODULE_DATA_DIR", data)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .env_remove("PYTHONPATH")
            .env_remove("PYTHONHOME")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x08000000 | 0x00000004);
        let mut child = command
            .spawn()
            .map_err(|e| format!("MODULE_START_FAILED: {e}"))?;
        let job = match OwnedJob::attach(child.id().ok_or("MODULE_PROCESS_EXITED")?) {
            Ok(job) => job,
            Err(error) => {
                let _ = child.kill().await;
                return Err(format!("MODULE_OWNERSHIP_FAILED: {error}"));
            }
        };
        let stdin = child.stdin.take().ok_or("MODULE_STDIN_UNAVAILABLE")?;
        let stdout = BufReader::new(child.stdout.take().ok_or("MODULE_STDOUT_UNAVAILABLE")?);
        if let Some(stderr) = child.stderr.take() {
            let diagnostics = diagnostics.clone();
            let id = installed.manifest.id.clone();
            tauri::async_runtime::spawn(async move {
                let mut stream = BufReader::new(stderr);
                while let Ok(line) = bounded_line(&mut stream, 65_536).await {
                    if line.is_empty() {
                        break;
                    }
                    // Package stderr is not trusted to be free of prompts or credentials.
                    diagnostics.record(
                        "info",
                        "module.stderr",
                        &format!("moduleId={id} bytes={}", line.len()),
                    );
                }
            });
        }
        let mut worker = Self {
            child,
            stdin,
            stdout,
            _job: job,
            diagnostics,
            module_id: installed.manifest.id.clone(),
            web_app: installed.manifest.web_app,
            pending_response: None,
            response_buffer: Vec::new(),
            shutdown_sent: false,
        };
        let verified = async {
            let hello = worker
                .request("runtime.hello", json!({}))
                .await
                .map_err(|e| e.message)?;
            if hello["protocolVersion"] != 1 {
                return Err("MODULE_PROTOCOL_INCOMPATIBLE".into());
            }
            let capabilities = hello["capabilities"]
                .as_array()
                .ok_or("MODULE_CAPABILITIES_INVALID")?;
            let actual: HashSet<&str> = capabilities.iter().filter_map(Value::as_str).collect();
            let mut expected: HashSet<&str> = installed
                .manifest
                .methods
                .iter()
                .map(String::as_str)
                .collect();
            expected.extend([
                "runtime.hello",
                "runtime.ping",
                "runtime.shutdown",
                "module.identity",
            ]);
            if installed.manifest.web_app {
                expected.extend(["module.webapp", "module.prepareShutdown"]);
            }
            if actual != expected || capabilities.len() != actual.len() {
                return Err("MODULE_CAPABILITIES_MISMATCH".into());
            }
            let identity = worker
                .request("module.identity", json!({}))
                .await
                .map_err(|e| e.message)?;
            if identity["id"] != installed.manifest.id
                || identity["version"] != installed.manifest.version
                || identity["hostApi"] != installed.manifest.host_api
            {
                return Err("MODULE_IDENTITY_MISMATCH".into());
            }
            Ok::<(), String>(())
        }
        .await;
        if let Err(error) = verified {
            let _ = worker.child.kill().await;
            return Err(error);
        }
        worker.diagnostics.record(
            "info",
            "module.worker_started",
            &format!(
                "moduleId={} version={}",
                installed.manifest.id, installed.manifest.version
            ),
        );
        Ok(worker)
    }
    async fn request(&mut self, method: &str, params: Value) -> Result<Value, CallError> {
        self.request_with_wait(method, params, WAIT).await
    }
    async fn request_with_wait(
        &mut self,
        method: &str,
        params: Value,
        wait: Duration,
    ) -> Result<Value, CallError> {
        let id = uuid::Uuid::new_v4().to_string();
        let started = std::time::Instant::now();
        let result = match timeout(wait, self.exchange(&id, method, params)).await {
            Ok(result) => result,
            Err(_) => Err(CallError {
                message: "MODULE_TIMEOUT: outcome unknown; request was not replayed".into(),
                fatal: true,
            }),
        };
        self.diagnostics.record(
            if result.is_ok() { "info" } else { "error" },
            "module.response",
            &format!(
                "moduleId={} requestId={id} method={method} elapsedMs={} outcome={}",
                self.module_id,
                started.elapsed().as_millis(),
                if result.is_ok() { "success" } else { "failure" }
            ),
        );
        result
    }
    async fn exchange(
        &mut self,
        id: &str,
        method: &str,
        params: Value,
    ) -> Result<Value, CallError> {
        // A timed-out Web App control call may finish later. Drain that one
        // response before a new command; never replay the timed-out operation.
        if self.pending_response.is_some() {
            self.read_response().await?;
        }
        let mut frame = serde_json::to_vec(
            &json!({"protocolVersion":1,"id":id,"method":method,"params":params}),
        )
        .map_err(|e| e.to_string())?;
        if frame.len() > REQUEST_LIMIT {
            return Err(CallError {
                message: "MODULE_REQUEST_TOO_LARGE".into(),
                fatal: false,
            });
        }
        frame.push(b'\n');
        self.pending_response = Some(id.to_owned());
        self.stdin
            .write_all(&frame)
            .await
            .map_err(|e| e.to_string())?;
        self.stdin.flush().await.map_err(|e| e.to_string())?;
        if method == "runtime.shutdown" {
            self.shutdown_sent = true;
        }
        self.read_response().await
    }
    async fn read_response(&mut self) -> Result<Value, CallError> {
        let line =
            bounded_line_buffered(&mut self.stdout, &mut self.response_buffer, RESPONSE_LIMIT)
                .await?;
        let id = self
            .pending_response
            .take()
            .ok_or("MODULE_RESPONSE_UNEXPECTED".to_owned())?;
        let response: Value =
            serde_json::from_slice(&line).map_err(|_| "MODULE_INVALID_RESPONSE".to_string())?;
        if response["protocolVersion"] != 1
            || response["id"] != id
            || response.get("result").is_some() == response.get("error").is_some()
        {
            return Err("MODULE_PROTOCOL_MISMATCH".to_string().into());
        }
        if let Some(error) = response.get("error") {
            let code = error["code"]
                .as_str()
                .ok_or_else(|| "MODULE_INVALID_ERROR".to_string())?;
            let message = error["message"]
                .as_str()
                .ok_or_else(|| "MODULE_INVALID_ERROR".to_string())?;
            return Err(CallError {
                message: format!("{code}: {message}"),
                fatal: false,
            });
        }
        Ok(response["result"].clone())
    }
    async fn stop(&mut self) -> Result<(), String> {
        if self.web_app {
            return self.stop_web_app().await;
        }
        let graceful = timeout(Duration::from_secs(2), async {
            self.exchange(
                &uuid::Uuid::new_v4().to_string(),
                "runtime.shutdown",
                json!({}),
            )
            .await
            .map_err(|e| e.message)?;
            self.child.wait().await.map_err(|e| e.to_string())
        })
        .await;
        if !matches!(graceful, Ok(Ok(status)) if status.success()) {
            if let Err(error) = self.child.kill().await {
                if !matches!(self.child.try_wait(), Ok(Some(_))) {
                    return Err(format!("MODULE_STOP_FAILED: {error}"));
                }
            }
        }
        self.diagnostics.record(
            "info",
            "module.worker_stopped",
            &format!("moduleId={}", self.module_id),
        );
        Ok(())
    }
    async fn stop_web_app(&mut self) -> Result<(), String> {
        if self
            .child
            .try_wait()
            .map_err(|error| error.to_string())?
            .is_some()
        {
            return Ok(());
        }
        if self.shutdown_sent && self.pending_response.is_some() {
            match timeout(WAIT, self.read_response()).await {
                Ok(Ok(_)) => {}
                Ok(Err(error)) => {
                    if self.pending_response.is_none() {
                        self.shutdown_sent = false;
                    }
                    return Err(format!("MODULE_SAFE_STOP_PENDING: {}; worker retained", error.message));
                }
                Err(_) => return Err("MODULE_SAFE_STOP_PENDING: waiting for shutdown acknowledgement; worker retained".into()),
            }
        }
        if !self.shutdown_sent {
            loop {
                let status = self.request("module.prepareShutdown", json!({})).await
                    .map_err(|error| format!("MODULE_SAFE_STOP_PENDING: {}; worker retained; retry when the control channel responds", error.message))?;
                if shutdown_ready(&status)? {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(250)).await;
            }
            // prepareShutdown must atomically reject new write operations before
            // reporting ready. Keep the process owned until it actually exits.
            if let Err(error) = self.request("runtime.shutdown", json!({})).await {
                if self.pending_response.is_none() {
                    self.shutdown_sent = false;
                }
                return Err(format!(
                    "MODULE_SAFE_STOP_PENDING: {}; worker retained",
                    error.message
                ));
            }
        }
        match timeout(WAIT, self.child.wait()).await {
            Ok(Ok(_)) => Ok(()),
            Ok(Err(error)) => Err(format!("MODULE_SAFE_STOP_PENDING: {error}")),
            Err(_) => Err("MODULE_SAFE_STOP_PENDING: shutdown acknowledged but process is still running; worker retained".into()),
        }
    }
}

fn shutdown_ready(value: &Value) -> Result<bool, String> {
    let ready = value
        .get("ready")
        .and_then(Value::as_bool)
        .ok_or("MODULE_SAFE_STOP_INVALID: missing ready")?;
    let active = value
        .get("active")
        .and_then(Value::as_u64)
        .ok_or("MODULE_SAFE_STOP_INVALID: missing active count")?;
    if ready && active != 0 {
        return Err("MODULE_SAFE_STOP_INVALID: ready contradicts active operations".into());
    }
    Ok(ready)
}

fn validate_web_app_url(value: &str, wrapper_url: &str) -> Result<String, String> {
    let invalid = || {
        "MODULE_WEB_APP_URL_INVALID: expected a distinct http://127.0.0.1:<port> application origin"
            .to_owned()
    };
    if value.len() > 4096
        || value
            .chars()
            .any(|character| character.is_whitespace() || character.is_control())
    {
        return Err(invalid());
    }
    let authority = value
        .strip_prefix("http://127.0.0.1:")
        .ok_or_else(invalid)?;
    let port_text = authority.split(['/', '?', '#']).next().unwrap_or_default();
    if port_text.is_empty() || !port_text.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(invalid());
    }
    let port = port_text
        .parse::<u16>()
        .ok()
        .filter(|port| *port != 0)
        .ok_or_else(invalid)?;
    let url = tauri::Url::parse(value).map_err(|_| invalid())?;
    let wrapper = tauri::Url::parse(wrapper_url).map_err(|_| invalid())?;
    if url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port_or_known_default() != Some(port)
        || url.origin() == wrapper.origin()
    {
        return Err(invalid());
    }
    Ok(format!("http://127.0.0.1:{port}"))
}

async fn bounded_line<R: AsyncBufRead + Unpin>(
    reader: &mut R,
    maximum: usize,
) -> Result<Vec<u8>, String> {
    let mut output = Vec::new();
    bounded_line_buffered(reader, &mut output, maximum).await
}

async fn bounded_line_buffered<R: AsyncBufRead + Unpin>(
    reader: &mut R,
    output: &mut Vec<u8>,
    maximum: usize,
) -> Result<Vec<u8>, String> {
    loop {
        let chunk = reader.fill_buf().await.map_err(|e| e.to_string())?;
        if chunk.is_empty() {
            return Err("MODULE_DISCONNECTED".into());
        }
        let end = chunk
            .iter()
            .position(|byte| *byte == b'\n')
            .map(|position| position + 1);
        let count = end.unwrap_or(chunk.len());
        if output.len() + count > maximum + 2 {
            return Err("MODULE_RESPONSE_TOO_LARGE".into());
        }
        output.extend_from_slice(&chunk[..count]);
        reader.consume(count);
        if end.is_some() {
            output.pop();
            if output.last() == Some(&b'\r') {
                output.pop();
            }
            if output.len() > maximum {
                return Err("MODULE_RESPONSE_TOO_LARGE".into());
            }
            return Ok(std::mem::take(output));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn application_urls_require_explicit_distinct_numeric_loopback_origin() {
        let wrapper = "http://127.0.0.1:50000/nonce/index.html";
        assert_eq!(
            validate_web_app_url("http://127.0.0.1:51234/?session=example", wrapper).unwrap(),
            "http://127.0.0.1:51234"
        );
        for value in [
            "https://127.0.0.1:51234/",
            "http://localhost:51234/",
            "http://127.1:51234/",
            "http://0x7f000001:51234/",
            "http://127.0.0.1/",
            "http://127.0.0.1:0/",
            "http://127.0.0.1:65536/",
            "http://user:secret@127.0.0.1:51234/",
            "http://127.0.0.1:51234@attacker.example/",
            "http://127.0.0.1:50000/other",
            "http://127.0.0.1:51234/\n",
        ] {
            assert!(validate_web_app_url(value, wrapper).is_err(), "{value}");
        }
    }

    #[test]
    fn shutdown_requires_well_formed_consistent_quiescence() {
        assert!(!shutdown_ready(&json!({"ready":false,"active":2})).unwrap());
        assert!(shutdown_ready(&json!({"ready":true,"active":0})).unwrap());
        for value in [
            json!({}),
            json!({"ready":true}),
            json!({"ready":true,"active":1}),
            json!({"ready":false,"active":-1}),
            json!({"ready":true,"active":0.5}),
        ] {
            assert!(shutdown_ready(&value).is_err());
        }
    }

    #[test]
    fn cancelled_response_read_retains_partial_frame_for_retry() {
        tauri::async_runtime::block_on(async {
            let (mut writer, stream) = tokio::io::duplex(256);
            let mut reader = BufReader::new(stream);
            writer.write_all(b"partial").await.unwrap();
            let mut buffer = Vec::new();
            assert!(timeout(
                Duration::from_millis(10),
                bounded_line_buffered(&mut reader, &mut buffer, 256)
            )
            .await
            .is_err());
            assert_eq!(buffer, b"partial");
            writer.write_all(b" response\nnext\n").await.unwrap();
            assert_eq!(
                bounded_line_buffered(&mut reader, &mut buffer, 256)
                    .await
                    .unwrap(),
                b"partial response"
            );
            assert!(buffer.is_empty());
            assert_eq!(
                bounded_line_buffered(&mut reader, &mut buffer, 256)
                    .await
                    .unwrap(),
                b"next"
            );
        });
    }

    #[cfg(windows)]
    async fn fixture_worker(mode: &str) -> (PathBuf, ModuleWorker) {
        let root =
            std::env::temp_dir().join(format!("opendesk-web-worker-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let script = r#"
$count = 0
$delayCount = 0
while ($null -ne ($line = [Console]::ReadLine())) {
    $request = $line | ConvertFrom-Json
    $result = @{value='ok'}
    if ($request.method -eq 'module.prepareShutdown') {
        $count++
        if ($env:OPENDESK_TEST_MODE -eq 'invalid' -and $count -eq 1) { $result = @{ready=$true;active=1} }
        elseif ($env:OPENDESK_TEST_MODE -eq 'busy' -and $count -lt 3) { $result = @{ready=$false;active=1} }
        else { $result = @{ready=$true;active=0} }
    }
    if ($request.method -eq 'demo.delay') { $delayCount++; $result = @{count=$delayCount} }
    if ($request.method -eq 'demo.count') { $result = @{count=$delayCount} }
    $response = @{protocolVersion=1;id=$request.id;result=$result} | ConvertTo-Json -Depth 8 -Compress
    if ($request.method -eq 'demo.delay') {
        $half = [int]($response.Length / 2)
        [Console]::Write($response.Substring(0,$half)); [Console]::Out.Flush()
        Start-Sleep -Milliseconds 150
        [Console]::WriteLine($response.Substring($half)); [Console]::Out.Flush()
    } else { [Console]::WriteLine($response); [Console]::Out.Flush() }
    if ($request.method -eq 'runtime.shutdown') { exit 0 }
}
"#;
        let mut command = Command::new("powershell.exe");
        command
            .args(["-NoProfile", "-NonInteractive", "-Command", script])
            .env("OPENDESK_TEST_MODE", mode)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .creation_flags(0x08000000 | 0x00000004);
        let mut child = command.spawn().unwrap();
        let job = OwnedJob::attach(child.id().unwrap()).unwrap();
        let stdin = child.stdin.take().unwrap();
        let stdout = BufReader::new(child.stdout.take().unwrap());
        let mut worker = ModuleWorker {
            child,
            stdin,
            stdout,
            _job: job,
            diagnostics: Arc::new(Diagnostics::new(&root)),
            module_id: "fixture".into(),
            web_app: true,
            pending_response: None,
            response_buffer: Vec::new(),
            shutdown_sent: false,
        };
        assert!(worker.request("demo.ping", json!({})).await.is_ok());
        (root, worker)
    }

    #[cfg(windows)]
    #[test]
    fn busy_web_worker_finishes_before_shutdown_and_control_error_keeps_it_alive() {
        tauri::async_runtime::block_on(async {
            let (root, mut worker) = fixture_worker("busy").await;
            let start = std::time::Instant::now();
            worker.stop().await.unwrap();
            assert!(start.elapsed() >= Duration::from_millis(450));
            assert!(worker.child.try_wait().unwrap().is_some());
            drop(worker);
            std::fs::remove_dir_all(root).unwrap();
            let (root, mut worker) = fixture_worker("invalid").await;
            let pid = worker.child.id();
            assert!(worker
                .stop()
                .await
                .unwrap_err()
                .contains("MODULE_SAFE_STOP_INVALID"));
            assert_eq!(worker.child.id(), pid);
            assert!(worker.child.try_wait().unwrap().is_none());
            assert!(worker.request("demo.ping", json!({})).await.is_ok());
            worker.stop().await.unwrap();
            drop(worker);
            std::fs::remove_dir_all(root).unwrap();
        });
    }

    #[cfg(windows)]
    #[test]
    fn timed_out_web_call_is_not_replayed_and_late_partial_reply_is_drained() {
        tauri::async_runtime::block_on(async {
            let (root, mut worker) = fixture_worker("delay").await;
            assert!(worker
                .request_with_wait("demo.delay", json!({}), Duration::from_millis(30))
                .await
                .is_err());
            assert!(worker.child.try_wait().unwrap().is_none());
            let result = worker
                .request("demo.count", json!({}))
                .await
                .unwrap_or_else(|error| panic!("{}", error.message));
            assert_eq!(result["count"], 1);
            worker.stop().await.unwrap();
            drop(worker);
            std::fs::remove_dir_all(root).unwrap();
        });
    }
}
