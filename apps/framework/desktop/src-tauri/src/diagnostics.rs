use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
    sync::Mutex,
    time::{SystemTime, UNIX_EPOCH},
};

pub struct Diagnostics {
    path: PathBuf,
    lock: Mutex<()>,
}
impl Diagnostics {
    pub fn new(root: &std::path::Path) -> Self {
        Self {
            path: root.join("logs/framework.jsonl"),
            lock: Mutex::new(()),
        }
    }
    pub fn record(&self, level: &str, event: &str, detail: &str) {
        let Ok(_guard) = self.lock.lock() else {
            return;
        };
        let result = (|| -> std::io::Result<()> {
            fs::create_dir_all(self.path.parent().expect("log parent"))?;
            // Bounded local diagnostic retention: current file + one previous file.
            if self
                .path
                .metadata()
                .map(|m| m.len() > 2 * 1024 * 1024)
                .unwrap_or(false)
            {
                let previous = self.path.with_file_name("framework.previous.jsonl");
                if previous.exists() {
                    fs::remove_file(&previous)?;
                }
                fs::rename(&self.path, previous)?;
            }
            let record = serde_json::json!({
                "timestampMs": SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis(),
                "level": level, "event": event, "detail": detail
            });
            writeln!(
                OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&self.path)?,
                "{record}"
            )?;
            Ok(())
        })();
        if let Err(error) = result {
            eprintln!("NimdaDesk diagnostic write failed: {error}");
        }
    }
}
