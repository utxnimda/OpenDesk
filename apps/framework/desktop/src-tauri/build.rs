fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "host_info",
            "settings_get",
            "settings_save",
            "worker_call",
            "worker_stop",
            "frontend_ready",
            "modules_list",
            "modules_import",
            "modules_select",
            "modules_enable",
            "modules_open",
            "modules_call",
            "modules_close",
            "modules_ui_ready",
            "modules_load_failed",
        ]),
    ))
    .expect("Tauri build metadata");
}
