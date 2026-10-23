mod companion;
mod companion_menu;
mod companion_smoke;
mod diagnostics;
mod module_packages;
mod module_smoke;
mod personalization;
mod process_job;
mod settings;
mod worker;

use diagnostics::Diagnostics;
use module_packages::ModuleManager;
use personalization::{CompanionChange, PetMode};
use serde_json::{json, Value};
use settings::{Settings, SettingsStore, Theme, UiStyle};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};
use tauri::{Emitter, Manager, State};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use worker::WorkerManager;

struct AppState {
    data_root: PathBuf,
    settings: SettingsStore,
    diagnostics: Arc<Diagnostics>,
    worker: WorkerManager,
    modules: Result<ModuleManager, String>,
    exiting: AtomicBool,
    smoke_test: bool,
    module_smoke: bool,
    module_smoke_started: AtomicBool,
    companion_smoke: bool,
    companion_smoke_started: AtomicBool,
    companion_ready: tokio::sync::Mutex<Option<String>>,
    companion_show_lock: tokio::sync::Mutex<()>,
    companion_menu: companion_menu::MenuSession,
    module_ui_ready: tokio::sync::Mutex<std::collections::BTreeMap<String, Value>>,
    module_ui_calls: tokio::sync::Mutex<std::collections::BTreeMap<String, Value>>,
    module_ui_closed: tokio::sync::Mutex<std::collections::HashSet<String>>,
}

#[tauri::command]
async fn host_info(state: State<'_, AppState>) -> Result<Value, String> {
    Ok(json!({
        "appVersion": env!("CARGO_PKG_VERSION"), "platform": std::env::consts::OS,
        "architecture": std::env::consts::ARCH, "dataDir": state.data_root,
        "workerState": state.worker.state().await, "packaged": !cfg!(debug_assertions),
        "moduleSmoke": state.module_smoke
    }))
}
#[tauri::command]
fn settings_get(state: State<'_, AppState>) -> Result<Settings, String> {
    state.settings.load()
}

fn validate_command_request(
    label: &str,
    allowed_windows: &[&str],
    body: &tauri::ipc::InvokeBody,
    fields: &[&str],
) -> Result<(), String> {
    if !allowed_windows.contains(&label) {
        return Err(
            "This window is not allowed to perform the requested settings operation.".into(),
        );
    }
    let tauri::ipc::InvokeBody::Json(Value::Object(arguments)) = body else {
        return Err("Command arguments must be a JSON object.".into());
    };
    if arguments
        .keys()
        .any(|field| !fields.contains(&field.as_str()))
    {
        return Err("Unknown command argument; settings were not changed.".into());
    }
    if fields.iter().any(|field| !arguments.contains_key(*field)) {
        return Err("Missing command argument; settings were not changed.".into());
    }
    Ok(())
}

fn validate_companion_action_request(
    label: &str,
    body: &tauri::ipc::InvokeBody,
    fields: &[&str],
    generation: Option<u64>,
) -> Result<(), String> {
    if label == companion_menu::LABEL {
        let mut menu_fields = fields.to_vec();
        menu_fields.push("menuGeneration");
        validate_command_request(label, &[companion_menu::LABEL], body, &menu_fields)?;
        if generation.is_none() {
            return Err("A companion menu generation is required.".into());
        }
        Ok(())
    } else {
        validate_command_request(label, &["main", companion::LABEL], body, fields)
    }
}

async fn authorize_companion_menu_action(
    app: &tauri::AppHandle,
    state: &AppState,
    generation: Option<u64>,
) -> Result<(), String> {
    if let Some(generation) = generation {
        let pet = state.settings.load()?.personalization.pet;
        let ready = state.companion_ready.lock().await;
        companion_menu::authorize(app, generation, &pet, ready.as_deref())?;
    }
    Ok(())
}

fn record_settings_write(
    state: &AppState,
    operation: &str,
    result: Result<Settings, String>,
) -> Result<Settings, String> {
    state.diagnostics.record(
        if result.is_ok() { "info" } else { "error" },
        operation,
        if result.is_ok() {
            "saved"
        } else {
            "save failed; existing data preserved"
        },
    );
    result
}

fn complete_settings_change(
    app: &tauri::AppHandle,
    state: &AppState,
    saved: Settings,
    applied: Result<(), String>,
) -> Result<Settings, String> {
    let menu_effect = companion_menu::reconcile(app, &saved.personalization.pet);
    // Notify after every committed change, even when a native window effect fails.
    let notification = app.emit("settings-changed", ()).map_err(|error| {
        state
            .diagnostics
            .record("error", "settings.notification", &error.to_string());
        format!("Settings were saved, but window notification failed: {error}")
    });
    if let Err(error) = applied.and(menu_effect) {
        state
            .diagnostics
            .record("error", "companion.apply_settings", &error);
        return Err(format!(
            "Settings were saved, but the companion window could not be updated: {error}"
        ));
    }
    notification?;
    Ok(saved)
}

fn recall_saved_companion(app: &tauri::AppHandle, settings: &Settings) -> Result<(), String> {
    let pet = &settings.personalization.pet;
    if !pet.enabled {
        companion::hide(app)?;
        return show_main_result(app);
    }
    match pet.mode {
        PetMode::Desktop => companion::show(app, pet),
        PetMode::InApp => {
            companion::hide(app)?;
            show_main_result(app)?;
            app.emit_to("main", "companion-recall", ())
                .map_err(|error| error.to_string())
        }
    }
}

#[tauri::command]
async fn companion_select(
    model_id: Option<String>,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Settings, String> {
    validate_command_request(window.label(), &["main"], request.body(), &["modelId"])?;
    let _guard = state.companion_show_lock.lock().await;
    let saved = record_settings_write(
        &state,
        "companion.select",
        state.settings.select_companion(model_id),
    )?;
    let applied = if saved.personalization.pet.enabled {
        recall_saved_companion(&app, &saved)
    } else {
        companion::hide(&app)
    };
    complete_settings_change(&app, &state, saved, applied)
}

#[tauri::command]
async fn companion_update(
    change: CompanionChange,
    menu_generation: Option<u64>,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Settings, String> {
    validate_companion_action_request(
        window.label(),
        request.body(),
        &["change"],
        menu_generation,
    )?;
    let recall_in_app = matches!(change, CompanionChange::Mode(PetMode::InApp));
    let _guard = state.companion_show_lock.lock().await;
    authorize_companion_menu_action(&app, &state, menu_generation).await?;
    let saved = record_settings_write(
        &state,
        "companion.update",
        state.settings.update_companion(change),
    )?;
    let pet = &saved.personalization.pet;
    let applied = if recall_in_app {
        recall_saved_companion(&app, &saved)
    } else if !pet.enabled {
        companion::hide(&app)
    } else if pet.mode == PetMode::Desktop {
        companion::show(&app, pet)
    } else {
        companion::hide(&app)
    };
    complete_settings_change(&app, &state, saved, applied)
}

#[tauri::command]
fn settings_save_appearance(
    theme: Theme,
    ui_style: UiStyle,
    close_to_tray: bool,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Settings, String> {
    validate_command_request(
        window.label(),
        &["main"],
        request.body(),
        &["theme", "uiStyle", "closeToTray"],
    )?;
    let saved = record_settings_write(
        &state,
        "settings.save_appearance",
        state
            .settings
            .save_appearance(theme, ui_style, close_to_tray),
    )?;
    complete_settings_change(&app, &state, saved, Ok(()))
}

#[tauri::command]
fn companion_open_main(
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    app: tauri::AppHandle,
) -> Result<(), String> {
    validate_command_request(
        window.label(),
        &["main", companion::LABEL, companion_menu::LABEL],
        request.body(),
        &[],
    )?;
    show_main_result(&app)
}

#[tauri::command]
async fn settings_save(
    settings: Settings,
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Settings, String> {
    if window.label() != "main" {
        return Err("Only the main window may save complete settings.".into());
    }
    settings.validate()?;
    let _guard = state.companion_show_lock.lock().await;
    let result = state.settings.save(settings);
    state.diagnostics.record(
        if result.is_ok() { "info" } else { "error" },
        "settings.save",
        if result.is_ok() {
            "saved"
        } else {
            "failed; existing data preserved"
        },
    );
    if result.is_ok() {
        if let Err(error) = app.emit("settings-changed", ()) {
            state
                .diagnostics
                .record("warn", "settings.notification", &error.to_string());
        }
    }
    let saved = result?;
    companion_menu::reconcile(&app, &saved.personalization.pet)?;
    Ok(saved)
}
#[tauri::command]
async fn companion_show(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    let _guard = state.companion_show_lock.lock().await;
    companion::show(&app, &state.settings.load()?.personalization.pet)
}
#[tauri::command]
async fn companion_hide(
    menu_generation: Option<u64>,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<(), String> {
    validate_companion_action_request(window.label(), request.body(), &[], menu_generation)?;
    let _guard = state.companion_show_lock.lock().await;
    authorize_companion_menu_action(&app, &state, menu_generation).await?;
    companion::hide(&app)
}

#[tauri::command]
async fn companion_menu_show(
    model_id: String,
    can_animate: bool,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<companion_menu::MenuContext, String> {
    validate_command_request(
        window.label(),
        &["main", companion::LABEL],
        request.body(),
        &["modelId", "canAnimate"],
    )?;
    let _guard = state.companion_show_lock.lock().await;
    let pet = state.settings.load()?.personalization.pet;
    if !companion::state(&app)?.visible {
        return Err("Show the desktop companion before opening its menu.".into());
    }
    let ready = state.companion_ready.lock().await;
    companion_menu::show(&app, &pet, ready.as_deref(), model_id, can_animate)
}

#[tauri::command]
async fn companion_menu_get_context(
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    state: State<'_, AppState>,
) -> Result<companion_menu::MenuContext, String> {
    validate_command_request(
        window.label(),
        &[companion_menu::LABEL],
        request.body(),
        &[],
    )?;
    let _guard = state.companion_show_lock.lock().await;
    let context = state.companion_menu.context()?;
    let pet = state.settings.load()?.personalization.pet;
    let ready = state.companion_ready.lock().await;
    if companion_menu::effective_model(&pet, ready.as_deref())? != context.model_id
        || state.companion_menu.changed_selection(&pet)?
    {
        return Err("The companion changed; reopen its menu.".into());
    }
    Ok(context)
}

#[tauri::command]
async fn companion_menu_close(
    generation: u64,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<(), String> {
    validate_command_request(
        window.label(),
        &["main", companion::LABEL, companion_menu::LABEL],
        request.body(),
        &["generation"],
    )?;
    let _guard = state.companion_show_lock.lock().await;
    companion_menu::close(&app, Some(generation))
}

#[tauri::command]
async fn companion_menu_ready(
    generation: u64,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<(), String> {
    validate_command_request(
        window.label(),
        &[companion_menu::LABEL],
        request.body(),
        &["generation"],
    )?;
    let _guard = state.companion_show_lock.lock().await;
    authorize_companion_menu_action(&app, &state, Some(generation)).await?;
    state.companion_menu.mark_ready(generation)?;
    state
        .diagnostics
        .record("info", "companion.menu_ready", &generation.to_string());
    Ok(())
}
#[tauri::command]
fn companion_start_drag(window: tauri::WebviewWindow) -> Result<(), String> {
    companion::start_drag(&window)
}
#[tauri::command]
fn companion_get_state(app: tauri::AppHandle) -> Result<companion::CompanionState, String> {
    companion::state(&app)
}
#[tauri::command]
async fn companion_ready(
    model_id: String,
    window: tauri::WebviewWindow,
    state: State<'_, AppState>,
) -> Result<(), String> {
    if window.label() != companion::LABEL {
        return Err("Only the companion surface may report readiness.".into());
    }
    if model_id.is_empty()
        || model_id.len() > 64
        || !model_id
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
    {
        return Err("Invalid companion readiness identity.".into());
    }
    state
        .diagnostics
        .record("info", "companion.ready", &model_id);
    *state.companion_ready.lock().await = Some(model_id);
    Ok(())
}
#[tauri::command]
async fn worker_call(
    method: String,
    params: Value,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    state.worker.call(&method, params).await
}
#[tauri::command]
async fn worker_stop(state: State<'_, AppState>) -> Result<(), String> {
    state.worker.stop().await
}

#[tauri::command]
async fn modules_list(state: State<'_, AppState>) -> Result<Value, String> {
    state
        .modules
        .as_ref()
        .map_err(Clone::clone)?
        .catalog()
        .await
}

#[tauri::command]
async fn modules_import(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let selected = tauri::async_runtime::spawn_blocking(|| {
        rfd::FileDialog::new()
            .set_title("导入 OpenDesk 页签包")
            .add_filter("OpenDesk 页签包", &["ndpkg"])
            .pick_file()
    })
    .await
    .map_err(|e| e.to_string())?;
    let manager = state.modules.as_ref().map_err(Clone::clone)?;
    if let Some(path) = selected {
        manager.import_package(&path).await?;
    }
    let _ = app.emit("modules-changed", ());
    manager.catalog().await
}

#[tauri::command]
async fn modules_select(
    id: String,
    version: String,
    digest: String,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let result = state
        .modules
        .as_ref()
        .map_err(Clone::clone)?
        .select(&id, &version, &digest)
        .await;
    let _ = app.emit("modules-changed", ());
    result
}

#[tauri::command]
async fn modules_enable(
    id: String,
    enabled: bool,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let result = state
        .modules
        .as_ref()
        .map_err(Clone::clone)?
        .set_enabled(&id, enabled)
        .await;
    let _ = app.emit("modules-changed", ());
    result
}

#[tauri::command]
async fn modules_open(id: String, state: State<'_, AppState>) -> Result<Value, String> {
    state
        .modules
        .as_ref()
        .map_err(Clone::clone)?
        .open(&id)
        .await
}

#[tauri::command]
async fn modules_call(
    token: String,
    method: String,
    params: Value,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let result = state
        .modules
        .as_ref()
        .map_err(Clone::clone)?
        .call(&token, &method, params)
        .await?;
    if state.module_smoke && method == "test.echo" {
        state
            .module_ui_calls
            .lock()
            .await
            .insert(token, result.clone());
    }
    Ok(result)
}

#[tauri::command]
async fn modules_load_failed(
    token: String,
    reason: String,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let result = state
        .modules
        .as_ref()
        .map_err(Clone::clone)?
        .report_load_failed(&token, &reason)
        .await;
    let _ = app.emit("modules-changed", ());
    result
}

#[tauri::command]
async fn modules_close(token: String, state: State<'_, AppState>) -> Result<(), String> {
    state
        .modules
        .as_ref()
        .map_err(Clone::clone)?
        .close(&token)
        .await?;
    if state.module_smoke {
        state.module_ui_closed.lock().await.insert(token);
    }
    Ok(())
}

#[tauri::command]
async fn modules_ui_ready(
    token: String,
    id: String,
    version: String,
    digest: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    state
        .modules
        .as_ref()
        .map_err(Clone::clone)?
        .validate_session(&token, &id, &version, &digest)
        .await?;
    state
        .diagnostics
        .record("info", "module.ui_ready", &format!("{id}@{version}"));
    if state.module_smoke {
        state
            .module_ui_ready
            .lock()
            .await
            .insert(token, json!({"id":id,"version":version,"digest":digest}));
    }
    Ok(())
}

// A real WebView must call this after Vue mounts. No worker is started in normal mode.
#[tauri::command]
async fn frontend_ready(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    state
        .diagnostics
        .record("info", "frontend.ready", "Vue mounted and IPC connected");
    if state.companion_smoke && !state.companion_smoke_started.swap(true, Ordering::SeqCst) {
        tauri::async_runtime::spawn(companion_smoke::run(app.clone()));
        return Ok(());
    }
    if state.module_smoke && !state.module_smoke_started.swap(true, Ordering::SeqCst) {
        let app = app.clone();
        tauri::async_runtime::spawn(async move {
            module_smoke::run(app).await;
        });
    }
    if state.smoke_test {
        let result = async {
            let settings = state.settings.load()?;
            state.settings.save(settings.clone())?;
            if state.settings.load()? != settings { return Err("Settings round trip failed".into()); }
            let first = state.worker.call("runtime.ping", json!({})).await?;
            let rejected = state.worker.call("runtime.ping", json!({"invalid":true})).await;
            if rejected.is_ok() || state.worker.state().await != "running" { return Err("Remote validation error did not preserve healthy worker".into()); }
            state.worker.stop().await?;
            let second = state.worker.call("runtime.ping", json!({})).await?;
            state.worker.stop().await?;
            Ok::<Value, String>(json!({"status":"passed","frontendMounted":true,"ipc":true,"settingsRoundTrip":true,"workerFirst":first,"workerRestart":second,"workerStopped":state.worker.state().await}))
        }.await;
        let report = match &result {
            Ok(value) => value.clone(),
            Err(error) => json!({"status":"failed","error":error}),
        };
        let report_path = state.data_root.join("smoke-result.json");
        std::fs::write(
            &report_path,
            serde_json::to_vec_pretty(&report).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        state.exiting.store(true, Ordering::SeqCst);
        app.exit(if result.is_ok() { 0 } else { 1 });
    }
    Ok(())
}

fn show_main_result(app: &tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or("The main window is not available.")?;
    window.show().map_err(|error| error.to_string())?;
    window.unminimize().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

fn show_main(app: &tauri::AppHandle) {
    if let Err(error) = show_main_result(app) {
        app.state::<AppState>()
            .diagnostics
            .record("warn", "window.activate", &error);
    }
}

fn request_exit(app: &tauri::AppHandle) {
    let state = app.state::<AppState>();
    if state.exiting.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let state = app.state::<AppState>();
        if let Ok(manager) = &state.modules {
            manager.shutdown().await;
        }
        match state.worker.stop().await {
            Ok(()) => state
                .diagnostics
                .record("info", "host.exiting", "owned worker stopped"),
            Err(error) => state
                .diagnostics
                .record("error", "host.exit_cleanup", &error),
        }
        app.exit(0);
    });
}

fn apply_startup_preferences(data_root: &std::path::Path, args: &[String]) -> Result<(), String> {
    for argument in args.iter().skip(1) {
        reject_retired_flag(argument)?;
    }
    if !args.iter().skip(1).any(|arg| arg == "--enable-companion") {
        return Ok(());
    }
    let store = SettingsStore::new(data_root);
    // Load first: malformed existing preferences must never be replaced by defaults.
    let mut settings = store.load()?;
    settings.personalization.pet.enabled = true;
    settings.personalization.pet.mode = personalization::PetMode::Desktop;
    store.save(settings)?;
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum StartupMode {
    Normal,
    CoreSmoke,
    ModuleSmoke,
    CompanionSmoke,
}

fn reject_retired_flag(argument: &str) -> Result<(), String> {
    if argument == "--import-wallpaper" || argument.starts_with("--import-wallpaper=") {
        return Err(
            "--import-wallpaper is no longer supported; background images have been removed."
                .into(),
        );
    }
    if argument == "--personalization-smoke" || argument.starts_with("--personalization-smoke=") {
        return Err(
            "--personalization-smoke is no longer supported; use --companion-smoke.".into(),
        );
    }
    Ok(())
}

/// Test processes may bypass single-instance routing only with an explicit
/// absolute framework-data root. Validate this before plugins or file writes.
fn startup_mode(
    args: &[String],
    data_root: Option<&std::path::Path>,
) -> Result<StartupMode, String> {
    let mut mode = StartupMode::Normal;
    for argument in args.iter().skip(1) {
        reject_retired_flag(argument)?;
        let candidate = match argument.as_str() {
            "--smoke-test" => StartupMode::CoreSmoke,
            "--module-smoke" => StartupMode::ModuleSmoke,
            "--companion-smoke" => StartupMode::CompanionSmoke,
            _ => continue,
        };
        if mode != StartupMode::Normal {
            return Err("Only one smoke mode may run at a time.".into());
        }
        mode = candidate;
    }
    if mode != StartupMode::Normal && !data_root.is_some_and(std::path::Path::is_absolute) {
        return Err("Smoke modes require an explicit absolute OPENDESK_DATA_DIR; default user data will not be used.".into());
    }
    Ok(mode)
}

#[cfg(test)]
mod command_boundary_tests {
    use super::*;
    use tauri::ipc::InvokeBody;

    #[test]
    fn local_commands_enforce_window_labels_and_exact_argument_keys() {
        let selection = InvokeBody::Json(json!({"modelId": "sample-role"}));
        assert!(validate_command_request("main", &["main"], &selection, &["modelId"]).is_ok());
        for label in ["companion", "module-tower-assistant", "unknown"] {
            assert!(validate_command_request(label, &["main"], &selection, &["modelId"]).is_err());
        }
        let change = InvokeBody::Json(json!({"change": {"type":"size","value":208}}));
        for label in ["main", "companion"] {
            assert!(
                validate_command_request(label, &["main", "companion"], &change, &["change"])
                    .is_ok()
            );
        }
        assert!(validate_command_request(
            "module-tower-assistant",
            &["main", "companion"],
            &change,
            &["change"]
        )
        .is_err());
        for body in [
            InvokeBody::Json(json!({})),
            InvokeBody::Json(json!({"modelId":null,"enabled":true})),
            InvokeBody::Json(json!({"modelId":null,"change":{"type":"size","value":208}})),
            InvokeBody::Json(json!(null)),
            InvokeBody::Raw(vec![1, 2]),
        ] {
            assert!(validate_command_request("main", &["main"], &body, &["modelId"]).is_err());
        }
        assert!(validate_command_request(
            "companion",
            &["main", "companion"],
            &InvokeBody::Json(json!({})),
            &[]
        )
        .is_ok());
        assert!(validate_command_request(
            "companion",
            &["main", "companion"],
            &InvokeBody::Json(json!({"target":"other"})),
            &[]
        )
        .is_err());
    }

    #[test]
    fn companion_capability_never_grants_full_settings_or_role_selection() {
        let main: Value = serde_json::from_str(include_str!("../capabilities/main.json")).unwrap();
        let companion: Value =
            serde_json::from_str(include_str!("../capabilities/companion.json")).unwrap();
        let main_permissions = main["permissions"].as_array().unwrap();
        let pet_permissions = companion["permissions"].as_array().unwrap();
        for forbidden in [
            "allow-settings-save",
            "allow-settings-save-appearance",
            "allow-companion-select",
        ] {
            assert!(!pet_permissions.contains(&json!(forbidden)));
            assert!(main_permissions.contains(&json!(forbidden)));
        }
        for permission in [
            "allow-companion-update",
            "allow-companion-open-main",
            "allow-companion-menu-show",
            "allow-companion-menu-close",
        ] {
            assert!(pet_permissions.contains(&json!(permission)));
            assert!(main_permissions.contains(&json!(permission)));
        }
        let menu: Value =
            serde_json::from_str(include_str!("../capabilities/companion-menu.json")).unwrap();
        assert_eq!(menu["windows"], json!(["companion-menu"]));
        let menu_permissions = menu["permissions"].as_array().unwrap();
        let expected = [
            "allow-settings-get",
            "allow-companion-update",
            "allow-companion-hide",
            "allow-companion-open-main",
            "allow-companion-menu-get-context",
            "allow-companion-menu-close",
            "allow-companion-menu-ready",
            "core:event:allow-listen",
            "core:event:allow-unlisten",
        ];
        assert_eq!(menu_permissions.len(), expected.len());
        for permission in expected {
            assert!(menu_permissions.contains(&json!(permission)));
        }
        for permission in [
            "core:menu:allow-new",
            "core:menu:allow-popup",
            "core:resources:allow-close",
        ] {
            assert!(!menu_permissions.contains(&json!(permission)));
            assert!(!pet_permissions.contains(&json!(permission)));
            assert!(!main_permissions.contains(&json!(permission)));
        }
    }

    #[test]
    fn menu_updates_require_a_generation_and_other_windows_cannot_supply_one() {
        use tauri::ipc::InvokeBody;
        let change = json!({"type":"size","value":160});
        let body = InvokeBody::Json(json!({"change":change,"menuGeneration":7}));
        assert!(validate_companion_action_request(
            companion_menu::LABEL,
            &body,
            &["change"],
            Some(7)
        )
        .is_ok());
        for label in ["main", "companion", "module-tower-assistant"] {
            assert!(validate_companion_action_request(label, &body, &["change"], Some(7)).is_err());
        }
        assert!(validate_companion_action_request(
            companion_menu::LABEL,
            &InvokeBody::Json(json!({"change":change})),
            &["change"],
            None
        )
        .is_err());
        assert!(validate_companion_action_request(
            companion_menu::LABEL,
            &InvokeBody::Json(json!({"change":change,"menuGeneration":null})),
            &["change"],
            None
        )
        .is_err());
        assert!(validate_companion_action_request(
            companion_menu::LABEL,
            &InvokeBody::Json(json!({"menuGeneration":7})),
            &[],
            Some(7)
        )
        .is_ok());
        assert!(validate_companion_action_request(
            companion_menu::LABEL,
            &InvokeBody::Json(json!({})),
            &[],
            None
        )
        .is_err());
        assert!(validate_companion_action_request(
            "companion",
            &InvokeBody::Json(json!({})),
            &[],
            None
        )
        .is_ok());
    }
}

#[cfg(test)]
mod startup_tests {
    use super::*;

    #[test]
    fn normal_startup_keeps_single_instance_even_with_an_explicit_data_root() {
        let root = std::env::temp_dir().join("opendesk-mode-test");
        let normal = vec!["opendesk".into(), "--enable-companion".into()];
        assert_eq!(startup_mode(&normal, None).unwrap(), StartupMode::Normal);
        assert_eq!(
            startup_mode(&normal, Some(&root)).unwrap(),
            StartupMode::Normal
        );
    }

    #[test]
    fn every_smoke_mode_requires_an_absolute_isolated_data_root() {
        let root = std::env::temp_dir().join("opendesk-mode-test");
        assert!(root.is_absolute());
        for (flag, expected) in [
            ("--smoke-test", StartupMode::CoreSmoke),
            ("--module-smoke", StartupMode::ModuleSmoke),
            ("--companion-smoke", StartupMode::CompanionSmoke),
        ] {
            let args = vec!["opendesk".into(), flag.into()];
            assert_eq!(startup_mode(&args, Some(&root)).unwrap(), expected);
            for invalid in [
                None,
                Some(std::path::Path::new("")),
                Some(std::path::Path::new("relative/test")),
            ] {
                assert!(startup_mode(&args, invalid)
                    .unwrap_err()
                    .contains("absolute OPENDESK_DATA_DIR"));
            }
        }
    }

    #[test]
    fn mixed_or_repeated_smoke_modes_are_rejected_before_startup_side_effects() {
        let root = std::env::temp_dir().join("opendesk-mode-test");
        for flags in [
            ["--smoke-test", "--module-smoke"],
            ["--companion-smoke", "--smoke-test"],
            ["--companion-smoke", "--companion-smoke"],
            ["--smoke-test", "--smoke-test"],
        ] {
            let args = vec!["opendesk".into(), flags[0].into(), flags[1].into()];
            assert!(startup_mode(&args, Some(&root))
                .unwrap_err()
                .contains("Only one smoke mode"));
        }
    }

    #[test]
    fn explicit_companion_cli_preserves_preferences_and_retired_flags_preserve_file() {
        let root = std::env::temp_dir().join(format!("opendesk-startup-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        let settings = Settings {
            theme: settings::Theme::Forest,
            close_to_tray: false,
            personalization: personalization::Personalization {
                pet: personalization::PetSettings {
                    enabled: false,
                    mode: personalization::PetMode::InApp,
                    model_id: Some("sample-companion".into()),
                    size: 208,
                    always_on_top: false,
                    animated: false,
                    action_mode: personalization::PetActionMode::Idle,
                },
            },
            ..Settings::default()
        };
        SettingsStore::new(&root).save(settings.clone()).unwrap();
        let settings_path = root.join("config/framework/settings.json");
        let original = std::fs::read(&settings_path).unwrap();
        assert!(apply_startup_preferences(
            &root,
            &[
                "opendesk".into(),
                "--import-wallpaper".into(),
                "relative.jpg".into()
            ]
        )
        .is_err());
        assert_eq!(std::fs::read(&settings_path).unwrap(), original);
        apply_startup_preferences(&root, &["opendesk".into(), "--enable-companion".into()])
            .unwrap();
        let loaded = SettingsStore::new(&root).load().unwrap();
        assert_eq!(loaded.theme, settings::Theme::Forest);
        assert!(!loaded.close_to_tray);
        assert_eq!(
            loaded.personalization.pet,
            personalization::PetSettings {
                enabled: true,
                mode: personalization::PetMode::Desktop,
                ..settings.personalization.pet
            }
        );
        let corrupt = r#"{"schemaVersion":99,"theme":"forest","closeToTray":false}"#;
        std::fs::write(&settings_path, corrupt).unwrap();
        assert!(apply_startup_preferences(
            &root,
            &["opendesk".into(), "--enable-companion".into()]
        )
        .is_err());
        assert_eq!(std::fs::read_to_string(&settings_path).unwrap(), corrupt);
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn retired_cli_flags_are_rejected_before_single_instance_or_file_writes() {
        let root = std::env::temp_dir().join("opendesk-mode-test");
        for flag in [
            "--import-wallpaper",
            "--import-wallpaper=C:/removed.jpg",
            "--personalization-smoke",
            "--personalization-smoke=true",
        ] {
            let args = vec!["opendesk".into(), flag.into()];
            for data_root in [None, Some(root.as_path())] {
                assert!(startup_mode(&args, data_root)
                    .unwrap_err()
                    .contains("no longer supported"));
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let args: Vec<String> = std::env::args().collect();
    let data_root_override = std::env::var_os("OPENDESK_DATA_DIR").map(PathBuf::from);
    let mode = match startup_mode(&args, data_root_override.as_deref()) {
        Ok(mode) => mode,
        Err(error) => {
            eprintln!("OpenDesk startup refused: {error}");
            std::process::exit(2);
        }
    };
    let mut builder = tauri::Builder::default();
    if mode == StartupMode::Normal {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _, _| {
            show_main(app)
        }));
    }
    let app = builder
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            host_info,
            settings_get,
            settings_save,
            settings_save_appearance,
            companion_select,
            companion_update,
            companion_open_main,
            companion_show,
            companion_hide,
            companion_start_drag,
            companion_get_state,
            companion_ready,
            companion_menu_show,
            companion_menu_get_context,
            companion_menu_close,
            companion_menu_ready,
            worker_call,
            worker_stop,
            modules_list,
            modules_import,
            modules_select,
            modules_enable,
            modules_open,
            modules_call,
            modules_close,
            modules_ui_ready,
            modules_load_failed,
            frontend_ready
        ])
        .setup(move |app| {
            let data_root = match data_root_override.clone() {
                Some(path) => {
                    if !path.is_absolute() {
                        return Err("OPENDESK_DATA_DIR must be absolute".into());
                    }
                    path
                }
                None => app.path().app_data_dir()?,
            };
            std::fs::create_dir_all(&data_root)?;
            apply_startup_preferences(&data_root, &args)?;
            let diagnostics = Arc::new(Diagnostics::new(&data_root));
            diagnostics.record("info", "host.starting", env!("CARGO_PKG_VERSION"));
            let smoke_test = mode == StartupMode::CoreSmoke;
            let module_smoke = mode == StartupMode::ModuleSmoke;
            let companion_smoke = mode == StartupMode::CompanionSmoke;
            let bundled_root = if cfg!(debug_assertions) {
                PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../../dist/bundled-modules")
            } else {
                app.path().resource_dir()?.join("bundled-modules")
            };
            let modules = ModuleManager::new(data_root.clone(), bundled_root, diagnostics.clone());
            if let Err(error) = &modules {
                diagnostics.record("error", "modules.initialize", error);
            }
            app.manage(AppState {
                settings: SettingsStore::new(&data_root),
                worker: WorkerManager::new(diagnostics.clone()),
                modules,
                data_root,
                diagnostics,
                exiting: AtomicBool::new(false),
                smoke_test,
                module_smoke,
                module_smoke_started: AtomicBool::new(false),
                companion_smoke,
                companion_smoke_started: AtomicBool::new(false),
                companion_ready: tokio::sync::Mutex::new(None),
                companion_show_lock: tokio::sync::Mutex::new(()),
                companion_menu: companion_menu::MenuSession::default(),
                module_ui_ready: tokio::sync::Mutex::new(std::collections::BTreeMap::new()),
                module_ui_calls: tokio::sync::Mutex::new(std::collections::BTreeMap::new()),
                module_ui_closed: tokio::sync::Mutex::new(std::collections::HashSet::new()),
            });
            use tauri::{
                menu::{Menu, MenuItem},
                tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
            };
            let show = MenuItem::with_id(app, "show", "打开 OpenDesk", true, None::<&str>)?;
            // Always available: with no selected/enabled pet it opens the main UI.
            let show_companion =
                MenuItem::with_id(app, "show-companion", "显示宠物", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &show_companion, &quit])?;
            TrayIconBuilder::with_id("main-tray")
                .icon(tauri::image::Image::from_bytes(include_bytes!(
                    "../icons/icon.png"
                ))?)
                .tooltip("OpenDesk")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main(app),
                    "show-companion" => {
                        let app = app.clone();
                        tauri::async_runtime::spawn(async move {
                            let state = app.state::<AppState>();
                            let _guard = state.companion_show_lock.lock().await;
                            let result = state
                                .settings
                                .load()
                                .and_then(|settings| recall_saved_companion(&app, &settings));
                            if let Err(error) = result {
                                state
                                    .diagnostics
                                    .record("error", "companion.recall", &error);
                            }
                        });
                    }
                    "quit" => request_exit(app),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if matches!(
                        event,
                        TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        }
                    ) {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;
            let shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space);
            if let Err(error) = app
                .global_shortcut()
                .on_shortcut(shortcut, |app, _, event| {
                    if event.state() == ShortcutState::Pressed {
                        show_main(app);
                    }
                })
            {
                app.state::<AppState>().diagnostics.record(
                    "warn",
                    "shortcut.unavailable",
                    &error.to_string(),
                );
            }
            if smoke_test || module_smoke || companion_smoke {
                // If the frontend fails to load, fail explicitly instead of leaving a hidden process.
                let app = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    tokio::time::sleep(std::time::Duration::from_secs(if module_smoke {
                        120
                    } else {
                        45
                    }))
                    .await;
                    let state = app.state::<AppState>();
                    if !state.exiting.load(Ordering::SeqCst) {
                        let _ = std::fs::write(
                            state.data_root.join(if companion_smoke {
                                "companion-smoke-result.json"
                            } else if module_smoke {
                                "module-smoke-result.json"
                            } else {
                                "smoke-result.json"
                            }),
                            r#"{"status":"failed","error":"Frontend ready timeout"}"#,
                        );
                        let _ = state.worker.stop().await;
                        if let Ok(manager) = &state.modules {
                            manager.shutdown().await;
                        }
                        state.exiting.store(true, Ordering::SeqCst);
                        app.exit(1);
                    }
                });
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == companion_menu::LABEL {
                match event {
                    tauri::WindowEvent::CloseRequested { api, .. } => {
                        if !window.state::<AppState>().exiting.load(Ordering::SeqCst) {
                            api.prevent_close();
                        }
                    }
                    tauri::WindowEvent::Focused(false) => {
                        match (
                            window.is_focused(),
                            window.state::<AppState>().companion_menu.is_visible(),
                        ) {
                            (Ok(false), Ok(true)) => {}
                            (Ok(_), Ok(_)) => return,
                            (focused, visible) => {
                                window.state::<AppState>().diagnostics.record(
                                    "error",
                                    "companion.menu_focus",
                                    &format!("focus={focused:?}; visible={visible:?}"),
                                );
                                return;
                            }
                        }
                    }
                    _ => return,
                }
                if let Err(error) = companion_menu::close(window.app_handle(), None) {
                    window.state::<AppState>().diagnostics.record(
                        "error",
                        "companion.menu_close",
                        &error,
                    );
                }
                return;
            }
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let state = window.state::<AppState>();
                if state.exiting.load(Ordering::SeqCst) {
                    return;
                }
                api.prevent_close();
                if window.label() == companion::LABEL {
                    if let Err(error) = companion::hide(window.app_handle()) {
                        state.diagnostics.record("error", "companion.hide", &error);
                    }
                    return;
                }
                if window.label() != "main" {
                    return;
                }
                if state
                    .settings
                    .load()
                    .map(|s| s.close_to_tray)
                    .unwrap_or(false)
                {
                    if let Err(error) = window.hide() {
                        state
                            .diagnostics
                            .record("error", "window.hide", &error.to_string());
                    }
                } else {
                    request_exit(window.app_handle());
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("OpenDesk failed to initialize");
    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = event {
            if !app.state::<AppState>().exiting.load(Ordering::SeqCst) {
                api.prevent_exit();
                request_exit(app);
            }
        }
    });
}
