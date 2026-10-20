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

pub struct Session {
    pub installed: Installed,
    pub root: PathBuf,
    pub data: PathBuf,
    pub generation: String,
    pub token: String,
    pub worker: Option<ModuleWorker>,
    pub error: Option<String>,
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
}

impl Session {
    pub async fn start(
        installed: Installed,
        root: PathBuf,
        data: PathBuf,
        diagnostics: Arc<Diagnostics>,
    ) -> Result<Self, String> {
        std::fs::create_dir_all(&data).map_err(|e| e.to_string())?;
        let worker = if installed.manifest.worker.is_some() {
            Some(ModuleWorker::spawn(&installed, &root, &data, diagnostics).await?)
        } else {
            None
        };
        let entry = installed
            .manifest
            .frontend
            .strip_prefix("frontend/")
            .ok_or("MODULE_FRONTEND_INVALID")?;
        let server = StaticServer::start(root.join("frontend"), entry)?;
        Ok(Self {
            installed,
            root,
            data,
            generation: uuid::Uuid::new_v4().to_string(),
            token: uuid::Uuid::new_v4().to_string(),
            worker,
            error: None,
            server,
        })
    }
    pub fn describe(&self) -> Value {
        json!({"moduleId":self.installed.manifest.id,"version":self.installed.manifest.version,"digest":self.installed.digest,"generation":self.generation,"url":self.server.url(),"token":self.token,"workerPid":self.worker.as_ref().and_then(|worker| worker.child.id())})
    }
    pub async fn stop_worker(&mut self) -> Result<(), String> {
        if let Some(mut worker) = self.worker.take() {
            worker.stop().await?;
        }
        Ok(())
    }
    pub fn check_health(&mut self) {
        let ended = self
            .worker
            .as_mut()
            .is_some_and(|worker| !matches!(worker.child.try_wait(), Ok(None)));
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
                }
                Err(error) => {
                    self.error = Some(error.clone());
                    return Err(error);
                }
            }
        }
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
            Ok(value) => Ok(value),
            Err(error) => {
                if error.fatal {
                    if let Some(mut worker) = self.worker.take() {
                        let _ = worker.child.kill().await;
                    }
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
            if actual != expected || capabilities.len() != actual.len() {
                return Err("MODULE_CAPABILITIES_MISMATCH".into());
            }
            let identity = worker
                .request("module.identity", json!({}))
                .await
                .map_err(|e| e.message)?;
            if identity["id"] != installed.manifest.id
                || identity["version"] != installed.manifest.version
                || identity["hostApi"] != 1
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
        let id = uuid::Uuid::new_v4().to_string();
        let started = std::time::Instant::now();
        let result = match timeout(WAIT, self.exchange(&id, method, params)).await {
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
        self.stdin
            .write_all(&frame)
            .await
            .map_err(|e| e.to_string())?;
        self.stdin.flush().await.map_err(|e| e.to_string())?;
        let line = bounded_line(&mut self.stdout, RESPONSE_LIMIT).await?;
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
}

async fn bounded_line<R: AsyncBufRead + Unpin>(
    reader: &mut R,
    maximum: usize,
) -> Result<Vec<u8>, String> {
    let mut output = Vec::new();
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
            return Ok(output);
        }
    }
}
