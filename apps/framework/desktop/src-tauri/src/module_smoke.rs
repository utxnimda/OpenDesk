//! Explicit --module-smoke only. Uses an isolated data root supplied by the runner.
use crate::{module_packages::ModuleManager, AppState};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    io::Read,
    path::{Path, PathBuf},
    sync::atomic::Ordering,
    time::Duration,
};
use tauri::{Emitter, Manager};

struct Fixture {
    path: PathBuf,
    version: String,
    sha256: String,
}

fn fixture_path(root: &Path, file: &str) -> Result<PathBuf, String> {
    if file.starts_with('.')
        || !file.ends_with(".ndpkg")
        || !file.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || b"._-".contains(&byte)
        })
    {
        return Err("Smoke fixture must use a plain .ndpkg filename".into());
    }
    let path = root.join(file).canonicalize().map_err(|e| e.to_string())?;
    if path.parent() != Some(root) || !path.is_file() {
        return Err("Smoke fixture must remain inside its fixture directory".into());
    }
    Ok(path)
}

fn fixture(
    root: &Path,
    index: &Value,
    purpose: &str,
    id: &str,
    expected_version: Option<&str>,
) -> Result<Fixture, String> {
    if index["schemaVersion"] != 1 || index["testFixtures"] != true {
        return Err(
            "Smoke fixture index must declare schemaVersion 1 and testFixtures true".into(),
        );
    }
    let rows = index["packages"]
        .as_array()
        .ok_or("Smoke fixture index has no packages array")?;
    let matches: Vec<_> = rows
        .iter()
        .filter(|row| row["purpose"] == purpose)
        .collect();
    if matches.len() != 1 {
        return Err(format!("Smoke requires exactly one {purpose} fixture"));
    }
    let entry = matches[0];
    let version = field(entry, "version")?;
    if entry["id"] != id
        || expected_version.is_some_and(|expected| version != expected)
        || version.split('.').count() != 3
        || !version.split('.').all(|part| {
            !part.is_empty()
                && (part == "0" || !part.starts_with('0'))
                && part.bytes().all(|byte| byte.is_ascii_digit())
        })
    {
        return Err(format!("Smoke {purpose} fixture identity is invalid"));
    }
    let expected_hash = field(entry, "sha256")?;
    if expected_hash.len() != 64
        || !expected_hash
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(format!("Smoke {purpose} fixture digest is invalid"));
    }
    let path = fixture_path(root, field(entry, "file")?)?;
    let mut source = std::fs::File::open(&path).map_err(|e| e.to_string())?;
    let mut hash = Sha256::new();
    let mut buffer = [0; 65536];
    loop {
        let count = source.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    if format!("{:x}", hash.finalize()) != expected_hash {
        return Err(format!("Smoke {purpose} fixture digest mismatch"));
    }
    Ok(Fixture {
        path,
        version: version.to_owned(),
        sha256: expected_hash.to_owned(),
    })
}

fn field<'a>(value: &'a Value, key: &str) -> Result<&'a str, String> {
    value[key]
        .as_str()
        .ok_or_else(|| format!("Smoke missing {key}"))
}
fn module<'a>(catalog: &'a Value, id: &str) -> Result<&'a Value, String> {
    catalog["modules"]
        .as_array()
        .and_then(|rows| rows.iter().find(|row| row["id"] == id))
        .ok_or_else(|| format!("Smoke module missing: {id}"))
}
fn digest(catalog: &Value, id: &str, version: &str) -> Result<String, String> {
    module(catalog, id)?["versions"]
        .as_array()
        .and_then(|rows| rows.iter().find(|row| row["version"] == version))
        .and_then(|row| row["digest"].as_str())
        .map(str::to_owned)
        .ok_or_else(|| format!("Smoke version missing: {id}@{version}"))
}
async fn ui_ready(app: &tauri::AppHandle, session: &Value) -> Result<(), String> {
    let token = field(session, "token")?;
    for _ in 0..150 {
        let state = app.state::<AppState>();
        if let Some(ready) = state.module_ui_ready.lock().await.get(token) {
            if ready["id"] == session["moduleId"] && ready["version"] == session["version"] {
                if let Some(reply) = state.module_ui_calls.lock().await.get(token) {
                    if reply["version"] == session["version"] {
                        return Ok(());
                    }
                }
            }
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    Err(format!(
        "Real iframe handshake/echo timed out: {}@{}",
        session["moduleId"], session["version"]
    ))
}
fn changed(app: &tauri::AppHandle) -> Result<(), String> {
    app.emit("modules-changed", ()).map_err(|e| e.to_string())
}

async fn exercise(app: &tauri::AppHandle, fixtures: &Path) -> Result<Value, String> {
    let fixtures = fixtures.canonicalize().map_err(|e| e.to_string())?;
    let index_path = fixtures
        .join("fixtures.json")
        .canonicalize()
        .map_err(|e| e.to_string())?;
    if index_path.parent() != Some(fixtures.as_path()) || !index_path.is_file() {
        return Err("Smoke fixture index must remain inside its fixture directory".into());
    }
    let index: Value =
        serde_json::from_slice(&std::fs::read(index_path).map_err(|e| e.to_string())?)
            .map_err(|e| format!("Invalid smoke fixture index: {e}"))?;
    let baseline_fixture = fixture(&fixtures, &index, "baseline", "test-lab", None)?;
    let update_fixture = fixture(
        &fixtures,
        &index,
        "compatible-update",
        "test-lab",
        Some("1.1.0"),
    )?;
    let incompatible_fixture =
        fixture(&fixtures, &index, "incompatible", "test-lab", Some("2.0.0"))?;
    let broken_fixture = fixture(&fixtures, &index, "ui-failure", "test-lab", Some("1.2.0"))?;
    let extra_fixture = fixture(&fixtures, &index, "new-module", "test-extra", Some("1.0.0"))?;
    let state = app.state::<AppState>();
    let manager = state.modules.as_ref().map_err(Clone::clone)?;
    let initial_catalog = manager.catalog().await?;
    let initial_modules = initial_catalog["modules"]
        .as_array()
        .ok_or("Smoke catalog has no modules")?;
    if initial_modules.len() != 1 || initial_modules[0]["id"] != "tower-assistant" {
        return Err("Module smoke requires a clean App bundle containing only tower-assistant; test-lab must be imported from fixtures".into());
    }
    let baseline_version = baseline_fixture.version.as_str();
    let imported_baseline = manager.import_package(&baseline_fixture.path).await?;
    let original_digest = digest(&imported_baseline, "test-lab", baseline_version)?;
    if original_digest != baseline_fixture.sha256 {
        return Err("Imported baseline differs from the fixture index".into());
    }
    manager
        .select("test-lab", baseline_version, &original_digest)
        .await?;
    manager.set_enabled("test-lab", true).await?;
    changed(app)?;
    app.emit("modules-smoke-navigate", json!({"id":"test-lab"}))
        .map_err(|e| e.to_string())?;
    let baseline = manager.open("test-lab").await?;
    ui_ready(app, &baseline).await?;
    let old_token = field(&baseline, "token")?;
    let first = manager
        .call(old_token, "test.echo", json!({"text":"baseline"}))
        .await?;
    if first["version"] != baseline_version {
        return Err("Baseline worker version mismatch".into());
    }
    let independent = manager.open("tower-assistant").await?;

    let rejected = manager.import_package(&incompatible_fixture.path).await?;
    let receipt = rejected["imports"]
        .as_array()
        .and_then(|rows| {
            rows.iter().rev().find(|row| {
                row["fileName"].as_str()
                    == incompatible_fixture
                        .path
                        .file_name()
                        .and_then(|name| name.to_str())
            })
        })
        .ok_or("Missing incompatible import receipt")?;
    if receipt["status"] != "rejected" || !Path::new(field(receipt, "storedPath")?).is_file() {
        return Err("Rejected import was not retained".into());
    }
    if manager.open("test-lab").await?["token"] != baseline["token"] {
        return Err("Rejected import replaced the active instance".into());
    }

    let imported = manager.import_package(&update_fixture.path).await?;
    let newer_digest = digest(&imported, "test-lab", "1.1.0")?;
    manager.select("test-lab", "1.1.0", &newer_digest).await?;
    changed(app)?;
    let newer = manager.open("test-lab").await?;
    ui_ready(app, &newer).await?;
    let second = manager
        .call(
            field(&newer, "token")?,
            "test.echo",
            json!({"text":"updated"}),
        )
        .await?;
    if second["version"] != "1.1.0" || second["marker"] == first["marker"] {
        return Err("Worker implementation did not switch".into());
    }
    if manager
        .call(old_token, "test.echo", json!({"text":"stale"}))
        .await
        .is_ok()
    {
        return Err("Old session was still accepted".into());
    }
    let independent_after = manager.open("tower-assistant").await?;
    if independent_after["token"] != independent["token"]
        || independent_after["workerPid"] != independent["workerPid"]
    {
        return Err("Switching B disturbed A".into());
    }
    manager
        .select("test-lab", baseline_version, &original_digest)
        .await?;
    changed(app)?;
    let reverted = manager.open("test-lab").await?;
    ui_ready(app, &reverted).await?;
    let third = manager
        .call(
            field(&reverted, "token")?,
            "test.echo",
            json!({"text":"reverted"}),
        )
        .await?;
    if third["version"] != baseline_version || third["marker"] != first["marker"] {
        return Err("Rollback implementation mismatch".into());
    }

    // Valid archive and host API, but the candidate page never completes its SDK handshake.
    let broken_catalog = manager.import_package(&broken_fixture.path).await?;
    let broken_digest = digest(&broken_catalog, "test-lab", "1.2.0")?;
    manager.select("test-lab", "1.2.0", &broken_digest).await?;
    changed(app)?;
    let mut restored = false;
    for _ in 0..250 {
        let catalog = manager.catalog().await?;
        let row = module(&catalog, "test-lab")?;
        if row["selectedVersion"] == baseline_version
            && row["lastError"]
                .as_str()
                .is_some_and(|value| !value.is_empty())
        {
            restored = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    if !restored {
        return Err("Failed iframe did not restore the previous selected package".into());
    }
    let recovered = manager.open("test-lab").await?;
    ui_ready(app, &recovered).await?;
    let recovered_reply = manager
        .call(
            field(&recovered, "token")?,
            "test.echo",
            json!({"text":"recovered after UI failure"}),
        )
        .await?;
    if recovered_reply["version"] != baseline_version {
        return Err("Recovered worker is not the previous version".into());
    }
    manager.set_enabled("test-lab", false).await?;
    changed(app)?;
    if manager.open("test-lab").await.is_ok()
        || manager
            .call(
                field(&recovered, "token")?,
                "test.echo",
                json!({"text":"disabled"}),
            )
            .await
            .is_ok()
    {
        return Err("Disabled module accepted execution".into());
    }
    // Wait for the actual parent UI to dispose the disabled iframe before the
    // next action, just as a user waits for the toggle result to render.
    let mut frame_closed = false;
    for _ in 0..100 {
        if state
            .module_ui_closed
            .lock()
            .await
            .contains(field(&recovered, "token")?)
        {
            frame_closed = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    if !frame_closed {
        return Err("Disabled iframe was not disposed by the parent UI".into());
    }
    manager.set_enabled("test-lab", true).await?;
    changed(app)?;
    app.emit("modules-smoke-navigate", json!({"id":"test-lab"}))
        .map_err(|e| e.to_string())?;
    let enabled = manager.open("test-lab").await?;
    ui_ready(app, &enabled).await?;

    let extra_catalog = manager.import_package(&extra_fixture.path).await?;
    let extra_digest = digest(&extra_catalog, "test-extra", "1.0.0")?;
    manager.select("test-extra", "1.0.0", &extra_digest).await?;
    manager.set_enabled("test-extra", true).await?;
    changed(app)?;
    app.emit("modules-smoke-navigate", json!({"id":"test-extra"}))
        .map_err(|e| e.to_string())?;
    let extra = manager.open("test-extra").await?;
    ui_ready(app, &extra).await?;
    let extra_reply = manager
        .call(
            field(&extra, "token")?,
            "test.echo",
            json!({"text":"new module"}),
        )
        .await?;
    manager.shutdown().await;
    let bundled_root = if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../../dist/bundled-modules")
    } else {
        app.path()
            .resource_dir()
            .map_err(|e| e.to_string())?
            .join("bundled-modules")
    };
    let restarted = ModuleManager::new(
        state.data_root.clone(),
        bundled_root,
        state.diagnostics.clone(),
    )?;
    let persisted = restarted.catalog().await?;
    if module(&persisted, "test-lab")?["selectedVersion"] != baseline_version
        || module(&persisted, "test-extra")?["enabled"] != true
    {
        return Err("Selections did not survive manager restart".into());
    }
    restarted.shutdown().await;
    Ok(
        json!({"status":"passed","appVersion":env!("CARGO_PKG_VERSION"),"initialBundleOnlyTower":true,"baselineImportedFromFixture":true,"baselineFixtureSha256":original_digest,"realIframeVersions":[baseline_version,"1.1.0",baseline_version],"realIframeWorkerRoundTrip":true,"baseline":first,"updated":second,"reverted":third,"recoveredAfterUiFailure":recovered_reply,"newModule":extra_reply,"incompatibleOriginalRetained":true,"staleSessionRejected":true,"disabledExecutionRejected":true,"independentModuleUnchanged":true,"selectionPersisted":true}),
    )
}

pub async fn run(app: tauri::AppHandle) {
    let fixtures = std::env::var_os("OPENDESK_MODULE_FIXTURES").map(PathBuf::from);
    let result = match fixtures {
        Some(path) if path.is_absolute() => exercise(&app, &path).await,
        _ => Err("OPENDESK_MODULE_FIXTURES must name an absolute test fixture directory".into()),
    };
    let state = app.state::<AppState>();
    if let Ok(manager) = &state.modules {
        manager.shutdown().await;
    }
    let _ = state.worker.stop().await;
    let report = match &result {
        Ok(value) => value.clone(),
        Err(error) => json!({"status":"failed","error":error}),
    };
    let saved = std::fs::write(
        state.data_root.join("module-smoke-result.json"),
        serde_json::to_vec_pretty(&report).unwrap_or_default(),
    );
    state.diagnostics.record(
        if result.is_ok() { "info" } else { "error" },
        "modules.smoke",
        &report.to_string(),
    );
    state.exiting.store(true, Ordering::SeqCst);
    app.exit(if result.is_ok() && saved.is_ok() {
        0
    } else {
        1
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn setup() -> (PathBuf, Value) {
        let root = std::env::temp_dir().join(format!("opendesk-fixtures-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let root = root.canonicalize().unwrap();
        let bytes = b"synthetic package bytes; archive validation belongs to the package store";
        std::fs::write(root.join("test-lab-1.0.6.ndpkg"), bytes).unwrap();
        let index = json!({"schemaVersion":1,"testFixtures":true,"packages":[{
            "id":"test-lab","version":"1.0.6","purpose":"baseline",
            "file":"test-lab-1.0.6.ndpkg","sha256":format!("{:x}", Sha256::digest(bytes))
        }]});
        (root, index)
    }

    #[test]
    fn baseline_is_resolved_from_fixture_index_and_checked_against_bytes() {
        let (root, index) = setup();
        let baseline = fixture(&root, &index, "baseline", "test-lab", None).unwrap();
        assert_eq!(baseline.version, "1.0.6");
        assert_eq!(baseline.path.parent(), Some(root.as_path()));
        std::fs::write(&baseline.path, b"changed package").unwrap();
        assert!(fixture(&root, &index, "baseline", "test-lab", None)
            .err()
            .unwrap()
            .contains("digest mismatch"));
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn fixture_index_rejects_missing_duplicate_and_wrong_identity_entries() {
        let (root, index) = setup();
        let mut invalid = index.clone();
        invalid["packages"].as_array_mut().unwrap().clear();
        assert!(fixture(&root, &invalid, "baseline", "test-lab", None).is_err());
        invalid = index.clone();
        invalid["packages"]
            .as_array_mut()
            .unwrap()
            .push(index["packages"][0].clone());
        assert!(fixture(&root, &invalid, "baseline", "test-lab", None).is_err());
        for (key, value) in [
            ("id", json!("other-module")),
            ("version", json!("1.0.06")),
            ("sha256", json!("not-a-digest")),
        ] {
            invalid = index.clone();
            invalid["packages"][0][key] = value;
            assert!(fixture(&root, &invalid, "baseline", "test-lab", None).is_err());
        }
        invalid = index.clone();
        invalid["testFixtures"] = json!(false);
        assert!(fixture(&root, &invalid, "baseline", "test-lab", None).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn fixture_filenames_cannot_escape_the_explicit_directory() {
        let (root, index) = setup();
        for file in [
            "../test.ndpkg",
            "sub/test.ndpkg",
            r"sub\test.ndpkg",
            "C:/test.ndpkg",
            ".ndpkg",
            "test.ndpkg:stream",
        ] {
            let mut invalid = index.clone();
            invalid["packages"][0]["file"] = json!(file);
            assert!(fixture(&root, &invalid, "baseline", "test-lab", None)
                .err()
                .unwrap()
                .contains("plain .ndpkg filename"));
        }
        std::fs::remove_dir_all(root).unwrap();
    }
}
