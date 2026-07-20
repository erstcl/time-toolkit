import secrets
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from time_toolkit import __version__
from time_toolkit.auth import SecretStore
from time_toolkit.config import ConfigStore
from time_toolkit.dates import parse_time_bound
from time_toolkit.errors import ConfigError, TimeToolkitError
from time_toolkit.models import primitive
from time_toolkit.service import TimeService


def _envelope(profile: str, server: str, data: Any, **meta: Any) -> dict[str, Any]:
    return primitive(
        {
            "schema_version": "1.0",
            "profile": profile,
            "server": server,
            "data": data,
            "meta": meta,
        }
    )


def create_app(
    *,
    api_key: str | None = None,
    config_store: ConfigStore | None = None,
    secret_store: SecretStore | None = None,
) -> FastAPI:
    """Create a localhost-oriented, authenticated, read-only HTTP adapter."""
    config = config_store or ConfigStore()
    stored_key = api_key or (secret_store or SecretStore()).get_service_api_key()
    if not stored_key:
        raise ConfigError("No HTTP service key; run `timetk service-key create`")

    app = FastAPI(
        title="Time Toolkit API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    bearer = HTTPBearer(auto_error=False)

    def authorize(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(bearer),
        ],
    ) -> None:
        valid = (
            credentials is not None
            and credentials.scheme.casefold() == "bearer"
            and secrets.compare_digest(credentials.credentials, stored_key)
        )
        if not valid:
            raise HTTPException(
                status_code=401,
                detail="Invalid or missing service API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

    Authenticated = Annotated[None, Depends(authorize)]

    @app.exception_handler(TimeToolkitError)
    async def time_toolkit_error(_: Request, exc: TimeToolkitError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code or 400,
            content={
                "schema_version": "1.0",
                "error": exc.message,
                "code": int(exc.exit_code),
                "details": primitive(exc.details),
            },
        )

    def run(profile: str, action: Callable[[TimeService], Any]) -> dict[str, Any]:
        with TimeService.open(profile, config=config) as service:
            return _envelope(profile, service.profile.base_url, action(service), read_only=True)

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "write_routes": False}

    @app.get("/v1/profiles", dependencies=[Depends(authorize)])
    def profiles() -> dict[str, Any]:
        default = config.default_profile_name()
        rows = [
            {**primitive(profile), "default": profile.name == default}
            for profile in config.list_profiles()
        ]
        return _envelope("", "", rows, read_only=True)

    @app.get("/v1/{profile}/me")
    def me(profile: str, _: Authenticated) -> dict[str, Any]:
        return run(profile, lambda service: service.me())

    @app.get("/v1/{profile}/channels")
    def channels(
        profile: str,
        _: Authenticated,
        pattern: str = "",
        channel_type: str = "",
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        return run(
            profile,
            lambda service: service.list_channels(
                pattern=pattern,
                channel_type=channel_type,
                limit=limit,
                max_pages=20,
            ),
        )

    @app.get("/v1/{profile}/unread")
    def unread(
        profile: str,
        _: Authenticated,
        channel: str = "",
        include_posts: bool = False,
        mentions_only: bool = False,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        return run(
            profile,
            lambda service: service.unread(
                channel=channel,
                with_posts=include_posts,
                mentions_only=mentions_only,
                limit=limit,
            ),
        )

    def bounds(service: TimeService, since: str, until: str) -> tuple[int | None, int | None]:
        return (
            parse_time_bound(since or None, timezone_name=service.profile.timezone),
            parse_time_bound(
                until or None,
                end_of_day=True,
                timezone_name=service.profile.timezone,
            ),
        )

    @app.get("/v1/{profile}/channel-posts")
    def channel_posts(
        profile: str,
        _: Authenticated,
        channel: str,
        since: str = "",
        until: str = "",
        authors: Annotated[list[str] | None, Query()] = None,
        contains: str = "",
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        def read(service: TimeService):
            since_ms, until_ms = bounds(service, since, until)
            return service.channel_posts(
                channel,
                since_ms=since_ms,
                until_ms=until_ms,
                authors=authors,
                contains=contains,
                limit=limit,
            )

        return run(profile, read)

    @app.get("/v1/{profile}/search")
    def search(
        profile: str,
        _: Authenticated,
        query: str = "",
        channels: Annotated[list[str] | None, Query()] = None,
        authors: Annotated[list[str] | None, Query()] = None,
        since: str = "",
        until: str = "",
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        def read(service: TimeService):
            since_ms, until_ms = bounds(service, since, until)
            return service.search_posts(
                query,
                channels=channels,
                authors=authors,
                since_ms=since_ms,
                until_ms=until_ms,
                limit=limit,
            )

        return run(profile, read)

    @app.get("/v1/{profile}/thread")
    def thread(profile: str, _: Authenticated, target: str) -> dict[str, Any]:
        return run(profile, lambda service: service.thread(target))

    @app.get("/v1/{profile}/followed-threads")
    def followed_threads(
        profile: str,
        _: Authenticated,
        include_posts: bool = False,
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        return run(
            profile,
            lambda service: service.followed_threads(limit=limit, with_posts=include_posts),
        )

    @app.get("/v1/{profile}/flagged")
    def flagged(
        profile: str,
        _: Authenticated,
        channel: str = "",
        limit: int = Query(default=100, ge=1, le=200),
    ) -> dict[str, Any]:
        return run(profile, lambda service: service.flagged_posts(channel=channel, limit=limit))

    @app.get("/v1/{profile}/pinned")
    def pinned(profile: str, _: Authenticated, channel: str) -> dict[str, Any]:
        return run(profile, lambda service: service.pinned_posts(channel))

    @app.get("/v1/{profile}/user-activity")
    def user_activity(
        profile: str,
        _: Authenticated,
        username: str,
        since: str = "",
        until: str = "",
        limit: int = Query(default=200, ge=1, le=200),
    ) -> dict[str, Any]:
        def read(service: TimeService):
            since_ms, until_ms = bounds(service, since, until)
            return service.user_activity(
                username, since_ms=since_ms, until_ms=until_ms, limit=limit
            )

        return run(profile, read)

    @app.get("/v1/{profile}/reactions")
    def reactions(profile: str, _: Authenticated, target: str) -> dict[str, Any]:
        return run(profile, lambda service: service.reactions(target))

    @app.get("/v1/{profile}/file-info")
    def file_info(profile: str, _: Authenticated, file_id: str) -> dict[str, Any]:
        return run(profile, lambda service: service.file_info(file_id))

    return app
