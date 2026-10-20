use crate::{diagnostics::Diagnostics, process_job::OwnedJob};
use serde_json::{json, Value};
use std::{path::PathBuf, process::Stdio, sync::Arc, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::Mutex,
    time::timeout,
};

const MAX_REQUEST_FRAME: usize = 65_536;
const MAX_FRAME: usize = 2 * 1024 * 1024;
const PUBLIC_METHODS: &str = include_str!("../../../../../config/worker-methods.json");
const REQUEST_TIMEOUT: Duration = Duration::from_secs(8);
pub struct WorkerManager {
    process: Mutex<Option<Worker>>,
    diagnostics: Arc<Diagnostics>,
}
struct Worker {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    _job: OwnedJob,
    diagnostics: Arc<Diagnostics>,
}

#[derive(Debug)]
enum RequestError {
    Transport(String),
    Remote { code: String, message: String },
}
impl From<String> for RequestError {
    fn from(value: String) -> Self {
        Self::Transport(value)
    }
}
impl From<&str> for RequestError {
    fn from(value: &str) -> Self {
        Self::Transport(value.into())
    }
}
impl std::fmt::Display for RequestError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Transport(message) => write!(f, "{message}"),
            Self::Remote { code, message } => write!(f, "{code}: {message}"),
        }
    }
}

impl WorkerManager {
    pub fn new(diagnostics: Arc<Diagnostics>) -> Self {
        Self {
            process: Mutex::new(None),
            diagnostics,
        }
    }
    pub async fn state(&self) -> &'static str {
        let mut slot = self.process.lock().await;
        if let Some(worker) = slot.as_mut() {
            if matches!(worker.child.try_wait(), Ok(None)) {
                return "running";
            }
            slot.take();
        }
        "stopped"
    }
    pub async fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        // Application composition owns the fixed public capability list.
        // The worker separately validates each registered method's parameters.
        let public_methods: Vec<String> = serde_json::from_str(PUBLIC_METHODS)
            .map_err(|_| "WORKER_POLICY_INVALID: public methods configuration is invalid")?;
        if !public_methods.iter().any(|entry| entry == method) {
            return Err("METHOD_NOT_ALLOWED: unregistered worker command".into());
        }
        if !params.is_object() {
            return Err("INVALID_PARAMS: expected object".into());
        }
        let mut slot = self.process.lock().await;
        if let Some(worker) = slot.as_mut() {
            if !matches!(worker.child.try_wait(), Ok(None)) {
                slot.take();
            }
        }
        if slot.is_none() {
            let mut worker = Worker::spawn(self.diagnostics.clone()).await?;
            let hello =
                match timeout(REQUEST_TIMEOUT, worker.request("runtime.hello", json!({}))).await {
                    Ok(Ok(value)) if value["protocolVersion"] == 1 => value,
                    result => {
                        let _ = worker.child.kill().await;
                        return Err(format!("WORKER_HANDSHAKE_FAILED: {result:?}"));
                    }
                };
            if hello["worker"] != "nimdadesk-python" {
                let _ = worker.child.kill().await;
                return Err("Unexpected worker identity".into());
            }
            self.diagnostics
                .record("info", "worker.started", "protocol=1");
            *slot = Some(worker);
        }
        let result = timeout(
            REQUEST_TIMEOUT,
            slot.as_mut()
                .expect("worker initialized")
                .request(method, params),
        )
        .await;
        match result {
            Ok(Ok(value)) => Ok(value),
            Ok(Err(error @ RequestError::Remote { .. })) => Err(error.to_string()),
            other => {
                if let Some(mut worker) = slot.take() {
                    let _ = worker.child.kill().await;
                }
                self.diagnostics
                    .record("error", "worker.request_failed", method);
                match other {
                    Ok(Err(error)) => Err(error.to_string()),
                    _ => Err("WORKER_TIMEOUT: worker stopped; request was not replayed".into()),
                }
            }
        }
    }
    pub async fn stop(&self) -> Result<(), String> {
        let mut slot = self.process.lock().await;
        if let Some(mut worker) = slot.take() {
            let graceful = timeout(Duration::from_secs(2), async {
                worker.request("runtime.shutdown", json!({})).await?;
                worker
                    .child
                    .wait()
                    .await
                    .map_err(|e| RequestError::Transport(e.to_string()))
            })
            .await;
            if !matches!(graceful, Ok(Ok(status)) if status.success()) {
                if let Err(error) = worker.child.kill().await {
                    if !matches!(worker.child.try_wait(), Ok(Some(_))) {
                        self.diagnostics.record(
                            "error",
                            "worker.stop_failed",
                            "termination unconfirmed; job cleanup requested",
                        );
                        return Err(format!("WORKER_STOP_FAILED: {error}"));
                    }
                }
            }
            self.diagnostics
                .record("info", "worker.stopped", "owned worker released");
        }
        Ok(())
    }
}

impl Worker {
    async fn spawn(diagnostics: Arc<Diagnostics>) -> Result<Self, String> {
        let mut command;
        if cfg!(debug_assertions) {
            let python = std::env::var_os("NIMDADESK_PYTHON")
                .ok_or("Set NIMDADESK_PYTHON or launch using npm run dev")?;
            let source = std::env::var_os("NIMDADESK_WORKER_SOURCE")
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                        .join("../../../../apps/composition/worker")
                });
            command = Command::new(python);
            command.arg("-u").arg(source.join("freeze_entry.py"));
        } else {
            let executable = std::env::current_exe().map_err(|e| e.to_string())?;
            let name = if cfg!(windows) {
                "nimdadesk-worker.exe"
            } else {
                "nimdadesk-worker"
            };
            command = Command::new(
                executable
                    .parent()
                    .ok_or("Invalid executable directory")?
                    .join(name),
            );
        }
        command
            .env_remove("PYTHONPATH")
            .env_remove("PYTHONHOME")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        // Start suspended: even Python venv redirectors cannot spawn an unowned
        // descendant before the Job Object is attached. OwnedJob resumes it.
        command.creation_flags(0x08000000 | 0x00000004); // CREATE_NO_WINDOW | CREATE_SUSPENDED
        let mut child = command
            .spawn()
            .map_err(|e| format!("WORKER_START_FAILED: {e}"))?;
        let job = match OwnedJob::attach(
            child
                .id()
                .ok_or("Worker exited before ownership established")?,
        ) {
            Ok(job) => job,
            Err(error) => {
                let _ = child.kill().await;
                return Err(format!("WORKER_OWNERSHIP_FAILED: {error}"));
            }
        };
        let stdin = child.stdin.take().ok_or("Worker stdin unavailable")?;
        let stdout = BufReader::new(child.stdout.take().ok_or("Worker stdout unavailable")?);
        if let Some(stderr) = child.stderr.take() {
            let diagnostics = diagnostics.clone();
            tauri::async_runtime::spawn(async move {
                let mut reader = BufReader::new(stderr);
                while let Ok(line) = bounded_line(&mut reader).await {
                    if line.is_empty() {
                        break;
                    }
                    // Only framework worker emits stderr; never log request payloads.
                    diagnostics.record("info", "worker.stderr", &String::from_utf8_lossy(&line));
                }
            });
        }
        Ok(Self {
            child,
            stdin,
            stdout,
            _job: job,
            diagnostics,
        })
    }
    async fn request(&mut self, method: &str, params: Value) -> Result<Value, RequestError> {
        let id = uuid::Uuid::new_v4().to_string();
        let started = std::time::Instant::now();
        let result = self.exchange(&id, method, params).await;
        self.diagnostics.record(
            if result.is_ok() { "info" } else { "error" },
            "worker.response",
            &format!(
                "requestId={id} method={method} elapsedMs={} outcome={}",
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
    ) -> Result<Value, RequestError> {
        let mut frame = serde_json::to_vec(
            &json!({"protocolVersion":1,"id":id,"method":method,"params":params}),
        )
        .map_err(|e| e.to_string())?;
        if frame.len() > MAX_REQUEST_FRAME {
            return Err("REQUEST_TOO_LARGE".into());
        }
        frame.push(b'\n');
        self.stdin
            .write_all(&frame)
            .await
            .map_err(|e| e.to_string())?;
        self.stdin.flush().await.map_err(|e| e.to_string())?;
        let line = bounded_line(&mut self.stdout).await?;
        if line.is_empty() {
            return Err("WORKER_DISCONNECTED: outcome unknown; no automatic replay".into());
        }
        let response: Value =
            serde_json::from_slice(&line).map_err(|e| format!("INVALID_WORKER_RESPONSE: {e}"))?;
        if response["protocolVersion"] != 1 || response["id"] != id {
            return Err("WORKER_PROTOCOL_MISMATCH".into());
        }
        if let Some(error) = response.get("error") {
            return Err(RequestError::Remote {
                code: error["code"]
                    .as_str()
                    .ok_or("INVALID_ERROR_ENVELOPE")?
                    .into(),
                message: error["message"]
                    .as_str()
                    .ok_or("INVALID_ERROR_ENVELOPE")?
                    .into(),
            });
        }
        response
            .get("result")
            .cloned()
            .ok_or("WORKER_RESPONSE_MISSING_RESULT".into())
    }
}

async fn bounded_line<R: tokio::io::AsyncBufRead + Unpin>(
    reader: &mut R,
) -> Result<Vec<u8>, String> {
    let mut output = Vec::new();
    loop {
        let chunk = reader.fill_buf().await.map_err(|e| e.to_string())?;
        if chunk.is_empty() {
            return if output.is_empty() {
                Ok(output)
            } else {
                Err("TRUNCATED_WORKER_FRAME".into())
            };
        }
        let end = chunk.iter().position(|&b| b == b'\n').map(|n| n + 1);
        let count = end.unwrap_or(chunk.len());
        if output.len() + count > MAX_FRAME + 2 {
            return Err("WORKER_FRAME_TOO_LARGE".into());
        }
        output.extend_from_slice(&chunk[..count]);
        reader.consume(count);
        if end.is_some() {
            output.pop(); // Exactly one LF, then an optional CR.
            if output.last() == Some(&b'\r') {
                output.pop();
            }
            if output.len() > MAX_FRAME {
                return Err("WORKER_FRAME_TOO_LARGE".into());
            }
            return Ok(output);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn frame_reader_rejects_truncation_and_oversize() {
        tauri::async_runtime::block_on(async {
            let mut valid = BufReader::new(&b"{\"ok\":true}\n"[..]);
            assert_eq!(bounded_line(&mut valid).await.unwrap(), br#"{"ok":true}"#);
            let mut truncated = BufReader::new(&b"unfinished"[..]);
            assert!(bounded_line(&mut truncated).await.is_err());
            let oversized = vec![b'x'; MAX_FRAME + 3];
            assert!(bounded_line(&mut BufReader::new(oversized.as_slice()))
                .await
                .is_err());
        });
    }
}
