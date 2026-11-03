"""全頭揃った速報と、成績段階の確定を区別する。"""

from typing import Annotated, Literal

from pydantic import Field

ResultScope = Annotated[
    Literal["complete", "official"],
    Field(description="complete: 完全な成績と全頭揃った速報。official: 成績段階だけ。速報は改訂可能"),
]


def include_provisional(scope: ResultScope) -> bool:
    if scope not in {"complete", "official"}:
        raise ValueError("result_scope must be complete or official")
    return scope == "complete"
