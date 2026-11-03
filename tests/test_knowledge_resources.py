"""開発者が同梱したMarkdownのMCP Resources公開契約。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from jvdata_bridge_mcp.config import JvDataSettings
from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.knowledge_resources import load_knowledge_resources
from jvdata_bridge_mcp.server import create_server
from jvdata_bridge_mcp.tools.knowledge import build_knowledge_list_tool, build_knowledge_read_tool
from jvdata_bridge_mcp.tools.race.forecast_context import evidence_after_target_date


def test_knowledge_is_listed_and_readable_without_database() -> None:
    database = JvDataDatabase(JvDataSettings(database_url="postgresql://reader@invalid/jvdata"))
    server = create_server(database)

    resources = asyncio.run(server.list_resources())
    uris = {str(resource.uri) for resource in resources}
    assert uris == {
        "jvdata://knowledge/spec/procedures/forecast-discipline",
        "jvdata://knowledge/spec/procedures/scenario-and-output",
        "jvdata://knowledge/spec/procedures/post-race-review",
        "jvdata://knowledge/spec/knowledge/horse-evaluation",
        "jvdata://knowledge/spec/knowledge/race-structure",
        "jvdata://knowledge/spec/knowledge/market-and-bets",
    }
    assert all(resource.mimeType == "text/markdown" for resource in resources)
    assert all("新潟" not in (resource.title or "") for resource in resources)
    assert all("新潟" not in (resource.description or "") for resource in resources)
    metadata = {
        str(resource.uri): resource.model_dump(by_alias=True)["_meta"]["jvdata/knowledge"] for resource in resources
    }
    assert all("published_at" not in item for item in metadata.values())
    assert all(len(item["content_sha256"]) == 64 for item in metadata.values())

    for uri in uris:
        contents = asyncio.run(server.read_resource(uri))
        assert len(contents) == 1
        assert contents[0].mime_type == "text/markdown"
        assert metadata[uri]["content_sha256"] == hashlib.sha256(contents[0].content.encode("utf-8")).hexdigest()
        if uri.endswith("horse-evaluation"):
            assert "Ability ― 純粋能力" in contents[0].content


def test_knowledge_tools_return_exact_resource_content_and_metadata_without_database() -> None:
    database = JvDataDatabase(JvDataSettings(database_url="postgresql://reader@invalid/jvdata"))
    server = create_server(database)

    async def exercise() -> None:
        listed = await server.call_tool("knowledge_list", {})
        assert isinstance(listed, tuple)
        payload = listed[1]
        resources = await server.list_resources()
        assert payload["count"] == len(resources) == 6
        assert len(payload["resources"]) == 6
        for item, resource in zip(payload["resources"], resources, strict=True):
            assert item["uri"] == str(resource.uri)
            assert "content" not in item
            metadata = resource.model_dump(by_alias=True)["_meta"]["jvdata/knowledge"]
            assert {key: item[key] for key in metadata} == metadata
            read = await server.call_tool("knowledge_read", {"uri": item["uri"]})
            assert isinstance(read, tuple)
            output = read[1]
            assert {key: output[key] for key in item} == item
            original = await server.read_resource(item["uri"])
            assert output["content"] == original[0].content
            assert output["content_sha256"] == hashlib.sha256(output["content"].encode("utf-8")).hexdigest()

        tools = {tool.name: tool for tool in await server.list_tools()}
        assert tools["knowledge_list"].inputSchema["properties"] == {}
        assert tools["knowledge_read"].inputSchema["required"] == ["uri"]
        for name in ("knowledge_list", "knowledge_read"):
            annotations = tools[name].annotations
            assert annotations is not None
            assert annotations.readOnlyHint is True
            assert annotations.destructiveHint is False
            assert annotations.idempotentHint is True
            assert annotations.openWorldHint is False

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "uri",
    [
        "jvdata://knowledge/spec/knowledge/missing",
        "jvdata://knowledge/spec/knowledge/../../outside.md",
        "file:///etc/passwd",
        "https://example.com/rule.md",
        "/etc/passwd",
    ],
)
def test_knowledge_read_rejects_unregistered_uri(uri: str) -> None:
    knowledge_read = build_knowledge_read_tool(load_knowledge_resources())
    with pytest.raises(ValueError, match="choose a URI from knowledge_list"):
        asyncio.run(knowledge_read(uri))


def test_knowledge_tools_keep_startup_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = "# 起動時のナレッジ\n\n同じ版の本文。\n"
    source = tmp_path / "rule.md"
    source.write_text(text, encoding="utf-8", newline="\n")
    uri = "jvdata://knowledge/spec/knowledge/test"
    manifest = {
        "schema_version": 1,
        "resources": [{"uri": uri, "name": "test", "kind": "knowledge", "status": "published", "path": "rule.md"}],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        "jvdata_bridge_mcp.server.load_knowledge_resources", lambda: load_knowledge_resources(tmp_path)
    )
    server = create_server(JvDataDatabase(JvDataSettings(database_url="postgresql://reader@invalid/jvdata")))
    source.write_text("# 起動後の変更", encoding="utf-8")

    async def exercise() -> None:
        listed = await server.call_tool("knowledge_list", {})
        assert isinstance(listed, tuple)
        assert listed[1]["count"] == 1
        read = await server.call_tool("knowledge_read", {"uri": uri})
        assert isinstance(read, tuple)
        assert read[1]["content"] == text
        assert read[1]["content_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert (await server.read_resource(uri))[0].content == text

    asyncio.run(exercise())


def test_knowledge_output_metadata_cannot_mutate_snapshot() -> None:
    resources = load_knowledge_resources()
    knowledge_list = build_knowledge_list_tool(resources)
    knowledge_read = build_knowledge_read_tool(resources)

    async def exercise() -> None:
        listed = await knowledge_list()
        item = listed.resources[0]
        original_start = item.supporting_evidence_periods[0]["start"]
        item.supporting_evidence_periods[0]["start"] = "1900-01-01"
        read = await knowledge_read(item.uri)
        assert read.supporting_evidence_periods[0]["start"] == original_start
        read.supporting_evidence_periods[0]["scope"] = "mutated"
        again = await knowledge_list()
        assert again.resources[0].supporting_evidence_periods[0]["scope"] != "mutated"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("target_date", "post_target_starts"),
    [
        ("2024-12-31", {"2025-01-12", "2026-07-19"}),
        ("2025-12-21", {"2025-01-12", "2026-07-19"}),
        ("2025-12-22", {"2026-07-19"}),
        ("2026-09-23", set()),
    ],
)
def test_published_evidence_keeps_separate_audits_and_target_date_classification(
    target_date: str, post_target_starts: set[str]
) -> None:
    boundary = date.fromisoformat(target_date)
    for resource in load_knowledge_resources():
        metadata = (resource.meta or {})["jvdata/knowledge"]
        periods = metadata["supporting_evidence_periods"]
        assert {(p["start"], p["end"], p["sample_size"]) for p in periods} == {
            ("2025-01-12", "2025-12-21", 300),
            ("2026-07-19", "2026-09-22", 72),
        }
        assert all(p["evidence_timing"] == "post_race_results" for p in periods)
        assert {p["start"] for p in periods if evidence_after_target_date(p, boundary)} == post_target_starts
        assert metadata["max_evidence_race_date"] is None  # old principles still have unknown supporting periods
        assert "published_at" not in metadata


def test_knowledge_manifest_rejects_paths_outside_its_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("# outside", encoding="utf-8")
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resources": [
                    {
                        "uri": "jvdata://knowledge/spec/knowledge/c999",
                        "name": "spec-knowledge-c999",
                        "kind": "knowledge",
                        "status": "candidate",
                        "path": "../outside.md",
                        "max_evidence_race_date": "2026-08-16",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid knowledge path"):
        load_knowledge_resources(root)


@pytest.mark.parametrize(
    "period",
    [
        {"start": "2026-09-22", "end": "2026-07-19", "sample_size": 72},
        {"start": "not-a-date", "end": "2026-09-22", "sample_size": 72},
        {"start": "2026-07-19", "end": "2026-09-22", "sample_size": True},
    ],
)
def test_knowledge_rejects_invalid_supporting_evidence_period(tmp_path: Path, period: dict[str, object]) -> None:
    (tmp_path / "rule.md").write_text("# rule", encoding="utf-8")
    period.update(kind="issue_audit", scope="new_rules_only")
    item = dict(
        uri="jvdata://knowledge/spec/knowledge/test",
        name="test",
        kind="knowledge",
        status="published",
        path="rule.md",
        supporting_evidence_periods=[period],
    )
    (tmp_path / "manifest.json").write_text(json.dumps({"schema_version": 1, "resources": [item]}), encoding="utf-8")
    with pytest.raises(ValueError, match="evidence"):
        load_knowledge_resources(tmp_path)
