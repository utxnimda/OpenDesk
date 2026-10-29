//! Opt-in read-only cross-module calls. Never start a second business worker.
use super::{Cell, ModuleManager, ToolReadAdmission};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::{atomic::Ordering, Arc};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ToolIdentity {
    pub name: String,
    pub module_id: String,
    pub version: String,
    pub digest: String,
    pub generation: String,
}

#[derive(Clone, Debug, PartialEq)]
pub(super) struct ToolGrant {
    identity: ToolIdentity,
    token: String,
    method: String,
    public: Value,
}

/// Created by the manager before opening the native consent dialog. Its private
/// snapshot cannot be reconstructed from module-provided arguments.
pub struct PreparedToolGrant {
    pub message: String,
    caller: Value,
    grants: Vec<ToolGrant>,
}

fn validate_grant_snapshot(expected: &[ToolGrant], available: &[ToolGrant]) -> Result<(), String> {
    if expected.is_empty() || expected.len() > 16 {
        return Err("TOOLS_GRANT_INVALID".into());
    }
    if expected.iter().any(|item| !available.contains(item)) {
        return Err("TOOLS_SELECTION_STALE: tool metadata changed while awaiting consent".into());
    }
    Ok(())
}

fn bounded_text(value: &Value, key: &str, limit: usize) -> Result<String, String> {
    let s = value[key].as_str().ok_or("TOOLS_DESCRIPTOR_INVALID")?;
    if s.is_empty() || s.chars().count() > limit || s.chars().any(char::is_control) {
        return Err("TOOLS_DESCRIPTOR_INVALID".into());
    }
    Ok(s.into())
}

fn descriptor(value: &Value, methods: &[String]) -> Result<(String, String), String> {
    let name = bounded_text(value, "name", 32)?;
    let method = bounded_text(value, "method", 128)?;
    if !name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_')
        || !methods.contains(&method)
        || method == "tools.describe"
        || value["readOnly"] != true
        || value["contractVersion"] != 1
        || value["inputSchema"]
            != json!({"type":"object","properties":{},"additionalProperties":false})
    {
        return Err("TOOLS_DESCRIPTOR_UNSUPPORTED: only explicitly exported, zero-argument read-only tools are supported".into());
    }
    bounded_text(value, "title", 80)?;
    bounded_text(value, "description", 1000)?;
    Ok((name, method))
}

impl ModuleManager {
    async fn tool_caller(&self, token: &str) -> Result<Value, String> {
        self.describe_existing(token).await
    }

    fn tool_caller_identity_current(
        &self,
        token: &str,
        id: &str,
        generation: &str,
    ) -> Result<(), String> {
        self.ensure_running()?;
        let tokens = self.tokens.lock().map_err(|_| "MODULE_LOCK_UNAVAILABLE")?;
        if !tokens
            .get(token)
            .is_some_and(|(owner, current)| owner == id && current == generation)
        {
            return Err("MODULE_SESSION_STALE".into());
        }
        Ok(())
    }

    fn admit_tool_read(
        &self,
        cell: &Arc<Cell>,
        token: &str,
        id: &str,
        generation: &str,
    ) -> Result<ToolReadAdmission, String> {
        let admission = ToolReadAdmission::begin(cell, &self.stopping)?;
        self.tool_caller_identity_current(token, id, generation)?;
        Ok(admission)
    }

    async fn discover_tools(&self, token: &str) -> Result<Vec<ToolGrant>, String> {
        let caller = self.tool_caller(token).await?;
        let cells: Vec<_> = self
            .cells
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .values()
            .cloned()
            .collect();
        let mut tools = Vec::new();
        for cell in cells {
            if cell.transition.load(Ordering::SeqCst) {
                continue;
            }
            let mut slot = cell.slot.lock().await;
            if slot.awaiting_ui || cell.transition.load(Ordering::SeqCst) {
                continue;
            }
            let Some(session) = slot.session.as_mut() else {
                continue;
            };
            if session.token == token
                || !session
                    .installed
                    .manifest
                    .methods
                    .iter()
                    .any(|m| m == "tools.describe")
            {
                continue;
            }
            let catalog = session.call("tools.describe", json!({})).await?;
            let entries = catalog["tools"].as_array().ok_or("TOOLS_CATALOG_INVALID")?;
            if catalog["contractVersion"] != 1 || entries.len() > 16 {
                return Err("TOOLS_CATALOG_INVALID".into());
            }
            for entry in entries {
                let (local_name, method) = descriptor(entry, &session.installed.manifest.methods)?;
                let name = format!(
                    "{}__{}",
                    session.installed.manifest.id.replace('-', "_"),
                    local_name
                );
                if name.len() > 64 || tools.iter().any(|g: &ToolGrant| g.identity.name == name) {
                    return Err("TOOLS_NAME_CONFLICT".into());
                }
                let identity = ToolIdentity {
                    name: name.clone(),
                    module_id: session.installed.manifest.id.clone(),
                    version: session.installed.manifest.version.clone(),
                    digest: session.installed.digest.clone(),
                    generation: session.generation.clone(),
                };
                let mut public =
                    serde_json::to_value(&identity).map_err(|_| "TOOLS_DESCRIPTOR_INVALID")?;
                for key in [
                    "title",
                    "description",
                    "inputSchema",
                    "readOnly",
                    "contractVersion",
                ] {
                    public[key] = entry[key].clone();
                }
                tools.push(ToolGrant {
                    identity,
                    token: session.token.clone(),
                    method,
                    public,
                });
            }
        }
        if self.tool_caller(token).await? != caller {
            return Err("MODULE_SESSION_STALE".into());
        }
        tools.sort_by(|a, b| a.identity.name.cmp(&b.identity.name));
        Ok(tools)
    }

    pub async fn list_tools(&self, token: &str) -> Result<Value, String> {
        Ok(Value::Array(
            self.discover_tools(token)
                .await?
                .into_iter()
                .map(|g| g.public)
                .collect(),
        ))
    }

    pub async fn prepare_tool_grant(
        &self,
        token: &str,
        requested: &[ToolIdentity],
    ) -> Result<PreparedToolGrant, String> {
        if requested.is_empty() || requested.len() > 16 {
            return Err("TOOLS_GRANT_INVALID".into());
        }
        let caller = self.tool_caller(token).await?;
        let available = self.discover_tools(token).await?;
        let mut labels = Vec::new();
        let mut grants = Vec::new();
        for identity in requested {
            let found = available
                .iter()
                .find(|g| &g.identity == identity)
                .ok_or("TOOLS_SELECTION_STALE")?;
            if grants.contains(found) {
                return Err("TOOLS_GRANT_INVALID".into());
            }
            labels.push(format!(
                "{} · {} v{}",
                found.public["title"].as_str().unwrap_or("工具"),
                identity.module_id,
                identity.version
            ));
            grants.push(found.clone());
        }
        let message = format!("{} 请求在本次打开期间读取以下插件数据：\n\n{}\n\n调用结果将返回请求方；如用于云端 AI，可能发送给你选择的模型服务。仅授权这些只读工具；关闭或切换插件后需重新授权。", caller["moduleId"].as_str().unwrap_or("插件"), labels.join("\n"));
        Ok(PreparedToolGrant {
            message,
            caller,
            grants,
        })
    }

    // Called only after the trusted main-window command obtained an explicit native decision.
    pub async fn grant_tools(
        &self,
        token: &str,
        prepared: PreparedToolGrant,
    ) -> Result<(), String> {
        if prepared.caller["token"].as_str() != Some(token) {
            return Err("MODULE_SESSION_STALE".into());
        }
        let available = self.discover_tools(token).await?;
        validate_grant_snapshot(&prepared.grants, &available)?;
        if self.tool_caller(token).await? != prepared.caller {
            return Err("MODULE_SESSION_STALE".into());
        }
        let id = prepared.caller["moduleId"]
            .as_str()
            .ok_or("MODULE_SESSION_STALE")?;
        let generation = prepared.caller["generation"]
            .as_str()
            .ok_or("MODULE_SESSION_STALE")?;
        let cell = self.cell(id)?;
        let _admission = self.admit_tool_read(&cell, token, id, generation)?;
        self.tool_grants
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .insert(token.into(), prepared.grants);
        Ok(())
    }

    pub async fn call_tool(
        &self,
        token: &str,
        identity: ToolIdentity,
        arguments: Value,
    ) -> Result<Value, String> {
        let caller = self.tool_caller(token).await?;
        let caller_id = caller["moduleId"].as_str().ok_or("MODULE_SESSION_STALE")?;
        let caller_generation = caller["generation"]
            .as_str()
            .ok_or("MODULE_SESSION_STALE")?;
        if caller_id == identity.module_id {
            return Err("TOOLS_SELF_CALL_UNSUPPORTED".into());
        }
        let caller_cell = self.cell(caller_id)?;
        if arguments != json!({}) {
            return Err("TOOLS_ARGUMENTS_INVALID: this tool accepts no arguments".into());
        }
        let grant = self
            .tool_grants
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .get(token)
            .and_then(|items| items.iter().find(|g| g.identity == identity))
            .cloned()
            .ok_or("TOOLS_PERMISSION_REQUIRED")?;
        // Resolve the pinned session directly: no fallback to another installed version.
        let cell = self.cell(&identity.module_id)?;
        if cell.transition.load(Ordering::SeqCst) {
            return Err("MODULE_SWITCHING".into());
        }
        let mut slot = cell.slot.lock().await;
        if cell.transition.load(Ordering::SeqCst) || slot.awaiting_ui {
            return Err("TOOLS_SELECTION_STALE".into());
        }
        let target = slot.session.as_mut().ok_or("TOOLS_SELECTION_STALE")?;
        if target.token != grant.token
            || target.generation != identity.generation
            || target.installed.digest != identity.digest
            || target.installed.manifest.version != identity.version
        {
            return Err("TOOLS_SELECTION_STALE".into());
        }
        // Acquire no caller slot mutex while holding the target slot. A separate
        // short admission protects caller lifecycle without an A->B/B->A deadlock.
        let _admission = self.admit_tool_read(&caller_cell, token, caller_id, caller_generation)?;
        if !self
            .tool_grants
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .get(token)
            .is_some_and(|items| items.contains(&grant))
        {
            return Err("TOOLS_PERMISSION_REQUIRED".into());
        }
        let value = target.call(&grant.method, arguments).await?;
        if !value.is_object()
            || serde_json::to_vec(&value)
                .map_err(|_| "TOOLS_RESULT_INVALID")?
                .len()
                > 65_536
        {
            return Err("TOOLS_RESULT_TOO_LARGE".into());
        }
        self.tool_caller_identity_current(token, caller_id, caller_generation)?;
        self.diagnostics.record(
            "info",
            "module.tool_completed",
            &format!("target={} tool={}", identity.module_id, identity.name),
        );
        Ok(
            json!({"source":{"moduleId":identity.module_id,"version":identity.version,"digest":identity.digest,"tool":identity.name},"data":value}),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::super::{
        tests::{install, ready, test_manager},
        Transition,
    };
    use super::*;
    use std::sync::{atomic::AtomicBool, Barrier};
    fn tool() -> Value {
        json!({"name":"snapshot","method":"example.snapshot","title":"读取快照","description":"读取当前实例的快照","contractVersion":1,"readOnly":true,"inputSchema":{"type":"object","properties":{},"additionalProperties":false}})
    }
    #[test]
    fn requires_explicit_read_only_export_and_declared_method() {
        let methods = vec!["example.snapshot".into()];
        assert!(descriptor(&tool(), &methods).is_ok());
        assert!(descriptor(&tool(), &[]).is_err());
        let mut t = tool();
        t["readOnly"] = json!(false);
        assert!(descriptor(&t, &methods).is_err());
        t = tool();
        t["inputSchema"]["properties"] = json!({"path":{"type":"string"}});
        assert!(descriptor(&t, &methods).is_err());
        t = tool();
        t["title"] = json!("fake\nconfirmation");
        assert!(descriptor(&t, &methods).is_err());
        t = tool();
        t["name"] = json!("../../exec");
        assert!(descriptor(&t, &methods).is_err());
    }
    #[test]
    fn grants_pin_version_digest_and_generation() {
        let a = ToolIdentity {
            name: "x".into(),
            module_id: "example".into(),
            version: "1.0.0".into(),
            digest: "a".repeat(64),
            generation: "first".into(),
        };
        for field in ["version", "digest", "generation"] {
            let mut value = serde_json::to_value(&a).unwrap();
            value[field] = json!("changed");
            assert_ne!(a, serde_json::from_value::<ToolIdentity>(value).unwrap());
        }
    }

    fn approved_snapshot() -> ToolGrant {
        ToolGrant {
            identity: ToolIdentity {
                name: "example__snapshot".into(),
                module_id: "example".into(),
                version: "1.0.0".into(),
                digest: "a".repeat(64),
                generation: "generation".into(),
            },
            token: "private-target-token".into(),
            method: "example.snapshot".into(),
            public: tool(),
        }
    }

    #[test]
    fn consent_snapshot_rejects_changed_method_and_public_description() {
        let expected = approved_snapshot();
        assert!(validate_grant_snapshot(&[expected.clone()], &[expected.clone()]).is_ok());
        let mut changed = expected.clone();
        changed.method = "example.different_snapshot".into();
        assert!(validate_grant_snapshot(&[expected.clone()], &[changed]).is_err());
        for key in ["title", "description", "inputSchema"] {
            let mut changed = expected.clone();
            changed.public[key] = json!("different after native dialog opened");
            assert!(validate_grant_snapshot(&[expected.clone()], &[changed]).is_err());
        }
        let mut reopened = expected.clone();
        reopened.token = "replacement-worker-view".into();
        assert!(validate_grant_snapshot(&[expected], &[reopened]).is_err());
    }

    #[test]
    fn read_and_lifecycle_admission_cannot_both_win_concurrently() {
        for _ in 0..64 {
            let cell = Arc::new(Cell::default());
            let stopping = Arc::new(AtomicBool::new(false));
            let barrier = Arc::new(Barrier::new(2));
            let reader = {
                let cell = cell.clone();
                let stopping = stopping.clone();
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    let admitted = ToolReadAdmission::begin(&cell, &stopping);
                    let accepted = admitted.is_ok();
                    // Keep the admission alive until the competing attempt finishes.
                    barrier.wait();
                    drop(admitted);
                    accepted
                })
            };
            let lifecycle = {
                let cell = cell.clone();
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    let transition = Transition::begin(&cell);
                    let accepted = transition.is_ok();
                    barrier.wait();
                    drop(transition);
                    accepted
                })
            };
            let read_accepted = reader.join().unwrap();
            let lifecycle_accepted = lifecycle.join().unwrap();
            assert!(!(read_accepted && lifecycle_accepted));
            assert_eq!(cell.active_tool_reads.load(Ordering::SeqCst), 0);
            assert!(!cell.transition.load(Ordering::SeqCst));
        }
    }

    #[test]
    fn existing_caller_token_cannot_be_admitted_during_close_or_disable() {
        tauri::async_runtime::block_on(async {
            let (root, manager) = test_manager();
            let digest = install(&manager, &root, "caller", "1.0.0");
            manager.select("caller", "1.0.0", &digest).await.unwrap();
            manager.set_enabled("caller", true).await.unwrap();
            let session = manager.open("caller").await.unwrap();
            ready(&manager, &session).await;
            let token = session["token"].as_str().unwrap();
            let generation = session["generation"].as_str().unwrap();
            let cell = manager.cell("caller").unwrap();
            let transition = Transition::begin(&cell).unwrap();
            // The old token remains valid while an API2 worker drains. It must
            // not permit a queued target read to enter after lifecycle begins.
            assert!(manager.tokens.lock().unwrap().contains_key(token));
            assert!(manager
                .admit_tool_read(&cell, token, "caller", generation)
                .is_err());
            assert_eq!(cell.active_tool_reads.load(Ordering::SeqCst), 0);
            drop(transition);
            let admission = manager
                .admit_tool_read(&cell, token, "caller", generation)
                .unwrap();
            assert!(manager
                .close(token)
                .await
                .unwrap_err()
                .starts_with("MODULE_BUSY"));
            assert!(manager
                .set_enabled("caller", false)
                .await
                .unwrap_err()
                .starts_with("MODULE_BUSY"));
            assert!(manager.describe_existing(token).await.is_ok());
            drop(admission);
            manager.close(token).await.unwrap();
            assert!(manager
                .admit_tool_read(&cell, token, "caller", generation)
                .is_err());
            assert_eq!(cell.active_tool_reads.load(Ordering::SeqCst), 0);
            manager.shutdown().await.unwrap();
            std::fs::remove_dir_all(root).unwrap();
        });
    }

    #[test]
    fn queued_target_lock_does_not_hold_a_caller_lease() {
        tauri::async_runtime::block_on(async {
            let caller = Arc::new(Cell::default());
            let target = Arc::new(Cell::default());
            let stopping = Arc::new(AtomicBool::new(false));
            let target_lock = target.slot.lock().await;
            let (queued_tx, queued_rx) = tokio::sync::oneshot::channel();
            let waiting = {
                let caller = caller.clone();
                let target = target.clone();
                let stopping = stopping.clone();
                tauri::async_runtime::spawn(async move {
                    queued_tx.send(()).unwrap();
                    let _target = target.slot.lock().await;
                    ToolReadAdmission::begin(&caller, &stopping).is_ok()
                })
            };
            queued_rx.await.unwrap();
            assert_eq!(caller.active_tool_reads.load(Ordering::SeqCst), 0);
            let closing = Transition::begin(&caller).unwrap();
            drop(target_lock);
            assert!(!waiting.await.unwrap());
            drop(closing);
            assert_eq!(caller.active_tool_reads.load(Ordering::SeqCst), 0);
        });
    }

    #[test]
    fn shutdown_preflight_leaves_all_modules_running_when_read_is_active() {
        tauri::async_runtime::block_on(async {
            let (root, manager) = test_manager();
            let mut sessions = Vec::new();
            for id in ["caller", "target"] {
                let digest = install(&manager, &root, id, "1.0.0");
                manager.select(id, "1.0.0", &digest).await.unwrap();
                manager.set_enabled(id, true).await.unwrap();
                let session = manager.open(id).await.unwrap();
                ready(&manager, &session).await;
                sessions.push(session);
            }
            let caller_cell = manager.cell("caller").unwrap();
            let admission = ToolReadAdmission::begin(&caller_cell, &manager.stopping).unwrap();
            assert!(manager
                .shutdown()
                .await
                .unwrap_err()
                .starts_with("MODULE_BUSY"));
            assert!(!manager.stopping.load(Ordering::SeqCst));
            for session in &sessions {
                assert_eq!(
                    manager
                        .describe_existing(session["token"].as_str().unwrap())
                        .await
                        .unwrap(),
                    *session
                );
            }
            drop(admission);
            manager.shutdown().await.unwrap();
            std::fs::remove_dir_all(root).unwrap();
        });
    }
}
