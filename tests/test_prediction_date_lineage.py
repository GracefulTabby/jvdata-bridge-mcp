"""日付メタデータが各SQLの集計後も対象レースへ辿れることを検証する。"""

import pytest
from sqlglot import exp
from sqlglot.lineage import lineage

from jvdata_bridge_mcp.tools.prediction import features, probabilities, scenarios


@pytest.mark.parametrize(
    "statement", [features._SQL, probabilities._SQL, scenarios._SQL], ids=["features", "probabilities", "scenarios"]
)
def test_prediction_history_date_resolves_to_target_race(statement: str) -> None:
    """未解決の列名や、過去走の日付を予測対象日の代わりに出さない。"""

    node = lineage("history_before_date", statement, dialect="postgres")
    leaves = [item for item in node.walk() if not item.downstream]
    assert leaves
    assert all(isinstance(item.expression, exp.Table) for item in leaves)
    assert {item.name for item in leaves} == {"r.race_date"}
    assert {(item.expression.db, item.expression.name) for item in leaves} == {("jv_data", "races")}
