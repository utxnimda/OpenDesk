use crate::{companion, personalization::PetMode, AppState};
use serde_json::{json, Value};
use std::sync::atomic::Ordering;
use tauri::Manager;

pub async fn run(app: tauri::AppHandle) {
    let state = app.state::<AppState>();
    let result = async {
        let settings = state.settings.load()?;
        if !settings.personalization.pet.enabled
            || settings.personalization.pet.mode != PetMode::Desktop
        {
            return Err(
                "Companion smoke requires an enabled desktop companion; pass --enable-companion."
                    .into(),
            );
        }
        state.settings.save(settings.clone())?;
        if state.settings.load()? != settings {
            return Err("Companion settings round trip failed.".into());
        }
        {
            let _guard = state.companion_show_lock.lock().await;
            companion::show(&app, &settings.personalization.pet)?;
        }
        let model = tokio::time::timeout(std::time::Duration::from_secs(30), async {
            loop {
                if let Some(model) = state.companion_ready.lock().await.clone() {
                    break model;
                }
                tokio::time::sleep(std::time::Duration::from_millis(50)).await;
            }
        })
        .await
        .map_err(|_| "Companion image/frontend ready timeout.")?;
        if settings
            .personalization
            .pet
            .model_id
            .as_ref()
            .is_some_and(|expected| expected != &model)
        {
            return Err("Companion displayed a different model from the saved preference.".into());
        }
        if !companion::state(&app)?.visible {
            return Err("Companion was not visible after show.".into());
        }
        companion::hide(&app)?;
        if companion::state(&app)?.visible {
            return Err("Companion remained visible after hide.".into());
        }
        {
            let _guard = state.companion_show_lock.lock().await;
            companion::show(&app, &settings.personalization.pet)?;
        }
        if !companion::state(&app)?.visible {
            return Err("Companion did not reopen.".into());
        }
        Ok::<Value, String>(json!({
            "status":"passed", "appVersion":env!("CARGO_PKG_VERSION"), "frontendMounted":true,
            "settingsRoundTrip":true, "companionImageReady":true, "companionModel":model,
            "companionShowHide":true, "companionReopened":true, "windowOwnership":"same-process"
        }))
    }
    .await;
    let report = match &result {
        Ok(value) => value.clone(),
        Err(error) => json!({"status":"failed","error":error}),
    };
    let report_path = state.data_root.join("companion-smoke-result.json");
    let written = serde_json::to_vec_pretty(&report)
        .map_err(|error| error.to_string())
        .and_then(|bytes| std::fs::write(&report_path, bytes).map_err(|error| error.to_string()));
    if let Err(error) = &written {
        state
            .diagnostics
            .record("error", "companion.smoke_report", error);
    }
    if let Ok(manager) = &state.modules {
        manager.shutdown().await;
    }
    let _ = state.worker.stop().await;
    state.exiting.store(true, Ordering::SeqCst);
    app.exit(if result.is_ok() && written.is_ok() {
        0
    } else {
        1
    });
}
