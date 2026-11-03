"""開発者が同梱したMarkdownの競馬ナレッジをMCP Resourcesへ変換する。"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from mcp.server.fastmcp.resources.types import TextResource
from mcp.types import Annotations
from pydantic import AnyUrl

KNOWLEDGE_ROOT = Path(__file__).resolve().parent / "knowledge"
MANIFEST_NAME = "manifest.json"
MAX_RESOURCE_BYTES = 65_536
_KINDS = frozenset({"procedure", "knowledge"})


def _required_string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"knowledge resource {key} must be a non-empty string")
    return value.strip()


def _knowledge_path(root: Path, relative_path: str) -> Path:
    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute() or not candidate.parts or any(part in (".", "..") for part in candidate.parts):
        raise ValueError(f"invalid knowledge path: {relative_path}")
    if candidate.suffix != ".md":
        raise ValueError(f"knowledge resource must be Markdown: {relative_path}")
    resolved = root.joinpath(*candidate.parts).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"knowledge resource file is unavailable: {relative_path}")
    return resolved


def _evidence_periods(item: dict[str, Any]) -> list[dict[str, Any]]:
    periods = item.get("supporting_evidence_periods", [])
    if not isinstance(periods, list):
        raise ValueError("knowledge supporting_evidence_periods must be an array")
    for period in periods:
        if not isinstance(period, dict):
            raise ValueError("knowledge supporting evidence period must be an object")
        try:
            start = date.fromisoformat(_required_string(period, "start"))
            end = date.fromisoformat(_required_string(period, "end"))
        except ValueError as exc:
            raise ValueError("knowledge supporting evidence period requires valid start/end dates") from exc
        if start > end:
            raise ValueError("knowledge supporting evidence period start must not follow end")
        period["start"], period["end"] = start.isoformat(), end.isoformat()
        if period.get("evidence_timing", "unknown") not in {"unknown", "pre_race_facts", "post_race_results"}:
            raise ValueError("knowledge supporting evidence_timing is invalid")
        size = period.get("sample_size")
        if size is not None and (type(size) is not int or size <= 0):
            raise ValueError("knowledge supporting evidence sample_size must be a positive integer")
        _required_string(period, "kind")
        _required_string(period, "scope")
    return periods


def _resource_from_item(root: Path, item: dict[str, Any]) -> TextResource:
    uri = _required_string(item, "uri")
    if not uri.startswith("jvdata://knowledge/") or "?" in uri or "#" in uri:
        raise ValueError(f"invalid knowledge resource URI: {uri}")
    name = _required_string(item, "name")
    kind = _required_string(item, "kind")
    if kind not in _KINDS:
        raise ValueError(f"invalid knowledge resource kind: {kind}")
    status = _required_string(item, "status")
    evidence_raw = item.get("max_evidence_race_date")
    if evidence_raw is not None:
        if not isinstance(evidence_raw, str):
            raise ValueError("knowledge max_evidence_race_date must be a date or null")
        try:
            date.fromisoformat(evidence_raw)
        except ValueError as exc:
            raise ValueError(f"invalid knowledge max_evidence_race_date: {evidence_raw}") from exc
    path = _knowledge_path(root, _required_string(item, "path"))
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_RESOURCE_BYTES:
        raise ValueError(f"knowledge resource size is invalid: {path.name}")
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"knowledge resource must be UTF-8: {path.name}") from exc
    title_raw = item.get("title")
    if title_raw is not None and (not isinstance(title_raw, str) or not title_raw.strip()):
        raise ValueError("knowledge resource title must be a non-empty string")
    summary_raw = item.get("description")
    if summary_raw is not None and (not isinstance(summary_raw, str) or not summary_raw.strip()):
        raise ValueError("knowledge resource description must be a non-empty string")
    description = summary_raw.strip() if isinstance(summary_raw, str) else ""
    return TextResource(
        uri=AnyUrl(uri),
        name=name,
        title=title_raw.strip() if isinstance(title_raw, str) else name,
        description=description,
        mime_type="text/markdown",
        text=content,
        annotations=Annotations(audience=["assistant"]),
        meta={
            "jvdata/knowledge": {
                "kind": kind,
                "status": status,
                "content_sha256": hashlib.sha256(raw).hexdigest(),
                "max_evidence_race_date": evidence_raw,
                "supporting_evidence_periods": _evidence_periods(item),
            }
        },
    )


def load_knowledge_resources(root: Path | None = None) -> list[TextResource]:
    """manifestで明示されたMarkdownだけを、起動時の固定Resourcesとして読み込む。"""

    resolved_root = (root or KNOWLEDGE_ROOT).resolve()
    try:
        manifest = json.loads((resolved_root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"knowledge manifest is unavailable or invalid: {resolved_root / MANIFEST_NAME}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("knowledge manifest schema_version must be 1")
    entries = manifest.get("resources")
    if not isinstance(entries, list):
        raise ValueError("knowledge manifest resources must be an array")
    resources: list[TextResource] = []
    seen_uris: set[str] = set()
    seen_names: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("knowledge resource entry must be an object")
        resource = _resource_from_item(resolved_root, item)
        uri = str(resource.uri)
        if uri in seen_uris or resource.name in seen_names:
            raise ValueError(f"duplicate knowledge resource: {uri}")
        seen_uris.add(uri)
        seen_names.add(resource.name)
        resources.append(resource)
    return resources
