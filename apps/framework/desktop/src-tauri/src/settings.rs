use crate::personalization::{CompanionChange, Personalization, PetActionMode};
use serde::{Deserialize, Serialize};
use std::{fs, path::PathBuf, sync::Mutex};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Settings {
    pub schema_version: u32,
    pub theme: Theme,
    #[serde(default)]
    pub ui_style: UiStyle,
    pub close_to_tray: bool,
    #[serde(default)]
    pub personalization: Personalization,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum UiStyle {
    #[default]
    Simple,
    Codex,
    Cursor,
    Web,
    Anime,
    Glass,
    Mint,
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
        self.personalization.validate()?;
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
        self.persist(&settings)?;
        Ok(settings)
    }

    /// Read, apply a partial change, validate and persist while holding one lock.
    /// Callers must not provide a stale whole-settings snapshot for local changes.
    pub fn update(
        &self,
        change: impl FnOnce(&mut Settings) -> Result<(), String>,
    ) -> Result<Settings, String> {
        let _guard = self
            .write_lock
            .lock()
            .map_err(|_| "Settings lock unavailable")?;
        let mut settings = self.load()?;
        change(&mut settings)?;
        settings.validate()?;
        self.persist(&settings)?;
        Ok(settings)
    }

    pub fn select_companion(&self, model_id: Option<String>) -> Result<Settings, String> {
        self.update(move |settings| {
            let pet = &mut settings.personalization.pet;
            if pet.model_id != model_id {
                pet.action_mode = PetActionMode::Idle;
            }
            pet.enabled = model_id.is_some();
            pet.model_id = model_id;
            Ok(())
        })
    }

    pub fn update_companion(&self, change: CompanionChange) -> Result<Settings, String> {
        self.update(move |settings| {
            change.apply(&mut settings.personalization.pet);
            Ok(())
        })
    }

    pub fn save_appearance(
        &self,
        theme: Theme,
        ui_style: UiStyle,
        close_to_tray: bool,
    ) -> Result<Settings, String> {
        self.update(move |settings| {
            settings.theme = theme;
            settings.ui_style = ui_style;
            settings.close_to_tray = close_to_tray;
            Ok(())
        })
    }

    fn persist(&self, settings: &Settings) -> Result<(), String> {
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
                serde_json::to_string_pretty(settings)
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
        Ok(())
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
    fn selecting_a_role_preserves_preferences_and_resets_only_a_changed_role_action() {
        let root = std::env::temp_dir().join(format!("opendesk-select-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        let mut original = Settings::default();
        original.theme = Theme::Graphite;
        original.ui_style = UiStyle::Anime;
        original.close_to_tray = false;
        original.personalization.pet.mode = crate::personalization::PetMode::InApp;
        original.personalization.pet.size = 208;
        original.personalization.pet.always_on_top = false;
        original.personalization.pet.animated = false;
        original.personalization.pet.model_id = Some("first-role".into());
        original.personalization.pet.action_mode = PetActionMode::Thinking;
        store.save(original.clone()).unwrap();

        let selected = store.select_companion(Some("first-role".into())).unwrap();
        let mut expected = original.clone();
        expected.personalization.pet.enabled = true;
        assert_eq!(selected, expected);
        assert_eq!(
            selected.personalization.pet.action_mode,
            PetActionMode::Thinking
        );

        let changed = store.select_companion(Some("second-role".into())).unwrap();
        expected.personalization.pet.model_id = Some("second-role".into());
        expected.personalization.pet.action_mode = PetActionMode::Idle;
        assert_eq!(changed, expected);
        let disabled = store.select_companion(None).unwrap();
        expected.personalization.pet.enabled = false;
        expected.personalization.pet.model_id = None;
        assert_eq!(disabled, expected);
        assert_eq!(store.load().unwrap(), expected);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn each_companion_change_preserves_other_fields_and_never_enables_a_disabled_role() {
        let root =
            std::env::temp_dir().join(format!("opendesk-pet-patch-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        let mut original = Settings::default();
        original.theme = Theme::Forest;
        original.personalization.pet.model_id = Some("disabled-role".into());
        let pet = original.personalization.pet.clone();
        for (change, expected_pet) in [
            (
                CompanionChange::Mode(crate::personalization::PetMode::InApp),
                crate::personalization::PetSettings {
                    mode: crate::personalization::PetMode::InApp,
                    ..pet.clone()
                },
            ),
            (
                CompanionChange::Size(256),
                crate::personalization::PetSettings {
                    size: 256,
                    ..pet.clone()
                },
            ),
            (
                CompanionChange::AlwaysOnTop(false),
                crate::personalization::PetSettings {
                    always_on_top: false,
                    ..pet.clone()
                },
            ),
            (
                CompanionChange::Animated(false),
                crate::personalization::PetSettings {
                    animated: false,
                    ..pet.clone()
                },
            ),
            (
                CompanionChange::ActionMode(PetActionMode::Thinking),
                crate::personalization::PetSettings {
                    action_mode: PetActionMode::Thinking,
                    ..pet
                },
            ),
        ] {
            store.save(original.clone()).unwrap();
            let mut expected = original.clone();
            expected.personalization.pet = expected_pet;
            assert_eq!(store.update_companion(change).unwrap(), expected);
            assert!(!store.load().unwrap().personalization.pet.enabled);
            assert_eq!(
                store
                    .load()
                    .unwrap()
                    .personalization
                    .pet
                    .model_id
                    .as_deref(),
                Some("disabled-role")
            );
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn rejected_partial_updates_preserve_original_bytes() {
        let root =
            std::env::temp_dir().join(format!("opendesk-rejected-patch-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        store.save(Settings::default()).unwrap();
        let original = fs::read(&store.path).unwrap();
        for size in [0, 95, 257, u16::MAX] {
            assert!(store.update_companion(CompanionChange::Size(size)).is_err());
            assert_eq!(fs::read(&store.path).unwrap(), original);
        }
        for id in ["", "../other", "Uppercase", "https://example.invalid/pet"] {
            assert!(store.select_companion(Some(id.into())).is_err());
            assert_eq!(fs::read(&store.path).unwrap(), original);
        }
        let malformed = r#"{"schemaVersion":99,"theme":"light","closeToTray":true}"#;
        fs::write(&store.path, malformed).unwrap();
        assert!(store.select_companion(Some("valid-role".into())).is_err());
        assert!(store
            .update_companion(CompanionChange::Animated(false))
            .is_err());
        assert!(store
            .save_appearance(Theme::Dark, UiStyle::Web, false)
            .is_err());
        assert_eq!(fs::read_to_string(&store.path).unwrap(), malformed);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn appearance_update_keeps_latest_pet_preferences() {
        let root = std::env::temp_dir().join(format!(
            "opendesk-appearance-patch-{}",
            uuid::Uuid::new_v4()
        ));
        let store = SettingsStore::new(&root);
        store.select_companion(Some("active-role".into())).unwrap();
        store.update_companion(CompanionChange::Size(240)).unwrap();
        let latest = store
            .update_companion(CompanionChange::ActionMode(PetActionMode::Thinking))
            .unwrap();
        let saved = store
            .save_appearance(Theme::Sand, UiStyle::Glass, false)
            .unwrap();
        assert_eq!(saved.personalization, latest.personalization);
        assert_eq!(saved.theme, Theme::Sand);
        assert_eq!(saved.ui_style, UiStyle::Glass);
        assert!(!saved.close_to_tray);
        assert_eq!(store.load().unwrap(), saved);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn partial_updates_hold_the_lock_across_read_change_and_write() {
        use std::{
            sync::{mpsc, Arc},
            time::Duration,
        };
        let root = std::env::temp_dir().join(format!(
            "opendesk-concurrent-patch-{}",
            uuid::Uuid::new_v4()
        ));
        let store = Arc::new(SettingsStore::new(&root));
        store.save(Settings::default()).unwrap();
        let (entered_tx, entered_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let first = Arc::clone(&store);
        let writer = std::thread::spawn(move || {
            first
                .update(|settings| {
                    settings.theme = Theme::Dark;
                    entered_tx.send(()).unwrap();
                    release_rx.recv_timeout(Duration::from_secs(5)).unwrap();
                    Ok(())
                })
                .unwrap()
        });
        entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        let (second_entered_tx, second_entered_rx) = mpsc::channel();
        let second = Arc::clone(&store);
        let pet_writer = std::thread::spawn(move || {
            second
                .update(|settings| {
                    second_entered_tx.send(()).unwrap();
                    settings.personalization.pet.size = 208;
                    Ok(())
                })
                .unwrap()
        });
        assert!(second_entered_rx
            .recv_timeout(Duration::from_millis(50))
            .is_err());
        release_tx.send(()).unwrap();
        writer.join().unwrap();
        pet_writer.join().unwrap();
        let saved = store.load().unwrap();
        assert_eq!(saved.theme, Theme::Dark);
        assert_eq!(saved.personalization.pet.size, 208);
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn save_round_trip_and_preserve_future_schema() {
        let root = std::env::temp_dir().join(format!("opendesk-settings-{}", uuid::Uuid::new_v4()));
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
        let root = std::env::temp_dir().join(format!("opendesk-settings-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        store.save(Settings::default()).unwrap();
        let original = r#"{"schemaVersion":1,"theme":"unknown-future-theme","closeToTray":true}"#;
        fs::write(&store.path, original).unwrap();
        assert!(store.load().unwrap_err().contains("file preserved"));
        assert!(store.save(Settings::default()).is_err());
        assert_eq!(fs::read_to_string(&store.path).unwrap(), original);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn legacy_settings_default_to_simple_without_changing_existing_preferences() {
        let root = std::env::temp_dir().join(format!("opendesk-settings-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        fs::create_dir_all(store.path.parent().unwrap()).unwrap();
        let original = r#"{"schemaVersion":1,"theme":"graphite","closeToTray":false}"#;
        fs::write(&store.path, original).unwrap();
        let settings = store.load().unwrap();
        assert_eq!(settings.ui_style, UiStyle::Simple);
        assert_eq!(settings.theme, Theme::Graphite);
        assert!(!settings.close_to_tray);
        assert_eq!(settings.personalization, Personalization::default());
        // Loading upgrades only the in-memory representation; a successful explicit save
        // is the first operation that writes the new optional field to an old file.
        assert_eq!(fs::read_to_string(&store.path).unwrap(), original);
        store.save(settings.clone()).unwrap();
        assert_eq!(SettingsStore::new(&root).load().unwrap(), settings);
        let persisted: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&store.path).unwrap()).unwrap();
        assert_eq!(persisted["uiStyle"], "simple");
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn each_ui_style_round_trips_independently_of_theme() {
        let root = std::env::temp_dir().join(format!("opendesk-settings-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        for (style, serialized) in [
            (UiStyle::Simple, "simple"),
            (UiStyle::Codex, "codex"),
            (UiStyle::Cursor, "cursor"),
            (UiStyle::Web, "web"),
            (UiStyle::Anime, "anime"),
            (UiStyle::Glass, "glass"),
            (UiStyle::Mint, "mint"),
        ] {
            let settings = Settings {
                ui_style: style,
                theme: Theme::Ocean,
                close_to_tray: false,
                ..Settings::default()
            };
            assert_eq!(store.save(settings.clone()).unwrap(), settings);
            assert_eq!(SettingsStore::new(&root).load().unwrap(), settings);
            let persisted: serde_json::Value =
                serde_json::from_str(&fs::read_to_string(&store.path).unwrap()).unwrap();
            assert_eq!(persisted["uiStyle"], serialized);
            assert_eq!(persisted["theme"], "ocean");
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn unknown_ui_style_is_rejected_without_replacing_original() {
        let root = std::env::temp_dir().join(format!("opendesk-settings-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        store.save(Settings::default()).unwrap();
        let original =
            r#"{"schemaVersion":1,"theme":"dark","uiStyle":"future-style","closeToTray":true}"#;
        fs::write(&store.path, original).unwrap();
        assert!(store.load().unwrap_err().contains("file preserved"));
        assert!(store.save(Settings::default()).is_err());
        assert_eq!(fs::read_to_string(&store.path).unwrap(), original);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn personalization_round_trip_and_invalid_existing_ranges_are_preserved() {
        let root =
            std::env::temp_dir().join(format!("opendesk-personalization-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        let mut settings = Settings::default();
        settings.personalization.pet.enabled = true;
        settings.personalization.pet.mode = crate::personalization::PetMode::InApp;
        settings.personalization.pet.size = 208;
        store.save(settings.clone()).unwrap();
        assert_eq!(SettingsStore::new(&root).load().unwrap(), settings);
        let mut invalid = serde_json::to_value(&settings).unwrap();
        invalid["personalization"]["pet"]["size"] = serde_json::json!(20);
        let original = serde_json::to_string(&invalid).unwrap();
        fs::write(&store.path, &original).unwrap();
        assert!(store.load().is_err());
        assert!(store.save(Settings::default()).is_err());
        assert_eq!(fs::read_to_string(&store.path).unwrap(), original);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn retired_background_and_effects_are_discarded_only_on_explicit_save() {
        let root = std::env::temp_dir().join(format!("opendesk-legacy-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        fs::create_dir_all(store.path.parent().unwrap()).unwrap();
        let original = r#"{
            "schemaVersion":1,"theme":"graphite","uiStyle":"glass","closeToTray":false,
            "personalization":{
                "background":{"assetId":"missing-retired-image","placement":"sidebar","opacity":75,"blur":0,"dim":25},
                "effects":{"material":"glass","transparency":78,"motion":"subtle","decoration":"stars"},
                "pet":{"enabled":true,"mode":"in-app","modelId":"sample-companion","size":208,"alwaysOnTop":false,"animated":false}
            }
        }"#;
        fs::write(&store.path, original).unwrap();
        let settings = store.load().unwrap();
        assert_eq!(fs::read_to_string(&store.path).unwrap(), original);
        assert!(!root.join("wallpapers").exists());
        assert_eq!(settings.theme, Theme::Graphite);
        assert_eq!(settings.ui_style, UiStyle::Glass);
        assert!(!settings.close_to_tray);
        assert_eq!(
            settings.personalization.pet,
            crate::personalization::PetSettings {
                enabled: true,
                mode: crate::personalization::PetMode::InApp,
                model_id: Some("sample-companion".into()),
                size: 208,
                always_on_top: false,
                animated: false,
                action_mode: PetActionMode::Idle,
            }
        );
        store.save(settings.clone()).unwrap();
        assert_eq!(SettingsStore::new(&root).load().unwrap(), settings);
        let persisted: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&store.path).unwrap()).unwrap();
        assert_eq!(persisted["personalization"].as_object().unwrap().len(), 1);
        assert_eq!(
            persisted["personalization"]["pet"]["modelId"],
            "sample-companion"
        );
        assert!(!root.join("wallpapers").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn legacy_compatibility_never_accepts_unknown_settings_invalid_pet_or_future_schema() {
        let root = std::env::temp_dir().join(format!("opendesk-invalid-{}", uuid::Uuid::new_v4()));
        let store = SettingsStore::new(&root);
        fs::create_dir_all(store.path.parent().unwrap()).unwrap();
        let mut baseline = serde_json::to_value(Settings::default()).unwrap();
        baseline["personalization"]["background"] = serde_json::json!({"assetId":"removed"});
        baseline["personalization"]["effects"] = serde_json::json!({"motion":"removed"});
        for (path, value) in [
            ("/personalization/unknown", serde_json::json!({})),
            ("/personalization/pet/unknown", serde_json::json!(true)),
            (
                "/personalization/pet/mode",
                serde_json::json!("external-process"),
            ),
            ("/personalization/pet/size", serde_json::json!(257)),
            ("/personalization/pet/enabled", serde_json::json!("true")),
            ("/schemaVersion", serde_json::json!(99)),
        ] {
            let mut invalid = baseline.clone();
            let (parent, key) = path.rsplit_once('/').unwrap();
            invalid.pointer_mut(parent).unwrap()[key] = value;
            let original = serde_json::to_string(&invalid).unwrap();
            fs::write(&store.path, &original).unwrap();
            assert!(store.load().is_err(), "must reject {path}");
            assert!(
                store.save(Settings::default()).is_err(),
                "must preserve {path}"
            );
            assert_eq!(fs::read_to_string(&store.path).unwrap(), original);
        }
        fs::remove_dir_all(root).unwrap();
    }
}
