from __future__ import annotations

import json
import stat

import pytest

from time_toolkit.config import ConfigStore, Profile, default_config_dir, normalize_base_url
from time_toolkit.errors import ConfigError, NotFoundError


def test_default_config_directory_uses_time_toolkit_name(monkeypatch):
    monkeypatch.delenv("TIME_TOOLKIT_CONFIG_DIR", raising=False)
    monkeypatch.setattr("time_toolkit.config.sys.platform", "darwin")

    assert default_config_dir().name == "time-toolkit"


def test_profiles_are_isolated_and_first_becomes_default(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    primary = store.add_profile("primary", "https://time.example.test/")
    secondary = store.add_profile("secondary", "https://chat.example.test")

    assert primary.base_url == "https://time.example.test"
    assert primary.write_policy == "approval"
    assert store.get_profile().name == "primary"
    assert [profile.name for profile in store.list_profiles()] == ["primary", "secondary"]

    store.set_default("secondary")
    assert store.get_profile().name == secondary.name


def test_configuration_contains_no_secret_and_is_owner_only(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.add_profile("example", "https://time.example.test")

    payload = json.loads(path.read_text())
    assert "token" not in json.dumps(payload).lower()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_profile_lookup_and_url_validation(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    with pytest.raises(NotFoundError):
        store.get_profile("missing")
    with pytest.raises(ConfigError):
        normalize_base_url("http://time.example.com")
    with pytest.raises(ConfigError):
        normalize_base_url("https://time.example.com/api/v4")
    with pytest.raises(ConfigError):
        normalize_base_url("https://user:password@time.example.com")
    with pytest.raises(ConfigError):
        normalize_base_url("https://[::1")
    with pytest.raises(ConfigError):
        normalize_base_url("https://time example.com")
    assert normalize_base_url("http://localhost:8065") == "http://localhost:8065"


def test_write_policy_is_generic_and_validated(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    automatic = store.add_profile(
        "automatic",
        "https://time.example.test",
        write_policy="fullauto",
    )
    assert store.get_profile("automatic").write_policy == "fullauto"
    assert automatic.to_dict()["write_policy"] == "fullauto"
    with pytest.raises(ConfigError, match="Write policy must be one of"):
        store.add_profile(
            "invalid",
            "https://time.example.test",
            write_policy="unrestricted",  # type: ignore[arg-type]
        )


def test_legacy_automated_write_setting_is_migrated_in_memory(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "default_profile": "manual",
                "profiles": {
                    "manual": {
                        "base_url": "https://time.example.test",
                        "automated_writes_enabled": False,
                    },
                    "automatic": {
                        "base_url": "https://chat.example.test",
                        "automated_writes_enabled": True,
                    },
                },
            }
        )
    )
    store = ConfigStore(path)

    assert store.get_profile("manual").write_policy == "approval"
    assert store.get_profile("automatic").write_policy == "fullauto"


def test_websocket_hosts_are_normalized_and_serialized(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    profile = store.add_profile(
        "example",
        "https://time.example.test",
        allowed_websocket_hosts=["Socket.Example.Test.", "socket.example.test", "ws.example:8443"],
    )

    assert profile.allowed_websocket_hosts == ("socket.example.test", "ws.example:8443")
    assert store.get_profile("example") == profile
    with pytest.raises(ConfigError, match="hostname"):
        store.update_profile(
            Profile(
                "example",
                "https://time.example.test",
                allowed_websocket_hosts=("https://evil.example",),
            )
        )
    with pytest.raises(ConfigError, match="malformed"):
        store.update_profile(
            Profile(
                "example",
                "https://time.example.test",
                allowed_websocket_hosts=("[::1",),
            )
        )
