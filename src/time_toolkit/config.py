from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

from time_toolkit.errors import ConfigError, NotFoundError

_PROFILE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
WritePolicy = Literal["readonly", "approval", "fullauto"]
WRITE_POLICIES = ("readonly", "approval", "fullauto")


def _profiles(data: dict[str, object]) -> dict[str, object]:
    if "profiles" not in data:
        data["profiles"] = {}
    profiles = data["profiles"]
    if not isinstance(profiles, dict):
        raise ConfigError("Configuration profiles must be an object")
    return profiles


def default_config_dir() -> Path:
    override = os.environ.get("TIME_TOOLKIT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "time-toolkit"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (
        Path(xdg).expanduser() / "time-toolkit" if xdg else Path.home() / ".config" / "time-toolkit"
    )


def normalize_base_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    try:
        parsed = urlparse(raw)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("Server URL is malformed") from exc
    if (
        parsed.scheme not in {"https", "http"}
        or not hostname
        or any(character.isspace() for character in hostname)
    ):
        raise ConfigError("Server URL must include http:// or https:// and a hostname")
    if parsed.username or parsed.password:
        raise ConfigError("Server URL must not contain a username or password")
    if port == 0:
        raise ConfigError("Server URL port must be between 1 and 65535")
    if parsed.scheme == "http" and hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ConfigError("Non-local Time profiles must use HTTPS")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigError("Server URL must not contain a path, query, or fragment")
    return raw


def normalize_write_policy(value: object) -> WritePolicy:
    policy = str(value).strip().lower()
    if policy not in WRITE_POLICIES:
        choices = ", ".join(WRITE_POLICIES)
        raise ConfigError(f"Write policy must be one of: {choices}")
    return cast(WritePolicy, policy)


def normalize_websocket_host(value: object) -> str:
    raw = str(value).strip().lower().rstrip(".")
    if (
        not raw
        or "://" in raw
        or any(character in raw for character in "/?#@")
        or any(character.isspace() for character in raw)
    ):
        raise ConfigError("WebSocket host must be a hostname with an optional port")
    try:
        parsed = urlparse(f"wss://{raw}")
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("WebSocket host is malformed") from exc
    if not hostname or parsed.username or parsed.password:
        raise ConfigError("WebSocket host must be a hostname with an optional port")
    if port == 0:
        raise ConfigError("WebSocket host port must be between 1 and 65535")
    hostname = hostname.lower().rstrip(".")
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return f"{hostname}:{port}" if port is not None else hostname


def validate_profile_name(value: str) -> str:
    name = value.strip().lower()
    if not _PROFILE_RE.fullmatch(name):
        raise ConfigError(
            "Profile name must start with a letter and contain only letters, digits, _ or -"
        )
    return name


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    base_url: str
    team_id: str = ""
    auth_method: str = "bearer"
    timezone: str = "Europe/Moscow"
    mcp_enabled: bool = True
    write_policy: WritePolicy = "approval"
    allowed_websocket_hosts: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, name: str, value: dict[str, object]) -> Profile:
        raw_policy = value.get("write_policy")
        if raw_policy is None:
            raw_policy = "fullauto" if value.get("automated_writes_enabled") is True else "approval"
        raw_hosts = value.get("allowed_websocket_hosts", ())
        if not isinstance(raw_hosts, list | tuple):
            raise ConfigError("allowed_websocket_hosts must be a list")
        return cls(
            name=name,
            base_url=normalize_base_url(str(value.get("base_url", ""))),
            team_id=str(value.get("team_id", "")),
            auth_method=str(value.get("auth_method", "bearer")),
            timezone=str(value.get("timezone", "Europe/Moscow")),
            mcp_enabled=bool(value.get("mcp_enabled", True)),
            write_policy=normalize_write_policy(raw_policy),
            allowed_websocket_hosts=tuple(
                dict.fromkeys(normalize_websocket_host(item) for item in raw_hosts)
            ),
        )

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data.pop("name")
        return data


class ConfigStore:
    """Small atomic JSON store. Secrets deliberately live elsewhere."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_config_dir() / "config.json"

    def load(self) -> dict[str, object]:
        if not self.path.exists():
            return {"version": 1, "default_profile": "", "profiles": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Cannot read configuration: {self.path}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("profiles", {}), dict):
            raise ConfigError(f"Invalid configuration format: {self.path}")
        return data

    def save(self, data: dict[str, object]) -> None:
        data["version"] = 2
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=".config-",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def list_profiles(self) -> list[Profile]:
        data = self.load()
        profiles = _profiles(data)
        return [Profile.from_dict(name, value) for name, value in sorted(profiles.items())]

    def get_profile(self, name: str | None = None) -> Profile:
        data = self.load()
        selected = validate_profile_name(name) if name else str(data.get("default_profile", ""))
        if not selected:
            raise ConfigError("No profile selected; pass --profile or set a default profile")
        profiles = _profiles(data)
        raw = profiles.get(selected)
        if not isinstance(raw, dict):
            raise NotFoundError(f"Profile not found: {selected}")
        return Profile.from_dict(selected, raw)

    def add_profile(
        self,
        name: str,
        base_url: str,
        *,
        team_id: str = "",
        timezone: str = "Europe/Moscow",
        mcp_enabled: bool = True,
        write_policy: WritePolicy = "approval",
        allowed_websocket_hosts: tuple[str, ...] | list[str] = (),
    ) -> Profile:
        clean_name = validate_profile_name(name)
        profile = Profile(
            name=clean_name,
            base_url=normalize_base_url(base_url),
            team_id=team_id.strip(),
            timezone=timezone.strip() or "Europe/Moscow",
            mcp_enabled=mcp_enabled,
            write_policy=normalize_write_policy(write_policy),
            allowed_websocket_hosts=tuple(
                dict.fromkeys(normalize_websocket_host(item) for item in allowed_websocket_hosts)
            ),
        )
        data = self.load()
        profiles = _profiles(data)
        if clean_name in profiles:
            raise ConfigError(f"Profile already exists: {clean_name}")
        profiles[clean_name] = profile.to_dict()
        if not data.get("default_profile"):
            data["default_profile"] = clean_name
        self.save(data)
        return profile

    def update_profile(self, profile: Profile) -> None:
        normalized = Profile.from_dict(profile.name, profile.to_dict())
        data = self.load()
        profiles = _profiles(data)
        if profile.name not in profiles:
            raise NotFoundError(f"Profile not found: {profile.name}")
        profiles[profile.name] = normalized.to_dict()
        self.save(data)

    def set_default(self, name: str) -> Profile:
        profile = self.get_profile(name)
        data = self.load()
        data["default_profile"] = profile.name
        self.save(data)
        return profile

    def remove_profile(self, name: str) -> Profile:
        profile = self.get_profile(name)
        data = self.load()
        profiles = _profiles(data)
        del profiles[profile.name]
        if data.get("default_profile") == profile.name:
            data["default_profile"] = sorted(profiles)[0] if profiles else ""
        self.save(data)
        return profile

    def default_profile_name(self) -> str:
        return str(self.load().get("default_profile", ""))
