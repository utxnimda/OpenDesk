//! The desktop companion is an owned auxiliary surface, not a separate process.
use crate::personalization::{PetMode, PetSettings};
use serde::Serialize;
use tauri::{Emitter, Manager};

pub const LABEL: &str = "companion";

/// Positions and work areas are physical pixels; requested content size is logical.
/// Keep an already-valid position exactly, including on monitors with negative origins.
fn clamp_position(
    position: tauri::PhysicalPosition<i32>,
    logical_size: tauri::LogicalSize<f64>,
    scale_factor: f64,
    work_area: &tauri::PhysicalRect<i32, u32>,
) -> Result<tauri::PhysicalPosition<i32>, String> {
    let physical_width = (logical_size.width * scale_factor).ceil();
    let physical_height = (logical_size.height * scale_factor).ceil();
    if !scale_factor.is_finite()
        || scale_factor <= 0.0
        || !physical_width.is_finite()
        || !physical_height.is_finite()
        || physical_width <= 0.0
        || physical_height <= 0.0
        || physical_width > f64::from(u32::MAX)
        || physical_height > f64::from(u32::MAX)
    {
        return Err("Invalid companion size or monitor scale factor.".into());
    }
    let clamp_axis = |value: i32, origin: i32, extent: u32, window_extent: f64| {
        let minimum = i64::from(origin);
        // A window larger than the available work area stays anchored at its origin,
        // keeping its controls reachable instead of creating an invalid clamp range.
        let maximum = (minimum + i64::from(extent) - window_extent as i64)
            .max(minimum)
            .min(i64::from(i32::MAX));
        i64::from(value).clamp(minimum, maximum) as i32
    };
    Ok(tauri::PhysicalPosition::new(
        clamp_axis(
            position.x,
            work_area.position.x,
            work_area.size.width,
            physical_width,
        ),
        clamp_axis(
            position.y,
            work_area.position.y,
            work_area.size.height,
            physical_height,
        ),
    ))
}

#[derive(Serialize)]
pub struct CompanionState {
    pub visible: bool,
}

pub fn state(app: &tauri::AppHandle) -> Result<CompanionState, String> {
    let visible = match app.get_webview_window(LABEL) {
        Some(window) => window.is_visible().map_err(|e| e.to_string())?,
        None => false,
    };
    Ok(CompanionState { visible })
}

pub fn show(app: &tauri::AppHandle, settings: &PetSettings) -> Result<(), String> {
    if !settings.enabled || settings.mode != PetMode::Desktop {
        return Err("Save enabled desktop companion mode before opening its window.".into());
    }
    let width = f64::from(settings.size + 80).max(240.0);
    let height = f64::from(settings.size + 100);
    if let Some(window) = app.get_webview_window(LABEL) {
        // Choose the monitor before resizing: growing a window near an edge must
        // not accidentally select the adjacent monitor as its new destination.
        let monitor = window
            .current_monitor()
            .map_err(|e| e.to_string())?
            .or(app.primary_monitor().map_err(|e| e.to_string())?);
        let previous_position = window.outer_position().map_err(|e| e.to_string())?;
        window
            .set_size(tauri::LogicalSize::new(width, height))
            .map_err(|e| e.to_string())?;
        if let Some(monitor) = monitor {
            let position = clamp_position(
                previous_position,
                tauri::LogicalSize::new(width, height),
                monitor.scale_factor(),
                monitor.work_area(),
            )?;
            if position != previous_position {
                window.set_position(position).map_err(|e| e.to_string())?;
            }
        }
        window
            .set_always_on_top(settings.always_on_top)
            .map_err(|e| e.to_string())?;
        window.show().map_err(|e| e.to_string())?;
        window
            .emit("companion-visibility-changed", true)
            .map_err(|e| e.to_string())?;
        return Ok(());
    }
    let window = tauri::WebviewWindowBuilder::new(
        app,
        LABEL,
        tauri::WebviewUrl::App("index.html?surface=companion".into()),
    )
    .title("OpenDesk Companion")
    .inner_size(width, height)
    .decorations(false)
    .transparent(true)
    .background_color(tauri::window::Color(0, 0, 0, 0))
    .shadow(false)
    .resizable(false)
    .focused(false)
    .skip_taskbar(true)
    .always_on_top(settings.always_on_top)
    .visible(false)
    .build()
    .map_err(|e| format!("Cannot create desktop companion: {e}"))?;
    let position_result = (|| -> Result<(), String> {
        let monitor = if let Some(main) = app.get_webview_window("main") {
            main.current_monitor().map_err(|e| e.to_string())?
        } else {
            None
        }
        .or(app.primary_monitor().map_err(|e| e.to_string())?);
        if let Some(monitor) = monitor {
            let area = monitor.work_area();
            let corner = clamp_position(
                tauri::PhysicalPosition::new(i32::MAX, i32::MAX),
                tauri::LogicalSize::new(width, height),
                monitor.scale_factor(),
                area,
            )?;
            let x = corner.x.saturating_sub(24).max(area.position.x);
            let y = corner.y.saturating_sub(24).max(area.position.y);
            window
                .set_position(tauri::PhysicalPosition::new(x, y))
                .map_err(|e| e.to_string())?;
        }
        window.show().map_err(|e| e.to_string())?;
        Ok(())
    })();
    if position_result.is_err() {
        let _ = window.destroy();
    }
    position_result
}

pub fn hide(app: &tauri::AppHandle) -> Result<(), String> {
    crate::companion_menu::close(app, None)?;
    if let Some(window) = app.get_webview_window(LABEL) {
        window.hide().map_err(|e| e.to_string())?;
        window
            .emit("companion-visibility-changed", false)
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn start_drag(window: &tauri::WebviewWindow) -> Result<(), String> {
    if window.label() != LABEL {
        return Err("Only the companion surface may start its own drag.".into());
    }
    window.start_dragging().map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tauri::{LogicalSize, PhysicalPosition, PhysicalRect, PhysicalSize};

    fn area(x: i32, y: i32, width: u32, height: u32) -> PhysicalRect<i32, u32> {
        PhysicalRect {
            position: PhysicalPosition::new(x, y),
            size: PhysicalSize::new(width, height),
        }
    }

    #[test]
    fn valid_position_is_preserved_when_resizing_or_shrinking() {
        let work = area(0, 0, 1920, 1040);
        let position = PhysicalPosition::new(350, 420);
        for size in [
            LogicalSize::new(336.0, 356.0),
            LogicalSize::new(240.0, 260.0),
        ] {
            assert_eq!(
                clamp_position(position, size, 1.0, &work).unwrap(),
                position
            );
        }
    }

    #[test]
    fn growing_from_160_to_256_repositions_only_the_overflowing_edges() {
        let work = area(0, 0, 1920, 1040);
        let old_position = PhysicalPosition::new(1656, 756); // 240x260, 24px inset.
        assert_eq!(
            clamp_position(old_position, LogicalSize::new(240.0, 260.0), 1.0, &work).unwrap(),
            old_position
        );
        assert_eq!(
            clamp_position(old_position, LogicalSize::new(336.0, 356.0), 1.0, &work).unwrap(),
            PhysicalPosition::new(1584, 684)
        );
        assert_eq!(
            clamp_position(
                PhysicalPosition::new(100, 756),
                LogicalSize::new(336.0, 356.0),
                1.0,
                &work
            )
            .unwrap(),
            PhysicalPosition::new(100, 684)
        );
    }

    #[test]
    fn negative_monitor_origins_and_fractional_dpi_use_physical_bounds() {
        let work = area(-2560, -200, 2560, 1400);
        assert_eq!(
            clamp_position(
                PhysicalPosition::new(-300, 1000),
                LogicalSize::new(336.0, 356.0),
                1.5,
                &work
            )
            .unwrap(),
            PhysicalPosition::new(-504, 666)
        );
        let valid = PhysicalPosition::new(-2200, -100);
        assert_eq!(
            clamp_position(valid, LogicalSize::new(336.0, 356.0), 1.5, &work).unwrap(),
            valid
        );
        let fractional = area(0, 0, 1200, 900);
        assert_eq!(
            clamp_position(
                PhysicalPosition::new(1100, 800),
                LogicalSize::new(241.0, 261.0),
                1.25,
                &fractional
            )
            .unwrap(),
            PhysicalPosition::new(898, 573)
        );
    }

    #[test]
    fn offscreen_and_oversized_windows_stay_reachable_without_overflow() {
        let work = area(-1920, 40, 1920, 1000);
        assert_eq!(
            clamp_position(
                PhysicalPosition::new(-3000, -400),
                LogicalSize::new(240.0, 260.0),
                1.0,
                &work
            )
            .unwrap(),
            work.position
        );
        assert_eq!(
            clamp_position(
                PhysicalPosition::new(300, 300),
                LogicalSize::new(3000.0, 2000.0),
                1.0,
                &work
            )
            .unwrap(),
            work.position
        );
        let extreme = area(i32::MAX - 100, i32::MIN, 500, 500);
        assert_eq!(
            clamp_position(
                PhysicalPosition::new(i32::MAX, i32::MIN),
                LogicalSize::new(240.0, 260.0),
                1.0,
                &extreme
            )
            .unwrap(),
            PhysicalPosition::new(i32::MAX, i32::MIN)
        );
    }

    #[test]
    fn invalid_dpi_is_rejected() {
        let work = area(0, 0, 1920, 1040);
        for scale in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(clamp_position(
                PhysicalPosition::new(0, 0),
                LogicalSize::new(240.0, 260.0),
                scale,
                &work
            )
            .is_err());
        }
    }
}
