use serde::{Deserialize, Serialize};
use std::{fs, path::PathBuf, sync::Mutex};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Settings {
    pub schema_version: u32,
    pub theme: Theme,
    pub close_to_tray: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Theme {
    System,
    Light,
    Dark,
    Sakura,
    Ocean,
    Forest,
    Sand,
    Graphite,
}

impl Default for Settings {
    fn default() -> Self {
        serde_json::from_str(include_str!(
            "../../../../../config/framework/defaults.json"
        ))
        .expect("checked framework defaults")
    }
}

impl Settings {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != 1 {
            return Err("Unsupported settings schema; existing data is preserved.".into());
        }
        Ok(())
    }
}

pub struct SettingsStore {
    path: PathBuf,
    write_lock: Mutex<()>,
}

impl SettingsStore {
    pub fn new(root: &std::path::Path) -> Self {
        Self {
            path: root.join("config/framework/settings.json"),
            write_lock: Mutex::new(()),
        }
    }

    pub fn load(&self) -> Result<Settings, String> {
        if !self.path.exists() {
            return Ok(Settings::default());
        }
        let text = fs::read_to_string(&self.path).map_err(|e| e.to_string())?;
        let settings: Settings = serde_json::from_str(&text)
            .map_err(|e| format!("Invalid settings; file preserved: {e}"))?;
        settings.validate()?;
        Ok(settings)
    }

    pub fn save(&self, settings: Settings) -> Result<Settings, String> {
        settings.validate()?;
        let _guard = self
            .write_lock
            .lock()
            .map_err(|_| "Settings lock unavailable")?;
        // Never silently overwrite a future or malformed schema.
        self.load()?;
        let parent = self.path.parent().ok_or("Invalid settings path")?;
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        let staged = parent.join(format!("settings-{}.tmp", uuid::Uuid::new_v4()));
        let result = (|| {
            use std::io::Write;
            let mut file = fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(&staged)
                .map_err(|e| e.to_string())?;
            file.write_all(
                serde_json::to_string_pretty(&settings)
                    .map_err(|e| e.to_string())?
                    .as_bytes(),
            )
            .map_err(|e| e.to_string())?;
            file.sync_all().map_err(|e| e.to_string())?;
            drop(file);
            replace_file(&staged, &self.path)
        })();
        if result.is_err() {
            let _ = fs::remove_file(&staged);
        }
        result?;
        Ok(settings)
    }
}

fn replace_file(from: &std::path::Path, to: &std::path::Path) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        use windows_sys::Win32::Storage::FileSystem::{
            MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
        };
        let from: Vec<u16> = from.as_os_str().encode_wide().chain(Some(0)).collect();
        let to: Vec<u16> = to.as_os_str().encode_wide().chain(Some(0)).collect();
        if unsafe {
            MoveFileExW(
                from.as_ptr(),
                to.as_ptr(),
                MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
            )
        } == 0
        {
            return Err(std::io::Error::last_os_error().to_string());
        }
        Ok(())
    }
    #[cfg(not(windows))]
    {
        fs::rename(from, to).map_err(|e| e.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn save_round_trip_and_preserve_future_schema() {
        let root =
            std::env::temp_dir().join(format!("nimdadesk-settings-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        assert_eq!(store.load().unwrap(), Settings::default());
        for theme in [
            Theme::System,
            Theme::Light,
            Theme::Dark,
            Theme::Sakura,
            Theme::Ocean,
            Theme::Forest,
            Theme::Sand,
            Theme::Graphite,
        ] {
            let settings = Settings {
                theme,
                ..Settings::default()
            };
            store.save(settings.clone()).unwrap();
            // A new store represents the next app session reading the same file.
            assert_eq!(SettingsStore::new(&root).load().unwrap(), settings);
        }
        fs::write(
            &store.path,
            r#"{"schemaVersion":99,"theme":"dark","closeToTray":false}"#,
        )
        .unwrap();
        assert!(store.save(Settings::default()).is_err());
        assert!(fs::read_to_string(&store.path).unwrap().contains("99"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unknown_theme_is_rejected_without_replacing_original() {
        let root =
            std::env::temp_dir().join(format!("nimdadesk-settings-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        store.save(Settings::default()).unwrap();
        let original = r#"{"schemaVersion":1,"theme":"unknown-future-theme","closeToTray":true}"#;
        fs::write(&store.path, original).unwrap();
        assert!(store.load().unwrap_err().contains("file preserved"));
        assert!(store.save(Settings::default()).is_err());
        assert_eq!(fs::read_to_string(&store.path).unwrap(), original);
        fs::remove_dir_all(root).unwrap();
    }
}
