//! One owned, transient menu surface. Preferences remain owned by SettingsStore.
use crate::{
    personalization::{PetMode, PetSettings},
    AppState,
};
use serde::Serialize;
use std::sync::Mutex;
use tauri::{Emitter, Manager};

pub const LABEL: &str = "companion-menu";
const MAX_GENERATION: u64 = (1 << 53) - 1;

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct MenuContext {
    pub generation: u64,
    pub model_id: String,
    pub can_animate: bool,
}

#[derive(Default)]
struct SessionState {
    generation: u64,
    active: Option<MenuContext>,
    selected_model_id: Option<String>,
    visible: bool,
    ready: bool,
}

#[derive(Default)]
pub struct MenuSession(Mutex<SessionState>);

pub fn effective_model<'a>(
    pet: &'a PetSettings,
    ready_model: Option<&'a str>,
) -> Result<&'a str, String> {
    if !pet.enabled || pet.mode != PetMode::Desktop {
        return Err("The desktop companion is not enabled.".into());
    }
    let model = pet
        .model_id
        .as_deref()
        .or(ready_model)
        .ok_or("The companion has not reported its current model yet.")?;
    if model.is_empty()
        || model.len() > 64
        || !model
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
    {
        return Err("Invalid companion model identity.".into());
    }
    Ok(model)
}

impl MenuSession {
    pub fn begin(
        &self,
        pet: &PetSettings,
        ready_model: Option<&str>,
        model_id: String,
        can_animate: bool,
    ) -> Result<MenuContext, String> {
        if effective_model(pet, ready_model)? != model_id {
            return Err("The companion model changed; reopen its menu.".into());
        }
        let mut state = self
            .0
            .lock()
            .map_err(|_| "Companion menu state lock failed.")?;
        if state.generation >= MAX_GENERATION {
            return Err("Companion menu generation limit reached; restart OpenDesk.".into());
        }
        state.generation += 1;
        let context = MenuContext {
            generation: state.generation,
            model_id,
            can_animate,
        };
        state.active = Some(context.clone());
        state.selected_model_id = pet.model_id.clone();
        state.visible = false;
        state.ready = false;
        Ok(context)
    }

    pub fn context(&self) -> Result<MenuContext, String> {
        self.0
            .lock()
            .map_err(|_| "Companion menu state lock failed.")?
            .active
            .clone()
            .ok_or("The companion menu is closed.".into())
    }

    pub fn mark_visible(&self, generation: u64) -> Result<(), String> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| "Companion menu state lock failed.")?;
        if state.active.as_ref().map(|c| c.generation) != Some(generation) {
            return Err("The companion menu was closed before it could be shown.".into());
        }
        state.visible = true;
        Ok(())
    }

    pub fn mark_ready(&self, generation: u64) -> Result<(), String> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| "Companion menu state lock failed.")?;
        if state.active.as_ref().map(|c| c.generation) != Some(generation) {
            return Err("Stale companion menu readiness.".into());
        }
        state.ready = true;
        Ok(())
    }

    pub fn is_ready(&self, generation: u64) -> Result<bool, String> {
        let state = self
            .0
            .lock()
            .map_err(|_| "Companion menu state lock failed.")?;
        Ok(
            state.active.as_ref().map(|c| c.generation) == Some(generation)
                && state.visible
                && state.ready,
        )
    }

    pub fn is_visible(&self) -> Result<bool, String> {
        Ok(self
            .0
            .lock()
            .map_err(|_| "Companion menu state lock failed.")?
            .visible)
    }

    pub fn authorize(
        &self,
        generation: u64,
        pet: &PetSettings,
        ready_model: Option<&str>,
    ) -> Result<(), String> {
        let state = self
            .0
            .lock()
            .map_err(|_| "Companion menu state lock failed.")?;
        let context = state
            .active
            .as_ref()
            .ok_or("The companion menu is closed.")?;
        if !state.visible
            || context.generation != generation
            || state.selected_model_id != pet.model_id
            || context.model_id != effective_model(pet, ready_model)?
        {
            return Err("Stale companion menu action; reopen the current companion menu.".into());
        }
        Ok(())
    }

    pub fn invalidate(&self, generation: Option<u64>) -> Result<bool, String> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| "Companion menu state lock failed.")?;
        if state.active.is_none()
            || generation.is_some_and(|g| state.active.as_ref().map(|c| c.generation) != Some(g))
        {
            return Ok(false);
        }
        state.active = None;
        state.visible = false;
        state.ready = false;
        Ok(true)
    }

    pub fn changed_selection(&self, pet: &PetSettings) -> Result<bool, String> {
        let state = self
            .0
            .lock()
            .map_err(|_| "Companion menu state lock failed.")?;
        Ok(state.active.is_some()
            && (!pet.enabled
                || pet.mode != PetMode::Desktop
                || pet.model_id != state.selected_model_id))
    }
}

/// Cursor, position and work area are physical pixels. Scale only the fixed logical menu size.
fn layout(
    cursor: tauri::PhysicalPosition<f64>,
    scale: f64,
    area: &tauri::PhysicalRect<i32, u32>,
) -> Result<(tauri::PhysicalPosition<i32>, tauri::PhysicalSize<u32>), String> {
    if !scale.is_finite()
        || scale <= 0.0
        || !cursor.x.is_finite()
        || !cursor.y.is_finite()
        || area.size.width == 0
        || area.size.height == 0
        || 480.0 * scale > f64::from(u32::MAX)
    {
        return Err("Invalid companion menu monitor geometry.".into());
    }
    let size = tauri::PhysicalSize::new(
        ((320.0 * scale).ceil() as u32).min(area.size.width),
        ((480.0 * scale).ceil() as u32).min(area.size.height),
    );
    let axis = |cursor: f64, origin: i32, extent: u32, size: u32| {
        let minimum = i64::from(origin);
        let maximum = (minimum + i64::from(extent) - i64::from(size)).min(i64::from(i32::MAX));
        (cursor.round() as i64)
            .saturating_add((8.0 * scale).ceil() as i64)
            .clamp(minimum, maximum) as i32
    };
    Ok((
        tauri::PhysicalPosition::new(
            axis(cursor.x, area.position.x, area.size.width, size.width),
            axis(cursor.y, area.position.y, area.size.height, size.height),
        ),
        size,
    ))
}

pub fn show(
    app: &tauri::AppHandle,
    pet: &PetSettings,
    ready_model: Option<&str>,
    model_id: String,
    can_animate: bool,
) -> Result<MenuContext, String> {
    let state = app.state::<AppState>();
    let cursor = app.cursor_position().map_err(|e| e.to_string())?;
    let monitor = app
        .monitor_from_point(cursor.x, cursor.y)
        .map_err(|e| e.to_string())?
        .or(app.primary_monitor().map_err(|e| e.to_string())?)
        .ok_or("No monitor is available for the companion menu.")?;
    let (position, size) = layout(cursor, monitor.scale_factor(), monitor.work_area())?;
    let context = state
        .companion_menu
        .begin(pet, ready_model, model_id, can_animate)?;
    let shown = (|| -> Result<(), String> {
        let window = match app.get_webview_window(LABEL) {
            Some(window) => window,
            None => tauri::WebviewWindowBuilder::new(
                app,
                LABEL,
                tauri::WebviewUrl::App("index.html?surface=companion-menu".into()),
            )
            .title("OpenDesk Companion Menu")
            .inner_size(320.0, 480.0)
            .decorations(false)
            .transparent(true)
            .background_color(tauri::window::Color(0, 0, 0, 0))
            .shadow(false)
            .resizable(false)
            .skip_taskbar(true)
            .always_on_top(true)
            .focused(false)
            .visible(false)
            .build()
            .map_err(|e| format!("Cannot create companion menu: {e}"))?,
        };
        window.set_position(position).map_err(|e| e.to_string())?;
        window.set_size(size).map_err(|e| e.to_string())?;
        window
            .emit("companion-menu-context-changed", ())
            .map_err(|e| e.to_string())?;
        window.show().map_err(|e| e.to_string())?;
        window.set_focus().map_err(|e| e.to_string())?;
        state.companion_menu.mark_visible(context.generation)?;
        Ok(())
    })();
    if let Err(error) = shown {
        if let Err(cleanup) = close(app, Some(context.generation)) {
            state
                .diagnostics
                .record("error", "companion.menu_cleanup", &cleanup);
        }
        return Err(error);
    }
    Ok(context)
}

pub fn close(app: &tauri::AppHandle, generation: Option<u64>) -> Result<(), String> {
    if app
        .state::<AppState>()
        .companion_menu
        .invalidate(generation)?
    {
        if let Some(window) = app.get_webview_window(LABEL) {
            window.hide().map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

pub fn reconcile(app: &tauri::AppHandle, pet: &PetSettings) -> Result<(), String> {
    if app
        .state::<AppState>()
        .companion_menu
        .changed_selection(pet)?
    {
        close(app, None)?;
    }
    Ok(())
}

pub fn authorize(
    app: &tauri::AppHandle,
    generation: u64,
    pet: &PetSettings,
    ready_model: Option<&str>,
) -> Result<(), String> {
    let window = app
        .get_webview_window(LABEL)
        .ok_or("The companion menu window is unavailable.")?;
    if !window.is_visible().map_err(|e| e.to_string())? {
        return Err("The companion menu is hidden.".into());
    }
    app.state::<AppState>()
        .companion_menu
        .authorize(generation, pet, ready_model)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn pet() -> PetSettings {
        PetSettings {
            enabled: true,
            mode: PetMode::Desktop,
            model_id: Some("test-model".into()),
            ..PetSettings::default()
        }
    }
    #[test]
    fn generation_blocks_late_close_ready_and_updates() {
        let session = MenuSession::default();
        let pet = pet();
        let first = session
            .begin(&pet, None, "test-model".into(), true)
            .unwrap();
        session.mark_visible(first.generation).unwrap();
        session.mark_ready(first.generation).unwrap();
        assert!(session.is_ready(first.generation).unwrap());
        let second = session
            .begin(&pet, None, "test-model".into(), false)
            .unwrap();
        session.mark_visible(second.generation).unwrap();
        assert!(!session.invalidate(Some(first.generation)).unwrap());
        assert!(session.mark_ready(first.generation).is_err());
        assert!(session.authorize(first.generation, &pet, None).is_err());
        session.authorize(second.generation, &pet, None).unwrap();
        assert!(session.invalidate(None).unwrap());
        assert!(session.mark_ready(second.generation).is_err());
        assert!(session.authorize(second.generation, &pet, None).is_err());
    }
    #[test]
    fn role_mode_disabled_and_legacy_default_are_checked() {
        let session = MenuSession::default();
        let mut pet = pet();
        pet.model_id = None;
        assert!(session
            .begin(&pet, None, "test-model".into(), true)
            .is_err());
        let context = session
            .begin(&pet, Some("test-model"), "test-model".into(), true)
            .unwrap();
        session.mark_visible(context.generation).unwrap();
        session
            .authorize(context.generation, &pet, Some("test-model"))
            .unwrap();
        assert!(session
            .authorize(context.generation, &pet, Some("other-model"))
            .is_err());
        pet.size = 256;
        pet.animated = false;
        assert!(!session.changed_selection(&pet).unwrap());
        pet.model_id = Some("test-model".into());
        assert!(session.authorize(context.generation, &pet, None).is_err());
        assert!(session.changed_selection(&pet).unwrap());
        pet.model_id = None;
        pet.mode = PetMode::InApp;
        assert!(session
            .authorize(context.generation, &pet, Some("test-model"))
            .is_err());
        pet.mode = PetMode::Desktop;
        pet.enabled = false;
        assert!(session
            .authorize(context.generation, &pet, Some("test-model"))
            .is_err());
    }
    #[test]
    fn physical_geometry_handles_negative_origin_dpi_and_small_work_area() {
        let area = tauri::PhysicalRect {
            position: tauri::PhysicalPosition::new(-2560, -200),
            size: tauri::PhysicalSize::new(2560, 1400),
        };
        let (position, size) =
            layout(tauri::PhysicalPosition::new(-50.0, 1100.0), 1.5, &area).unwrap();
        assert_eq!(size, tauri::PhysicalSize::new(480, 720));
        assert_eq!(position, tauri::PhysicalPosition::new(-480, 480));
        let small = tauri::PhysicalRect {
            position: tauri::PhysicalPosition::new(10, 20),
            size: tauri::PhysicalSize::new(200, 300),
        };
        assert_eq!(
            layout(tauri::PhysicalPosition::new(-9999.0, 9999.0), 2.0, &small).unwrap(),
            (small.position, small.size)
        );
        for scale in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(layout(tauri::PhysicalPosition::new(0.0, 0.0), scale, &area).is_err());
        }
    }
}
