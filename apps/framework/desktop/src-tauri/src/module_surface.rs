//! A borrowed module view. Its window never starts, stops or owns a worker.
use crate::{validate_command_request, AppState};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::Mutex;
use tauri::{Manager, State};

pub const LABEL: &str = "module-surface";
const MAX_CONTEXT_BYTES: usize = 8192;

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SurfaceOptions {
    pub view: String,
    pub title: String,
    pub width: f64,
    pub height: f64,
    pub always_on_top: bool,
    pub context: Value,
}

impl SurfaceOptions {
    fn validate(&self) -> Result<(), String> {
        if self.view.is_empty()
            || self.view.len() > 48
            || !self.view.as_bytes()[0].is_ascii_lowercase()
            || !self
                .view
                .bytes()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-')
        {
            return Err("MODULE_SURFACE_VIEW_INVALID".into());
        }
        if self.title.trim().is_empty()
            || self.title.chars().count() > 80
            || self.title.chars().any(char::is_control)
        {
            return Err("MODULE_SURFACE_TITLE_INVALID".into());
        }
        if !self.width.is_finite()
            || self.width.fract() != 0.0
            || !(320.0..=1000.0).contains(&self.width)
            || !self.height.is_finite()
            || self.height.fract() != 0.0
            || !(240.0..=1400.0).contains(&self.height)
        {
            return Err("MODULE_SURFACE_SIZE_INVALID".into());
        }
        if !self.context.is_object()
            || serde_json::to_vec(&self.context)
                .map_err(|e| e.to_string())?
                .len()
                > MAX_CONTEXT_BYTES
        {
            return Err("MODULE_SURFACE_CONTEXT_INVALID".into());
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SurfaceContext {
    pub generation: String,
    pub instance: Value,
    pub view: String,
    pub context: Value,
    pub always_on_top: bool,
    #[serde(skip)]
    options: SurfaceOptions,
}

impl SurfaceContext {
    fn new(instance: Value, options: SurfaceOptions) -> Result<Self, String> {
        // The descriptor comes from ModuleManager, never from a page-supplied URL.
        if instance.get("token").and_then(Value::as_str).is_none() {
            return Err("MODULE_SURFACE_INSTANCE_INVALID".into());
        }
        Ok(Self {
            generation: uuid::Uuid::new_v4().to_string(),
            instance,
            view: options.view.clone(),
            context: options.context.clone(),
            always_on_top: options.always_on_top,
            options,
        })
    }
    fn token(&self) -> &str {
        self.instance["token"]
            .as_str()
            .expect("validated module descriptor")
    }
    fn matches(&self, token: &str, options: &SurfaceOptions) -> bool {
        self.token() == token && self.options == *options
    }
}

#[derive(Default)]
pub struct SurfaceSession(Mutex<Option<SurfaceContext>>);

impl SurfaceSession {
    pub fn current(&self) -> Result<Option<SurfaceContext>, String> {
        Ok(self
            .0
            .lock()
            .map_err(|_| "MODULE_SURFACE_LOCK_UNAVAILABLE")?
            .clone())
    }
    fn require(&self, generation: &str) -> Result<SurfaceContext, String> {
        self.current()?
            .filter(|c| c.generation == generation)
            .ok_or("MODULE_SURFACE_STALE".into())
    }
    fn replace(&self, context: Option<SurfaceContext>) -> Result<(), String> {
        *self
            .0
            .lock()
            .map_err(|_| "MODULE_SURFACE_LOCK_UNAVAILABLE")? = context;
        Ok(())
    }
    fn set_top(&self, generation: &str, enabled: bool) -> Result<(), String> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| "MODULE_SURFACE_LOCK_UNAVAILABLE")?;
        let context = state
            .as_mut()
            .filter(|c| c.generation == generation)
            .ok_or("MODULE_SURFACE_STALE")?;
        context.always_on_top = enabled;
        context.options.always_on_top = enabled;
        Ok(())
    }
}

#[derive(Debug, PartialEq)]
struct Geometry {
    width: f64,
    height: f64,
    min_width: f64,
    min_height: f64,
    max_width: f64,
    max_height: f64,
    x: i32,
    y: i32,
}

fn geometry(
    width: f64,
    height: f64,
    scale: f64,
    area: &tauri::PhysicalRect<i32, u32>,
) -> Result<Geometry, String> {
    if !scale.is_finite() || scale <= 0.0 || area.size.width == 0 || area.size.height == 0 {
        return Err("MODULE_SURFACE_MONITOR_INVALID".into());
    }
    let max_width = (f64::from(area.size.width) / scale).min(1000.0);
    let max_height = (f64::from(area.size.height) / scale).min(1400.0);
    let width = width.min(max_width);
    let height = height.min(max_height);
    let free_x = i64::from(area.size.width) - (width * scale).ceil() as i64;
    let free_y = i64::from(area.size.height) - (height * scale).ceil() as i64;
    let x = (i64::from(area.position.x) + (free_x - 24).max(0))
        .clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32;
    let y = (i64::from(area.position.y) + free_y.clamp(0, 24))
        .clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32;
    Ok(Geometry {
        width,
        height,
        min_width: 320.0_f64.min(max_width),
        min_height: 240.0_f64.min(max_height),
        max_width,
        max_height,
        x,
        y,
    })
}

fn authorize_window(window: &tauri::WebviewWindow, context: &SurfaceContext) -> Result<(), String> {
    let url = window.url().map_err(|e| e.to_string())?;
    if window.label() != LABEL
        || !url
            .query_pairs()
            .any(|(key, value)| key == "surface-generation" && value == context.generation)
    {
        return Err("MODULE_SURFACE_STALE_WINDOW".into());
    }
    Ok(())
}

// Callers serialize window operations with module_surface_lock. Clear the
// binding before waiting for native destruction so late requests are rejected.
async fn destroy(app: &tauri::AppHandle, state: &AppState) -> Result<(), String> {
    if let Some(window) = app.get_webview_window(LABEL) {
        window
            .destroy()
            .map_err(|e| format!("MODULE_SURFACE_CLOSE_FAILED: {e}"))?;
        state.module_surface.replace(None)?;
        for _ in 0..50 {
            if app.get_webview_window(LABEL).is_none() {
                return Ok(());
            }
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
        return Err("MODULE_SURFACE_CLOSE_PENDING: native window has not closed yet".into());
    }
    state.module_surface.replace(None)
}

/// Called after module lifecycle operations, including failed activation rollback.
pub async fn reconcile(app: &tauri::AppHandle, state: &AppState) -> Result<(), String> {
    let _guard = state.module_surface_lock.lock().await;
    let Some(context) = state.module_surface.current()? else {
        return Ok(());
    };
    let manager = state.modules.as_ref().map_err(Clone::clone)?;
    if manager.describe_existing(context.token()).await.is_err() {
        destroy(app, state).await?;
    }
    Ok(())
}

pub async fn close_native(
    app: &tauri::AppHandle,
    state: &AppState,
    generation: &str,
) -> Result<(), String> {
    let _guard = state.module_surface_lock.lock().await;
    // A queued close from an old native window cannot destroy its replacement.
    if state.module_surface.require(generation).is_err() {
        return Ok(());
    }
    destroy(app, state).await
}

#[tauri::command]
pub async fn module_surface_open(
    token: String,
    options: SurfaceOptions,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<SurfaceContext, String> {
    validate_command_request(
        window.label(),
        &["main"],
        request.body(),
        &["token", "options"],
    )?;
    open(&app, &state, &window, &token, options).await
}

/// Same native path is exercised by explicit, isolated module smoke runs.
pub async fn open(
    app: &tauri::AppHandle,
    state: &AppState,
    window: &tauri::WebviewWindow,
    token: &str,
    options: SurfaceOptions,
) -> Result<SurfaceContext, String> {
    options.validate()?;
    let _guard = state.module_surface_lock.lock().await;
    let manager = state.modules.as_ref().map_err(Clone::clone)?;
    let instance = manager.describe_existing(token).await?;
    if let Some(current) = state.module_surface.current()? {
        if current.matches(token, &options) {
            if let Some(native) = app.get_webview_window(LABEL) {
                native.show().map_err(|e| e.to_string())?;
                native.set_focus().map_err(|e| e.to_string())?;
                return Ok(current);
            }
        }
    }
    let monitor = window
        .current_monitor()
        .map_err(|e| e.to_string())?
        .or(app.primary_monitor().map_err(|e| e.to_string())?)
        .ok_or("MODULE_SURFACE_MONITOR_UNAVAILABLE")?;
    let geometry = geometry(
        options.width,
        options.height,
        monitor.scale_factor(),
        monitor.work_area(),
    )?;
    destroy(app, state).await?;
    let context = SurfaceContext::new(instance, options.clone())?;
    state.module_surface.replace(Some(context.clone()))?;
    let result = (|| -> Result<(), String> {
        let native = tauri::WebviewWindowBuilder::new(
            app,
            LABEL,
            tauri::WebviewUrl::App(
                format!(
                    "index.html?surface=module-surface&surface-generation={}",
                    context.generation
                )
                .into(),
            ),
        )
        .title(&options.title)
        .inner_size(geometry.width, geometry.height)
        .min_inner_size(geometry.min_width, geometry.min_height)
        .max_inner_size(geometry.max_width, geometry.max_height)
        .transparent(true)
        .background_color(tauri::window::Color(0, 0, 0, 0))
        .decorations(false)
        .shadow(false)
        .resizable(true)
        .skip_taskbar(false)
        .always_on_top(options.always_on_top)
        .focused(false)
        .visible(false)
        .build()
        .map_err(|e| format!("MODULE_SURFACE_CREATE_FAILED: {e}"))?;
        native
            .set_position(tauri::PhysicalPosition::new(geometry.x, geometry.y))
            .map_err(|e| e.to_string())?;
        native.show().map_err(|e| e.to_string())?;
        native.set_focus().map_err(|e| e.to_string())?;
        Ok(())
    })();
    if let Err(error) = result {
        if let Err(cleanup) = destroy(app, state).await {
            return Err(format!("{error}; {cleanup}"));
        }
        return Err(error);
    }
    // Switching the underlying package while native creation was in flight
    // cannot leave a view bound to a revoked token.
    if let Err(error) = manager.describe_existing(token).await {
        destroy(app, state).await?;
        return Err(error);
    }
    Ok(context)
}

async fn call_with_binding<F, Future>(
    session: &SurfaceSession,
    gate: &tokio::sync::Mutex<()>,
    generation: &str,
    authorize: impl FnOnce(&SurfaceContext) -> Result<(), String>,
    call: F,
) -> Result<Value, String>
where
    F: FnOnce(SurfaceContext) -> Future,
    Future: std::future::Future<Output = Result<Value, String>>,
{
    let context = {
        let _guard = gate.lock().await;
        let context = session.require(generation)?;
        authorize(&context)?;
        context
    };
    // A slow admitted worker request must not block native close/drag/top.
    // ModuleManager independently rechecks its token before admitting the call.
    let result = call(context).await;
    session.require(generation)?;
    result
}

#[tauri::command]
pub async fn module_surface_context(
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    state: State<'_, AppState>,
) -> Result<SurfaceContext, String> {
    validate_command_request(window.label(), &[LABEL], request.body(), &[])?;
    let _guard = state.module_surface_lock.lock().await;
    let context = state
        .module_surface
        .current()?
        .ok_or("MODULE_SURFACE_CLOSED")?;
    authorize_window(&window, &context)?;
    state
        .modules
        .as_ref()
        .map_err(Clone::clone)?
        .describe_existing(context.token())
        .await?;
    Ok(context)
}

#[tauri::command]
pub async fn module_surface_call(
    generation: String,
    method: String,
    params: Value,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    validate_command_request(
        window.label(),
        &[LABEL],
        request.body(),
        &["generation", "method", "params"],
    )?;
    let manager = state.modules.as_ref().map_err(Clone::clone)?;
    let smoke_echo = state.module_smoke && method == "test.echo";
    let result = call_with_binding(
        &state.module_surface,
        &state.module_surface_lock,
        &generation,
        |context| authorize_window(&window, context),
        |context| async move { manager.call(context.token(), &method, params).await },
    )
    .await?;
    if smoke_echo {
        state
            .module_ui_calls
            .lock()
            .await
            .insert(generation, result.clone());
    }
    Ok(result)
}

#[tauri::command]
pub async fn module_surface_close(
    generation: String,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<(), String> {
    validate_command_request(window.label(), &[LABEL], request.body(), &["generation"])?;
    let _guard = state.module_surface_lock.lock().await;
    let context = state.module_surface.require(&generation)?;
    authorize_window(&window, &context)?;
    destroy(&app, &state).await
}

#[tauri::command]
pub async fn module_surface_start_drag(
    generation: String,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    state: State<'_, AppState>,
) -> Result<(), String> {
    validate_command_request(window.label(), &[LABEL], request.body(), &["generation"])?;
    let _guard = state.module_surface_lock.lock().await;
    let context = state.module_surface.require(&generation)?;
    authorize_window(&window, &context)?;
    window.start_dragging().map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn module_surface_set_always_on_top(
    generation: String,
    enabled: bool,
    window: tauri::WebviewWindow,
    request: tauri::ipc::Request<'_>,
    state: State<'_, AppState>,
) -> Result<(), String> {
    validate_command_request(
        window.label(),
        &[LABEL],
        request.body(),
        &["generation", "enabled"],
    )?;
    let _guard = state.module_surface_lock.lock().await;
    let context = state.module_surface.require(&generation)?;
    authorize_window(&window, &context)?;
    window
        .set_always_on_top(enabled)
        .map_err(|e| e.to_string())?;
    state.module_surface.set_top(&generation, enabled)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn options() -> SurfaceOptions {
        SurfaceOptions {
            view: "metrics".into(),
            title: "统计".into(),
            width: 440.0,
            height: 620.0,
            always_on_top: true,
            context: json!({"kind":"damage"}),
        }
    }
    #[test]
    fn options_reject_paths_unknown_fields_oversize_context_and_invalid_numbers() {
        assert!(options().validate().is_ok());
        for view in ["../metrics", "https://example.com", "", "Metrics", "a_b"] {
            let mut value = options();
            value.view = view.into();
            assert!(value.validate().is_err());
        }
        for number in [f64::NAN, f64::INFINITY, 0.0, 319.0, 1001.0] {
            let mut value = options();
            value.width = number;
            assert!(value.validate().is_err());
        }
        let mut value = options();
        value.title = "😀".repeat(81);
        assert!(value.validate().is_err());
        value = options();
        value.context = json!({"v":"甲".repeat(3000)});
        assert!(value.validate().is_err());
        value = options();
        value.context = json!([]);
        assert!(value.validate().is_err());
        let raw = json!({"view":"metrics","title":"x","width":400,"height":600,"alwaysOnTop":true,"context":{},"url":"http://127.0.0.1"});
        assert!(serde_json::from_value::<SurfaceOptions>(raw).is_err());
    }
    #[test]
    fn replacement_and_close_revoke_generation_without_changing_borrowed_identity() {
        let session = SurfaceSession::default();
        let instance =
            json!({"token":"owned-token","generation":"worker-generation","workerPid":1234});
        let first = SurfaceContext::new(instance.clone(), options()).unwrap();
        session.replace(Some(first.clone())).unwrap();
        assert!(session
            .require(&first.generation)
            .unwrap()
            .matches("owned-token", &options()));
        session.set_top(&first.generation, false).unwrap();
        assert!(!session.require(&first.generation).unwrap().always_on_top);
        let second = SurfaceContext::new(instance.clone(), options()).unwrap();
        assert_ne!(first.generation, second.generation);
        session.replace(Some(second.clone())).unwrap();
        assert!(session.require(&first.generation).is_err());
        assert_eq!(
            session.require(&second.generation).unwrap().instance,
            instance
        );
        session.replace(None).unwrap();
        assert!(session.require(&second.generation).is_err());
    }
    #[test]
    fn pending_worker_does_not_block_window_controls_and_cannot_return_into_a_replacement() {
        tauri::async_runtime::block_on(async {
            let session = std::sync::Arc::new(SurfaceSession::default());
            let gate = std::sync::Arc::new(tokio::sync::Mutex::new(()));
            let first = SurfaceContext::new(json!({"token":"owned-token"}), options()).unwrap();
            session.replace(Some(first.clone())).unwrap();
            let (began_tx, began_rx) = tokio::sync::oneshot::channel();
            let (finished_tx, finished_rx) = tokio::sync::oneshot::channel();
            let pending_session = session.clone();
            let pending_gate = gate.clone();
            let pending = tauri::async_runtime::spawn(async move {
                call_with_binding(
                    &pending_session,
                    &pending_gate,
                    &first.generation,
                    |_| Ok(()),
                    |_| async {
                        began_tx.send(()).unwrap();
                        finished_rx.await.unwrap();
                        Ok(json!({"oldResult":true}))
                    },
                )
                .await
            });
            began_rx.await.unwrap();
            {
                let _native_control = gate
                    .try_lock()
                    .expect("slow worker retained the window lock");
                session.replace(None).unwrap();
            }
            finished_tx.send(()).unwrap();
            assert_eq!(pending.await.unwrap().unwrap_err(), "MODULE_SURFACE_STALE");
        });
    }
    #[test]
    fn high_dpi_negative_monitor_bounds_keep_the_entire_window_reachable() {
        let area = tauri::PhysicalRect {
            position: tauri::PhysicalPosition::new(-1280, -200),
            size: tauri::PhysicalSize::new(1280, 720),
        };
        let fit = geometry(1000.0, 1400.0, 2.0, &area).unwrap();
        assert_eq!((fit.width, fit.height), (640.0, 360.0));
        assert_eq!((fit.x, fit.y), (-1280, -200));
        assert!(fit.min_width <= fit.width && fit.min_height <= fit.height);
        assert!(geometry(440.0, 620.0, f64::NAN, &area).is_err());
    }
    #[test]
    fn surface_commands_reject_other_windows_and_unexpected_arguments() {
        let exact = tauri::ipc::InvokeBody::Json(json!({"generation":"sample"}));
        assert!(validate_command_request(LABEL, &[LABEL], &exact, &["generation"]).is_ok());
        for label in ["main", "companion", "companion-menu", "arbitrary"] {
            assert!(validate_command_request(label, &[LABEL], &exact, &["generation"]).is_err());
        }
        for invalid in [
            json!({}),
            json!({"generation":"sample","token":"other-module"}),
            json!({"generation":"sample","url":"http://127.0.0.1:1234"}),
        ] {
            assert!(validate_command_request(
                LABEL,
                &[LABEL],
                &tauri::ipc::InvokeBody::Json(invalid),
                &["generation"]
            )
            .is_err());
        }
    }
    #[test]
    fn surface_capability_cannot_open_packages_stop_workers_or_touch_other_windows() {
        let value: Value =
            serde_json::from_str(include_str!("../capabilities/module-surface.json")).unwrap();
        assert_eq!(value["windows"], json!([LABEL]));
        let permissions = value["permissions"].as_array().unwrap();
        assert_eq!(permissions.len(), 6);
        for denied in [
            "allow-modules-open",
            "allow-modules-call",
            "allow-modules-close",
            "allow-worker-call",
            "allow-worker-stop",
            "allow-module-surface-open",
        ] {
            assert!(!permissions.contains(&json!(denied)));
        }
    }
}
