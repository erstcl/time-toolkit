from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from time_toolkit.models import OutputEnvelope, primitive


def emit(
    data: Any,
    *,
    profile: str = "",
    server: str = "",
    output_format: str = "text",
    meta: dict[str, Any] | None = None,
    stream: TextIO = sys.stdout,
) -> None:
    envelope = OutputEnvelope(profile=profile, server=server, data=data, meta=meta or {})
    payload = primitive(envelope)
    if output_format == "json":
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        return
    if output_format == "ndjson":
        items = payload["data"] if isinstance(payload["data"], list) else [payload["data"]]
        for item in items:
            row = {
                "schema_version": payload["schema_version"],
                "profile": profile,
                "server": server,
                "data": item,
            }
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        return
    _emit_text(data, stream)


def emit_error(message: str, *, code: int, details: dict[str, Any] | None = None) -> None:
    payload = {"error": message, "code": code, "details": details or {}}
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)


def _emit_text(value: Any, stream: TextIO) -> None:
    data = primitive(value)
    if isinstance(data, list):
        if not data:
            stream.write("No results.\n")
            return
        for item in data:
            _emit_text_item(item, stream)
        return
    _emit_text_item(data, stream)


def _emit_text_item(item: Any, stream: TextIO) -> None:
    if not isinstance(item, dict):
        stream.write(f"{item}\n")
        return
    if "message" in item and "id" in item:
        author = item.get("author") or item.get("user_id") or "unknown"
        timestamp = item.get("create_at_iso") or item.get("create_at") or ""
        stream.write(f"[{timestamp}] @{author}: {item.get('message', '')}\n")
        if item.get("permalink"):
            stream.write(f"  {item['permalink']}\n")
        return
    if "display_name" in item or "name" in item:
        label = item.get("label") or item.get("display_name") or item.get("name")
        suffix = f" ({item['type']})" if item.get("type") else ""
        stream.write(f"{label}{suffix}\n")
        return
    stream.write(json.dumps(item, ensure_ascii=False, indent=2) + "\n")
