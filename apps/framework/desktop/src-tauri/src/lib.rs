mod diagnostics;
mod module_packages;
mod module_smoke;
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
fn settings_save(settings: Settings, state: State<'_, AppState>) -> Result<Settings, String> {
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
    result
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
            .set_title("导入 NimdaDesk 页签包")
            .add_filter("NimdaDesk 页签包", &["ndpkg"])
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            show_main(app)
        }))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            host_info,
            settings_get,
            settings_save,
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
        .setup(|app| {
            let data_root = match std::env::var_os("NIMDADESK_DATA_DIR") {
                Some(path) => {
                    let path = PathBuf::from(path);
                    if !path.is_absolute() {
                        return Err("NIMDADESK_DATA_DIR must be absolute".into());
                    }
                    path
                }
                None => app.path().app_data_dir()?,
            };
            std::fs::create_dir_all(&data_root)?;
            let diagnostics = Arc::new(Diagnostics::new(&data_root));
            diagnostics.record("info", "host.starting", env!("CARGO_PKG_VERSION"));
            let smoke_test = std::env::args().any(|arg| arg == "--smoke-test");
            let module_smoke = std::env::args().any(|arg| arg == "--module-smoke");
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
                module_ui_ready: tokio::sync::Mutex::new(std::collections::BTreeMap::new()),
                module_ui_calls: tokio::sync::Mutex::new(std::collections::BTreeMap::new()),
                module_ui_closed: tokio::sync::Mutex::new(std::collections::HashSet::new()),
            });
            use tauri::{
                menu::{Menu, MenuItem},
                tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
            };
            let show = MenuItem::with_id(app, "show", "打开 NimdaDesk", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::with_id("main-tray")
                .icon(tauri::image::Image::from_bytes(include_bytes!(
                    "../icons/icon.png"
                ))?)
                .tooltip("NimdaDesk")
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
            if smoke_test || module_smoke {
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
                            state.data_root.join(if module_smoke {
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
        .expect("NimdaDesk failed to initialize");
    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { api, .. } = event {
            if !app.state::<AppState>().exiting.load(Ordering::SeqCst) {
                api.prevent_exit();
                request_exit(app);
            }
        }
    });
}
