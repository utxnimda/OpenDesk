use crate::{
    companion, companion_menu, complete_settings_change,
    personalization::{CompanionChange, PetActionMode, PetMode},
    recall_saved_companion,
    settings::{Settings, Theme, UiStyle},
    AppState,
};
use serde_json::{json, Value};
use std::sync::atomic::Ordering;
use tauri::Manager;

async fn open_ready_menu(
    app: &tauri::AppHandle,
    model: &str,
    can_animate: bool,
) -> Result<companion_menu::MenuContext, String> {
    let state = app.state::<AppState>();
    let context = {
        let _guard = state.companion_show_lock.lock().await;
        let pet = state.settings.load()?.personalization.pet;
        let ready = state.companion_ready.lock().await;
        companion_menu::show(app, &pet, ready.as_deref(), model.to_owned(), can_animate)?
    };
    tokio::time::timeout(std::time::Duration::from_secs(20), async {
        loop {
            if state.companion_menu.is_ready(context.generation)? {
                break Ok::<(), String>(());
            }
            tokio::time::sleep(std::time::Duration::from_millis(30)).await;
        }
    })
    .await
    .map_err(|_| "Companion menu frontend/panel readiness timeout.")??;
    Ok(context)
}

fn menu_is_closed(app: &tauri::AppHandle) -> Result<bool, String> {
    Ok(app.state::<AppState>().companion_menu.context().is_err()
        && app
            .get_webview_window(companion_menu::LABEL)
            .map(|window| {
                window
                    .is_visible()
                    .map(|visible| !visible)
                    .map_err(|error| error.to_string())
            })
            .transpose()?
            .unwrap_or(true))
}

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
        // The real menu Vue surface must read its generation and report after nextTick.
        // Directly marking Rust ready here would not exercise the new WebView or ACL.
        let first_menu = open_ready_menu(&app, &model, true).await?;
        let menu_window = app.get_webview_window(companion_menu::LABEL).ok_or("Companion menu window missing")?;
        let menu_size = menu_window.inner_size().map_err(|error| error.to_string())?;
        let menu_position = menu_window.outer_position().map_err(|error| error.to_string())?;
        let monitor = menu_window.current_monitor().map_err(|error| error.to_string())?.ok_or("Companion menu monitor missing")?;
        let work_area = monitor.work_area();
        if menu_size.width > work_area.size.width || menu_size.height > work_area.size.height
            || menu_position.x < work_area.position.x || menu_position.y < work_area.position.y
            || i64::from(menu_position.x) + i64::from(menu_size.width) > i64::from(work_area.position.x) + i64::from(work_area.size.width)
            || i64::from(menu_position.y) + i64::from(menu_size.height) > i64::from(work_area.position.y) + i64::from(work_area.size.height) {
            return Err("Companion menu exceeded the monitor work area.".into());
        }
        let windows_before = app.webview_windows().len();
        let second_menu = open_ready_menu(&app, &model, false).await?;
        if second_menu.generation <= first_menu.generation || app.webview_windows().len() != windows_before || second_menu.can_animate {
            return Err("Companion menu did not reuse its owned surface with a new context.".into());
        }
        companion_menu::close(&app, Some(first_menu.generation))?;
        if !state.companion_menu.is_ready(second_menu.generation)? || !menu_window.is_visible().map_err(|error| error.to_string())? {
            return Err("A stale menu close hid the current menu.".into());
        }
        if state.companion_menu.mark_ready(first_menu.generation).is_ok()
            || companion_menu::authorize(&app, first_menu.generation, &settings.personalization.pet, Some(&model)).is_ok() {
            return Err("A stale companion menu generation was accepted.".into());
        }
        companion_menu::authorize(&app, second_menu.generation, &settings.personalization.pet, Some(&model))?;
        if state.settings.load()? != settings { return Err("Opening the companion menu changed saved settings.".into()); }
        companion_menu::close(&app, Some(second_menu.generation))?;
        if !menu_is_closed(&app)? { return Err("Companion menu stayed visible after close.".into()); }
        let mut legacy = serde_json::to_value(&settings).map_err(|error| error.to_string())?;
        legacy["personalization"]["pet"].as_object_mut().ok_or("Missing pet settings object")?.remove("actionMode");
        let legacy: Settings = serde_json::from_value(legacy).map_err(|error| error.to_string())?;
        if legacy.personalization.pet.action_mode != PetActionMode::Idle {
            return Err("Legacy pet settings did not default actionMode to idle.".into());
        }
        {
            let _guard = state.companion_show_lock.lock().await;
            let selected = state.settings.select_companion(Some(model.clone()))?;
            let mut expected = settings.personalization.pet.clone();
            expected.enabled = true;
            if expected.model_id.as_deref() != Some(model.as_str()) {
                expected.action_mode = PetActionMode::Idle;
            }
            expected.model_id = Some(model.clone());
            if selected.personalization.pet != expected {
                return Err("Selecting the active companion overwrote other pet preferences.".into());
            }
            let applied = recall_saved_companion(&app, &selected);
            complete_settings_change(&app, &state, selected, applied)?;
        }
        let resized = {
            let _guard = state.companion_show_lock.lock().await;
            let saved = state.settings.update_companion(CompanionChange::Size(256))?;
            let applied = companion::show(&app, &saved.personalization.pet);
            complete_settings_change(&app, &state, saved, applied)?
        };
        let window = app.get_webview_window(companion::LABEL).ok_or("Companion window missing after resize")?;
        tokio::time::timeout(std::time::Duration::from_secs(2), async {
            loop {
                let size = window.inner_size().map_err(|error| error.to_string())?;
                let scale = window.scale_factor().map_err(|error| error.to_string())?;
                let expected_width = (336.0 * scale).round();
                let expected_height = (356.0 * scale).round();
                if (f64::from(size.width) - expected_width).abs() <= 1.0 && (f64::from(size.height) - expected_height).abs() <= 1.0 {
                    break Ok::<(), String>(());
                }
                tokio::time::sleep(std::time::Duration::from_millis(20)).await;
            }
        }).await.map_err(|_| "Companion native resize did not reach the saved size.")??;
        let appearance = state.settings.save_appearance(Theme::Ocean, UiStyle::Codex, !settings.close_to_tray)?;
        if appearance.personalization != resized.personalization {
            return Err("Saving appearance overwrote pet preferences.".into());
        }
        complete_settings_change(&app, &state, appearance, Ok(()))?;
        open_ready_menu(&app, &model, true).await?;
        {
            let _guard = state.companion_show_lock.lock().await;
            let in_app = state.settings.update_companion(CompanionChange::Mode(PetMode::InApp))?;
            let applied = recall_saved_companion(&app, &in_app);
            complete_settings_change(&app, &state, in_app, applied)?;
            if companion::state(&app)?.visible {
                return Err("Desktop window remained visible after switching to in-app mode.".into());
            }
            if !menu_is_closed(&app)? { return Err("Companion menu survived switching away from desktop mode.".into()); }
            let desktop = state.settings.update_companion(CompanionChange::Mode(PetMode::Desktop))?;
            let applied = recall_saved_companion(&app, &desktop);
            complete_settings_change(&app, &state, desktop, applied)?;
            if !companion::state(&app)?.visible {
                return Err("Desktop window did not return after switching mode.".into());
            }
            let disabled = state.settings.select_companion(None)?;
            complete_settings_change(&app, &state, disabled, companion::hide(&app))?;
            if companion::state(&app)?.visible {
                return Err("Companion remained visible after selecting no role.".into());
            }
            let disabled_update = state.settings.update_companion(CompanionChange::Animated(false))?;
            if disabled_update.personalization.pet.enabled || disabled_update.personalization.pet.model_id.is_some() {
                return Err("Updating a disabled companion silently selected or enabled a role.".into());
            }
            let restored = state.settings.save(settings.clone())?;
            let applied = companion::show(&app, &restored.personalization.pet);
            complete_settings_change(&app, &state, restored, applied)?;
        }
        open_ready_menu(&app, &model, true).await?;
        companion::hide(&app)?;
        if companion::state(&app)?.visible {
            return Err("Companion remained visible after hide.".into());
        }
        if !menu_is_closed(&app)? { return Err("Hiding the companion did not close its menu.".into()); }
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
            "companionShowHide":true, "companionReopened":true, "windowOwnership":"same-process",
            "legacyActionModeDefault":true,"companionPartialUpdates":true,
            "companionSelectionPreservesPreferences":true,"appearancePreservesPet":true,
            "companionModeSwitch":true,"companionResize":true,"disabledCompanionRemainsDisabled":true,
            "menuPanelReady":true,"menuWindowReused":true,"menuStaleGenerationRejected":true,
            "menuClose":true,"menuClampedToWorkArea":true,"menuClosedOnPetHide":true,"menuClosedOnModeChange":true,
            "menuSizePhysical":{"width":menu_size.width,"height":menu_size.height},
            "menuPositionPhysical":{"x":menu_position.x,"y":menu_position.y}
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
