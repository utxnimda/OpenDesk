//! Explicit --module-smoke only. Uses an isolated data root supplied by the runner.
use crate::{module_packages::ModuleManager, AppState};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    sync::atomic::Ordering,
    time::Duration,
};
use tauri::{Emitter, Manager};

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
    let state = app.state::<AppState>();
    let manager = state.modules.as_ref().map_err(Clone::clone)?;
    let initial_catalog = manager.catalog().await?;
    let baseline_version = field(module(&initial_catalog, "test-lab")?, "selectedVersion")?;
    let original_digest = digest(&initial_catalog, "test-lab", baseline_version)?;
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

    let rejected = manager
        .import_package(&fixtures.join("test-lab-2.0.0-incompatible.ndpkg"))
        .await?;
    let receipt = rejected["imports"]
        .as_array()
        .and_then(|rows| {
            rows.iter()
                .rev()
                .find(|row| row["fileName"] == "test-lab-2.0.0-incompatible.ndpkg")
        })
        .ok_or("Missing incompatible import receipt")?;
    if receipt["status"] != "rejected" || !Path::new(field(receipt, "storedPath")?).is_file() {
        return Err("Rejected import was not retained".into());
    }
    if manager.open("test-lab").await?["token"] != baseline["token"] {
        return Err("Rejected import replaced the active instance".into());
    }

    let imported = manager
        .import_package(&fixtures.join("test-lab-1.1.0.ndpkg"))
        .await?;
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
    let broken_catalog = manager
        .import_package(&fixtures.join("test-lab-1.2.0-ui-failure.ndpkg"))
        .await?;
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

    let extra_catalog = manager
        .import_package(&fixtures.join("test-extra-1.0.0.ndpkg"))
        .await?;
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
        json!({"status":"passed","appVersion":env!("CARGO_PKG_VERSION"),"realIframeVersions":[baseline_version,"1.1.0",baseline_version],"realIframeWorkerRoundTrip":true,"baseline":first,"updated":second,"reverted":third,"recoveredAfterUiFailure":recovered_reply,"newModule":extra_reply,"incompatibleOriginalRetained":true,"staleSessionRejected":true,"disabledExecutionRejected":true,"independentModuleUnchanged":true,"selectionPersisted":true}),
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
