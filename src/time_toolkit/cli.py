from __future__ import annotations

import argparse
import getpass
import json
import logging
import secrets
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from time_toolkit import __version__
from time_toolkit.auth import AuthContext, SecretStore
from time_toolkit.client import TimeClient
from time_toolkit.config import WRITE_POLICIES, ConfigStore, normalize_base_url
from time_toolkit.dates import parse_time_bound
from time_toolkit.errors import ConfirmationRequired, ExitCode, TimeToolkitError, UsageError
from time_toolkit.models import primitive
from time_toolkit.output import emit
from time_toolkit.service import TimeService, WriteMode

log = logging.getLogger(__name__)


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _require_profile(args: argparse.Namespace, *, explicit: bool = False) -> str | None:
    value = getattr(args, "profile", None)
    if explicit and not value:
        raise UsageError("Write operations require an explicit --profile")
    return value


def _format(args: argparse.Namespace) -> str:
    return str(getattr(args, "output_format", "text"))


def _write_mode(args: argparse.Namespace) -> WriteMode:
    return "automated" if getattr(args, "yes", False) else "confirmed"


def _emit_for(service: TimeService, data: Any, args: argparse.Namespace, **meta: Any) -> None:
    emit(
        data,
        profile=service.profile.name,
        server=service.profile.base_url,
        output_format=_format(args),
        meta=meta,
    )


def _read_message(value: str | None) -> str:
    if value and value != "-":
        return value
    if sys.stdin.isatty():
        raise UsageError("Pass --message or pipe the message through stdin")
    return sys.stdin.read().strip()


def _confirm(args: argparse.Namespace, preview: dict[str, Any]) -> bool:
    if getattr(args, "dry_run", False):
        emit(preview, output_format=_format(args))
        return False
    if getattr(args, "yes", False):
        return True
    if not sys.stdin.isatty():
        raise ConfirmationRequired("Non-interactive writes require --yes")
    emit(preview, output_format="text")
    answer = input("Proceed? [y/N] ").strip().casefold()
    if answer not in {"y", "yes", "д", "да"}:
        raise ConfirmationRequired("Operation cancelled")
    return True


def _time_bounds(args: argparse.Namespace, timezone_name: str) -> tuple[int | None, int | None]:
    return (
        parse_time_bound(getattr(args, "since", None), timezone_name=timezone_name),
        parse_time_bound(
            getattr(args, "until", None), end_of_day=True, timezone_name=timezone_name
        ),
    )


def cmd_profile(args: argparse.Namespace) -> int:
    config = ConfigStore()
    secrets = SecretStore()
    if args.profile_command == "list":
        default = config.default_profile_name()
        rows = [
            {**primitive(profile), "default": profile.name == default}
            for profile in config.list_profiles()
        ]
        emit(rows, output_format=_format(args))
        return 0
    if args.profile_command == "add":
        profile = config.add_profile(
            args.name,
            args.url,
            team_id=args.team_id,
            timezone=args.timezone,
            mcp_enabled=not args.disable_mcp,
            write_policy=args.write_policy,
            allowed_websocket_hosts=args.websocket_hosts,
        )
        emit(profile, output_format=_format(args))
        return 0
    if args.profile_command == "show":
        emit(config.get_profile(args.name), output_format=_format(args))
        return 0
    if args.profile_command == "update":
        profile = config.get_profile(args.name)
        updated = replace(
            profile,
            base_url=(normalize_base_url(args.url) if args.url is not None else profile.base_url),
            team_id=args.team_id if args.team_id is not None else profile.team_id,
            timezone=args.timezone if args.timezone is not None else profile.timezone,
            mcp_enabled=(args.mcp_enabled if args.mcp_enabled is not None else profile.mcp_enabled),
            write_policy=(
                args.write_policy if args.write_policy is not None else profile.write_policy
            ),
            allowed_websocket_hosts=(
                tuple(args.websocket_hosts)
                if args.websocket_hosts is not None
                else profile.allowed_websocket_hosts
            ),
        )
        config.update_profile(updated)
        emit(config.get_profile(updated.name), output_format=_format(args))
        return 0
    if args.profile_command == "default":
        emit(config.set_default(args.name), output_format=_format(args))
        return 0
    if args.profile_command == "remove":
        profile = config.get_profile(args.name)
        preview = {
            "operation": "remove_profile",
            "profile": profile.name,
            "server": profile.base_url,
            "removes_keychain_secrets": True,
        }
        if not _confirm(args, preview):
            return 0
        removed = config.remove_profile(profile.name)
        secrets.delete(profile.name)
        secrets.delete(profile.name, "csrf")
        emit({"removed": removed.name}, output_format=_format(args))
        return 0
    raise UsageError("Missing profile subcommand")


def cmd_auth(args: argparse.Namespace) -> int:
    profile_name = _require_profile(args, explicit=True)
    if profile_name is None:
        raise UsageError("Authentication requires an explicit --profile")
    config = ConfigStore()
    secrets = SecretStore()
    profile = config.get_profile(profile_name)
    if args.auth_command == "set":
        token = sys.stdin.read().strip() if args.stdin else getpass.getpass("Time token: ").strip()
        secrets.set(profile.name, token)
        if args.csrf:
            csrf = getpass.getpass("CSRF token: ").strip()
            if csrf:
                secrets.set(profile.name, csrf, "csrf")
        config.update_profile(replace(profile, auth_method=args.method))
        emit(
            {"profile": profile.name, "stored": True, "method": args.method},
            output_format=_format(args),
        )
        return 0
    if args.auth_command == "status":
        stored = bool(secrets.get(profile.name))
        result: dict[str, Any] = {
            "profile": profile.name,
            "server": profile.base_url,
            "stored": stored,
            "method": profile.auth_method,
        }
        if args.check and stored:
            with TimeService.open(profile.name, config=config, secrets=secrets) as service:
                result["user"] = service.me()
                result["valid"] = True
        emit(result, output_format=_format(args))
        return 0
    if args.auth_command == "clear":
        preview = {"operation": "clear_auth", "profile": profile.name, "server": profile.base_url}
        if not _confirm(args, preview):
            return 0
        secrets.delete(profile.name)
        secrets.delete(profile.name, "csrf")
        emit({"profile": profile.name, "cleared": True}, output_format=_format(args))
        return 0
    raise UsageError("Missing auth subcommand")


def _with_service(args: argparse.Namespace, action: Callable[[TimeService], Any]) -> int:
    with TimeService.open(_require_profile(args)) as service:
        result = action(service)
        _emit_for(service, result, args)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    def public_check(profile_name: str | None) -> dict[str, Any]:
        profile = ConfigStore().get_profile(profile_name)
        with TimeClient(profile.base_url, AuthContext("")) as client:
            return {
                "ok": True,
                "profile": profile.name,
                "server": profile.base_url,
                "ping": client.ping(),
                "authenticated": False,
            }

    if args.all:
        config = ConfigStore()
        results: list[dict[str, Any]] = []
        for profile in config.list_profiles():
            try:
                if args.public:
                    results.append(
                        {"profile": profile.name, "ok": True, "result": public_check(profile.name)}
                    )
                else:
                    with TimeService.open(profile.name, config=config) as service:
                        results.append(
                            {"profile": profile.name, "ok": True, "result": service.doctor()}
                        )
            except TimeToolkitError as exc:
                results.append({"profile": profile.name, "ok": False, "error": exc.message})
        emit(results, output_format=_format(args))
        return 0 if results and all(row["ok"] for row in results) else int(ExitCode.GENERAL)
    if args.public:
        emit(public_check(_require_profile(args)), output_format=_format(args))
        return 0
    return _with_service(args, lambda service: service.doctor())


def cmd_me(args: argparse.Namespace) -> int:
    return _with_service(args, lambda service: service.me())


def cmd_teams(args: argparse.Namespace) -> int:
    return _with_service(args, lambda service: service.teams())


def cmd_channels(args: argparse.Namespace) -> int:
    return _with_service(
        args,
        lambda service: service.list_channels(
            pattern=args.pattern,
            channel_type=args.type,
            limit=args.limit,
            max_pages=args.max_pages,
        ),
    )


def cmd_dms(args: argparse.Namespace) -> int:
    return _with_service(
        args, lambda service: service.list_dms(with_user=args.with_user, limit=args.limit)
    )


def cmd_posts(args: argparse.Namespace) -> int:
    def action(service: TimeService):
        since, until = _time_bounds(args, service.profile.timezone)
        return service.channel_posts(
            args.channel,
            since_ms=since,
            until_ms=until,
            authors=_csv(args.author),
            contains=args.contains,
            limit=args.limit,
        )

    return _with_service(args, action)


def cmd_search(args: argparse.Namespace) -> int:
    def action(service: TimeService):
        since, until = _time_bounds(args, service.profile.timezone)
        return service.search_posts(
            args.query,
            channels=_csv(args.channel),
            authors=_csv(args.author),
            since_ms=since,
            until_ms=until,
            limit=args.limit,
        )

    return _with_service(args, action)


def cmd_thread(args: argparse.Namespace) -> int:
    return _with_service(args, lambda service: service.thread(args.target))


def cmd_threads(args: argparse.Namespace) -> int:
    return _with_service(
        args,
        lambda service: service.followed_threads(limit=args.limit, with_posts=args.with_posts),
    )


def cmd_unread(args: argparse.Namespace) -> int:
    return _with_service(
        args,
        lambda service: service.unread(
            channel=args.channel,
            with_posts=args.with_posts,
            mentions_only=False,
            limit=args.limit,
        ),
    )


def cmd_mentions(args: argparse.Namespace) -> int:
    def action(service: TimeService):
        if args.unread:
            return service.unread(
                channel=args.channel,
                with_posts=True,
                mentions_only=True,
                limit=args.limit,
            )
        since, until = _time_bounds(args, service.profile.timezone)
        posts = service.search_posts(
            f"@{service.me().username}",
            channels=_csv(args.channel),
            since_ms=since,
            until_ms=until,
            limit=args.limit,
        )
        return [post for post in posts if post.is_mention]

    return _with_service(args, action)


def cmd_flagged(args: argparse.Namespace) -> int:
    return _with_service(
        args,
        lambda service: service.flagged_posts(channel=args.channel, limit=args.limit),
    )


def cmd_pinned(args: argparse.Namespace) -> int:
    return _with_service(args, lambda service: service.pinned_posts(args.channel))


def cmd_user(args: argparse.Namespace) -> int:
    def action(service: TimeService):
        since, until = _time_bounds(args, service.profile.timezone)
        return service.user_activity(
            args.username, since_ms=since, until_ms=until, limit=args.limit
        )

    return _with_service(args, action)


def cmd_posts_by_user(args: argparse.Namespace) -> int:
    def action(service: TimeService):
        since, until = _time_bounds(args, service.profile.timezone)
        return service.search_posts(
            "",
            channels=_csv(args.channel),
            authors=_csv(args.user),
            since_ms=since,
            until_ms=until,
            limit=args.limit,
        )

    return _with_service(args, action)


def cmd_reactions(args: argparse.Namespace) -> int:
    return _with_service(args, lambda service: service.reactions(args.target))


def cmd_readers(args: argparse.Namespace) -> int:
    return _with_service(args, lambda service: service.readers(args.targets))


def cmd_file(args: argparse.Namespace) -> int:
    if args.file_command == "info":
        return _with_service(args, lambda service: service.file_info(args.file_id))
    if args.file_command == "download":
        destination = Path(args.output).expanduser()

        def action(service: TimeService):
            path = service.download_file(args.file_id, destination, overwrite=args.overwrite)
            return {"file_id": args.file_id, "path": str(path), "downloaded": True}

        return _with_service(args, action)
    if args.file_command == "upload":
        profile_name = _require_profile(args, explicit=True)
        files = [Path(value).expanduser() for value in args.files]
        with TimeService.open(profile_name, write_mode=_write_mode(args)) as service:
            preview = _write_preview(
                service,
                operation="upload_files",
                target=args.channel,
                extra={"files": [str(path) for path in files]},
            )
            if not _confirm(args, preview):
                return 0
            _emit_for(service, {"file_ids": service.upload_files(args.channel, files)}, args)
        return 0
    raise UsageError("Missing file subcommand")


def cmd_resolve(args: argparse.Namespace) -> int:
    def action(service: TimeService):
        result: dict[str, Any] = {}
        if args.user:
            result["users"] = service.search_users(args.user, limit=args.limit)
        if args.channel:
            result["channel"] = service.resolve_channel(args.channel)
        if not result:
            raise UsageError("Pass --user or --channel")
        return result

    return _with_service(args, action)


def _write_preview(
    service: TimeService,
    *,
    operation: str,
    target: str,
    message: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "dry_run": True,
        "operation": operation,
        "profile": service.profile.name,
        "server": service.profile.base_url,
        "target": target,
        "message": message,
        **(extra or {}),
    }


def cmd_post(args: argparse.Namespace) -> int:
    profile_name = _require_profile(args, explicit=True)
    message = _read_message(args.message)
    with TimeService.open(profile_name, write_mode=_write_mode(args)) as service:
        preview = _write_preview(
            service,
            operation="create_post",
            target=args.target,
            message=message,
            extra={"file_ids": args.file_ids},
        )
        if not _confirm(args, preview):
            return 0
        result = service.create_post(
            args.target,
            message,
            file_ids=args.file_ids,
            idempotency_key=args.idempotency_key,
        )
        _emit_for(service, result, args)
    return 0


def cmd_reply(args: argparse.Namespace) -> int:
    profile_name = _require_profile(args, explicit=True)
    message = _read_message(args.message)
    with TimeService.open(profile_name, write_mode=_write_mode(args)) as service:
        preview = _write_preview(
            service,
            operation="reply",
            target=args.target,
            message=message,
            extra={"file_ids": args.file_ids},
        )
        if not _confirm(args, preview):
            return 0
        result = service.reply(
            args.target,
            message,
            file_ids=args.file_ids,
            idempotency_key=args.idempotency_key,
        )
        _emit_for(service, result, args)
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    profile_name = _require_profile(args, explicit=True)
    message = _read_message(args.message)
    with TimeService.open(profile_name, write_mode=_write_mode(args)) as service:
        preview = _write_preview(
            service, operation="edit_post", target=args.target, message=message
        )
        if not _confirm(args, preview):
            return 0
        _emit_for(service, service.edit_post(args.target, message), args)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    profile_name = _require_profile(args, explicit=True)
    with TimeService.open(profile_name, write_mode=_write_mode(args)) as service:
        preview = _write_preview(service, operation="delete_post", target=args.target)
        if not _confirm(args, preview):
            return 0
        _emit_for(service, service.delete_post(args.target), args)
    return 0


def cmd_pin(args: argparse.Namespace) -> int:
    profile_name = _require_profile(args, explicit=True)
    pinned = args.command == "pin"
    with TimeService.open(profile_name, write_mode=_write_mode(args)) as service:
        preview = _write_preview(
            service, operation="pin_post" if pinned else "unpin_post", target=args.target
        )
        if not _confirm(args, preview):
            return 0
        _emit_for(service, service.pin_post(args.target, pinned=pinned), args)
    return 0


def cmd_react(args: argparse.Namespace) -> int:
    profile_name = _require_profile(args, explicit=True)
    add = args.command == "react"
    with TimeService.open(profile_name, write_mode=_write_mode(args)) as service:
        preview = _write_preview(
            service,
            operation="add_reaction" if add else "remove_reaction",
            target=args.target,
            extra={"emoji": args.emoji},
        )
        if not _confirm(args, preview):
            return 0
        _emit_for(service, service.react(args.target, args.emoji, add=add), args)
    return 0


def cmd_simple_write(args: argparse.Namespace) -> int:
    profile_name = _require_profile(args, explicit=True)
    with TimeService.open(profile_name, write_mode=_write_mode(args)) as service:
        operation = args.command.replace("-", "_")
        preview = _write_preview(service, operation=operation, target=args.target)
        if not _confirm(args, preview):
            return 0
        if args.command in {"flag", "unflag"}:
            result = service.flag_post(args.target, flagged=args.command == "flag")
        elif args.command in {"follow", "unfollow"}:
            result = service.follow_thread(args.target, following=args.command == "follow")
        elif args.command == "mark-unread":
            result = service.mark_unread(args.target)
        elif args.command == "mark-read":
            result = service.mark_channel_read(args.target)
        else:
            raise UsageError(f"Unsupported write operation: {args.command}")
        _emit_for(service, result, args)
    return 0


def cmd_mcp(_: argparse.Namespace) -> int:
    try:
        from time_toolkit.mcp_server import run_mcp_server
    except ImportError as exc:
        raise UsageError("MCP support is not installed; install time-toolkit[mcp]") from exc
    run_mcp_server()
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    try:
        from time_toolkit.realtime import RealtimeService
    except ImportError as exc:
        raise UsageError(
            "Realtime support is not installed; install time-toolkit[realtime]"
        ) from exc

    config = ConfigStore()
    profile_name = _require_profile(args) or config.get_profile().name
    with RealtimeService.open(profile_name, config=config) as service:
        channel_ids = {service.resolve_channel(value).id for value in args.channel}
        events = set(args.event or ["posted"])
        maximum = 1 if args.once else max(0, args.max_events)
        for count, event in enumerate(
            service.iter_events(
                event_types=events,
                channel_ids=channel_ids,
                reconnect=not args.no_reconnect,
                max_reconnects=max(0, args.max_reconnects),
            ),
            start=1,
        ):
            output_format = "ndjson" if _format(args) in {"json", "ndjson"} else "text"
            emit(
                event,
                profile=service.profile.name,
                server=service.server,
                output_format=output_format,
                meta={"stream": "websocket"},
            )
            if maximum and count >= maximum:
                break
    return 0


def cmd_service_key(args: argparse.Namespace) -> int:
    store = SecretStore()
    if args.service_key_command == "status":
        emit({"configured": bool(store.get_service_api_key())}, output_format=_format(args))
        return 0
    if args.service_key_command == "create":
        existing = bool(store.get_service_api_key())
        if existing or args.dry_run:
            preview = {
                "operation": ("rotate_service_api_key" if existing else "create_service_api_key"),
                "effect": (
                    "existing HTTP clients will stop authenticating"
                    if existing
                    else "a new key will be stored in the operating-system keychain"
                ),
            }
            if not _confirm(args, preview):
                return 0
        value = secrets.token_urlsafe(32)
        store.set_service_api_key(value)
        emit(
            {
                "created": True,
                "api_key": value,
                "warning": "This key is shown once; store it in the calling service's secrets",
            },
            output_format=_format(args),
        )
        return 0
    if args.service_key_command == "clear":
        preview = {
            "operation": "clear_service_api_key",
            "effect": "the HTTP adapter cannot start until a new key is created",
        }
        if not _confirm(args, preview):
            return 0
        store.delete_service_api_key()
        emit({"cleared": True}, output_format=_format(args))
        return 0
    raise UsageError("Missing service-key subcommand")


def cmd_serve(args: argparse.Namespace) -> int:
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.allow_network:
        raise UsageError("Non-local HTTP binding requires --allow-network")
    try:
        import uvicorn

        from time_toolkit.http_api import create_app
    except ImportError as exc:
        raise UsageError("HTTP support is not installed; install time-toolkit[service]") from exc
    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        log_level="debug" if args.debug else "info",
    )
    return 0


def _add_time_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--since", help="YYYY-MM-DD, ISO-8601, 7d, 24h, or 30m")
    parser.add_argument("--until", help="inclusive upper time bound")


def _add_write_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="show the exact action only")
    parser.add_argument("--yes", action="store_true", help="confirm a non-interactive write")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="timetk",
        description="Time Toolkit command line for Time Messenger (Mattermost API v4)",
    )
    parser.add_argument("--version", action="version", version=f"timetk {__version__}")
    parser.add_argument("-p", "--profile", help="Time profile; required explicitly for writes")
    parser.add_argument(
        "-o",
        "--format",
        dest="output_format",
        choices=["text", "json", "ndjson"],
        default="text",
    )
    parser.add_argument("--debug", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    profile = commands.add_parser("profile", help="manage Time profiles")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("list")
    add = profile_commands.add_parser("add")
    add.add_argument("name")
    add.add_argument("url")
    add.add_argument("--team-id", default="")
    add.add_argument("--timezone", default="Europe/Moscow")
    add.add_argument("--disable-mcp", action="store_true")
    add.add_argument("--write-policy", choices=WRITE_POLICIES, default="approval")
    add.add_argument(
        "--websocket-host",
        dest="websocket_hosts",
        action="append",
        default=[],
        help="allow an advertised WebSocket hostname; repeat for multiple hosts",
    )
    show = profile_commands.add_parser("show")
    show.add_argument("name")
    update = profile_commands.add_parser("update")
    update.add_argument("name")
    update.add_argument("--url")
    update.add_argument("--team-id")
    update.add_argument("--timezone")
    mcp_group = update.add_mutually_exclusive_group()
    mcp_group.add_argument("--enable-mcp", dest="mcp_enabled", action="store_true")
    mcp_group.add_argument("--disable-mcp", dest="mcp_enabled", action="store_false")
    update.set_defaults(mcp_enabled=None)
    update.add_argument("--write-policy", choices=WRITE_POLICIES)
    update.set_defaults(write_policy=None)
    websocket_hosts = update.add_mutually_exclusive_group()
    websocket_hosts.add_argument(
        "--websocket-host",
        dest="websocket_hosts",
        action="append",
        help="replace allowed WebSocket hosts; repeat for multiple hosts",
    )
    websocket_hosts.add_argument(
        "--clear-websocket-hosts",
        dest="websocket_hosts",
        action="store_const",
        const=[],
    )
    update.set_defaults(websocket_hosts=None)
    default = profile_commands.add_parser("default")
    default.add_argument("name")
    remove = profile_commands.add_parser("remove")
    remove.add_argument("name")
    _add_write_flags(remove)
    profile.set_defaults(func=cmd_profile)

    auth = commands.add_parser("auth", help="manage keychain authentication")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    auth_set = auth_commands.add_parser("set")
    auth_set.add_argument("--stdin", action="store_true", help="read the token from stdin")
    auth_set.add_argument("--method", choices=["bearer", "cookie"], default="bearer")
    auth_set.add_argument("--csrf", action="store_true", help="also prompt for a CSRF token")
    auth_status = auth_commands.add_parser("status")
    auth_status.add_argument("--check", action="store_true", help="verify the token with Time")
    auth_clear = auth_commands.add_parser("clear")
    _add_write_flags(auth_clear)
    auth.set_defaults(func=cmd_auth)

    doctor = commands.add_parser("doctor", help="check profiles and connectivity")
    doctor.add_argument("--all", action="store_true")
    doctor.add_argument("--public", action="store_true", help="check servers without a token")
    doctor.set_defaults(func=cmd_doctor)

    commands.add_parser("me", help="show the current user").set_defaults(func=cmd_me)
    commands.add_parser("teams", help="list visible teams").set_defaults(func=cmd_teams)

    commands.add_parser("mcp", help="run the local Codex MCP server over stdio").set_defaults(
        func=cmd_mcp
    )

    watch = commands.add_parser("watch", help="stream live Time events over WebSocket")
    watch.add_argument("--channel", action="append", default=[], help="channel name or ID")
    watch.add_argument("--event", action="append", default=[], help="event type; repeatable")
    watch.add_argument("--once", action="store_true", help="stop after the first matching event")
    watch.add_argument(
        "--max-events", type=int, default=0, help="stop after N events; 0 is unlimited"
    )
    watch.add_argument("--no-reconnect", action="store_true")
    watch.add_argument(
        "--max-reconnects", type=int, default=0, help="0 retries forever until interrupted"
    )
    watch.set_defaults(func=cmd_watch)

    service_key = commands.add_parser("service-key", help="manage the local HTTP API key")
    service_key_commands = service_key.add_subparsers(dest="service_key_command", required=True)
    service_key_create = service_key_commands.add_parser("create")
    _add_write_flags(service_key_create)
    service_key_commands.add_parser("status")
    service_key_clear = service_key_commands.add_parser("clear")
    _add_write_flags(service_key_clear)
    service_key.set_defaults(func=cmd_service_key)

    serve = commands.add_parser("serve", help="run the authenticated read-only HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--allow-network",
        action="store_true",
        help="allow binding beyond the local machine; TLS is still your responsibility",
    )
    serve.set_defaults(func=cmd_serve)

    channels = commands.add_parser("channels", help="list channels")
    channels.add_argument("--pattern", default="")
    channels.add_argument("--type", choices=["O", "P", "D", "G"], default="")
    channels.add_argument("--limit", type=int, default=100)
    channels.add_argument("--max-pages", type=int, default=10)
    channels.set_defaults(func=cmd_channels)

    dms = commands.add_parser("dms", help="list direct and group messages")
    dms.add_argument("--with", dest="with_user", default="")
    dms.add_argument("--limit", type=int, default=100)
    dms.set_defaults(func=cmd_dms)

    posts = commands.add_parser("posts", help="read a channel")
    posts.add_argument("channel")
    posts.add_argument("--author", default="", help="comma-separated usernames")
    posts.add_argument("--contains", default="")
    posts.add_argument("--limit", type=int, default=100)
    _add_time_filters(posts)
    posts.set_defaults(func=cmd_posts)

    search = commands.add_parser("search", help="search visible posts")
    search.add_argument("query")
    search.add_argument("--channel", default="", help="comma-separated channels")
    search.add_argument("--author", default="", help="comma-separated usernames")
    search.add_argument("--limit", type=int, default=100)
    _add_time_filters(search)
    search.set_defaults(func=cmd_search)

    thread = commands.add_parser("thread", help="read a complete thread")
    thread.add_argument("target", help="post ID or Time URL")
    thread.set_defaults(func=cmd_thread)

    threads = commands.add_parser("threads", help="list followed threads")
    threads.add_argument("--limit", type=int, default=100)
    threads.add_argument("--with-posts", action="store_true")
    threads.set_defaults(func=cmd_threads)

    unread = commands.add_parser("unread", help="show unread channels and counts")
    unread.add_argument("--channel", default="")
    unread.add_argument("--with-posts", action="store_true")
    unread.add_argument("--limit", type=int, default=100)
    unread.set_defaults(func=cmd_unread)

    mentions = commands.add_parser("mentions", help="show historical or unread mentions")
    mentions.add_argument("--unread", action="store_true")
    mentions.add_argument("--channel", default="")
    mentions.add_argument("--limit", type=int, default=100)
    _add_time_filters(mentions)
    mentions.set_defaults(func=cmd_mentions)

    flagged = commands.add_parser("flagged", help="list flagged posts")
    flagged.add_argument("--channel", default="")
    flagged.add_argument("--limit", type=int, default=100)
    flagged.set_defaults(func=cmd_flagged)

    pinned = commands.add_parser("pinned", help="list pinned posts in a channel")
    pinned.add_argument("channel")
    pinned.set_defaults(func=cmd_pinned)

    user = commands.add_parser("user", help="show a user and recent activity")
    user.add_argument("username")
    user.add_argument("--limit", type=int, default=200)
    _add_time_filters(user)
    user.set_defaults(func=cmd_user)

    by_user = commands.add_parser("posts-by-user", help="find posts from users")
    by_user.add_argument("--user", required=True, help="comma-separated usernames")
    by_user.add_argument("--channel", default="")
    by_user.add_argument("--limit", type=int, default=100)
    _add_time_filters(by_user)
    by_user.set_defaults(func=cmd_posts_by_user)

    resolve = commands.add_parser("resolve", help="resolve users and channels")
    resolve.add_argument("--user", default="")
    resolve.add_argument("--channel", default="")
    resolve.add_argument("--limit", type=int, default=20)
    resolve.set_defaults(func=cmd_resolve)

    reactions = commands.add_parser("reactions", help="list reactions on a post")
    reactions.add_argument("target")
    reactions.set_defaults(func=cmd_reactions)

    readers = commands.add_parser("readers", help="show read receipts for your posts")
    readers.add_argument("targets", nargs="+")
    readers.set_defaults(func=cmd_readers)

    files = commands.add_parser("file", help="inspect, upload, or download files")
    file_commands = files.add_subparsers(dest="file_command", required=True)
    file_info = file_commands.add_parser("info")
    file_info.add_argument("file_id")
    file_download = file_commands.add_parser("download")
    file_download.add_argument("file_id")
    file_download.add_argument("--output", required=True)
    file_download.add_argument("--overwrite", action="store_true")
    file_upload = file_commands.add_parser("upload")
    file_upload.add_argument("channel")
    file_upload.add_argument("files", nargs="+")
    _add_write_flags(file_upload)
    files.set_defaults(func=cmd_file)

    post = commands.add_parser("post", help="send a channel or direct message")
    post.add_argument("target", help="channel name/ID or @username")
    post.add_argument("-m", "--message")
    post.add_argument("--file-id", dest="file_ids", action="append", default=[])
    post.add_argument("--idempotency-key", default="")
    _add_write_flags(post)
    post.set_defaults(func=cmd_post)

    reply = commands.add_parser("reply", help="reply to a thread")
    reply.add_argument("target", help="root post ID or Time URL")
    reply.add_argument("-m", "--message")
    reply.add_argument("--file-id", dest="file_ids", action="append", default=[])
    reply.add_argument("--idempotency-key", default="")
    _add_write_flags(reply)
    reply.set_defaults(func=cmd_reply)

    edit = commands.add_parser("edit", help="edit your post")
    edit.add_argument("target")
    edit.add_argument("-m", "--message")
    _add_write_flags(edit)
    edit.set_defaults(func=cmd_edit)

    delete = commands.add_parser("delete", help="delete your post")
    delete.add_argument("target")
    _add_write_flags(delete)
    delete.set_defaults(func=cmd_delete)

    for name in ("pin", "unpin"):
        action = commands.add_parser(name, help=f"{name} a post")
        action.add_argument("target")
        _add_write_flags(action)
        action.set_defaults(func=cmd_pin)

    for name in ("react", "unreact"):
        action = commands.add_parser(name, help=f"{name} to a post")
        action.add_argument("target")
        action.add_argument("emoji")
        _add_write_flags(action)
        action.set_defaults(func=cmd_react)

    simple_writes = ("flag", "unflag", "follow", "unfollow", "mark-unread", "mark-read")
    for name in simple_writes:
        action = commands.add_parser(name, help=name.replace("-", " "))
        action.add_argument("target")
        _add_write_flags(action)
        action.set_defaults(func=cmd_simple_write)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args))
    except TimeToolkitError as exc:
        if _format(args) in {"json", "ndjson"}:
            print(
                json.dumps(
                    primitive(
                        {
                            "schema_version": "1.0",
                            "error": exc.message,
                            "code": int(exc.exit_code),
                            "details": exc.details,
                        }
                    ),
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
        else:
            print(f"Error: {exc.message}", file=sys.stderr)
            if exc.details:
                for key, value in exc.details.items():
                    print(f"  {key}: {value}", file=sys.stderr)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
