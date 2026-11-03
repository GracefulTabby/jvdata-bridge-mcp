"""DBへ接続せず、実際のMCP公開スキーマからドキュメントを更新する。"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from pathlib import Path

from jvdata_bridge_mcp.config import JvDataSettings
from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.server import create_server
from jvdata_bridge_mcp.tools.all import build_all_tools


def schema_type(schema: dict[str, object]) -> str:
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "anyOf" in schema:
        return " / ".join(schema_type(part) for part in schema["anyOf"])
    if schema.get("type") == "array":
        return f"{schema_type(schema.get('items', {}))}[]"
    if schema.get("type") == "object" and isinstance(schema.get("additionalProperties"), dict):
        return f"object<string, {schema_type(schema['additionalProperties'])}>"
    return str(schema.get("type", "object"))


def schema_table(schema: dict[str, object], *, inputs: bool = False) -> str:
    columns = "必須 / 省略時" if inputs else "必須キー"
    rows = [f"| 項目 | 型 | {columns} | 説明 |", "|---|---|---|---|"]
    for name, field in schema.get("properties", {}).items():
        required = ("必須" if inputs else "はい") if name in schema.get("required", []) else "いいえ"
        if inputs and "default" in field:
            default = json.dumps(field["default"], ensure_ascii=False).replace("|", "\\|")
            required = f"省略可 / `{default}`"
        description = str(field.get("description", "")).replace("|", "\\|").replace("\n", "<br>")
        rows.append(f"| `{name}` | {schema_type(field)} | {required} | {description} |")
    return "\n".join(rows)


def update_reference(tools: dict[str, object], payload: dict[str, object]) -> None:
    path = Path("docs/tool-reference.md")
    source = path.read_text(encoding="utf-8")
    policies = ["| 表記 | 実際の範囲 |", "|---|---|"]
    policies.extend(f"| {name} | {detail} |" for name, detail in payload["date_policy_definitions"].items())
    source = re.sub(r"\| 表記 \| 実際の範囲 \|\n.*?(?=\n\n)", "\n".join(policies), source, count=1, flags=re.DOTALL)
    for entry in payload["tools"]:
        name = entry["name"]
        tool = tools[name]
        pattern = rf'(<a id="{name}"></a>\n.*?)(?=\n<a id="|\Z)'
        match = re.search(pattern, source, re.DOTALL)
        if match is None:
            raise ValueError(f"missing reference section: {name}")
        section = match[1]
        section = re.sub(r"\*\*目的\*\*:.*?(?=\n\n)", f"**目的**: {entry['purpose']}。", section, flags=re.DOTALL)
        section = re.sub(
            r"\*\*日付基準\*\*:.*?(?=\n\n)",
            f"**日付基準**: {entry['date_policy']}。{entry['date_detail']}",
            section,
            flags=re.DOTALL,
        )
        section = re.sub(r"\*\*注意事項\*\*:.*?(?=\n\n)", f"**注意事項**: {entry['notes']}", section, flags=re.DOTALL)
        file, line = entry["source_file"], entry["source_line"]
        section = re.sub(
            r"\*\*実装\*\*:.*?(?=\n\n)", f"**実装**: [{file}:{line}](../{file}#L{line})", section, flags=re.DOTALL
        )
        section = section.split("**入力**", 1)[0]
        section += "**入力**\n\n" + schema_table(tool.inputSchema, inputs=True)
        section += f"\n\n**出力の概要**: {entry['output_summary']}\n\n**出力のトップレベル**\n\n"
        output = tool.outputSchema or {}
        section += schema_table(output)
        definitions = output.get("$defs", {})
        if definitions:
            section += f"\n\n<details>\n<summary>出力の入れ子定義（{len(definitions)}型）</summary>\n"
            for definition_name, definition in definitions.items():
                section += f"\n**`{definition_name}`**\n\n" + schema_table(definition) + "\n"
            section += "\n</details>"
        source = source[: match.start()] + section.rstrip() + "\n" + source[match.end() :]
        arguments = ", ".join(
            f"`{key}`{'*' if key in tool.inputSchema.get('required', []) else ''}"
            for key in tool.inputSchema.get("properties", {})
        )
        row_pattern = rf"^\| (\d+) \| \[`{name}`\]\(#{name}\) \|.*$"
        source = re.sub(
            row_pattern,
            lambda row: (
                f"| {row[1]} | [`{name}`](#{name}) | {entry['purpose']} | {entry['date_policy']} | {arguments} | {entry['output_summary']} |"
            ),
            source,
            flags=re.MULTILINE,
        )
    path.write_text(source, encoding="utf-8")


async def main() -> None:
    path = Path("docs/tool-schemas.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    tools = {tool.name: tool for tool in await create_server().list_tools()}
    if set(tools) != {entry["name"] for entry in payload["tools"]}:
        raise ValueError("tool catalog names must match the registered toolset")
    functions = {tool.__name__: tool for tool in build_all_tools(JvDataDatabase(JvDataSettings.from_env()))}
    payload["tool_count"] = len(tools)
    for entry in payload["tools"]:
        tool = tools[entry["name"]]
        function = functions[entry["name"]]
        entry["source_file"] = Path(inspect.getsourcefile(function)).relative_to(Path.cwd()).as_posix()
        entry["source_line"] = function.__code__.co_firstlineno
        entry["date_detail"] = payload["date_policy_definitions"][entry["date_policy"]]
        entry["inputSchema"] = tool.inputSchema
        entry["outputSchema"] = tool.outputSchema
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    update_reference(tools, payload)


if __name__ == "__main__":
    asyncio.run(main())
