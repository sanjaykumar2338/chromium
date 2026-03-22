from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ConfigError(Exception):
    """Raised when config parsing or validation fails."""


_SUPPORTED_BROWSER_TYPES = {
    "chrome": "Google Chrome",
    "chromium": "Chromium",
    "chrome_for_testing": "Chrome for Testing",
}


@dataclass(slots=True)
class AppConfig:
    chrome_path: Path
    instances: int
    profiles_root: Path
    browser_type: str = "chrome"
    profile_mode: str = "auto_detect"
    cycle_existing_profiles: bool = True
    extension_folders: list[Path] = field(default_factory=list)
    use_proxy: bool = False
    proxy_server: str | None = None
    window_width: int = 1280
    window_height: int = 800
    cleanup_cache_on_start: bool = False
    relaunch_delay_seconds: float = 2.0
    check_interval_seconds: float = 2.0
    extra_chrome_flags: list[str] = field(default_factory=list)
    log_file: Path = Path("chrome_profile_manager.log")

    @property
    def browser_display_name(self) -> str:
        return _SUPPORTED_BROWSER_TYPES.get(self.browser_type, self.browser_type)

    @property
    def extension_auto_load_expected(self) -> bool:
        return self.browser_type in {"chromium", "chrome_for_testing"}


def load_config(config_path: str | Path) -> AppConfig:
    path = Path(config_path).expanduser()
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in config: {exc}") from exc

    return _validate(data, path.parent)


def _validate(raw: dict[str, Any], base_dir: Path) -> AppConfig:
    browser_type = _validate_browser_type(raw.get("browser_type", "chrome"))
    chrome_path = _resolve_browser_executable(raw.get("chrome_path"), base_dir)
    instances = _require_int(raw.get("instances"), "instances", min_value=1)
    profiles_root = _resolve_dir_path(raw.get("profiles_root"), "profiles_root", base_dir)
    profile_mode = _validate_profile_mode(raw.get("profile_mode", "auto_detect"))
    cycle_existing_profiles = bool(raw.get("cycle_existing_profiles", True))

    extension_folders = _resolve_extension_folders(raw.get("extension_folders"), base_dir)
    use_proxy = bool(raw.get("use_proxy", False))
    proxy_server = _validate_proxy_server(use_proxy, raw.get("proxy_server"))

    window_width = _require_int(raw.get("window_width", 1280), "window_width", min_value=320)
    window_height = _require_int(raw.get("window_height", 800), "window_height", min_value=240)
    cleanup_cache_on_start = bool(raw.get("cleanup_cache_on_start", False))
    relaunch_delay_seconds = _require_float(
        raw.get("relaunch_delay_seconds", 2.0),
        "relaunch_delay_seconds",
        min_value=0.0,
    )
    check_interval_seconds = _require_float(
        raw.get("check_interval_seconds", 2.0),
        "check_interval_seconds",
        min_value=0.2,
    )

    extra_chrome_flags_raw = raw.get("extra_chrome_flags", [])
    if not isinstance(extra_chrome_flags_raw, list) or not all(
        isinstance(item, str) for item in extra_chrome_flags_raw
    ):
        raise ConfigError("extra_chrome_flags must be a list of strings.")
    extra_chrome_flags = [item.strip() for item in extra_chrome_flags_raw if item.strip()]

    log_file = _resolve_output_path(raw.get("log_file", "chrome_profile_manager.log"), base_dir)

    return AppConfig(
        chrome_path=chrome_path,
        instances=instances,
        profiles_root=profiles_root,
        browser_type=browser_type,
        profile_mode=profile_mode,
        cycle_existing_profiles=cycle_existing_profiles,
        extension_folders=extension_folders,
        use_proxy=use_proxy,
        proxy_server=proxy_server,
        window_width=window_width,
        window_height=window_height,
        cleanup_cache_on_start=cleanup_cache_on_start,
        relaunch_delay_seconds=relaunch_delay_seconds,
        check_interval_seconds=check_interval_seconds,
        extra_chrome_flags=extra_chrome_flags,
        log_file=log_file,
    )


def _resolve_browser_executable(value: Any, base_dir: Path) -> Path:
    if not value or not isinstance(value, str):
        raise ConfigError(
            "chrome_path is required and must be a string path to a Chromium-compatible "
            "browser executable."
        )

    path = _resolve_path(value, base_dir)
    if not path.is_file():
        raise ConfigError(
            f"chrome_path does not exist or is not a browser executable file: {path}"
        )
    return path


def _resolve_file(value: Any, key: str, base_dir: Path) -> Path:
    if not value or not isinstance(value, str):
        raise ConfigError(f"{key} is required and must be a string path.")

    path = _resolve_path(value, base_dir)
    if not path.is_file():
        raise ConfigError(f"{key} does not exist or is not a file: {path}")
    return path


def _resolve_dir_path(value: Any, key: str, base_dir: Path) -> Path:
    if not value or not isinstance(value, str):
        raise ConfigError(f"{key} is required and must be a string path.")

    path = _resolve_path(value, base_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_output_path(value: Any, base_dir: Path) -> Path:
    if not value or not isinstance(value, str):
        raise ConfigError("log_file must be a string path.")
    path = _resolve_path(value, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_extension_folders(value: Any, base_dir: Path) -> list[Path]:
    if value in (None, "", []):
        return []

    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        candidates = value
    else:
        raise ConfigError("extension_folders must be a string or list of strings.")

    resolved: list[Path] = []
    for folder in candidates:
        if not folder.strip():
            continue
        path = _resolve_path(folder.strip(), base_dir)
        resolved.append(path)
    return resolved


def _validate_profile_mode(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("profile_mode must be a non-empty string.")

    mode = value.strip().lower()
    if mode != "auto_detect":
        raise ConfigError("profile_mode currently supports only 'auto_detect'.")
    return mode


def _validate_browser_type(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("browser_type must be a non-empty string.")

    browser_type = value.strip().lower()
    if browser_type not in _SUPPORTED_BROWSER_TYPES:
        supported = ", ".join(sorted(_SUPPORTED_BROWSER_TYPES))
        raise ConfigError(f"browser_type must be one of: {supported}.")
    return browser_type


def _validate_proxy_server(use_proxy: bool, value: Any) -> str | None:
    if not use_proxy:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip() or None
        raise ConfigError("proxy_server must be a string when provided.")

    if not isinstance(value, str) or not value.strip():
        raise ConfigError("proxy_server is required when use_proxy is true.")

    return _normalize_proxy_server(value.strip())


def _normalize_proxy_server(proxy_value: str) -> str:
    if "://" in proxy_value:
        parsed = urlparse(proxy_value)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https", "socks4", "socks5"}:
            raise ConfigError(
                "proxy_server scheme must be one of: http, https, socks4, socks5."
            )
        if parsed.hostname is None or parsed.port is None:
            raise ConfigError("proxy_server must include host and port.")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ConfigError("proxy_server must not include path, query, or fragment.")

        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{scheme}://{host}:{parsed.port}"

    host, sep, port = proxy_value.rpartition(":")
    if not sep or not host or not port:
        raise ConfigError("proxy_server must be in 'host:port' or 'scheme://host:port' format.")
    if any(char.isspace() for char in host) or "/" in host or "\\" in host:
        raise ConfigError("proxy_server host contains invalid characters.")
    if not port.isdigit():
        raise ConfigError("proxy_server port must be numeric.")

    port_num = int(port)
    if not (1 <= port_num <= 65535):
        raise ConfigError("proxy_server port must be between 1 and 65535.")
    return f"{host}:{port_num}"


def _require_int(value: Any, key: str, min_value: int | None = None) -> int:
    if type(value) is not int:
        raise ConfigError(f"{key} must be an integer.")
    if min_value is not None and value < min_value:
        raise ConfigError(f"{key} must be >= {min_value}.")
    return value


def _require_float(value: Any, key: str, min_value: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a number.")
    result = float(value)
    if min_value is not None and result < min_value:
        raise ConfigError(f"{key} must be >= {min_value}.")
    return result


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()
