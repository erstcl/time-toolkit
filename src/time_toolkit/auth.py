from __future__ import annotations

import os
import re
from dataclasses import dataclass

from time_toolkit.errors import AuthenticationError, ConfigError

_SERVICE = "dev.erstcl.time-toolkit"
_SERVICE_API_USERNAME = "service:http-api"


@dataclass(frozen=True, slots=True)
class AuthContext:
    token: str
    method: str = "bearer"
    csrf_token: str = ""

    def headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        if self.method == "cookie":
            headers = {
                "Cookie": f"MMAUTHTOKEN={self.token}",
                "X-Requested-With": "XMLHttpRequest",
            }
            if self.csrf_token:
                headers["X-CSRF-Token"] = self.csrf_token
            return headers
        return {"Authorization": f"Bearer {self.token}"}

    def websocket_headers(self) -> dict[str, str]:
        headers = self.headers()
        if self.token and "Cookie" not in headers:
            headers["Cookie"] = f"MMAUTHTOKEN={self.token}"
            headers["X-Requested-With"] = "XMLHttpRequest"
        return headers


def _environment_key(profile: str, kind: str) -> str:
    safe = re.sub(r"[^A-Z0-9]", "_", profile.upper())
    suffix = "TOKEN" if kind == "token" else "CSRF"
    return f"TIME_TOOLKIT_{safe}_{suffix}"


class SecretStore:
    """Tokens from environment overrides or the operating-system keychain."""

    def _keyring(self):
        try:
            import keyring
        except ImportError as exc:
            raise ConfigError("Keyring support is not installed") from exc
        return keyring

    @staticmethod
    def _username(profile: str, kind: str) -> str:
        return f"{profile}:{kind}"

    def get(self, profile: str, kind: str = "token") -> str:
        override = os.environ.get(_environment_key(profile, kind), "")
        if override:
            return override
        try:
            return self._keyring().get_password(_SERVICE, self._username(profile, kind)) or ""
        except Exception as exc:
            raise ConfigError("Cannot access the operating-system keychain") from exc

    def set(self, profile: str, value: str, kind: str = "token") -> None:
        secret = value.strip()
        if not secret:
            raise ConfigError("Secret cannot be empty")
        try:
            self._keyring().set_password(_SERVICE, self._username(profile, kind), secret)
        except Exception as exc:
            raise ConfigError("Cannot write to the operating-system keychain") from exc

    def delete(self, profile: str, kind: str = "token") -> None:
        try:
            self._keyring().delete_password(_SERVICE, self._username(profile, kind))
        except Exception as exc:
            name = exc.__class__.__name__
            if name not in {"PasswordDeleteError", "KeyringError"}:
                raise ConfigError("Cannot remove the secret from the keychain") from exc

    def context(self, profile: str, method: str) -> AuthContext:
        token = self.get(profile)
        if not token:
            env_key = _environment_key(profile, "token")
            raise AuthenticationError(
                f"No token for profile {profile}; run `timetk -p {profile} auth set` "
                f"or set {env_key}"
            )
        csrf = self.get(profile, "csrf") if method == "cookie" else ""
        return AuthContext(token=token, method=method, csrf_token=csrf)

    def get_service_api_key(self) -> str:
        override = os.environ.get("TIME_TOOLKIT_SERVICE_API_KEY", "")
        if override:
            return override
        try:
            return self._keyring().get_password(_SERVICE, _SERVICE_API_USERNAME) or ""
        except Exception as exc:
            raise ConfigError("Cannot access the operating-system keychain") from exc

    def set_service_api_key(self, value: str) -> None:
        secret = value.strip()
        if not secret:
            raise ConfigError("Service API key cannot be empty")
        try:
            self._keyring().set_password(_SERVICE, _SERVICE_API_USERNAME, secret)
        except Exception as exc:
            raise ConfigError("Cannot write to the operating-system keychain") from exc

    def delete_service_api_key(self) -> None:
        try:
            self._keyring().delete_password(_SERVICE, _SERVICE_API_USERNAME)
        except Exception as exc:
            if exc.__class__.__name__ != "PasswordDeleteError":
                raise ConfigError("Cannot remove the service key from the keychain") from exc
