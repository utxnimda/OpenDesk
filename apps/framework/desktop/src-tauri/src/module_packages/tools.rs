//! Opt-in cross-module tool mediation (v1 read-only zero-arg + v2 bounded schema & tiered approval)
//! plus host-mediated module catalog listing and controlled module wake/focus.
//! Never starts a second conflicting business worker.
use super::{store::valid_id, Cell, ModuleManager, ToolReadAdmission};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashSet},
    sync::{atomic::Ordering, Arc},
    time::{Duration, Instant},
};

const MAX_TOOL_COUNT: usize = 16;
const MAX_SCHEMA_PROPS: usize = 12;
const MAX_STRING_ARG_CHARS: usize = 500;
const MAX_ARRAY_ARG_ITEMS: usize = 32;
const MAX_ARGUMENTS_BYTES: usize = 16_384;
const MAX_RESULT_BYTES: usize = 65_536;

const FORBIDDEN_ARG_NAMES: &[&str] = &[
    "command", "cmd", "shell", "script", "exec", "eval", "args", "argv", "code", "sh", "bash",
    "powershell", "pwsh",
];

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ToolAccess {
    Read,
    UiNavigate,
    Write,
}

impl ToolAccess {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Read => "read",
            Self::UiNavigate => "ui-navigate",
            Self::Write => "write",
        }
    }

    fn from_str(raw: &str) -> Result<Self, String> {
        match raw {
            "read" => Ok(Self::Read),
            "ui-navigate" => Ok(Self::UiNavigate),
            "write" => Ok(Self::Write),
            _ => Err("TOOLS_DESCRIPTOR_UNSUPPORTED: access must be read, ui-navigate, or write".into()),
        }
    }
}

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
    access: ToolAccess,
    input_schema: Value,
    public: Value,
}

/// Created by the manager before opening the native consent dialog. Its private
/// snapshot cannot be reconstructed from module-provided arguments.
pub struct PreparedToolGrant {
    pub message: String,
    caller: Value,
    grants: Vec<ToolGrant>,
}

/// Prepared, validated tool invocation ready to execute directly (read/ui-navigate)
/// or awaiting an explicit per-call native confirmation dialog (write).
pub struct ApprovedToolCall {
    caller_token: String,
    caller_id: String,
    caller_generation: String,
    grant: ToolGrant,
    arguments: Value,
}

pub enum PreparedToolCall {
    Direct(ApprovedToolCall),
    NeedsApproval {
        message: String,
        call: ApprovedToolCall,
    },
}

pub enum PreparedModuleOpen {
    AlreadyOpen {
        module_id: String,
        label: String,
        version: String,
        focus: bool,
    },
    NeedsApproval {
        message: String,
        module_id: String,
        label: String,
        version: String,
        focus: bool,
    },
}

fn validate_grant_snapshot(expected: &[ToolGrant], available: &[ToolGrant]) -> Result<(), String> {
    if expected.is_empty() || expected.len() > MAX_TOOL_COUNT {
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

fn is_forbidden_method_name(method: &str) -> bool {
    if method == "tools.describe"
        || method.starts_with("runtime.")
        || method.starts_with("module.")
    {
        return true;
    }
    let lower = method.to_ascii_lowercase();
    let tail = lower.rsplit('.').next().unwrap_or(&lower);
    matches!(
        tail,
        "exec" | "execute" | "shell" | "spawn" | "eval" | "run_command" | "run_shell"
    )
}

fn is_forbidden_arg_name(key: &str) -> bool {
    let lower = key.to_ascii_lowercase();
    FORBIDDEN_ARG_NAMES.iter().any(|bad| lower == *bad)
}

fn contains_path_traversal(value: &str) -> bool {
    value
        .split(['/', '\\'])
        .any(|segment| segment == "..")
}

/// Strictly validate a bounded contractVersion=2 JSON Schema.
/// Only flat objects with additionalProperties: false and scalar / string-array fields are allowed.
fn validate_input_schema_v2(schema: &Value) -> Result<(), String> {
    let obj = schema
        .as_object()
        .ok_or("TOOLS_SCHEMA_INVALID: inputSchema must be an object")?;
    let allowed_root = ["type", "properties", "required", "additionalProperties"];
    if obj.keys().any(|k| !allowed_root.contains(&k.as_str())) {
        return Err("TOOLS_SCHEMA_INVALID: unsupported root schema keyword".into());
    }
    if obj.get("type").and_then(Value::as_str) != Some("object")
        || obj.get("additionalProperties") != Some(&Value::Bool(false))
    {
        return Err("TOOLS_SCHEMA_INVALID: schema must be object with additionalProperties=false".into());
    }
    let props = obj
        .get("properties")
        .and_then(Value::as_object)
        .ok_or("TOOLS_SCHEMA_INVALID: properties must be an object")?;
    if props.len() > MAX_SCHEMA_PROPS {
        return Err("TOOLS_SCHEMA_INVALID: too many properties".into());
    }

    for (key, spec) in props {
        if key.is_empty()
            || key.len() > 48
            || !key
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
            || is_forbidden_arg_name(key)
        {
            return Err(format!("TOOLS_SCHEMA_INVALID: forbidden or invalid property name '{key}'"));
        }
        let spec_obj = spec
            .as_object()
            .ok_or("TOOLS_SCHEMA_INVALID: property schema must be an object")?;
        let allowed_prop = [
            "type",
            "description",
            "enum",
            "minLength",
            "maxLength",
            "minimum",
            "maximum",
            "items",
            "maxItems",
            "default",
        ];
        if spec_obj
            .keys()
            .any(|k| !allowed_prop.contains(&k.as_str()))
        {
            return Err("TOOLS_SCHEMA_INVALID: unsupported property keyword".into());
        }
        if let Some(desc) = spec_obj.get("description") {
            let text = desc.as_str().ok_or("TOOLS_SCHEMA_INVALID")?;
            if text.chars().count() > 240 || text.chars().any(char::is_control) {
                return Err("TOOLS_SCHEMA_INVALID".into());
            }
        }
        match spec_obj.get("type").and_then(Value::as_str) {
            Some("string") => {
                if let Some(max_len) = spec_obj.get("maxLength") {
                    let n = max_len.as_u64().ok_or("TOOLS_SCHEMA_INVALID")?;
                    if n == 0 || n > MAX_STRING_ARG_CHARS as u64 {
                        return Err("TOOLS_SCHEMA_INVALID: string maxLength out of bounds".into());
                    }
                }
                if let Some(enum_val) = spec_obj.get("enum") {
                    let list = enum_val.as_array().ok_or("TOOLS_SCHEMA_INVALID")?;
                    if list.is_empty() || list.len() > 32 {
                        return Err("TOOLS_SCHEMA_INVALID".into());
                    }
                    for item in list {
                        let s = item.as_str().ok_or("TOOLS_SCHEMA_INVALID")?;
                        if s.len() > 128 || s.chars().any(char::is_control) {
                            return Err("TOOLS_SCHEMA_INVALID".into());
                        }
                    }
                }
            }
            Some("integer") | Some("number") | Some("boolean") => {}
            Some("array") => {
                let items = spec_obj
                    .get("items")
                    .and_then(Value::as_object)
                    .ok_or("TOOLS_SCHEMA_INVALID: array requires items")?;
                if items.get("type").and_then(Value::as_str) != Some("string") {
                    return Err("TOOLS_SCHEMA_INVALID: array items must be string".into());
                }
                if let Some(max_items) = spec_obj.get("maxItems") {
                    let n = max_items.as_u64().ok_or("TOOLS_SCHEMA_INVALID")?;
                    if n == 0 || n > MAX_ARRAY_ARG_ITEMS as u64 {
                        return Err("TOOLS_SCHEMA_INVALID".into());
                    }
                }
            }
            _ => return Err("TOOLS_SCHEMA_INVALID: unsupported property type".into()),
        }
    }

    if let Some(req) = obj.get("required") {
        let arr = req.as_array().ok_or("TOOLS_SCHEMA_INVALID")?;
        let mut seen = HashSet::new();
        for item in arr {
            let name = item.as_str().ok_or("TOOLS_SCHEMA_INVALID")?;
            if !props.contains_key(name) || !seen.insert(name) {
                return Err("TOOLS_SCHEMA_INVALID: required field missing from properties".into());
            }
        }
    }
    Ok(())
}

/// Authoritative runtime validation of tool call arguments against the tool's verified schema.
pub(super) fn validate_arguments_against_schema(
    schema: &Value,
    arguments: &Value,
) -> Result<(), String> {
    let encoded = serde_json::to_vec(arguments).map_err(|_| "TOOLS_ARGUMENTS_INVALID")?;
    if encoded.len() > MAX_ARGUMENTS_BYTES {
        return Err("TOOLS_ARGUMENTS_INVALID: payload too large".into());
    }
    let arg_obj = arguments
        .as_object()
        .ok_or("TOOLS_ARGUMENTS_INVALID: arguments must be a JSON object")?;
    let props = schema["properties"]
        .as_object()
        .ok_or("TOOLS_SCHEMA_INVALID")?;

    for key in arg_obj.keys() {
        if !props.contains_key(key) {
            return Err(format!("TOOLS_ARGUMENTS_INVALID: unknown argument '{key}'"));
        }
    }
    if let Some(required) = schema.get("required").and_then(Value::as_array) {
        for req in required {
            if let Some(name) = req.as_str() {
                if !arg_obj.contains_key(name) {
                    return Err(format!("TOOLS_ARGUMENTS_INVALID: missing required argument '{name}'"));
                }
            }
        }
    }

    for (key, value) in arg_obj {
        let spec = &props[key];
        match spec["type"].as_str() {
            Some("string") => {
                let text = value
                    .as_str()
                    .ok_or_else(|| format!("TOOLS_ARGUMENTS_INVALID: '{key}' must be string"))?;
                let max_len = spec
                    .get("maxLength")
                    .and_then(Value::as_u64)
                    .map(|v| (v as usize).min(MAX_STRING_ARG_CHARS))
                    .unwrap_or(MAX_STRING_ARG_CHARS);
                let min_len = spec.get("minLength").and_then(Value::as_u64).unwrap_or(0) as usize;
                let count = text.chars().count();
                if count < min_len
                    || count > max_len
                    || text.chars().any(char::is_control)
                    || contains_path_traversal(text)
                {
                    return Err(format!("TOOLS_ARGUMENTS_INVALID: '{key}' failed string bounds or safety check"));
                }
                if let Some(enum_list) = spec.get("enum").and_then(Value::as_array) {
                    if !enum_list.iter().any(|e| e.as_str() == Some(text)) {
                        return Err(format!("TOOLS_ARGUMENTS_INVALID: '{key}' not in allowed enum"));
                    }
                }
            }
            Some("integer") => {
                let n = value
                    .as_i64()
                    .ok_or_else(|| format!("TOOLS_ARGUMENTS_INVALID: '{key}' must be integer"))?;
                if let Some(min) = spec.get("minimum").and_then(Value::as_i64) {
                    if n < min {
                        return Err(format!("TOOLS_ARGUMENTS_INVALID: '{key}' below minimum"));
                    }
                }
                if let Some(max) = spec.get("maximum").and_then(Value::as_i64) {
                    if n > max {
                        return Err(format!("TOOLS_ARGUMENTS_INVALID: '{key}' above maximum"));
                    }
                }
            }
            Some("number") => {
                let n = value
                    .as_f64()
                    .ok_or_else(|| format!("TOOLS_ARGUMENTS_INVALID: '{key}' must be number"))?;
                if !n.is_finite() {
                    return Err(format!("TOOLS_ARGUMENTS_INVALID: '{key}' must be finite"));
                }
                if let Some(min) = spec.get("minimum").and_then(Value::as_f64) {
                    if n < min {
                        return Err(format!("TOOLS_ARGUMENTS_INVALID: '{key}' below minimum"));
                    }
                }
                if let Some(max) = spec.get("maximum").and_then(Value::as_f64) {
                    if n > max {
                        return Err(format!("TOOLS_ARGUMENTS_INVALID: '{key}' above maximum"));
                    }
                }
            }
            Some("boolean") => {
                if !value.is_boolean() {
                    return Err(format!("TOOLS_ARGUMENTS_INVALID: '{key}' must be boolean"));
                }
            }
            Some("array") => {
                let arr = value
                    .as_array()
                    .ok_or_else(|| format!("TOOLS_ARGUMENTS_INVALID: '{key}' must be array"))?;
                let max_items = spec
                    .get("maxItems")
                    .and_then(Value::as_u64)
                    .map(|v| (v as usize).min(MAX_ARRAY_ARG_ITEMS))
                    .unwrap_or(MAX_ARRAY_ARG_ITEMS);
                if arr.len() > max_items {
                    return Err(format!("TOOLS_ARGUMENTS_INVALID: '{key}' exceeds maxItems"));
                }
                for item in arr {
                    let s = item
                        .as_str()
                        .ok_or_else(|| format!("TOOLS_ARGUMENTS_INVALID: '{key}' items must be string"))?;
                    if s.chars().count() > MAX_STRING_ARG_CHARS
                        || s.chars().any(char::is_control)
                        || contains_path_traversal(s)
                    {
                        return Err(format!("TOOLS_ARGUMENTS_INVALID: '{key}' contains unsafe array item"));
                    }
                }
            }
            _ => return Err("TOOLS_SCHEMA_INVALID".into()),
        }
    }
    Ok(())
}

fn descriptor(
    value: &Value,
    methods: &[String],
) -> Result<(String, String, ToolAccess, u64, Value), String> {
    let name = bounded_text(value, "name", 32)?;
    let method = bounded_text(value, "method", 128)?;
    if !name
        .bytes()
        .all(|c| c.is_ascii_alphanumeric() || c == b'_')
        || !methods.contains(&method)
        || is_forbidden_method_name(&method)
    {
        return Err("TOOLS_DESCRIPTOR_UNSUPPORTED: invalid tool name or undeclared/forbidden method".into());
    }
    bounded_text(value, "title", 80)?;
    bounded_text(value, "description", 1000)?;

    let contract_version = value["contractVersion"]
        .as_u64()
        .ok_or("TOOLS_DESCRIPTOR_UNSUPPORTED")?;

    match contract_version {
        1 => {
            if value["readOnly"] != true
                || value["inputSchema"]
                    != json!({"type":"object","properties":{},"additionalProperties":false})
            {
                return Err(
                    "TOOLS_DESCRIPTOR_UNSUPPORTED: contractVersion 1 only supports zero-argument read-only tools"
                        .into(),
                );
            }
            Ok((
                name,
                method,
                ToolAccess::Read,
                1,
                value["inputSchema"].clone(),
            ))
        }
        2 => {
            let access_str = value["access"]
                .as_str()
                .ok_or("TOOLS_DESCRIPTOR_UNSUPPORTED: contractVersion 2 requires access")?;
            let access = ToolAccess::from_str(access_str)?;
            let expected_read_only = access == ToolAccess::Read;
            if let Some(ro) = value.get("readOnly").and_then(Value::as_bool) {
                if ro != expected_read_only {
                    return Err("TOOLS_DESCRIPTOR_UNSUPPORTED: readOnly must match access=='read'".into());
                }
            }
            validate_input_schema_v2(&value["inputSchema"])?;
            Ok((name, method, access, 2, value["inputSchema"].clone()))
        }
        _ => Err("TOOLS_DESCRIPTOR_UNSUPPORTED: unsupported contractVersion".into()),
    }
}

fn format_args_summary(arguments: &Value) -> String {
    let raw = serde_json::to_string_pretty(arguments).unwrap_or_else(|_| "{}".into());
    if raw.chars().count() <= 480 {
        raw
    } else {
        let truncated: String = raw.chars().take(480).collect();
        format!("{truncated}\n…（已截断）")
    }
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
            // Negotiate contractVersion 2 with backwards-compatible fallback to v1 `{}`.
            let catalog = match session
                .call("tools.describe", json!({"maxContractVersion": 2}))
                .await
            {
                Ok(value) => value,
                Err(_) => session.call("tools.describe", json!({})).await?,
            };
            let entries = catalog["tools"].as_array().ok_or("TOOLS_CATALOG_INVALID")?;
            let top_ver = catalog["contractVersion"].as_u64().unwrap_or(0);
            if (top_ver != 1 && top_ver != 2) || entries.len() > MAX_TOOL_COUNT {
                return Err("TOOLS_CATALOG_INVALID".into());
            }
            for entry in entries {
                let (local_name, method, access, entry_ver, input_schema) =
                    descriptor(entry, &session.installed.manifest.methods)?;
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
                for key in ["title", "description", "inputSchema"] {
                    public[key] = entry[key].clone();
                }
                public["contractVersion"] = json!(entry_ver);
                public["readOnly"] = json!(access == ToolAccess::Read);
                public["access"] = json!(access.as_str());
                tools.push(ToolGrant {
                    identity,
                    token: session.token.clone(),
                    method,
                    access,
                    input_schema,
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

    /// Stage 2: Return enabled, valid sibling modules and whether they are currently opened & ready.
    pub async fn list_modules_for_tools(&self, token: &str) -> Result<Value, String> {
        let caller = self.tool_caller(token).await?;
        let caller_id = caller["moduleId"]
            .as_str()
            .ok_or("MODULE_SESSION_STALE")?
            .to_string();
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

        let mut selected_map: BTreeMap<String, _> = BTreeMap::new();
        for version in state.versions {
            if version.manifest.id == caller_id {
                continue;
            }
            if let Some(sel) = state.selections.get(&version.manifest.id) {
                if sel.enabled && sel.digest == version.digest {
                    selected_map.insert(version.manifest.id.clone(), version);
                }
            }
        }

        let mut items = Vec::new();
        for (module_id, installed) in selected_map {
            let mut opened = false;
            let mut ready = false;
            let mut generation = Value::Null;
            if let Some(cell) = cells.get(&module_id) {
                if !cell.transition.load(Ordering::SeqCst) {
                    let slot = cell.slot.lock().await;
                    if let Some(session) = slot.session.as_ref() {
                        opened = true;
                        ready = !slot.awaiting_ui;
                        generation = json!(session.generation);
                    }
                }
            }
            let supports_tools = installed
                .manifest
                .methods
                .iter()
                .any(|m| m == "tools.describe");
            items.push(json!({
                "moduleId": module_id,
                "label": installed.manifest.label,
                "version": installed.manifest.version,
                "enabled": true,
                "opened": opened,
                "ready": ready,
                "generation": generation,
                "supportsTools": supports_tools,
            }));
        }

        if self.tool_caller(token).await? != caller {
            return Err("MODULE_SESSION_STALE".into());
        }
        Ok(Value::Array(items))
    }

    /// Stage 2: Validate a request from an authorized caller module to open/focus a sibling module.
    pub async fn prepare_module_open(
        &self,
        token: &str,
        module_id: &str,
        focus: bool,
    ) -> Result<PreparedModuleOpen, String> {
        if !valid_id(module_id) {
            return Err("MODULE_ID_INVALID".into());
        }
        let caller = self.tool_caller(token).await?;
        let caller_id = caller["moduleId"].as_str().ok_or("MODULE_SESSION_STALE")?;
        if caller_id == module_id {
            return Err("TOOLS_SELF_CALL_UNSUPPORTED".into());
        }

        let installed = {
            let store = self.store.lock().map_err(|_| "MODULE_STORE_UNAVAILABLE")?;
            let sel = store
                .state
                .selections
                .get(module_id)
                .ok_or("MODULE_NOT_SELECTED")?;
            if !sel.enabled {
                return Err("MODULE_DISABLED".into());
            }
            let item = store.lookup(module_id, &sel.version, &sel.digest)?.clone();
            store.check_schema(&item)?;
            item
        };

        let existing_cell = {
            self.cells
                .lock()
                .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
                .get(module_id)
                .cloned()
        };
        if let Some(cell) = existing_cell {
            if !cell.transition.load(Ordering::SeqCst) {
                let slot = cell.slot.lock().await;
                if let Some(_session) = slot.session.as_ref() {
                    if !slot.awaiting_ui {
                        return Ok(PreparedModuleOpen::AlreadyOpen {
                            module_id: module_id.into(),
                            label: installed.manifest.label,
                            version: installed.manifest.version,
                            focus,
                        });
                    }
                }
            }
        }

        let focus_hint = if focus {
            "并切换到该页签界面"
        } else {
            "并在后台完成就绪初始化（不切换当前页面）"
        };
        let message = format!(
            "{} 请求打开功能页签「{}」（{} v{}）{}。\n\n仅激活已安装并启用的官方/受信任本地页签；打开后仍需单独授权其业务工具才可读取或操作数据。",
            caller_id, installed.manifest.label, module_id, installed.manifest.version, focus_hint
        );
        Ok(PreparedModuleOpen::NeedsApproval {
            message,
            module_id: module_id.into(),
            label: installed.manifest.label,
            version: installed.manifest.version,
            focus,
        })
    }

    /// Stage 2: Poll until the main window has mounted and confirmed UI readiness of `module_id`.
    pub async fn wait_module_ready_for_tools(
        &self,
        token: &str,
        module_id: &str,
        label: &str,
        version: &str,
        timeout_ms: u64,
    ) -> Result<Value, String> {
        let deadline = Instant::now() + Duration::from_millis(timeout_ms);
        let mut generation = String::new();
        let mut ready = false;
        while Instant::now() < deadline {
            self.ensure_running()?;
            let _ = self.tool_caller(token).await?;
            let maybe_cell = {
                self.cells
                    .lock()
                    .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
                    .get(module_id)
                    .cloned()
            };
            if let Some(cell) = maybe_cell {
                if !cell.transition.load(Ordering::SeqCst) {
                    let slot = cell.slot.lock().await;
                    if let Some(session) = slot.session.as_ref() {
                        if !slot.awaiting_ui {
                            generation = session.generation.clone();
                            ready = true;
                            break;
                        }
                    }
                }
            }
            tokio::time::sleep(Duration::from_millis(120)).await;
        }
        if !ready {
            return Err(format!(
                "MODULE_OPEN_TIMEOUT: 等待页签 {module_id} 就绪超时，请检查该页签是否加载失败。"
            ));
        }
        let tools: Vec<Value> = self
            .discover_tools(token)
            .await?
            .into_iter()
            .filter(|g| g.identity.module_id == module_id)
            .map(|g| g.public)
            .collect();
        self.diagnostics.record(
            "info",
            "module.tool_opened_module",
            &format!("target={module_id} version={version} tools={}", tools.len()),
        );
        Ok(json!({
            "moduleId": module_id,
            "label": label,
            "version": version,
            "opened": true,
            "ready": true,
            "generation": generation,
            "tools": tools,
        }))
    }

    pub async fn prepare_tool_grant(
        &self,
        token: &str,
        requested: &[ToolIdentity],
    ) -> Result<PreparedToolGrant, String> {
        if requested.is_empty() || requested.len() > MAX_TOOL_COUNT {
            return Err("TOOLS_GRANT_INVALID".into());
        }
        let caller = self.tool_caller(token).await?;
        let available = self.discover_tools(token).await?;
        let mut labels = Vec::new();
        let mut grants = Vec::new();
        let mut has_write = false;
        for identity in requested {
            let found = available
                .iter()
                .find(|g| &g.identity == identity)
                .ok_or("TOOLS_SELECTION_STALE")?;
            if grants.contains(found) {
                return Err("TOOLS_GRANT_INVALID".into());
            }
            let access_tag = match found.access {
                ToolAccess::Read => "只读",
                ToolAccess::UiNavigate => "导航",
                ToolAccess::Write => {
                    has_write = true;
                    "写操作·逐次确认"
                }
            };
            labels.push(format!(
                "[{}] {} · {} v{}",
                access_tag,
                found.public["title"].as_str().unwrap_or("工具"),
                identity.module_id,
                identity.version
            ));
            grants.push(found.clone());
        }
        let write_note = if has_write {
            "\n\n注意：标记为「写操作·逐次确认」的工具在每次实际执行前，仍会再次弹窗展示具体参数并由你确认。"
        } else {
            ""
        };
        let message = format!(
            "{} 请求在本次打开期间访问以下插件工具：\n\n{}\n\n调用结果将返回请求方；如用于云端 AI，可能发送给你选择的模型服务。关闭或切换插件后需重新授权。{}",
            caller["moduleId"].as_str().unwrap_or("插件"),
            labels.join("\n"),
            write_note
        );
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

    pub async fn prepare_tool_call(
        &self,
        token: &str,
        identity: ToolIdentity,
        arguments: Value,
    ) -> Result<PreparedToolCall, String> {
        let caller = self.tool_caller(token).await?;
        let caller_id = caller["moduleId"]
            .as_str()
            .ok_or("MODULE_SESSION_STALE")?
            .to_string();
        let caller_generation = caller["generation"]
            .as_str()
            .ok_or("MODULE_SESSION_STALE")?
            .to_string();
        if caller_id == identity.module_id {
            return Err("TOOLS_SELF_CALL_UNSUPPORTED".into());
        }
        let grant = self
            .tool_grants
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .get(token)
            .and_then(|items| items.iter().find(|g| g.identity == identity))
            .cloned()
            .ok_or("TOOLS_PERMISSION_REQUIRED")?;

        validate_arguments_against_schema(&grant.input_schema, &arguments)?;

        let approved = ApprovedToolCall {
            caller_token: token.into(),
            caller_id: caller_id.clone(),
            caller_generation,
            grant: grant.clone(),
            arguments: arguments.clone(),
        };

        if grant.access == ToolAccess::Write {
            let digest_short: String = identity.digest.chars().take(12).collect();
            let title = grant.public["title"].as_str().unwrap_or(&identity.name);
            let args_summary = format_args_summary(&arguments);
            let message = format!(
                "{caller_id} 请求执行写操作，请核对目标与参数：\n\n目标插件：{} v{}（摘要 {digest_short}…）\n操作名称：{title}（{}）\n参数：\n{args_summary}\n\n确认后将立即调用目标插件执行该操作；拒绝则不会产生任何修改。",
                identity.module_id, identity.version, identity.name
            );
            return Ok(PreparedToolCall::NeedsApproval {
                message,
                call: approved,
            });
        }

        Ok(PreparedToolCall::Direct(approved))
    }

    pub async fn execute_prepared_tool_call(
        &self,
        token: &str,
        approved: ApprovedToolCall,
    ) -> Result<Value, String> {
        if approved.caller_token != token {
            return Err("MODULE_SESSION_STALE".into());
        }
        let identity = &approved.grant.identity;
        let caller_cell = self.cell(&approved.caller_id)?;
        let cell = self.cell(&identity.module_id)?;
        if cell.transition.load(Ordering::SeqCst) {
            return Err("MODULE_SWITCHING".into());
        }
        let mut slot = cell.slot.lock().await;
        if cell.transition.load(Ordering::SeqCst) || slot.awaiting_ui {
            return Err("TOOLS_SELECTION_STALE".into());
        }
        let target = slot.session.as_mut().ok_or("TOOLS_SELECTION_STALE")?;
        if target.token != approved.grant.token
            || target.generation != identity.generation
            || target.installed.digest != identity.digest
            || target.installed.manifest.version != identity.version
        {
            return Err("TOOLS_SELECTION_STALE".into());
        }
        // Acquire no caller slot mutex while holding the target slot. A separate
        // short admission protects caller lifecycle without an A->B/B->A deadlock.
        let _admission = self.admit_tool_read(
            &caller_cell,
            token,
            &approved.caller_id,
            &approved.caller_generation,
        )?;
        if !self
            .tool_grants
            .lock()
            .map_err(|_| "MODULE_LOCK_UNAVAILABLE")?
            .get(token)
            .is_some_and(|items| items.contains(&approved.grant))
        {
            return Err("TOOLS_PERMISSION_REQUIRED".into());
        }
        let value = target
            .call(&approved.grant.method, approved.arguments)
            .await?;
        if !value.is_object()
            || serde_json::to_vec(&value)
                .map_err(|_| "TOOLS_RESULT_INVALID")?
                .len()
                > MAX_RESULT_BYTES
        {
            return Err("TOOLS_RESULT_TOO_LARGE".into());
        }
        self.tool_caller_identity_current(
            token,
            &approved.caller_id,
            &approved.caller_generation,
        )?;
        self.diagnostics.record(
            "info",
            "module.tool_completed",
            &format!(
                "caller={} target={} tool={} access={}",
                approved.caller_id,
                identity.module_id,
                identity.name,
                approved.grant.access.as_str()
            ),
        );
        Ok(json!({
            "source": {
                "moduleId": identity.module_id,
                "version": identity.version,
                "digest": identity.digest,
                "tool": identity.name,
                "access": approved.grant.access.as_str(),
            },
            "data": value
        }))
    }

    /// Non-interactive entry point used by tests and non-write paths; refuses write calls
    /// that did not pass through the native per-call confirmation dialog.
    #[allow(dead_code)]
    pub async fn call_tool(
        &self,
        token: &str,
        identity: ToolIdentity,
        arguments: Value,
    ) -> Result<Value, String> {
        match self.prepare_tool_call(token, identity, arguments).await? {
            PreparedToolCall::Direct(approved) => {
                self.execute_prepared_tool_call(token, approved).await
            }
            PreparedToolCall::NeedsApproval { .. } => {
                Err("TOOLS_WRITE_APPROVAL_REQUIRED".into())
            }
        }
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

    fn tool_v1() -> Value {
        json!({
            "name": "snapshot",
            "method": "example.snapshot",
            "title": "读取快照",
            "description": "读取当前实例的快照",
            "contractVersion": 1,
            "readOnly": true,
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": false}
        })
    }

    fn tool_v2_read() -> Value {
        json!({
            "name": "search",
            "method": "example.search",
            "title": "按关键词检索",
            "description": "按关键词只读检索条目",
            "contractVersion": 2,
            "access": "read",
            "readOnly": true,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 100},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50}
                },
                "required": ["query"],
                "additionalProperties": false
            }
        })
    }

    fn tool_v2_write() -> Value {
        json!({
            "name": "update_tag",
            "method": "example.tag",
            "title": "更新标签",
            "description": "修改目标条目的受控标签",
            "contractVersion": 2,
            "access": "write",
            "readOnly": false,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "itemId": {"type": "string", "maxLength": 64},
                    "tag": {"type": "string", "maxLength": 32}
                },
                "required": ["itemId", "tag"],
                "additionalProperties": false
            }
        })
    }

    #[test]
    fn requires_explicit_read_only_export_and_declared_method() {
        let methods = vec!["example.snapshot".into(), "example.search".into(), "example.tag".into()];
        assert!(descriptor(&tool_v1(), &methods).is_ok());
        assert!(descriptor(&tool_v1(), &[]).is_err());
        let mut t = tool_v1();
        t["readOnly"] = json!(false);
        assert!(descriptor(&t, &methods).is_err());
        t = tool_v1();
        t["inputSchema"]["properties"] = json!({"path":{"type":"string"}});
        assert!(descriptor(&t, &methods).is_err());
        t = tool_v1();
        t["title"] = json!("fake\nconfirmation");
        assert!(descriptor(&t, &methods).is_err());
        t = tool_v1();
        t["name"] = json!("../../exec");
        assert!(descriptor(&t, &methods).is_err());

        // v2 read & write accepted with bounded schema
        let (_, _, access_r, ver_r, _) = descriptor(&tool_v2_read(), &methods).unwrap();
        assert_eq!(access_r, ToolAccess::Read);
        assert_eq!(ver_r, 2);
        let (_, _, access_w, ver_w, _) = descriptor(&tool_v2_write(), &methods).unwrap();
        assert_eq!(access_w, ToolAccess::Write);
        assert_eq!(ver_w, 2);

        // v2 rejects forbidden argument names and nested object schemas
        let mut bad_arg = tool_v2_read();
        bad_arg["inputSchema"]["properties"]["command"] = json!({"type": "string"});
        assert!(descriptor(&bad_arg, &methods).is_err());

        let mut nested = tool_v2_read();
        nested["inputSchema"]["properties"]["extra"] = json!({"type": "object"});
        assert!(descriptor(&nested, &methods).is_err());

        let mut forbidden_method = tool_v2_read();
        forbidden_method["method"] = json!("example.exec");
        assert!(descriptor(&forbidden_method, &["example.exec".into()]).is_err());
    }

    #[test]
    fn runtime_argument_validation_enforces_schema_bounds_and_traversal_safety() {
        let schema = &tool_v2_read()["inputSchema"];
        assert!(validate_arguments_against_schema(schema, &json!({"query": "hello", "limit": 10})).is_ok());
        assert!(validate_arguments_against_schema(schema, &json!({})).is_err());
        assert!(validate_arguments_against_schema(schema, &json!({"query": "ok", "extra": 1})).is_err());
        assert!(validate_arguments_against_schema(schema, &json!({"query": "ok", "limit": 999})).is_err());
        assert!(validate_arguments_against_schema(schema, &json!({"query": "../secret"})).is_err());
        assert!(validate_arguments_against_schema(schema, &json!({"query": "line1\nline2"})).is_err());
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
            access: ToolAccess::Read,
            input_schema: tool_v1()["inputSchema"].clone(),
            public: tool_v1(),
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
    fn list_modules_for_tools_reports_enabled_siblings_without_self() {
        tauri::async_runtime::block_on(async {
            let (root, manager) = test_manager();
            let caller_digest = install(&manager, &root, "caller", "1.0.0");
            let sibling_digest = install(&manager, &root, "sibling", "2.0.0");
            manager.select("caller", "1.0.0", &caller_digest).await.unwrap();
            manager.set_enabled("caller", true).await.unwrap();
            manager.select("sibling", "2.0.0", &sibling_digest).await.unwrap();
            manager.set_enabled("sibling", true).await.unwrap();

            let session = manager.open("caller").await.unwrap();
            ready(&manager, &session).await;
            let token = session["token"].as_str().unwrap();

            let modules = manager.list_modules_for_tools(token).await.unwrap();
            let arr = modules.as_array().unwrap();
            assert_eq!(arr.len(), 1);
            assert_eq!(arr[0]["moduleId"], "sibling");
            assert_eq!(arr[0]["version"], "2.0.0");
            assert_eq!(arr[0]["opened"], false);
            assert_eq!(arr[0]["ready"], false);

            // Preparing open on an unopened sibling requires approval
            match manager.prepare_module_open(token, "sibling", false).await.unwrap() {
                PreparedModuleOpen::NeedsApproval { module_id, focus, .. } => {
                    assert_eq!(module_id, "sibling");
                    assert!(!focus);
                }
                PreparedModuleOpen::AlreadyOpen { .. } => panic!("expected approval for unopened module"),
            }
            // Self-open is refused
            assert!(manager.prepare_module_open(token, "caller", false).await.is_err());
        });
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
            let blocked = manager.admit_tool_read(&cell, token, "caller", generation);
            assert_eq!(blocked.err().as_deref(), Some("MODULE_SWITCHING"));
            drop(transition);

            let admitted = manager.admit_tool_read(&cell, token, "caller", generation);
            assert!(admitted.is_ok());
            assert!(Transition::begin(&cell)
                .err()
                .unwrap()
                .contains("MODULE_BUSY"));
            drop(admitted);

            manager.close(token).await.unwrap();
            let closed = manager.admit_tool_read(&cell, token, "caller", generation);
            assert_eq!(closed.err().as_deref(), Some("MODULE_SESSION_STALE"));
            assert_eq!(cell.active_tool_reads.load(Ordering::SeqCst), 0);
        });
    }

    #[test]
    fn write_tool_requires_per_call_approval_and_rejects_direct_call() {
        tauri::async_runtime::block_on(async {
            let (root, manager) = test_manager();
            let caller_digest = install(&manager, &root, "caller", "1.0.0");
            manager.select("caller", "1.0.0", &caller_digest).await.unwrap();
            manager.set_enabled("caller", true).await.unwrap();
            let session = manager.open("caller").await.unwrap();
            ready(&manager, &session).await;
            let token = session["token"].as_str().unwrap();

            let write_identity = ToolIdentity {
                name: "target__update_tag".into(),
                module_id: "target".into(),
                version: "1.0.0".into(),
                digest: "b".repeat(64),
                generation: "gen-1".into(),
            };
            let grant = ToolGrant {
                identity: write_identity.clone(),
                token: "target-token".into(),
                method: "example.tag".into(),
                access: ToolAccess::Write,
                input_schema: tool_v2_write()["inputSchema"].clone(),
                public: tool_v2_write(),
            };
            manager
                .tool_grants
                .lock()
                .unwrap()
                .insert(token.into(), vec![grant]);

            // Direct call without approval dialog must fail
            let err = manager
                .call_tool(
                    token,
                    write_identity.clone(),
                    json!({"itemId": "w1", "tag": "fav"}),
                )
                .await
                .unwrap_err();
            assert_eq!(err, "TOOLS_WRITE_APPROVAL_REQUIRED");

            // prepare_tool_call returns NeedsApproval with full argument summary
            match manager
                .prepare_tool_call(
                    token,
                    write_identity,
                    json!({"itemId": "w1", "tag": "fav"}),
                )
                .await
                .unwrap()
            {
                PreparedToolCall::NeedsApproval { message, .. } => {
                    assert!(message.contains("target v1.0.0"));
                    assert!(message.contains("w1"));
                    assert!(message.contains("fav"));
                }
                PreparedToolCall::Direct(_) => panic!("expected write call to require approval"),
            }
        });
    }
}
