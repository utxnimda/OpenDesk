mod companion;
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
use serde_json::{json, Value};
use settings::{Settings, SettingsStore};
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
#[tauri::command]
fn settings_save(
    settings: Settings,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Settings, String> {
    settings.validate()?;
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
    result
}
#[tauri::command]
async fn companion_show(app: tauri::AppHandle, state: State<'_, AppState>) -> Result<(), String> {
    let _guard = state.companion_show_lock.lock().await;
    companion::show(&app, &state.settings.load()?.personalization.pet)
}
#[tauri::command]
fn companion_hide(app: tauri::AppHandle) -> Result<(), String> {
    companion::hide(&app)
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

fn show_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        for result in [window.show(), window.unminimize(), window.set_focus()] {
            if let Err(error) = result {
                app.state::<AppState>().diagnostics.record(
                    "warn",
                    "window.activate",
                    &error.to_string(),
                );
            }
        }
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
            companion_show,
            companion_hide,
            companion_start_drag,
            companion_get_state,
            companion_ready,
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
                module_ui_ready: tokio::sync::Mutex::new(std::collections::BTreeMap::new()),
                module_ui_calls: tokio::sync::Mutex::new(std::collections::BTreeMap::new()),
                module_ui_closed: tokio::sync::Mutex::new(std::collections::HashSet::new()),
            });
            use tauri::{
                menu::{Menu, MenuItem},
                tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
            };
            let show = MenuItem::with_id(app, "show", "打开 OpenDesk", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::with_id("main-tray")
                .icon(tauri::image::Image::from_bytes(include_bytes!(
                    "../icons/icon.png"
                ))?)
                .tooltip("OpenDesk")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main(app),
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
