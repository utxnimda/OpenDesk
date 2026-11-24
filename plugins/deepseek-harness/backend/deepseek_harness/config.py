"""Versioned, strictly validated configuration storage for the DeepSeek Harness plugin."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
MAX_PATH_LEN = 2048
MAX_TOKEN_LEN = 2048

LAUNCH_MODES = ("managed", "external")
DSH_SOURCES = ("auto", "custom")
SOURCE_LAUNCH_MODES = ("built", "dev")
HOME_MODES = ("isolated", "system", "custom")

ALLOWED_KEYS = frozenset({
    "schemaVersion",
    "launchMode",
    "dshSource",
    "sourceLaunchMode",
    "customDshPath",
    "nodePath",
    "homeMode",
    "customHomeDir",
    "workspaceDir",
    "profile",
    "autoStart",
    "externalUrl",
})


class ConfigError(ValueError):
    """Raised when configuration input or stored JSON violates the schema."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DshConfig:
    schemaVersion: int = SCHEMA_VERSION
    launchMode: str = "managed"
    dshSource: str = "auto"
    sourceLaunchMode: str = "built"
    customDshPath: str = ""
    nodePath: str = ""
    homeMode: str = "isolated"
    customHomeDir: str = ""
    workspaceDir: str = ""
    profile: str = "web"
    autoStart: bool = True
    externalUrl: str = ""

    def to_public_dict(self) -> dict:
        return asdict(self)


def _check_string(value, field: str, limit: int = MAX_PATH_LEN) -> str:
    if not isinstance(value, str):
        raise ConfigError("INVALID_CONFIG", f"{field} 必须是字符串。")
    if len(value) > limit or "\0" in value or any(ord(ch) < 32 for ch in value):
        raise ConfigError("INVALID_CONFIG", f"{field} 包含非法字符或超出长度限制。")
    return value.strip()


def _check_optional_abs_path(value, field: str) -> str:
    text = _check_string(value, field)
    if not text:
        return ""
    if not Path(text).is_absolute():
        raise ConfigError("INVALID_CONFIG", f"{field} 必须填写绝对路径。")
    return text


def validate_external_url(raw: str) -> tuple[str, int, str]:
    """Validate an external loopback dsh web URL; returns (origin, port, launch_token)."""
    text = _check_string(raw, "externalUrl", 4096)
    if not text:
        raise ConfigError("INVALID_CONFIG", "外部模式必须填写本地 dsh web 地址（如 http://127.0.0.1:3080/?token=...）。")
    parsed = urlsplit(text)
    if parsed.scheme != "http" or parsed.username or parsed.password:
        raise ConfigError("INVALID_CONFIG", "外部地址只允许无凭据的本机 http://127.0.0.1:<端口> 或 http://localhost:<端口>。")
    host = (parsed.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost"):
        raise ConfigError("INVALID_CONFIG", "外部地址只允许本机回环地址（127.0.0.1 或 localhost）。")
    port = parsed.port
    if not port or not (1 <= port <= 65535):
        raise ConfigError("INVALID_CONFIG", "外部地址必须包含有效端口（1-65535）。")
    token = ""
    if parsed.query:
        for pair in parsed.query.split("&"):
            if pair.startswith("token="):
                token = pair[6:]
                if len(token) > MAX_TOKEN_LEN or any(c.isspace() or ord(c) < 32 for c in token):
                    raise ConfigError("INVALID_CONFIG", "外部地址中的 token 无效。")
                break
    return (f"http://127.0.0.1:{port}", port, token)


def validate_config_data(raw: dict) -> DshConfig:
    if not isinstance(raw, dict):
        raise ConfigError("INVALID_CONFIG", "配置格式必须是 JSON 对象。")
    unknown = set(raw.keys()) - ALLOWED_KEYS
    if unknown:
        raise ConfigError("INVALID_CONFIG", f"配置包含未知字段：{', '.join(sorted(unknown))}。")

    schema_version = raw.get("schemaVersion", SCHEMA_VERSION)
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise ConfigError(
            "CONFIG_SCHEMA_UNSUPPORTED",
            f"不支持的配置版本 {schema_version!r}，已保留原配置文件。",
        )

    launch_mode = _check_string(raw.get("launchMode", "managed"), "launchMode", 32)
    if launch_mode not in LAUNCH_MODES:
        raise ConfigError("INVALID_CONFIG", f"launchMode 仅支持 {', '.join(LAUNCH_MODES)}。")

    dsh_source = _check_string(raw.get("dshSource", "auto"), "dshSource", 32)
    if dsh_source not in DSH_SOURCES:
        raise ConfigError("INVALID_CONFIG", f"dshSource 仅支持 {', '.join(DSH_SOURCES)}。")

    source_launch_mode = _check_string(raw.get("sourceLaunchMode", "built"), "sourceLaunchMode", 32) or "built"
    if source_launch_mode not in SOURCE_LAUNCH_MODES:
        raise ConfigError("INVALID_CONFIG", f"sourceLaunchMode 仅支持 {', '.join(SOURCE_LAUNCH_MODES)}。")

    custom_dsh_path = _check_optional_abs_path(raw.get("customDshPath", ""), "customDshPath")
    node_path = _check_optional_abs_path(raw.get("nodePath", ""), "nodePath")

    home_mode = _check_string(raw.get("homeMode", "isolated"), "homeMode", 32)
    if home_mode not in HOME_MODES:
        raise ConfigError("INVALID_CONFIG", f"homeMode 仅支持 {', '.join(HOME_MODES)}。")

    custom_home_dir = _check_optional_abs_path(raw.get("customHomeDir", ""), "customHomeDir")
    workspace_dir = _check_optional_abs_path(raw.get("workspaceDir", ""), "workspaceDir")

    profile = _check_string(raw.get("profile", "web"), "profile", 64) or "web"
    if not PROFILE_RE.match(profile):
        raise ConfigError("INVALID_CONFIG", "profile 名称仅允许字母、数字、点、下划线和短横线。")
    if profile.lower() == "desktop":
        raise ConfigError("INVALID_CONFIG", "desktop 是 DSH 官方桌面客户端保留名称，CLI 拒绝启动该 profile。")

    auto_start = raw.get("autoStart", True)
    if not isinstance(auto_start, bool):
        raise ConfigError("INVALID_CONFIG", "autoStart 必须是布尔值。")

    external_url = _check_string(raw.get("externalUrl", ""), "externalUrl", 4096)

    if dsh_source == "custom" and launch_mode == "managed" and not custom_dsh_path:
        raise ConfigError("INVALID_CONFIG", "自定义 dsh 来源必须填写 dsh 仓库目录或入口文件的绝对路径。")
    if home_mode == "custom" and launch_mode == "managed" and not custom_home_dir:
        raise ConfigError("INVALID_CONFIG", "自定义 DSH_HOME 模式必须填写绝对目录路径。")
    if launch_mode == "external":
        validate_external_url(external_url)

    return DshConfig(
        schemaVersion=SCHEMA_VERSION,
        launchMode=launch_mode,
        dshSource=dsh_source,
        sourceLaunchMode=source_launch_mode,
        customDshPath=custom_dsh_path,
        nodePath=node_path,
        homeMode=home_mode,
        customHomeDir=custom_home_dir,
        workspaceDir=workspace_dir,
        profile=profile,
        autoStart=auto_start,
        externalUrl=external_url,
    )


def redact_external_url(url: str) -> str:
    """Return a display-safe version of an external URL without leaking its launch token."""
    if not url:
        return ""
    parsed = urlsplit(url)
    if not parsed.query:
        return url
    safe_pairs = []
    for pair in parsed.query.split("&"):
        if pair.startswith("token="):
            safe_pairs.append("token=***")
        else:
            safe_pairs.append(pair)
    query = "&".join(safe_pairs)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    return f"{base}?{query}" if query else base


class ConfigStore:
    """Single-authoritative, atomic JSON config store. Never overwrites corrupt/future data."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "config.json"

    def load(self) -> DshConfig:
        if not self.path.exists():
            return DshConfig()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfigError(
                "CONFIG_CORRUPTED",
                f"配置文件损坏或无法读取（已保留原文件，不会覆盖）：{error}",
            ) from error
        return validate_config_data(raw)

    def save(self, config: DshConfig) -> DshConfig:
        validated = validate_config_data(config.to_public_dict())
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(validated.to_public_dict(), ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=str(self.data_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return validated
