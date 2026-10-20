//! Independently versioned local module packages. No remote downloads or generic execution API.
mod runtime;
mod static_server;
mod store;

use crate::diagnostics::Diagnostics;
use runtime::Session;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashMap},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
};
use store::{Installed, Selection, Store};

#[derive(Default)]
struct Slot {
    session: Option<Session>,
    error: Option<String>,
    last_error: Option<String>,
    awaiting_ui: bool,
    ui_fallback: Option<Selection>,
}
#[derive(Default)]
struct Cell {
    slot: tokio::sync::Mutex<Slot>,
    transition: AtomicBool,
}
struct Transition(Arc<Cell>);
impl Transition {
    fn begin(cell: &Arc<Cell>) -> Result<Self, String> {
        if cell.transition.swap(true, Ordering::SeqCst) {
            return Err("MODULE_BUSY: another lifecycle operation is in progress".into());
        }
        Ok(Self(cell.clone()))
    }
}
impl Drop for Transition {
    fn drop(&mut self) {
        self.0.transition.store(false, Ordering::SeqCst);
    }
}

pub struct ModuleManager {
    store: Arc<Mutex<Store>>,
    cells: Mutex<HashMap<String, Arc<Cell>>>,
    tokens: Mutex<HashMap<String, (String, String)>>,
    diagnostics: Arc<Diagnostics>,
    stopping: AtomicBool,
}

impl ModuleManager {
    pub fn new(
        data_root: PathBuf,
        bundled_root: PathBuf,
        diagnostics: Arc<Diagnostics>,
    ) -> Result<Self, String> {
        let store = Store::new(data_root, &bundled_root)?;
        Ok(Self {
            store: Arc::new(Mutex::new(store)),
            cells: Mutex::new(HashMap::new()),
            tokens: Mutex::new(HashMap::new()),
            diagnostics,
            stopping: AtomicBool::new(false),
        })
    }
    fn cell(&self, id: &str) -> Result<Arc<Cell>, String> {
        if !store::valid_id(id) {
            return Err("MODULE_ID_INVALID".into());
        }
        let mut cells = self.cells.lock().map_err(|_| "MODULE_LOCK_UNAVAILABLE")?;
        Ok(cells.entry(id.into()).or_default().clone())
    }
    fn ensure_running(&self) -> Result<(), String> {
        if self.stopping.load(Ordering::SeqCst) {
            Err("MODULE_HOST_STOPPING".into())
        } else {
            Ok(())
        }
    }
    fn insert_token(&self, session: &Session) -> Result<(), String> {
        self.tokens
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .insert(
                session.token.clone(),
                (
                    session.installed.manifest.id.clone(),
                    session.generation.clone(),
                ),
            );
        Ok(())
    }
    fn revoke(&self, session: &Session) {
        if let Ok(mut tokens) = self.tokens.lock() {
            tokens.remove(&session.token);
        }
    }
    fn prepare(
        &self,
        id: &str,
        version: &str,
        digest: &str,
    ) -> Result<(Installed, PathBuf, PathBuf, Option<Selection>), String> {
        let store = self.store.lock().map_err(|_| "MODULE_STORE_UNAVAILABLE")?;
        let installed = store.lookup(id, version, digest)?;
        store.check_schema(&installed)?;
        store.verify_files(&installed)?;
        Ok((
            installed.clone(),
            store.path(&installed),
            store.module_data(id),
            store.state.selections.get(id).cloned(),
        ))
    }
    pub async fn catalog(&self) -> Result<Value, String> {
        let state = self
            .store
            .lock()
            .map_err(|_| "MODULE_STORE_UNAVAILABLE")?
            .state
            .clone();
        let cells = self
            .cells
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .clone();
        let mut grouped: BTreeMap<String, Vec<Installed>> = BTreeMap::new();
        for version in state.versions {
            grouped
                .entry(version.manifest.id.clone())
                .or_default()
                .push(version);
        }
        let mut modules = Vec::new();
        for (id, versions) in grouped {
            let selection = state.selections.get(&id);
            let enabled = selection.is_some_and(|selected| selected.enabled);
            let label = selection
                .and_then(|selected| versions.iter().find(|item| item.digest == selected.digest))
                .unwrap_or(&versions[0])
                .manifest
                .label
                .clone();
            let mut runtime_state = if selection.is_none() {
                "unselected"
            } else if enabled {
                "stopped"
            } else {
                "disabled"
            };
            let mut error = None;
            let mut last_error = None;
            if let Some(cell) = cells.get(&id) {
                if cell.transition.load(Ordering::SeqCst) {
                    runtime_state = "switching";
                } else if let Ok(mut slot) = cell.slot.try_lock() {
                    last_error = slot.last_error.clone();
                    if let Some(session) = slot.session.as_mut() {
                        session.check_health();
                    }
                    error = slot.error.clone().or_else(|| {
                        slot.session
                            .as_ref()
                            .and_then(|session| session.error.clone())
                    });
                    if error.is_some() {
                        runtime_state = "failed";
                    } else if slot.session.is_some() {
                        runtime_state = if slot.awaiting_ui {
                            "loading"
                        } else {
                            "running"
                        };
                    }
                } else if enabled {
                    runtime_state = "busy";
                }
            }
            let version_entries = versions.iter().map(|item| {
                let schema_error = state.data_schemas.get(&id)
                    .filter(|schema| **schema != item.manifest.data_schema)
                    .map(|schema| format!("MODULE_DATA_SCHEMA_INCOMPATIBLE: package requires dataSchema={}; existing module data uses dataSchema={schema}. This host requires identical schemas and does not migrate data.", item.manifest.data_schema));
                json!({"version":item.manifest.version,"digest":item.digest,"source":item.source,"sourceCommit":item.manifest.source.commit,"sourceDirty":item.manifest.source.dirty,"sourceRepository":item.manifest.source.repository,"compatible":schema_error.is_none(),"error":schema_error})
            }).collect::<Vec<_>>();
            modules.push(json!({"id":id,"label":label,"enabled":enabled,"selectedVersion":selection.map(|s| &s.version),"selectedDigest":selection.map(|s| &s.digest),"state":runtime_state,"error":error,"lastError":last_error,"versions":version_entries}));
        }
        Ok(json!({"hostApi":store::HOST_API,"modules":modules,"imports":state.imports}))
    }
    pub async fn import_package(&self, path: &Path) -> Result<Value, String> {
        self.ensure_running()?;
        let path = path.to_owned();
        let store = self.store.clone();
        let receipt = tauri::async_runtime::spawn_blocking(move || {
            store
                .lock()
                .map_err(|_| "MODULE_STORE_UNAVAILABLE".to_owned())?
                .import_package(&path, "imported", None)
        })
        .await
        .map_err(|e| e.to_string())??;
        self.diagnostics.record(
            if receipt.status == "rejected" {
                "warn"
            } else {
                "info"
            },
            "module.import",
            &format!("receiptId={} status={}", receipt.id, receipt.status),
        );
        self.catalog().await
    }
    pub async fn open(&self, id: &str) -> Result<Value, String> {
        self.ensure_running()?;
        let cell = self.cell(id)?;
        if cell.transition.load(Ordering::SeqCst) {
            return Err("MODULE_SWITCHING".into());
        }
        let mut slot = cell.slot.lock().await;
        if cell.transition.load(Ordering::SeqCst) {
            return Err("MODULE_SWITCHING".into());
        }
        let selection = self
            .store
            .lock()
            .map_err(|_| "MODULE_STORE_UNAVAILABLE")?
            .state
            .selections
            .get(id)
            .cloned()
            .ok_or("MODULE_NOT_SELECTED")?;
        if !selection.enabled {
            return Err("MODULE_DISABLED".into());
        }
        if let Some(session) = slot.session.as_mut() {
            session.restore_worker(self.diagnostics.clone()).await?;
            let description = session.describe();
            slot.error = None;
            return Ok(description);
        }
        let (installed, root, data, _) = self.prepare(id, &selection.version, &selection.digest)?;
        match Session::start(installed, root, data, self.diagnostics.clone()).await {
            Ok(session) => {
                let description = session.describe();
                self.insert_token(&session)?;
                slot.session = Some(session);
                slot.error = None;
                slot.awaiting_ui = true;
                Ok(description)
            }
            Err(error) => {
                slot.error = Some(error.clone());
                Err(error)
            }
        }
    }
    pub async fn select(&self, id: &str, version: &str, digest: &str) -> Result<Value, String> {
        self.ensure_running()?;
        let cell = self.cell(id)?;
        let transition = Transition::begin(&cell)?;
        let mut slot = cell.slot.lock().await;
        let (installed, root, data, previous) = self.prepare(id, version, digest)?;
        if previous
            .as_ref()
            .is_some_and(|old| old.version == version && old.digest == digest)
        {
            drop(slot);
            drop(transition);
            return self.catalog().await;
        }
        // A package lock drains its admitted short request; other packages keep running.
        if let Some(old) = slot.session.as_mut() {
            if let Err(error) = old.stop_worker().await {
                let _ = old.restore_worker(self.diagnostics.clone()).await;
                return Err(error);
            }
        }
        let mut candidate =
            match Session::start(installed.clone(), root, data, self.diagnostics.clone()).await {
                Ok(candidate) => candidate,
                Err(error) => {
                    if let Some(old) = slot.session.as_mut() {
                        if let Err(restore) = old.restore_worker(self.diagnostics.clone()).await {
                            slot.error =
                                Some(format!("{error}; old worker restoration failed: {restore}"));
                        }
                    }
                    return Err(error);
                }
            };
        let enabled = previous.as_ref().is_some_and(|selection| selection.enabled);
        let keep_open = enabled && slot.session.is_some();
        if !keep_open {
            if let Err(error) = candidate.stop_worker().await {
                if let Some(old) = slot.session.as_mut() {
                    let _ = old.restore_worker(self.diagnostics.clone()).await;
                }
                return Err(error);
            }
        }
        let committed = (|| {
            let mut store = self.store.lock().map_err(|_| "MODULE_STORE_UNAVAILABLE")?;
            store.check_schema(&installed)?;
            let mut next = store.state.clone();
            next.data_schemas
                .insert(id.into(), installed.manifest.data_schema);
            next.selections.insert(
                id.into(),
                Selection {
                    version: version.into(),
                    digest: digest.into(),
                    enabled,
                },
            );
            store.commit(next)
        })();
        if let Err(error) = committed {
            let _ = candidate.stop_worker().await;
            if let Some(old) = slot.session.as_mut() {
                let _ = old.restore_worker(self.diagnostics.clone()).await;
            }
            return Err(error);
        }
        if let Some(old) = slot.session.take() {
            self.revoke(&old);
        }
        if keep_open {
            self.insert_token(&candidate)?;
            slot.session = Some(candidate);
        }
        slot.error = None;
        slot.awaiting_ui = true;
        // Several quick selections may happen before any new iframe is ready.
        // Preserve the last fallback instead of replacing it with an unconfirmed
        // intermediate version. Never roll a version back to itself.
        slot.ui_fallback = slot
            .ui_fallback
            .clone()
            .or(previous)
            .filter(|fallback| fallback.version != version || fallback.digest != digest);
        self.diagnostics.record(
            "info",
            "module.selected",
            &format!("moduleId={id} version={version} digest={digest}"),
        );
        drop(slot);
        drop(transition);
        self.catalog().await
    }
    pub async fn set_enabled(&self, id: &str, enabled: bool) -> Result<Value, String> {
        self.ensure_running()?;
        let cell = self.cell(id)?;
        let transition = Transition::begin(&cell)?;
        let mut slot = cell.slot.lock().await;
        let previous = self
            .store
            .lock()
            .map_err(|_| "MODULE_STORE_UNAVAILABLE")?
            .state
            .selections
            .get(id)
            .cloned()
            .ok_or("MODULE_NOT_SELECTED")?;
        if previous.enabled != enabled {
            if !enabled {
                if let Some(session) = slot.session.as_mut() {
                    if let Err(error) = session.stop_worker().await {
                        let _ = session.restore_worker(self.diagnostics.clone()).await;
                        return Err(error);
                    }
                }
            }
            let committed = (|| {
                let mut store = self.store.lock().map_err(|_| "MODULE_STORE_UNAVAILABLE")?;
                let mut next = store.state.clone();
                next.selections
                    .get_mut(id)
                    .ok_or("MODULE_NOT_SELECTED")?
                    .enabled = enabled;
                store.commit(next)
            })();
            if let Err(error) = committed {
                if let Some(session) = slot.session.as_mut() {
                    let _ = session.restore_worker(self.diagnostics.clone()).await;
                }
                return Err(error);
            }
            if !enabled {
                if let Some(session) = slot.session.take() {
                    self.revoke(&session);
                }
                slot.awaiting_ui = false;
            }
            slot.error = None;
        }
        drop(slot);
        drop(transition);
        self.catalog().await
    }
    pub async fn call(&self, token: &str, method: &str, params: Value) -> Result<Value, String> {
        self.ensure_running()?;
        let (id, generation) = self
            .tokens
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .get(token)
            .cloned()
            .ok_or("MODULE_SESSION_STALE")?;
        let cell = self.cell(&id)?;
        if cell.transition.load(Ordering::SeqCst) {
            return Err("MODULE_SWITCHING".into());
        }
        let mut slot = cell.slot.lock().await;
        if cell.transition.load(Ordering::SeqCst) {
            return Err("MODULE_SWITCHING".into());
        }
        let session = slot.session.as_mut().ok_or("MODULE_SESSION_STALE")?;
        if session.token != token || session.generation != generation {
            return Err("MODULE_SESSION_STALE".into());
        }
        session.call(method, params).await
    }
    pub async fn validate_session(
        &self,
        token: &str,
        id: &str,
        version: &str,
        digest: &str,
    ) -> Result<(), String> {
        self.ensure_running()?;
        let (owner, generation) = self
            .tokens
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .get(token)
            .cloned()
            .ok_or("MODULE_SESSION_STALE")?;
        if owner != id {
            return Err("MODULE_SESSION_IDENTITY_MISMATCH".into());
        }
        let cell = self.cell(id)?;
        if cell.transition.load(Ordering::SeqCst) {
            return Err("MODULE_SWITCHING".into());
        }
        let mut slot = cell.slot.lock().await;
        if cell.transition.load(Ordering::SeqCst) {
            return Err("MODULE_SWITCHING".into());
        }
        let session = slot.session.as_ref().ok_or("MODULE_SESSION_STALE")?;
        if session.token != token
            || session.generation != generation
            || session.installed.manifest.version != version
            || session.installed.digest != digest
        {
            return Err("MODULE_SESSION_IDENTITY_MISMATCH".into());
        }
        // Only the matching iframe's successful identity handshake commits UI readiness.
        slot.awaiting_ui = false;
        slot.ui_fallback = None;
        slot.error = None;
        Ok(())
    }
    pub async fn report_load_failed(&self, token: &str, reason: &str) -> Result<Value, String> {
        self.ensure_running()?;
        let identity = self
            .tokens
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .get(token)
            .cloned();
        let Some((id, generation)) = identity else {
            return self.catalog().await;
        };
        let cell = self.cell(&id)?;
        // A late failure from a previous lifecycle must not interfere with a new switch.
        let transition = match Transition::begin(&cell) {
            Ok(transition) => transition,
            Err(_) => return self.catalog().await,
        };
        let mut slot = cell.slot.lock().await;
        if !slot.awaiting_ui
            || !slot
                .session
                .as_ref()
                .is_some_and(|session| session.token == token && session.generation == generation)
        {
            drop(slot);
            drop(transition);
            return self.catalog().await;
        }
        let reason: String = reason
            .chars()
            .filter(|character| !character.is_control())
            .take(512)
            .collect();
        let reason = if reason.trim().is_empty() {
            "Module UI failed before readiness confirmation".to_owned()
        } else {
            reason
        };
        slot.last_error = Some(reason.clone());
        self.diagnostics.record(
            "error",
            "module.ui_load_failed",
            &format!("moduleId={id} generation={generation} reason={reason}"),
        );
        let previous = slot.ui_fallback.clone();
        let mut candidate = slot.session.take().ok_or("MODULE_SESSION_STALE")?;
        if let Err(error) = candidate.stop_worker().await {
            slot.error = Some(format!("{reason}; candidate stop failed: {error}"));
            slot.session = Some(candidate);
            return Err(error);
        }
        if let Some(previous) = previous {
            let previous_package = self.prepare(&id, &previous.version, &previous.digest);
            let (installed, root, data, _) = match previous_package {
                Ok(value) => value,
                Err(error) => {
                    let _ = candidate.restore_worker(self.diagnostics.clone()).await;
                    slot.error = Some(format!("{reason}; rollback preparation failed: {error}"));
                    slot.session = Some(candidate);
                    return Err(error);
                }
            };
            let mut restored = if previous.enabled {
                match Session::start(installed, root, data, self.diagnostics.clone()).await {
                    Ok(session) => Some(session),
                    Err(error) => {
                        let _ = candidate.restore_worker(self.diagnostics.clone()).await;
                        slot.error = Some(format!(
                            "{reason}; previous worker restoration failed: {error}"
                        ));
                        slot.session = Some(candidate);
                        return Err(error);
                    }
                }
            } else {
                None
            };
            let committed = (|| {
                let mut store = self.store.lock().map_err(|_| "MODULE_STORE_UNAVAILABLE")?;
                let mut next = store.state.clone();
                next.selections.insert(id.clone(), previous.clone());
                store.commit(next)
            })();
            if let Err(error) = committed {
                if let Some(session) = restored.as_mut() {
                    let _ = session.stop_worker().await;
                }
                let _ = candidate.restore_worker(self.diagnostics.clone()).await;
                slot.error = Some(format!("{reason}; rollback persistence failed: {error}"));
                slot.session = Some(candidate);
                return Err(error);
            }
            // Persist the rollback first. Only now revoke the failed generation and
            // publish the independently prepared previous version's new session.
            self.revoke(&candidate);
            if let Some(session) = restored.as_ref() {
                self.insert_token(session)?;
            }
            slot.session = restored;
            slot.error = None;
            slot.awaiting_ui = slot.session.is_some();
            slot.ui_fallback = None;
            self.diagnostics.record(
                "warn",
                "module.ui_rollback",
                &format!(
                    "moduleId={id} restoredVersion={} failedGeneration={generation}",
                    previous.version
                ),
            );
        } else {
            self.revoke(&candidate);
            slot.error = Some(reason);
            slot.awaiting_ui = false;
            slot.ui_fallback = None;
        }
        drop(slot);
        drop(transition);
        self.catalog().await
    }
    pub async fn close(&self, token: &str) -> Result<(), String> {
        let identity = self
            .tokens
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .get(token)
            .cloned();
        let Some((id, generation)) = identity else {
            return Ok(());
        };
        let cell = self.cell(&id)?;
        let mut slot = cell.slot.lock().await;
        if slot
            .session
            .as_ref()
            .is_some_and(|session| session.token == token && session.generation == generation)
        {
            if let Some(mut session) = slot.session.take() {
                self.revoke(&session);
                session.stop_worker().await?;
            }
        }
        Ok(())
    }
    pub async fn shutdown(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        let cells = match self.cells.lock() {
            Ok(cells) => cells.values().cloned().collect::<Vec<_>>(),
            Err(_) => return,
        };
        for cell in cells {
            cell.transition.store(true, Ordering::SeqCst);
            let mut slot = cell.slot.lock().await;
            if let Some(mut session) = slot.session.take() {
                self.revoke(&session);
                if let Err(error) = session.stop_worker().await {
                    self.diagnostics
                        .record("error", "module.shutdown_failed", &error);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, io::Write};

    fn test_manager() -> (PathBuf, ModuleManager) {
        let root = std::env::temp_dir().join(format!(
            "nimdadesk-module-lifecycle-{}",
            uuid::Uuid::new_v4()
        ));
        fs::create_dir_all(&root).unwrap();
        let manager = ModuleManager::new(
            root.join("data"),
            root.join("no-bundles"),
            Arc::new(Diagnostics::new(&root)),
        )
        .unwrap();
        (root, manager)
    }
    fn install(manager: &ModuleManager, root: &Path, id: &str, version: &str) -> String {
        let path = root.join(format!("{id}-{version}.ndpkg"));
        let mut archive = zip::ZipWriter::new(fs::File::create(&path).unwrap());
        let options = zip::write::SimpleFileOptions::default();
        archive.start_file("manifest.json", options).unwrap();
        let manifest = json!({"schemaVersion":1,"id":id,"label":id,"version":version,"hostApi":1,"target":"windows-x64","frontend":"frontend/index.html","worker":null,"methods":[],"dataSchema":1,"source":{"commit":null,"dirty":true}});
        archive
            .write_all(serde_json::to_string(&manifest).unwrap().as_bytes())
            .unwrap();
        archive.start_file("frontend/index.html", options).unwrap();
        archive.write_all(b"<p>UI lifecycle fixture</p>").unwrap();
        archive.finish().unwrap();
        manager
            .store
            .lock()
            .unwrap()
            .import_package(&path, "imported", None)
            .unwrap()
            .digest
            .unwrap()
    }
    fn module(catalog: &Value, id: &str) -> Value {
        catalog["modules"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["id"] == id)
            .unwrap()
            .clone()
    }
    async fn ready(manager: &ModuleManager, session: &Value) {
        manager
            .validate_session(
                session["token"].as_str().unwrap(),
                session["moduleId"].as_str().unwrap(),
                session["version"].as_str().unwrap(),
                session["digest"].as_str().unwrap(),
            )
            .await
            .unwrap();
    }

    #[test]
    fn ui_failure_restores_confirmed_version_and_ignores_stale_or_ready_reports() {
        tauri::async_runtime::block_on(async {
            let (root, manager) = test_manager();
            let first = install(&manager, &root, "demo", "1.0.0");
            let second = install(&manager, &root, "demo", "1.1.0");
            let third = install(&manager, &root, "demo", "1.2.0");
            manager.select("demo", "1.0.0", &first).await.unwrap();
            manager.set_enabled("demo", true).await.unwrap();
            let initial = manager.open("demo").await.unwrap();
            assert_eq!(
                module(&manager.catalog().await.unwrap(), "demo")["state"],
                "loading"
            );
            ready(&manager, &initial).await;
            let ignored = manager
                .report_load_failed(initial["token"].as_str().unwrap(), "late component error")
                .await
                .unwrap();
            assert_eq!(module(&ignored, "demo")["state"], "running");
            assert!(module(&ignored, "demo")["lastError"].is_null());

            manager.select("demo", "1.1.0", &second).await.unwrap();
            let intermediate = manager.open("demo").await.unwrap();
            manager.select("demo", "1.2.0", &third).await.unwrap();
            let candidate = manager.open("demo").await.unwrap();
            let stale = manager
                .report_load_failed(intermediate["token"].as_str().unwrap(), "stale error")
                .await
                .unwrap();
            assert_eq!(module(&stale, "demo")["selectedVersion"], "1.2.0");
            manager
                .close(initial["token"].as_str().unwrap())
                .await
                .unwrap();
            assert_eq!(
                manager.open("demo").await.unwrap()["token"],
                candidate["token"]
            );

            let rollback = manager
                .report_load_failed(
                    candidate["token"].as_str().unwrap(),
                    "UI handshake timed out",
                )
                .await
                .unwrap();
            assert_eq!(module(&rollback, "demo")["selectedVersion"], "1.0.0");
            assert_eq!(module(&rollback, "demo")["state"], "loading");
            assert_eq!(
                module(&rollback, "demo")["lastError"],
                "UI handshake timed out"
            );
            assert!(module(&rollback, "demo")["error"].is_null());
            let restored = manager.open("demo").await.unwrap();
            ready(&manager, &restored).await;
            let duplicate = manager
                .report_load_failed(candidate["token"].as_str().unwrap(), "duplicate error")
                .await
                .unwrap();
            assert_eq!(module(&duplicate, "demo")["state"], "running");
            assert_eq!(
                manager.open("demo").await.unwrap()["token"],
                restored["token"]
            );
            let reloaded = Store::new(root.join("data"), &root.join("no-bundles")).unwrap();
            assert_eq!(reloaded.state.selections["demo"].version, "1.0.0");
            manager.shutdown().await;
            fs::remove_dir_all(root).unwrap();
        });
    }

    #[test]
    fn first_ui_failure_only_fails_its_module_and_keeps_other_session() {
        tauri::async_runtime::block_on(async {
            let (root, manager) = test_manager();
            let a = install(&manager, &root, "module-a", "1.0.0");
            let b = install(&manager, &root, "module-b", "1.0.0");
            manager.select("module-a", "1.0.0", &a).await.unwrap();
            manager.set_enabled("module-a", true).await.unwrap();
            manager.select("module-b", "1.0.0", &b).await.unwrap();
            manager.set_enabled("module-b", true).await.unwrap();
            let active_a = manager.open("module-a").await.unwrap();
            ready(&manager, &active_a).await;
            let failed_b = manager.open("module-b").await.unwrap();
            let catalog = manager
                .report_load_failed(failed_b["token"].as_str().unwrap(), "first load failed")
                .await
                .unwrap();
            assert_eq!(module(&catalog, "module-b")["state"], "failed");
            assert_eq!(module(&catalog, "module-b")["selectedVersion"], "1.0.0");
            assert_eq!(module(&catalog, "module-a")["state"], "running");
            assert_eq!(
                manager.open("module-a").await.unwrap()["token"],
                active_a["token"]
            );
            manager
                .close(failed_b["token"].as_str().unwrap())
                .await
                .unwrap();
            assert!(manager
                .validate_session(failed_b["token"].as_str().unwrap(), "module-b", "1.0.0", &b)
                .await
                .is_err());
            manager.shutdown().await;
            fs::remove_dir_all(root).unwrap();
        });
    }
}
