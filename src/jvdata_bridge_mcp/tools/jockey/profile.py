"""騎手プロフィールを直接SQLで取得するMCPツール。"""

from __future__ import annotations

from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._runtime import fetch_rows

JockeyCode = Annotated[str, Field(description="騎手コード")]
JockeyName = Annotated[str, Field(description="騎手名")]


class JockeyProfileItem(BaseModel):
    """騎手マスターのプロフィール1件。"""

    model_config = ConfigDict(extra="forbid")

    jockey_code: str = Field(description="騎手コード")
    jockey_name: str | None = Field(default=None, description="騎手名")
    jockey_name_kana: str | None = Field(default=None, description="騎手名かな")
    jockey_short_name: str | None = Field(default=None, description="騎手略称")
    sex_name: str | None = Field(default=None, description="性別")
    qualification_name: str | None = Field(default=None, description="資格名")
    apprentice_name: str | None = Field(default=None, description="見習区分")
    apprentice_weight_reduction: str | None = Field(default=None, description="見習減量の表記")
    affiliation_name: str | None = Field(default=None, description="所属")
    is_active: bool | None = Field(default=None, description="現役か")
    registration_status: str | None = Field(default=None, description="登録状態")
    licensed_date: str | None = Field(default=None, description="免許日")
    deregistered_date: str | None = Field(default=None, description="抹消日")
    birth_date: str | None = Field(default=None, description="生年月日")


class GetJockeyProfileOutput(BaseModel):
    """騎手マスターのプロフィール。"""

    profiles: list[JockeyProfileItem] = Field(description="騎手プロフィール")


_SQL = """
SELECT
    j.jockey_id AS jockey_code,
    j.jockey_name,
    j.jockey_name_kana,
    j.jockey_short_name,
    j.sex_name,
    j.qualification_name,
    j.apprentice_name,
    j.apprentice_weight_reduction,
    j.affiliation_name,
    j.is_active,
    j.registration_status,
    j.licensed_date,
    j.deregistered_date,
    j.birth_date
FROM jv_mart.jockeys AS j
WHERE (:jockey_code = '' OR j.jockey_id = :jockey_code)
  AND (:jockey_name = '' OR j.jockey_name ILIKE '%' || :jockey_name || '%')
ORDER BY j.is_active DESC, j.jockey_id
LIMIT :limit
"""


def build_get_jockey_profile_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したget_jockey_profile関数を作る。"""

    async def get_jockey_profile(
        jockey_code: JockeyCode = "",
        jockey_name: JockeyName = "",
    ) -> GetJockeyProfileOutput:
        """騎手のプロフィールを取得する。

        騎手名、資格、所属、現役状態、免許日を返す。

        Args:
            jockey_code: 騎手コード。
            jockey_name: 騎手名の部分一致条件。

        Returns:
            騎手プロフィール。
        """

        normalized_jockey_code = jockey_code.strip()
        normalized_jockey_name = jockey_name.strip()
        if not normalized_jockey_code and not normalized_jockey_name:
            raise ValueError("jockey_code or jockey_name is required")
        rows = await fetch_rows(
            database,
            _SQL,
            {"jockey_code": normalized_jockey_code, "jockey_name": normalized_jockey_name, "limit": 100},
        )
        return GetJockeyProfileOutput(profiles=rows)

    return get_jockey_profile
