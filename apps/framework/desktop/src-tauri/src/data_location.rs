use serde::Deserialize;
use std::{
    fs,
    io::ErrorKind,
    path::{Component, Path, PathBuf},
};

const PORTABLE_MARKER: &str = "opendesk.portable.json";

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PortableLocation {
    schema_version: u32,
    data_directory: String,
}

/// Explicit environment configuration wins; an absent marker keeps the platform default.
/// A malformed local marker must never silently open a different user's data directory.
pub fn configured_root(
    explicit: Option<PathBuf>,
    executable: &Path,
) -> Result<Option<PathBuf>, String> {
    if let Some(path) = explicit {
        if !path.is_absolute() {
            return Err("OPENDESK_DATA_DIR must be absolute".into());
        }
        return Ok(Some(path));
    }
    let directory = executable
        .parent()
        .filter(|path| path.is_absolute())
        .ok_or("Cannot resolve the executable directory for portable data")?;
    let marker = directory.join(PORTABLE_MARKER);
    let metadata = match fs::symlink_metadata(&marker) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("Cannot read {PORTABLE_MARKER}: {error}")),
    };
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > 4096 {
        return Err(format!(
            "{PORTABLE_MARKER} must be an ordinary JSON file of at most 4096 bytes"
        ));
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(format!("{PORTABLE_MARKER} cannot be a reparse point"));
        }
    }
    let location: PortableLocation = serde_json::from_slice(
        &fs::read(&marker).map_err(|error| format!("Cannot read {PORTABLE_MARKER}: {error}"))?,
    )
    .map_err(|error| format!("Invalid {PORTABLE_MARKER}; existing data preserved: {error}"))?;
    if location.schema_version != 1 {
        return Err(format!("Unsupported {PORTABLE_MARKER} schemaVersion"));
    }
    let relative = Path::new(&location.data_directory);
    if location.data_directory.trim().is_empty()
        || location.data_directory.chars().any(char::is_control)
        || relative
            .components()
            .any(|part| matches!(part, Component::Prefix(_) | Component::RootDir))
    {
        return Err(format!(
            "{PORTABLE_MARKER} dataDirectory must be a nonempty relative path"
        ));
    }
    let mut resolved = directory.to_path_buf();
    for component in relative.components() {
        match component {
            Component::Normal(part) => resolved.push(part),
            Component::CurDir => {}
            Component::ParentDir if resolved.pop() => {}
            _ => {
                return Err(format!(
                    "{PORTABLE_MARKER} dataDirectory escapes the filesystem root"
                ))
            }
        }
    }
    if !resolved.is_absolute() || resolved.file_name().is_none() {
        return Err(format!(
            "{PORTABLE_MARKER} dataDirectory cannot be the filesystem root"
        ));
    }
    Ok(Some(resolved))
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root =
                std::env::temp_dir().join(format!("opendesk-portable-{}", uuid::Uuid::new_v4()));
            fs::create_dir_all(root.join("run/OpenDesk")).unwrap();
            Self(root)
        }
        fn executable(&self) -> PathBuf {
            self.0.join("run/OpenDesk/OpenDesk.exe")
        }
        fn marker(&self, value: &str) {
            fs::write(self.0.join("run/OpenDesk").join(PORTABLE_MARKER), value).unwrap();
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn portable_marker_resolves_from_executable_and_not_working_directory() {
        let fixture = Fixture::new();
        assert_eq!(configured_root(None, &fixture.executable()).unwrap(), None);
        fixture.marker(r#"{"schemaVersion":1,"dataDirectory":"../../runtime"}"#);
        assert_eq!(
            configured_root(None, &fixture.executable()).unwrap(),
            Some(fixture.0.join("runtime"))
        );
    }

    #[test]
    fn explicit_data_root_wins_without_reading_a_broken_marker() {
        let fixture = Fixture::new();
        fixture.marker("not json");
        let explicit = fixture.0.join("explicit");
        assert_eq!(
            configured_root(Some(explicit.clone()), &fixture.executable()).unwrap(),
            Some(explicit)
        );
        assert!(
            configured_root(Some(PathBuf::from("relative")), &fixture.executable())
                .unwrap_err()
                .contains("must be absolute")
        );
    }

    #[test]
    fn invalid_marker_refuses_startup_instead_of_falling_back() {
        let fixture = Fixture::new();
        for invalid in [
            "not json",
            r#"{"schemaVersion":2,"dataDirectory":"data"}"#,
            r#"{"schemaVersion":1,"dataDirectory":""}"#,
            r#"{"schemaVersion":1,"dataDirectory":"/data"}"#,
            r#"{"schemaVersion":1,"dataDirectory":false}"#,
            r#"{"schemaVersion":1,"dataDirectory":"data","extra":true}"#,
            r#"{"schemaVersion":1,"dataDirectory":"../../../../../../../../../../.."}"#,
        ] {
            fixture.marker(invalid);
            assert!(
                configured_root(None, &fixture.executable()).is_err(),
                "{invalid}"
            );
        }
        fixture.marker(&" ".repeat(4097));
        assert!(configured_root(None, &fixture.executable())
            .unwrap_err()
            .contains("4096"));
    }

    #[cfg(windows)]
    #[test]
    fn portable_marker_rejects_windows_root_and_drive_relative_paths() {
        let fixture = Fixture::new();
        for path in [r"C:\data", r"C:data", r"\data", r"\\server\share\data"] {
            fixture
                .marker(&serde_json::json!({"schemaVersion":1,"dataDirectory":path}).to_string());
            assert!(
                configured_root(None, &fixture.executable()).is_err(),
                "{path}"
            );
        }
    }
}
