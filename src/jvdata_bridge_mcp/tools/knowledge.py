"""Resourcesを直接扱えないクライアント向けのナレッジ参照ツール。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from copy import deepcopy
from typing import Annotated, Any, Literal

from mcp.server.fastmcp.resources.types import TextResource
from pydantic import BaseModel, ConfigDict, Field

KNOWLEDGE_TOOL_NAMES = frozenset({"knowledge_list", "knowledge_read"})


class KnowledgeResourceInfo(BaseModel):
    """元Resourceと同じ識別情報と根拠メタデータ。"""

    model_config = ConfigDict(extra="forbid")

    uri: str = Field(description="knowledge_readへ渡す登録済みResource URI")
    name: str = Field(description="Resource名")
    title: str = Field(description="ナレッジのタイトル")
    description: str = Field(description="内容と適用範囲の説明")
    mime_type: str = Field(description="本文のMIME型")
    kind: Literal["procedure", "knowledge"] = Field(description="手順または判断知識")
    status: str = Field(description="Resourceの公開状態")
    content_sha256: str = Field(description="UTF-8本文バイト列のSHA-256。利用した版の識別に使う")
    max_evidence_race_date: str | None = Field(description="確認済みの最大根拠日。不明はnull")
    supporting_evidence_periods: list[dict[str, Any]] = Field(description="根拠資料ごとの期間・標本数・適用範囲")


class KnowledgeListOutput(BaseModel):
    """公開ナレッジの一覧。本文はknowledge_readで取得する。"""

    resources: list[KnowledgeResourceInfo] = Field(
        description="公開ナレッジのURI・概要・根拠情報の一覧。本文は含まない"
    )
    count: int = Field(description="公開ナレッジの件数")


class KnowledgeReadOutput(KnowledgeResourceInfo):
    """公開ナレッジの全文と、元Resourceの根拠情報。"""

    content: str = Field(description="登録済みResourceと同一のMarkdown全文")


def _resource_info(resource: TextResource) -> KnowledgeResourceInfo:
    metadata = deepcopy((resource.meta or {})["jvdata/knowledge"])
    return KnowledgeResourceInfo(
        uri=str(resource.uri),
        name=resource.name,
        title=resource.title or resource.name,
        description=resource.description or "",
        mime_type=resource.mime_type,
        **metadata,
    )


def build_knowledge_list_tool(resources: Sequence[TextResource]) -> Callable[..., object]:
    """起動時に読み込んだResourcesだけを一覧する。"""

    snapshot = tuple(resources)

    async def knowledge_list() -> KnowledgeListOutput:
        """予想・レビュー前に参照するナレッジのURI・概要・本文ハッシュ・根拠期間を一覧する。

        Resourcesを直接読めないクライアントでも使える。関連するURIを選び、
        knowledge_read(uri)で本文を読む。根拠期間や適用範囲は本文と合わせて確認する。
        DB接続は不要。起動時に読み込んだ公開Resourceと同じ版を返す。
        """
        return KnowledgeListOutput(resources=[_resource_info(resource) for resource in snapshot], count=len(snapshot))

    return knowledge_list


def build_knowledge_read_tool(resources: Sequence[TextResource]) -> Callable[..., object]:
    """登録済みURIに一致する起動時の本文だけを返す。"""

    snapshot = {str(resource.uri): resource for resource in resources}

    async def knowledge_read(
        uri: Annotated[str, Field(min_length=1, max_length=512, description="knowledge_listが返したResource URI")],
    ) -> KnowledgeReadOutput:
        """knowledge_listで選んだナレッジのMarkdown全文と、URI・本文ハッシュ・根拠期間を取得する。

        予想の馬評価・買い目選定やレース後レビューに関連する手順・判断知識を読むときに使う。
        元Resourceと同じ内容を返す。利用したURI・content_sha256・根拠期間を記録し、
        根拠期間が不明な原則と、期間が明記された知見を区別する。
        DB接続は不要。任意のファイルパスや外部URLは受け付けない。
        """
        resource = snapshot.get(uri)
        if resource is None:
            raise ValueError("knowledge resource is not registered; choose a URI from knowledge_list")
        return KnowledgeReadOutput(**_resource_info(resource).model_dump(), content=resource.text)

    return knowledge_read
