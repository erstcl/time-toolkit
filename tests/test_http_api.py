from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from time_toolkit.config import ConfigStore
from time_toolkit.http_api import create_app


def test_http_adapter_is_read_only_and_requires_bearer(tmp_path: Path):
    config = ConfigStore(tmp_path / "config.json")
    config.add_profile("example", "https://time.example.test")
    client = TestClient(create_app(api_key="service-secret", config_store=config))

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["write_routes"] is False
    assert client.get("/v1/profiles").status_code == 401

    profiles = client.get("/v1/profiles", headers={"Authorization": "Bearer service-secret"})
    assert profiles.status_code == 200
    assert profiles.json()["data"][0]["name"] == "example"
    assert profiles.json()["data"][0]["base_url"] == "https://time.example.test"


def test_http_adapter_rejects_wrong_key(tmp_path: Path):
    config = ConfigStore(tmp_path / "config.json")
    client = TestClient(create_app(api_key="right-key", config_store=config))
    response = client.get("/v1/profiles", headers={"Authorization": "Bearer wrong-key"})
    assert response.status_code == 401
