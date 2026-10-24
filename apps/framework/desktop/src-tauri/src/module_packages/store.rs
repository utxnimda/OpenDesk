use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, HashSet},
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};

pub const HOST_API: u32 = 2;
const MAX_ZIP: u64 = 128 * 1024 * 1024;
const MAX_FILE: u64 = 128 * 1024 * 1024;
const MAX_TOTAL: u64 = 512 * 1024 * 1024;
const MAX_FILES: usize = 4096;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Source {
    pub commit: Option<String>,
    pub dirty: bool,
    #[serde(default)]
    pub repository: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Manifest {
    pub schema_version: u32,
    pub id: String,
    pub label: String,
    pub version: String,
    pub host_api: u32,
    #[serde(default, skip_serializing_if = "is_false")]
    pub web_app: bool,
    pub target: String,
    pub frontend: String,
    pub worker: Option<String>,
    pub methods: Vec<String>,
    pub data_schema: u32,
    pub source: Source,
}

fn is_false(value: &bool) -> bool {
    !*value
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Installed {
    pub manifest: Manifest,
    pub digest: String,
    pub source: String,
    pub files: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Selection {
    pub version: String,
    pub digest: String,
    pub enabled: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Receipt {
    pub id: String,
    pub file_name: String,
    pub stored_path: String,
    pub status: String,
    pub error: Option<String>,
    pub module_id: Option<String>,
    pub version: Option<String>,
    pub digest: Option<String>,
    pub created_at: u64,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct State {
    pub schema_version: u32,
    pub versions: Vec<Installed>,
    pub selections: BTreeMap<String, Selection>,
    pub data_schemas: BTreeMap<String, u32>,
    pub imports: Vec<Receipt>,
}
impl Default for State {
    fn default() -> Self {
        Self {
            schema_version: 1,
            versions: Vec::new(),
            selections: BTreeMap::new(),
            data_schemas: BTreeMap::new(),
            imports: Vec::new(),
        }
    }
}

pub struct Store {
    pub root: PathBuf,
    pub data_root: PathBuf,
    pub state: State,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BundledCatalog {
    schema_version: u32,
    host_version: String,
    modules: Vec<BundledEntry>,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct BundledEntry {
    id: String,
    version: String,
    file: String,
    sha256: String,
    default_enabled: bool,
}

pub fn valid_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 80
        && value.bytes().next().is_some_and(|b| b.is_ascii_lowercase())
        && value
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'.')
        && !value.ends_with('.')
        && safe_relative(value).is_ok()
}
fn valid_version(value: &str) -> bool {
    let parts: Vec<_> = value.split('.').collect();
    parts.len() == 3
        && parts.iter().all(|part| {
            !part.is_empty()
                && part.len() <= 9
                && (part.len() == 1 || !part.starts_with('0'))
                && part.bytes().all(|b| b.is_ascii_digit())
        })
}
fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn local_absolute_file(path: &Path) -> bool {
    if !path.is_absolute() {
        return false;
    }
    #[cfg(windows)]
    {
        use std::path::{Component, Prefix};
        matches!(path.components().next(), Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)))
    }
    #[cfg(not(windows))]
    {
        true
    }
}

pub fn safe_relative(value: &str) -> Result<PathBuf, String> {
    if value.is_empty()
        || value.len() > 240
        || value.starts_with('/')
        || value.contains('\\')
        || value.contains(':')
    {
        return Err("PACKAGE_PATH_INVALID: expected a bounded relative slash path".into());
    }
    for part in value.split('/') {
        if part.is_empty()
            || part == "."
            || part == ".."
            || part.ends_with(['.', ' '])
            || part.chars().any(|c| c < ' ' || "<>\"|?*".contains(c))
        {
            return Err("PACKAGE_PATH_INVALID: unsafe path component".into());
        }
        let stem = part
            .split('.')
            .next()
            .unwrap_or_default()
            .to_ascii_uppercase();
        let numbered = stem
            .strip_prefix("COM")
            .or_else(|| stem.strip_prefix("LPT"));
        if matches!(
            stem.as_str(),
            "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$"
        ) || numbered.is_some_and(|n| {
            matches!(
                n,
                "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
            )
        }) {
            return Err("PACKAGE_PATH_INVALID: Windows reserved name".into());
        }
    }
    Ok(PathBuf::from(value))
}

impl Manifest {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != 1
            || !valid_id(&self.id)
            || !valid_version(&self.version)
            || self.label.trim().is_empty()
            || self.label.len() > 160
            || self.data_schema == 0
        {
            return Err(
                "PACKAGE_MANIFEST_INVALID: identity, label, schema or version is invalid".into(),
            );
        }
        if !matches!(self.host_api, 1 | 2) || self.target != "windows-x64" {
            return Err(format!(
                "PACKAGE_INCOMPATIBLE: package requires hostApi={} target={}; this host supports hostApi={} target=windows-x64",
                self.host_api, self.target, HOST_API
            ));
        }
        if self.web_app != (self.host_api == 2) || (self.web_app && self.worker.is_none()) {
            return Err("PACKAGE_WEB_APP_INVALID: webApp requires hostApi=2 and a worker; hostApi=1 retains the static module contract".into());
        }
        safe_relative(&self.frontend)?;
        if !self.frontend.starts_with("frontend/") || !self.frontend.ends_with(".html") {
            return Err(
                "PACKAGE_ENTRY_INVALID: frontend must be an HTML file under frontend/".into(),
            );
        }
        if let Some(worker) = &self.worker {
            safe_relative(worker)?;
            if !worker.starts_with("worker/") || !worker.ends_with(".exe") {
                return Err("PACKAGE_ENTRY_INVALID: worker must be an exe under worker/".into());
            }
        } else if !self.methods.is_empty() {
            return Err("PACKAGE_METHODS_INVALID: methods require a worker".into());
        }
        let mut names = HashSet::new();
        for method in &self.methods {
            if method.is_empty()
                || method.len() > 128
                || !method
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'-'))
                || method.starts_with("runtime.")
                || method.starts_with("module.")
                || !names.insert(method)
            {
                return Err(
                    "PACKAGE_METHODS_INVALID: duplicate, invalid or reserved method".into(),
                );
            }
        }
        if let Some(commit) = &self.source.commit {
            if !matches!(commit.len(), 40 | 64) || !commit.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err("PACKAGE_SOURCE_INVALID: commit must be a full Git hash".into());
            }
        }
        if let Some(repository) = &self.source.repository {
            let parsed = tauri::Url::parse(repository)
                .map_err(|_| "PACKAGE_SOURCE_INVALID: malformed repository URL")?;
            if repository.len() > 2048
                || parsed.scheme() != "https"
                || parsed.host_str().is_none()
                || !parsed.username().is_empty()
                || parsed.password().is_some()
                || repository.contains('\\')
                || repository
                    .chars()
                    .any(|c| c.is_control() || c.is_whitespace())
            {
                return Err(
                    "PACKAGE_SOURCE_INVALID: repository must be an HTTPS URL without credentials"
                        .into(),
                );
            }
        }
        Ok(())
    }
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut file = File::open(path).map_err(|e| package_io("hash.open", path, &e))?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let length = file
            .read(&mut buffer)
            .map_err(|e| package_io("hash.read", path, &e))?;
        if length == 0 {
            break;
        }
        hash.update(&buffer[..length]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn package_io(stage: &str, path: &Path, error: &std::io::Error) -> String {
    format!(
        "PACKAGE_IO_FAILED: stage={stage} path='{}' osError={:?}: {error}",
        path.display(),
        error.raw_os_error()
    )
}

fn publish_staging(store_root: &Path, staging: &Path, destination: &Path) -> Result<(), String> {
    // The only retried operation is this same-store directory rename. Never
    // change ACLs, overwrite a destination, re-extract, or retry arbitrary I/O.
    let root = store_root
        .canonicalize()
        .map_err(|e| package_io("publish.resolve_store", store_root, &e))?;
    let source = staging
        .canonicalize()
        .map_err(|e| package_io("publish.resolve_staging", staging, &e))?;
    let parent_path = destination.parent().ok_or("PACKAGE_PUBLISH_PATH_INVALID")?;
    let parent = parent_path
        .canonicalize()
        .map_err(|e| package_io("publish.resolve_parent", parent_path, &e))?;
    if source.parent() != Some(root.join("staging").as_path())
        || !parent.starts_with(root.join("store"))
        || !source.is_dir()
    {
        return Err(
            "PACKAGE_PUBLISH_PATH_INVALID: resolved paths must remain in this store".into(),
        );
    }
    let target = parent.join(
        destination
            .file_name()
            .ok_or("PACKAGE_PUBLISH_PATH_INVALID")?,
    );
    let started = std::time::Instant::now();
    let budget = std::time::Duration::from_secs(2);
    let mut attempts = 0u32;
    loop {
        match fs::symlink_metadata(&target) {
            Ok(_) => {
                return Err(format!(
                    "PACKAGE_STORE_CONFLICT: refusing to overwrite existing destination '{}'",
                    target.display()
                ))
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(package_io("publish.check_destination", &target, &error)),
        }
        attempts += 1;
        match fs::rename(&source, &target) {
            Ok(()) => return Ok(()),
            Err(error) => {
                let elapsed = started.elapsed();
                let transient = cfg!(windows) && matches!(error.raw_os_error(), Some(5 | 32 | 33));
                if !transient || elapsed >= budget || !source.is_dir() {
                    return Err(format!("PACKAGE_PUBLISH_FAILED: stage=publish.rename source='{}' destination='{}' attempts={attempts} elapsedMs={} osError={:?}: {error}", source.display(), target.display(), elapsed.as_millis(), error.raw_os_error()));
                }
                let delay = std::time::Duration::from_millis((u64::from(attempts) * 50).min(250));
                std::thread::sleep(delay.min(budget.saturating_sub(elapsed)));
            }
        }
    }
}

pub fn atomic_json<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    let parent = path.parent().ok_or("Invalid state path")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let temporary = parent.join(format!(".state-{}.tmp", uuid::Uuid::new_v4()));
    let result = (|| {
        let bytes = serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?;
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|e| e.to_string())?;
        file.write_all(&bytes).map_err(|e| e.to_string())?;
        file.sync_all().map_err(|e| e.to_string())?;
        drop(file);
        #[cfg(windows)]
        {
            use std::os::windows::ffi::OsStrExt;
            use windows_sys::Win32::Storage::FileSystem::{
                MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
            };
            let from: Vec<u16> = temporary.as_os_str().encode_wide().chain(Some(0)).collect();
            let to: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
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
        }
        #[cfg(not(windows))]
        fs::rename(&temporary, path).map_err(|e| e.to_string())?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary);
    }
    result
}

impl Store {
    pub fn new(data_root: PathBuf, bundled_root: &Path) -> Result<Self, String> {
        let root = data_root.join("module-packages");
        let state_path = root.join("registry.json");
        let state: State = if state_path.exists() {
            serde_json::from_slice(&fs::read(&state_path).map_err(|e| e.to_string())?).map_err(
                |e| format!("MODULE_CONFIG_INVALID: existing configuration preserved: {e}"),
            )?
        } else {
            State::default()
        };
        let mut store = Self {
            root,
            data_root,
            state,
        };
        store.validate_state()?;
        fs::create_dir_all(store.root.join("imports")).map_err(|e| e.to_string())?;
        if bundled_root.join("app-manifest.json").exists() {
            store.install_bundled(bundled_root)?;
        }
        Ok(store)
    }
    fn validate_state(&self) -> Result<(), String> {
        if self.state.schema_version != 1 {
            return Err("MODULE_CONFIG_UNSUPPORTED: configuration preserved".into());
        }
        let mut identities = HashSet::new();
        for version in &self.state.versions {
            version.manifest.validate()?;
            if !valid_digest(&version.digest)
                || !matches!(version.source.as_str(), "bundled" | "imported")
                || !identities.insert((&version.manifest.id, &version.manifest.version))
            {
                return Err(
                    "MODULE_CONFIG_INVALID: duplicate or invalid installed identity".into(),
                );
            }
            for (name, digest) in &version.files {
                safe_relative(name)?;
                if !valid_digest(digest) {
                    return Err("MODULE_CONFIG_INVALID: invalid content digest".into());
                }
            }
        }
        for (id, selected) in &self.state.selections {
            let installed = self.lookup(id, &selected.version, &selected.digest)?;
            if self.state.data_schemas.get(id) != Some(&installed.manifest.data_schema) {
                return Err(
                    "MODULE_CONFIG_INVALID: selected version and data schema differ".into(),
                );
            }
        }
        for (id, schema) in &self.state.data_schemas {
            if !valid_id(id) || *schema == 0 {
                return Err("MODULE_CONFIG_INVALID: invalid data namespace".into());
            }
        }
        Ok(())
    }
    pub fn commit(&mut self, next: State) -> Result<(), String> {
        atomic_json(&self.root.join("registry.json"), &next)?;
        self.state = next;
        Ok(())
    }
    pub fn lookup(&self, id: &str, version: &str, digest: &str) -> Result<Installed, String> {
        self.state
            .versions
            .iter()
            .find(|item| {
                item.manifest.id == id && item.manifest.version == version && item.digest == digest
            })
            .cloned()
            .ok_or("MODULE_VERSION_NOT_INSTALLED".into())
    }
    pub fn path(&self, installed: &Installed) -> PathBuf {
        self.root
            .join("store")
            .join(&installed.manifest.id)
            .join(&installed.manifest.version)
            .join(&installed.digest)
    }
    pub fn module_data(&self, id: &str) -> PathBuf {
        self.data_root.join("modules").join(id).join("data")
    }
    pub fn check_schema(&self, installed: &Installed) -> Result<(), String> {
        if self
            .state
            .data_schemas
            .get(&installed.manifest.id)
            .is_some_and(|schema| *schema != installed.manifest.data_schema)
        {
            return Err("MODULE_DATA_SCHEMA_INCOMPATIBLE: this release only supports identical schemas; existing version and data preserved".into());
        }
        Ok(())
    }
    pub fn verify_files(&self, installed: &Installed) -> Result<(), String> {
        let package_path = self.path(installed);
        let mut component = self.root.join("store");
        for part in [
            &installed.manifest.id,
            &installed.manifest.version,
            &installed.digest,
        ] {
            component.push(part);
            if is_link(
                &fs::symlink_metadata(&component)
                    .map_err(|e| format!("MODULE_FILES_MISSING: {e}"))?,
            ) {
                return Err("MODULE_INTEGRITY_FAILED: redirected package directory".into());
            }
        }
        let root = package_path
            .canonicalize()
            .map_err(|e| format!("MODULE_FILES_MISSING: {e}"))?;
        let mut actual = HashSet::new();
        collect_installed_files(&root, &root, &mut actual)?;
        if actual.len() != installed.files.len()
            || actual
                .iter()
                .any(|name| !installed.files.contains_key(name))
        {
            return Err("MODULE_INTEGRITY_FAILED: unexpected or missing installed files".into());
        }
        for (relative, digest) in &installed.files {
            let path = root.join(safe_relative(relative)?);
            if sha256_file(&path)? != *digest {
                return Err("MODULE_INTEGRITY_FAILED: installed files changed".into());
            }
        }
        Ok(())
    }
    pub fn import_package(
        &mut self,
        path: &Path,
        origin: &str,
        expected: Option<&str>,
    ) -> Result<Receipt, String> {
        if !local_absolute_file(path) || !fs::metadata(path).map_err(|e| e.to_string())?.is_file() {
            return Err("PACKAGE_SOURCE_INVALID: select a local package file".into());
        }
        let id = uuid::Uuid::new_v4().to_string();
        let receipt_root = self.root.join("imports").join(&id);
        fs::create_dir_all(&receipt_root)
            .map_err(|e| package_io("import.create_receipt_directory", &receipt_root, &e))?;
        let original = receipt_root.join("original.ndpkg");
        // Retain the selected original before compatibility or archive validation.
        fs::copy(path, &original).map_err(|e| {
            format!(
                "PACKAGE_ARCHIVE_FAILED: source='{}' {}",
                path.display(),
                package_io("import.retain_original", &original, &e)
            )
        })?;
        let mut receipt = Receipt {
            id,
            file_name: path
                .file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .into_owned(),
            stored_path: original.to_string_lossy().into_owned(),
            status: "rejected".into(),
            error: None,
            module_id: None,
            version: None,
            digest: None,
            created_at: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis() as u64,
        };
        let result = (|| {
            let digest = sha256_file(&original)?;
            receipt.digest = Some(digest.clone());
            if expected.is_some_and(|value| value != digest) {
                return Err("PACKAGE_DIGEST_MISMATCH".into());
            }
            if fs::metadata(&original)
                .map_err(|e| package_io("import.original_metadata", &original, &e))?
                .len()
                > MAX_ZIP
            {
                return Err("PACKAGE_TOO_LARGE: archive exceeds 128 MiB".into());
            }
            let (manifest, files) = inspect_archive(&original)?;
            receipt.module_id = Some(manifest.id.clone());
            receipt.version = Some(manifest.version.clone());
            manifest.validate()?;
            if let Some(existing) = self.state.versions.iter().find(|item| {
                item.manifest.id == manifest.id && item.manifest.version == manifest.version
            }) {
                if existing.digest != digest {
                    return Err(
                        "PACKAGE_VERSION_CONFLICT: this id/version already has different content"
                            .into(),
                    );
                }
                receipt.status = "duplicate".into();
                return Ok(None);
            }
            let installed = Installed {
                manifest,
                digest,
                source: origin.into(),
                files,
            };
            let destination = self.path(&installed);
            let staging = self.root.join("staging").join(&receipt.id);
            fs::create_dir_all(&staging)
                .map_err(|e| package_io("extract.create_staging", &staging, &e))?;
            extract_archive(&original, &staging)?;
            if destination.exists() {
                return Err("PACKAGE_STORE_CONFLICT: unindexed directory preserved".into());
            }
            let parent = destination.parent().ok_or("Invalid package store path")?;
            fs::create_dir_all(parent)
                .map_err(|e| package_io("publish.create_parent", parent, &e))?;
            publish_staging(&self.root, &staging, &destination)?;
            receipt.status = "installed".into();
            Ok(Some(installed))
        })();
        let mut next = self.state.clone();
        match result {
            Ok(Some(installed)) => next.versions.push(installed),
            Ok(None) => {}
            Err(error) => receipt.error = Some(error),
        }
        atomic_json(&receipt_root.join("receipt.json"), &receipt)?;
        next.imports.push(receipt.clone());
        self.commit(next)?;
        Ok(receipt)
    }
    fn install_bundled(&mut self, root: &Path) -> Result<(), String> {
        let catalog: BundledCatalog = serde_json::from_slice(
            &fs::read(root.join("app-manifest.json")).map_err(|e| e.to_string())?,
        )
        .map_err(|e| format!("BUNDLED_CATALOG_INVALID: {e}"))?;
        if catalog.schema_version != 1 || !valid_version(&catalog.host_version) {
            return Err("BUNDLED_CATALOG_INVALID".into());
        }
        let mut seen = HashSet::new();
        for entry in catalog.modules {
            if !valid_id(&entry.id)
                || !valid_version(&entry.version)
                || !valid_digest(&entry.sha256)
                || !seen.insert(entry.id.clone())
            {
                return Err("BUNDLED_CATALOG_INVALID: invalid or duplicate entry".into());
            }
            let path = root.join(safe_relative(&entry.file)?);
            if self
                .lookup(&entry.id, &entry.version, &entry.sha256)
                .is_err()
            {
                let receipt = self.import_package(&path, "bundled", Some(&entry.sha256))?;
                if receipt.status == "rejected" {
                    return Err(format!(
                        "BUNDLED_PACKAGE_REJECTED: {}",
                        receipt.error.unwrap_or_default()
                    ));
                }
                if receipt.module_id.as_deref() != Some(&entry.id)
                    || receipt.version.as_deref() != Some(&entry.version)
                {
                    return Err("BUNDLED_CATALOG_IDENTITY_MISMATCH".into());
                }
            }
            // A newer host never overwrites an existing pin or disabled selection.
            if !self.state.selections.contains_key(&entry.id) {
                let installed = self.lookup(&entry.id, &entry.version, &entry.sha256)?;
                self.check_schema(&installed)?;
                let mut next = self.state.clone();
                next.data_schemas
                    .insert(entry.id.clone(), installed.manifest.data_schema);
                next.selections.insert(
                    entry.id,
                    Selection {
                        version: entry.version,
                        digest: entry.sha256,
                        enabled: entry.default_enabled,
                    },
                );
                self.commit(next)?;
            }
        }
        Ok(())
    }
}

fn is_link(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}
fn collect_installed_files(
    root: &Path,
    directory: &Path,
    files: &mut HashSet<String>,
) -> Result<(), String> {
    for item in fs::read_dir(directory).map_err(|e| e.to_string())? {
        let path = item.map_err(|e| e.to_string())?.path();
        let metadata = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
        if is_link(&metadata) {
            return Err("MODULE_INTEGRITY_FAILED: redirected installed entry".into());
        }
        if metadata.is_dir() {
            collect_installed_files(root, &path, files)?;
        } else if metadata.is_file() {
            let relative = path
                .strip_prefix(root)
                .map_err(|e| e.to_string())?
                .to_string_lossy()
                .replace('\\', "/");
            if files.len() >= MAX_FILES {
                return Err("MODULE_INTEGRITY_FAILED: too many installed files".into());
            }
            files.insert(relative);
        } else {
            return Err("MODULE_INTEGRITY_FAILED: unsupported installed entry".into());
        }
    }
    Ok(())
}

fn inspect_archive(path: &Path) -> Result<(Manifest, BTreeMap<String, String>), String> {
    let mut archive = zip::ZipArchive::new(File::open(path).map_err(|e| e.to_string())?)
        .map_err(|e| format!("PACKAGE_ZIP_INVALID: {e}"))?;
    if archive.is_empty() || archive.len() > MAX_FILES {
        return Err("PACKAGE_FILE_LIMIT: expected 1 to 4096 entries".into());
    }
    let mut seen = HashSet::new();
    let mut paths: BTreeMap<String, ()> = BTreeMap::new();
    let mut files = BTreeMap::new();
    let mut total = 0u64;
    let mut manifest = None;
    for index in 0..archive.len() {
        let mut file = archive.by_index(index).map_err(|e| e.to_string())?;
        let name = file.name().to_owned();
        safe_relative(&name)?;
        // Directory records, symlinks and device entries are unnecessary: only regular files are accepted.
        if file.is_dir()
            || file
                .unix_mode()
                .is_some_and(|mode| !matches!(mode & 0o170000, 0 | 0o100000))
        {
            return Err("PACKAGE_FILE_TYPE_INVALID: only regular files are allowed".into());
        }
        let key = name.to_lowercase();
        if !seen.insert(key.clone()) {
            return Err("PACKAGE_PATH_COLLISION: duplicate case-insensitive file".into());
        }
        for previous in paths.keys() {
            let previous: &String = previous;
            if key.starts_with(&(previous.clone() + "/"))
                || previous.starts_with(&(key.clone() + "/"))
            {
                return Err("PACKAGE_PATH_COLLISION: file/directory overlap".into());
            }
        }
        paths.insert(key, ());
        if file.size() > MAX_FILE {
            return Err("PACKAGE_FILE_TOO_LARGE: single file exceeds 128 MiB".into());
        }
        total = total
            .checked_add(file.size())
            .ok_or("PACKAGE_SIZE_OVERFLOW")?;
        if total > MAX_TOTAL {
            return Err("PACKAGE_EXPANDED_TOO_LARGE: total exceeds 512 MiB".into());
        }
        let mut hasher = Sha256::new();
        let mut data = Vec::new();
        let mut read = 0u64;
        let mut buffer = [0u8; 65536];
        loop {
            let count = file.read(&mut buffer).map_err(|e| e.to_string())?;
            if count == 0 {
                break;
            }
            read += count as u64;
            if read > MAX_FILE || read > file.size() {
                return Err("PACKAGE_ENTRY_SIZE_MISMATCH".into());
            }
            hasher.update(&buffer[..count]);
            if name == "manifest.json" {
                if read > 65536 {
                    return Err("PACKAGE_MANIFEST_TOO_LARGE".into());
                }
                data.extend_from_slice(&buffer[..count]);
            }
        }
        if read != file.size() {
            return Err("PACKAGE_ENTRY_SIZE_MISMATCH".into());
        }
        if name == "manifest.json" {
            manifest = Some(
                serde_json::from_slice::<Manifest>(&data)
                    .map_err(|e| format!("PACKAGE_MANIFEST_INVALID: {e}"))?,
            );
        }
        files.insert(name, format!("{:x}", hasher.finalize()));
    }
    let manifest = manifest.ok_or("PACKAGE_MANIFEST_MISSING")?;
    if !files.contains_key(&manifest.frontend)
        || manifest
            .worker
            .as_ref()
            .is_some_and(|entry| !files.contains_key(entry))
    {
        return Err("PACKAGE_ENTRY_MISSING".into());
    }
    Ok((manifest, files))
}

fn extract_archive(path: &Path, staging: &Path) -> Result<(), String> {
    let mut archive = zip::ZipArchive::new(
        File::open(path).map_err(|e| package_io("extract.open_archive", path, &e))?,
    )
    .map_err(|e| {
        format!(
            "PACKAGE_ZIP_INVALID: stage=extract.read_directory path='{}': {e}",
            path.display()
        )
    })?;
    for index in 0..archive.len() {
        let mut input = archive.by_index(index).map_err(|e| {
            format!(
                "PACKAGE_ZIP_INVALID: stage=extract.open_entry index={index} archive='{}': {e}",
                path.display()
            )
        })?;
        let output = staging.join(safe_relative(input.name())?);
        let parent = output.parent().ok_or("Invalid extraction path")?;
        fs::create_dir_all(parent).map_err(|e| package_io("extract.create_parent", parent, &e))?;
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&output)
            .map_err(|e| package_io("extract.create_file", &output, &e))?;
        let expected = input.size();
        let copied = std::io::copy(&mut input.by_ref().take(MAX_FILE + 1), &mut file)
            .map_err(|e| package_io("extract.write_file", &output, &e))?;
        if copied != expected || copied > MAX_FILE {
            return Err("PACKAGE_ENTRY_SIZE_MISMATCH".into());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn web_apps_require_explicit_api_two_and_worker_without_changing_legacy_manifests() {
        let base = serde_json::json!({"schemaVersion":1,"id":"demo","label":"Demo","version":"1.0.0","hostApi":1,"target":"windows-x64","frontend":"frontend/index.html","worker":null,"methods":[],"dataSchema":1,"source":{"commit":null,"dirty":true}});
        let legacy: Manifest = serde_json::from_value(base.clone()).unwrap();
        legacy.validate().unwrap();
        assert!(!legacy.web_app);
        assert!(serde_json::to_value(legacy)
            .unwrap()
            .get("webApp")
            .is_none());
        let mut web = base.clone();
        web["webApp"] = serde_json::json!(true);
        assert!(serde_json::from_value::<Manifest>(web.clone())
            .unwrap()
            .validate()
            .is_err());
        web["hostApi"] = serde_json::json!(2);
        assert!(serde_json::from_value::<Manifest>(web.clone())
            .unwrap()
            .validate()
            .is_err());
        web["worker"] = serde_json::json!("worker/module-worker.exe");
        serde_json::from_value::<Manifest>(web.clone())
            .unwrap()
            .validate()
            .unwrap();
        web["webApp"] = serde_json::json!(false);
        assert!(serde_json::from_value::<Manifest>(web.clone())
            .unwrap()
            .validate()
            .is_err());
        web["webApp"] = serde_json::json!(true);
        web["methods"] = serde_json::json!(["module.webapp"]);
        assert!(serde_json::from_value::<Manifest>(web)
            .unwrap()
            .validate()
            .is_err());
    }
    fn temporary() -> PathBuf {
        let root =
            std::env::temp_dir().join(format!("opendesk-module-test-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&root).unwrap();
        root
    }
    fn package(root: &Path, filename: &str, version: &str, schema: u32, content: &str) -> PathBuf {
        let path = root.join(filename);
        let mut zip = zip::ZipWriter::new(File::create(&path).unwrap());
        let manifest = serde_json::json!({"schemaVersion":1,"id":"demo","label":"Demo","version":version,"hostApi":1,"target":"windows-x64","frontend":"frontend/index.html","worker":null,"methods":[],"dataSchema":schema,"source":{"commit":null,"dirty":true}});
        let options = zip::write::SimpleFileOptions::default();
        zip.start_file("manifest.json", options).unwrap();
        zip.write_all(serde_json::to_string(&manifest).unwrap().as_bytes())
            .unwrap();
        zip.start_file("frontend/index.html", options).unwrap();
        zip.write_all(content.as_bytes()).unwrap();
        zip.finish().unwrap();
        path
    }
    #[test]
    fn invalid_import_retains_original_and_conflicts_do_not_change_selection() {
        let root = temporary();
        let mut store = Store::new(root.join("data"), &root.join("none")).unwrap();
        let bad = root.join("bad.ndpkg");
        fs::write(&bad, b"not a zip").unwrap();
        let rejected = store.import_package(&bad, "imported", None).unwrap();
        assert_eq!(rejected.status, "rejected");
        assert_eq!(fs::read(rejected.stored_path).unwrap(), b"not a zip");
        let first = package(&root, "first.ndpkg", "1.0.3", 1, "first");
        let imported = store.import_package(&first, "imported", None).unwrap();
        assert_eq!(imported.status, "installed");
        assert_eq!(
            store
                .import_package(&first, "imported", None)
                .unwrap()
                .status,
            "duplicate"
        );
        let conflicting = package(&root, "conflict.ndpkg", "1.0.3", 1, "changed");
        let rejected = store
            .import_package(&conflicting, "imported", None)
            .unwrap();
        assert!(rejected.error.unwrap().contains("VERSION_CONFLICT"));
        assert_eq!(store.state.versions.len(), 1);
        let mut state = store.state.clone();
        state.selections.insert(
            "demo".into(),
            Selection {
                version: "1.0.3".into(),
                digest: imported.digest.unwrap(),
                enabled: false,
            },
        );
        state.data_schemas.insert("demo".into(), 1);
        store.commit(state).unwrap();
        let reopened = Store::new(root.join("data"), &root.join("none")).unwrap();
        assert!(!reopened.state.selections["demo"].enabled);
        let incompatible = package(&root, "schema.ndpkg", "1.0.4", 2, "new");
        let imported = store
            .import_package(&incompatible, "imported", None)
            .unwrap();
        let item = store
            .lookup("demo", "1.0.4", &imported.digest.unwrap())
            .unwrap();
        assert!(store.check_schema(&item).is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn unsafe_paths_and_bad_config_are_rejected() {
        for path in [
            "../x", "/x", "a\\b", "a:b", "a/../b", "a//b", "CON.txt", "a/LPT1", "tail.", "tail ",
            "a?b",
        ] {
            assert!(safe_relative(path).is_err(), "{path}");
        }
        let root = temporary();
        fs::create_dir_all(root.join("data/module-packages")).unwrap();
        let path = root.join("data/module-packages/registry.json");
        fs::write(&path, b"bad config").unwrap();
        assert!(Store::new(root.join("data"), &root.join("none")).is_err());
        assert_eq!(fs::read(path).unwrap(), b"bad config");
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn hostile_archives_are_rejected_before_extraction() {
        let root = temporary();
        let mut store = Store::new(root.join("data"), &root.join("none")).unwrap();
        for (label, names) in [
            ("traversal", vec!["../escape"]),
            ("collision", vec!["frontend/a.js", "frontend/A.js"]),
            ("reserved", vec!["frontend/CON.txt"]),
            ("overlap", vec!["frontend", "frontend/a.js"]),
        ] {
            let path = root.join(format!("{label}.ndpkg"));
            let mut archive = zip::ZipWriter::new(File::create(&path).unwrap());
            for name in names {
                archive
                    .start_file(name, zip::write::SimpleFileOptions::default())
                    .unwrap();
                archive.write_all(b"payload").unwrap();
            }
            archive.finish().unwrap();
            let receipt = store.import_package(&path, "imported", None).unwrap();
            assert_eq!(receipt.status, "rejected", "{label}");
            assert!(Path::new(&receipt.stored_path).is_file());
        }
        let oversized = package(&root, "oversized-entry.ndpkg", "1.0.3", 1, "hello");
        let mut bytes = fs::read(&oversized).unwrap();
        let position = bytes
            .windows(4)
            .position(|value| value == b"PK\x01\x02")
            .unwrap();
        bytes[position + 24..position + 28].copy_from_slice(&((MAX_FILE + 1) as u32).to_le_bytes());
        fs::write(&oversized, bytes).unwrap();
        let rejected = store.import_package(&oversized, "imported", None).unwrap();
        assert_eq!(rejected.status, "rejected");
        assert!(rejected.error.unwrap().contains("TOO_LARGE"));
        assert!(store.state.versions.is_empty());
        assert!(!root.join("escape").exists());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn bundled_updates_preserve_user_pin_and_disabled_state() {
        let root = temporary();
        let bundled = root.join("bundled");
        fs::create_dir_all(&bundled).unwrap();
        let initial = package(&bundled, "demo.ndpkg", "1.0.3", 1, "first");
        let write_catalog = |version: &str, path: &Path| {
            atomic_json(&bundled.join("app-manifest.json"), &serde_json::json!({"schemaVersion":1,"hostVersion":"0.3.0","modules":[{"id":"demo","version":version,"file":"demo.ndpkg","sha256":sha256_file(path).unwrap(),"defaultEnabled":true}]})).unwrap();
        };
        write_catalog("1.0.3", &initial);
        let mut store = Store::new(root.join("data"), &bundled).unwrap();
        assert!(store.state.selections["demo"].enabled);
        let custom = package(&root, "custom.ndpkg", "1.0.4", 1, "custom");
        let receipt = store.import_package(&custom, "imported", None).unwrap();
        let mut next = store.state.clone();
        next.selections.insert(
            "demo".into(),
            Selection {
                version: "1.0.4".into(),
                digest: receipt.digest.unwrap(),
                enabled: false,
            },
        );
        store.commit(next).unwrap();
        let updated = package(&bundled, "demo.ndpkg", "1.0.5", 1, "updated");
        write_catalog("1.0.5", &updated);
        let reopened = Store::new(root.join("data"), &bundled).unwrap();
        assert_eq!(reopened.state.selections["demo"].version, "1.0.4");
        assert!(!reopened.state.selections["demo"].enabled);
        assert_eq!(reopened.state.versions.len(), 3);
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn publishing_never_overwrites_existing_destination() {
        let root = temporary();
        let staging = root.join("staging/owned-import");
        let destination = root.join("store/demo/1.0.0/existing-digest");
        fs::create_dir_all(&staging).unwrap();
        fs::create_dir_all(&destination).unwrap();
        fs::write(staging.join("candidate.txt"), b"candidate").unwrap();
        fs::write(destination.join("existing.txt"), b"preserve").unwrap();
        let error = publish_staging(&root, &staging, &destination).unwrap_err();
        assert!(error.contains("PACKAGE_STORE_CONFLICT"));
        assert_eq!(
            fs::read(staging.join("candidate.txt")).unwrap(),
            b"candidate"
        );
        assert_eq!(
            fs::read(destination.join("existing.txt")).unwrap(),
            b"preserve"
        );
        let available = root.join("store/demo/1.0.0/new-digest");
        publish_staging(&root, &staging, &available).unwrap();
        assert!(!staging.exists());
        assert_eq!(
            fs::read(available.join("candidate.txt")).unwrap(),
            b"candidate"
        );
        fs::remove_dir_all(root).unwrap();
    }
}
