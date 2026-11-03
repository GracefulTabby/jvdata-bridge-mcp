"""対象出走馬の前走からの条件替わり事実を取得するMCPツール。"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Callable

from pydantic import BaseModel, ConfigDict, Field

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.tools._race_boundary import before_target_race_sql
from jvdata_bridge_mcp.tools._runtime import fetch_rows
from jvdata_bridge_mcp.tools.race._class_facts import OfficialClassFact, class_join_sql

RaceCode = Annotated[str, Field(description="対象レースを識別する16桁のレースコード")]
RecentRuns = Annotated[int, Field(description="比較用に取得する近走数")]


class ConditionValueChange(BaseModel):
    """現在値と前走値の比較。"""

    model_config = ConfigDict(extra="forbid")

    current: int | float | str | None = Field(default=None, description="今回の値")
    previous: int | float | str | None = Field(default=None, description="前走の値")
    delta: int | float | None = Field(default=None, description="今回と前走の差")
    direction: str | None = Field(default=None, description="変更方向")
    changed: bool | None = Field(default=None, description="値が変わったか")


class RaceClassChange(BaseModel):
    """クラス変更の比較。クラス順位がない場合はunknownを返す。"""

    model_config = ConfigDict(extra="forbid")

    current: str | None = Field(default=None, description="今回のクラス")
    previous: str | None = Field(default=None, description="前走のクラス")
    change: str = Field(default="unknown", description="クラス変更")
    current_rank: int | None = Field(default=None, description="今回のクラス順位")
    previous_rank: int | None = Field(default=None, description="前走のクラス順位")
    rank_delta: int | None = Field(default=None, description="クラス順位の差")
    comparable: bool | None = None
    comparability_reason: str = "domain_unknown"
    current_domain: str | None = None
    previous_domain: str | None = None


class RaceConditionChanges(BaseModel):
    """前走から今回への条件変更。"""

    model_config = ConfigDict(extra="forbid")

    distance: ConditionValueChange = Field(description="距離変更")
    surface: ConditionValueChange = Field(description="走路の材質のみ。回り・内外は別項目")
    race_domain: ConditionValueChange = Field(default_factory=ConditionValueChange)
    course_direction: ConditionValueChange = Field(default_factory=ConditionValueChange)
    course_layout: ConditionValueChange = Field(default_factory=ConditionValueChange)
    track_configuration: ConditionValueChange = Field(default_factory=ConditionValueChange)
    racecourse: ConditionValueChange = Field(description="競馬場変更")
    race_class: RaceClassChange = Field(description="クラス変更")
    track_condition: ConditionValueChange = Field(description="馬場状態変更")
    interval_days: int | None = Field(default=None, description="前走からの間隔。日")
    interval_category: str | None = Field(default=None, description="間隔区分")
    jockey_changed: bool | None = Field(default=None, description="騎手が変わったか")
    assigned_weight_change: float | None = Field(default=None, description="負担重量の変更。kg")
    assigned_weight_comparable: bool | None = None
    distance_comparable: bool | None = None
    interval_semantics: str = "days_since_last_stored_JRA_start; outside_JRA_activity_unknown; not_verified_rest"


class RaceCurrentConditions(BaseModel):
    """今回の出走条件。"""

    model_config = ConfigDict(extra="forbid")

    jockey_name: str | None = Field(default=None, description="今回の騎手")
    trainer_name: str | None = Field(default=None, description="今回の調教師")
    assigned_weight: float | None = Field(default=None, description="今回の負担重量。kg")
    racecourse_name: str | None = Field(default=None, description="今回の競馬場")
    track: str | None = Field(default=None, description="今回の芝・ダート・回り")
    distance: int | None = Field(default=None, description="今回の距離。m")
    class_fact: OfficialClassFact | None = None
    class_name: str | None = Field(default=None, description="今回のクラス")
    track_condition: str | None = Field(default=None, description="今回の馬場状態")


class FinishPositionFact(BaseModel):
    """着順の原文と数値。"""

    model_config = ConfigDict(extra="forbid")

    raw: str | None = Field(default=None, description="JRAデータの着順原文")
    value: int | None = Field(default=None, description="着順")
    status: str = Field(default="unavailable", description="着順データの状態")


class RunningTimeFact(BaseModel):
    """走破タイム。"""

    model_config = ConfigDict(extra="forbid")

    raw: str | None = Field(default=None, description="走破タイムの原文")
    seconds: float | None = Field(default=None, description="走破タイム。秒")
    formatted: str | None = Field(default=None, description="走破タイム。分:秒.1")


class TimeDifferenceFact(BaseModel):
    """勝ち馬とのタイム差。"""

    model_config = ConfigDict(extra="forbid")

    raw: str | None = Field(default=None, description="タイム差の原文")
    seconds: float | None = Field(default=None, description="タイム差。秒")


class CornerPositionsFact(BaseModel):
    """各コーナーの通過順位。"""

    model_config = ConfigDict(extra="forbid")

    first_corner: int | None = Field(default=None, description="第1コーナー")
    second_corner: int | None = Field(default=None, description="第2コーナー")
    third_corner: int | None = Field(default=None, description="第3コーナー")
    fourth_corner: int | None = Field(default=None, description="第4コーナー")


class RaceLastStart(BaseModel):
    """対象馬の直近完了走。完全速報を含み、状態を保持する。"""

    model_config = ConfigDict(extra="forbid")

    is_final_result: bool | None = None
    result_status_code: str | None = None
    fact_id: str | None = None
    run_index: int = Field(default=1, description="対象馬内の近走順位")
    race_code: str = Field(description="前走レースコード")
    race_date: str | None = Field(default=None, description="前走開催日")
    racecourse_name: str | None = Field(default=None, description="前走競馬場")
    race_name: str | None = Field(default=None, description="前走レース名")
    class_fact: OfficialClassFact | None = None
    class_name: str | None = Field(default=None, description="前走クラス")
    track: str | None = Field(default=None, description="前走の芝・ダート・回り")
    distance: int | None = Field(default=None, description="前走距離。m")
    track_condition: str | None = Field(default=None, description="前走馬場状態")
    field_size: int | None = Field(default=None, description="前走の分析対象頭数")
    finish_position: FinishPositionFact = Field(description="前走着順")
    popularity: int | None = Field(default=None, description="前走単勝人気")
    win_odds: float | None = Field(default=None, description="前走単勝オッズ")
    running_time: RunningTimeFact = Field(description="前走走破タイム")
    time_difference: TimeDifferenceFact = Field(description="前走タイム差")
    last_3f: float | None = Field(default=None, description="前走上がり3ハロン")
    corner_positions: CornerPositionsFact = Field(description="前走コーナー通過順位")
    jockey_name: str | None = Field(default=None, description="前走騎手")
    trainer_name: str | None = Field(default=None, description="前走調教師")
    assigned_weight: float | None = Field(default=None, description="前走負担重量。kg")
    body_weight: str | None = Field(default=None, description="前走馬体重。未収録時null")


class RecentConditionContext(BaseModel):
    """条件比較用の近走コンテキスト。"""

    model_config = ConfigDict(extra="forbid")

    fact_id: str | None = None
    run_index: int = Field(description="対象馬内の近走順位")
    race_date: str | None = Field(default=None, description="開催日")
    racecourse_name: str | None = Field(default=None, description="競馬場")
    track: str | None = Field(default=None, description="芝・ダート・回り")
    distance: int | None = Field(default=None, description="距離。m")
    class_fact: OfficialClassFact | None = None
    class_name: str | None = Field(default=None, description="クラス")


class RaceConditionChangeItem(BaseModel):
    """対象馬1頭の前走比較事実。"""

    model_config = ConfigDict(extra="forbid")

    horse_number: str = Field(description="馬番")
    horse_name: str | None = Field(default=None, description="馬名")
    horse_id: str = Field(description="競走馬ID")
    current_conditions: RaceCurrentConditions = Field(description="今回の条件")
    last_start: RaceLastStart | None = Field(default=None, description="直近確定走")
    condition_changes: RaceConditionChanges = Field(description="前走からの条件変更")
    career: dict[str, object] = Field(default_factory=dict)
    recent_contexts: list[RecentConditionContext] = Field(description="比較に使った近走条件")


class RaceContext(BaseModel):
    """対象レースの条件と出走頭数。"""

    model_config = ConfigDict(extra="forbid")

    race_code: str
    race_name: str | None = None
    race_date: str | None = None
    start_time: str | None = None
    racecourse_name: str | None = None
    track: str | None = None
    distance: int | None = None
    class_name: str | None = None
    track_condition: str | None = None
    field_size: int | None = None
    declared_entry_count: int | None = None
    starter_count: int | None = None
    active_entry_count: int | None = None


class RaceConditionChangeFactsOutput(BaseModel):
    """対象出走馬の前走からの条件替わり事実。"""

    result_policy: str = "complete_official_or_provisional; provisional_can_be_revised"

    race_code: str = Field(description="対象レースコード")
    recent_runs_requested: int = Field(description="比較用に取得した近走数")
    race_context: RaceContext | None = Field(default=None, description="対象レース条件")
    items: list[RaceConditionChangeItem] = Field(description="馬ごとの条件替わり事実")
    count: int = Field(description="条件比較を返した馬数")


def _track_description(track_name: str | None) -> str | None:
    """read modelの走路名を芝・ダートの表示値へ正規化する。"""

    if not track_name:
        return None
    if "障害" in track_name:
        return track_name.strip()
    surface = "ダート" if "ダート" in track_name else "芝" if "芝" in track_name else None
    direction = "左" if "左回り" in track_name else "右" if "右回り" in track_name else None
    outer = (
        "外→内"
        if "外→内" in track_name
        else "内→外"
        if "内→外" in track_name
        else "外"
        if "外回り" in track_name
        else "内"
        if "内回り" in track_name
        else ""
    )
    if surface and direction:
        return f"{surface}・{direction}{outer}"
    return track_name.strip()


def track_components(track: object) -> dict[str, str | None]:
    """材質、平地/障害、回り、内外を独立に読む。未知の内回りは推測しない。"""

    name = str(track or "")
    return {
        "domain": "jump" if "障害" in name else "flat" if "芝" in name or "ダート" in name else None,
        "material": "芝・ダート"
        if "芝・ダート" in name and "障害" in name
        else "ダート"
        if "ダート" in name
        else "芝"
        if "芝" in name
        else None,
        "direction": "straight" if "直線" in name else "left" if "左" in name else "right" if "右" in name else None,
        "layout": "outer_to_inner"
        if "外→内" in name
        else "inner_to_outer"
        if "内→外" in name
        else "outer"
        if "外" in name
        else "inner"
        if "内" in name
        else None,
    }


def _value_change(current: object, previous: object) -> ConditionValueChange:
    return ConditionValueChange(
        current=current,
        previous=previous,
        changed=current != previous if current is not None and previous is not None else None,
    )


def _course_name(value: object) -> str | None:
    """競馬場名から「競馬場」の接尾辞を除く。"""

    if value is None:
        return None
    name = str(value).strip()
    return name.removesuffix("競馬場") or None


def _class_display(value: object) -> str | None:
    """read modelのクラス名を公開用の表示トークンへ正規化する。"""

    if value is None:
        return None
    label = str(value).strip()
    normalized = label.upper().replace(" ", "")
    # グレードはローマ数字で表示する。長いトークンを先に照合し、
    # GIIIをGII/GIとして誤分類しない。
    for source, display in (
        ("GIII", "GIII"),
        ("GII", "GII"),
        ("GI", "GI"),
        ("G3", "GIII"),
        ("G2", "GII"),
        ("G1", "GI"),
        ("JPN1", "JPN1"),
        ("JPN2", "JPN2"),
        ("JPN3", "JPN3"),
    ):
        if source in normalized:
            return display
    return label


def _class_rank(label: str | None) -> int | None:
    """クラス表記に対応する比較用の順序値を返す。"""

    if label is None:
        return None

    normalized = label.upper().replace(" ", "")
    ranked_keywords = (
        # 上位トークンを先に判定する。例えばGIIIはGII/GIにも部分一致する。
        ("GIII", 70),
        ("GII", 80),
        ("GI", 90),
        ("G1", 90),
        ("JPN1", 90),
        ("G2", 80),
        ("JPN2", 80),
        ("G3", 70),
        ("JPN3", 70),
        ("LISTED", 65),
        ("L", 65),
        ("オープン", 60),
        ("OP", 60),
        ("3勝", 50),
        ("1600万", 50),
        ("2勝", 40),
        ("1000万", 40),
        ("1勝", 30),
        ("500万", 30),
        ("未勝利", 10),
        ("新馬", 5),
    )
    for keyword, rank in ranked_keywords:
        if keyword in normalized:
            return rank
    return None


def _class_change_payload(current: str | None, previous: str | None) -> dict[str, object]:
    """現在・前走のクラス情報と順序値の差をまとめる。"""

    current_rank = _class_rank(current)
    previous_rank = _class_rank(previous)
    rank_delta = current_rank - previous_rank if current_rank is not None and previous_rank is not None else None
    if current_rank is None or previous_rank is None:
        change = "unknown"
    elif current_rank > previous_rank:
        change = "upgrade"
    elif current_rank < previous_rank:
        change = "downgrade"
    else:
        change = "same"
    return {
        "current": current,
        "previous": previous,
        "change": change,
        "current_rank": current_rank,
        "previous_rank": previous_rank,
        "rank_delta": rank_delta,
    }


def _interval_category(interval_days: int | None) -> str | None:
    """前走間隔を日数に応じた表示区分へ分類する。"""

    if interval_days is None:
        return None
    if interval_days <= 14:
        return "中1〜2週"
    if interval_days <= 28:
        return "中3〜4週"
    if interval_days <= 56:
        return "中5〜8週"
    return "2か月以上"


def _as_date(value: object) -> date | None:
    """fetch_rowsのISO値を日付へ変換する。"""

    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _format_running_time(seconds: float | None) -> str | None:
    """秒値をJRA表示に近い分:秒.1へ変換する。"""

    if seconds is None or seconds < 0:
        return None
    minutes, remaining = divmod(round(seconds * 10), 600)
    return f"{minutes}:{remaining / 10:04.1f}"


def _raw_running_time(seconds: float | None) -> str | None:
    """秒値をJRAの ``MMSS`` (1/10秒) raw表記へ戻す。"""

    if seconds is None or seconds < 0:
        return None
    total_tenths = round(seconds * 10)
    minutes, remainder = divmod(total_tenths, 600)
    whole_seconds, tenths = divmod(remainder, 10)
    return f"{minutes}{whole_seconds:02d}{tenths}"


def _raw_time_difference(seconds: float | None) -> str | None:
    """秒差をJRAの符号付き十分の一秒raw表記へ戻す。"""

    if seconds is None:
        return None
    sign = "+" if seconds >= 0 else "-"
    return f"{sign}{round(abs(seconds) * 10):03d}"


_SQL = f"""
WITH race_context AS MATERIALIZED (
    SELECT
        r.race_id,
        r.race_date,
        r.race_name, r.grade_code, r.grade_name,
        CAST(r.scheduled_start_at AS time) AS scheduled_start_time,
        r.venue_name,
        r.track_name,
        r.distance_meters,
        current_class.class_name, current_class.class_fact,
        CASE WHEN r.track_name LIKE '%芝%' THEN r.turf_condition_name ELSE r.dirt_condition_name END AS track_condition,
        r.registered_horses,
        r.starters,
        r.track_name AS track_description
    FROM jv_data.races AS r
    {class_join_sql("r", "current_class")}
    WHERE r.race_id = :race_code
), target_cards AS MATERIALIZED (
    SELECT DISTINCT ON (c.race_id, c.horse_number)
        c.race_id,
        c.horse_number,
        c.horse_id,
        c.horse_name,
        c.jockey_name,
        c.trainer_name,
        c.assigned_weight_kg, c.horse_age
    FROM jv_mart.race_cards AS c
    WHERE c.race_id = :race_code
      AND c.horse_number IS NOT NULL

    ORDER BY c.race_id, c.horse_number, c.horse_id
), ranked_history AS (
    -- 馬ごとに別々のLATERAL検索をせず、対象馬の履歴を一度に順位付けする。
    SELECT
        t.horse_number AS target_horse_number,
        t.horse_id AS target_horse_id,
        h.race_id,
        h.race_date,
        h.race_name,
        h.venue_name,
        h.track_name,
        h.distance_meters,
        h.finish_position,
        h.is_complete_result,h.is_final_result,h.result_status_code,
        h.final_win_odds,
        h.final_win_popularity,
        h.elapsed_seconds,
        h.time_difference_seconds,
        h.last_three_furlongs_seconds,
        h.corner_1_position,
        h.corner_2_position,
        h.corner_3_position,
        h.corner_4_position,
        ROW_NUMBER() OVER (
            PARTITION BY t.horse_id
            ORDER BY h.race_date DESC, h.race_id DESC
        ) AS run_index
    FROM target_cards AS t
    CROSS JOIN race_context AS rc
    JOIN jv_mart.horse_runs AS h
      ON h.horse_id = t.horse_id
     AND {before_target_race_sql("h", "rc.race_date")}
     AND h.is_complete_result IS TRUE
     AND (h.is_final_result IS TRUE OR h.result_status_code='provisional_complete')
     AND h.is_starter IS TRUE
), history_details AS MATERIALIZED (
    SELECT
        h.*,
        history_class.class_name AS previous_class_name, history_class.class_fact AS previous_class_fact,
        CASE WHEN h.track_name LIKE '%芝%' THEN pr.turf_condition_name ELSE pr.dirt_condition_name END AS previous_track_condition,
        COALESCE(pr.starters, pr.registered_horses) AS previous_field_size,
        previous_card.jockey_name AS previous_jockey_name,
        previous_card.trainer_name AS previous_trainer_name,
        previous_card.assigned_weight_kg AS previous_assigned_weight,
        previous_card.body_weight_kg AS previous_body_weight,
        previous_card.body_weight_change_kg AS previous_body_weight_change
    FROM ranked_history AS h
    LEFT JOIN jv_data.races AS pr ON pr.race_id = h.race_id
    LEFT JOIN LATERAL (
        SELECT
            c2.horse_age, c2.jockey_name,
            c2.trainer_name,
            c2.assigned_weight_kg,
            c2.body_weight_kg,
            c2.body_weight_change_kg
        FROM jv_mart.race_cards AS c2
        WHERE c2.race_id = h.race_id
          AND c2.horse_id = h.target_horse_id
        ORDER BY c2.horse_number
        LIMIT 1
    ) AS previous_card ON TRUE
    {class_join_sql("pr", "history_class", "previous_card.horse_age")}
    WHERE h.run_index <= :recent_runs
), history_summary AS (
    SELECT
        h.target_horse_number,
        h.target_horse_id,
        MAX(h.race_id) FILTER (WHERE h.run_index = 1) AS previous_race_code,
        MAX(h.race_date) FILTER (WHERE h.run_index = 1) AS previous_race_date,
        BOOL_AND(h.is_final_result) FILTER (WHERE h.run_index = 1) AS previous_is_final_result,
        MAX(h.result_status_code) FILTER (WHERE h.run_index = 1) AS previous_result_status_code,
        MAX(h.race_name) FILTER (WHERE h.run_index = 1) AS previous_race_name,
        MAX(h.venue_name) FILTER (WHERE h.run_index = 1) AS previous_racecourse_name,
        MAX(h.track_name) FILTER (WHERE h.run_index = 1) AS previous_track_name,
        MAX(h.distance_meters) FILTER (WHERE h.run_index = 1) AS previous_distance,
        (jsonb_agg(h.previous_class_fact ORDER BY h.run_index)->0) AS previous_class_fact,
        MAX(h.previous_class_name) FILTER (WHERE h.run_index = 1) AS previous_class_name,
        MAX(h.previous_track_condition) FILTER (WHERE h.run_index = 1) AS previous_track_condition,
        MAX(h.previous_field_size) FILTER (WHERE h.run_index = 1) AS previous_field_size,
        MAX(h.finish_position) FILTER (WHERE h.run_index = 1) AS previous_finish_position,
        MAX(h.final_win_odds) FILTER (WHERE h.run_index = 1) AS previous_win_odds,
        MAX(h.final_win_popularity) FILTER (WHERE h.run_index = 1) AS previous_popularity,
        MAX(h.elapsed_seconds) FILTER (WHERE h.run_index = 1) AS previous_elapsed_seconds,
        MAX(h.time_difference_seconds) FILTER (WHERE h.run_index = 1) AS previous_time_difference_seconds,
        MAX(h.last_three_furlongs_seconds) FILTER (WHERE h.run_index = 1) AS previous_last_3f,
        MAX(h.corner_1_position) FILTER (WHERE h.run_index = 1) AS previous_corner_1,
        MAX(h.corner_2_position) FILTER (WHERE h.run_index = 1) AS previous_corner_2,
        MAX(h.corner_3_position) FILTER (WHERE h.run_index = 1) AS previous_corner_3,
        MAX(h.corner_4_position) FILTER (WHERE h.run_index = 1) AS previous_corner_4,
        MAX(h.previous_jockey_name) FILTER (WHERE h.run_index = 1) AS previous_jockey_name,
        MAX(h.previous_trainer_name) FILTER (WHERE h.run_index = 1) AS previous_trainer_name,
        MAX(h.previous_assigned_weight) FILTER (WHERE h.run_index = 1) AS previous_assigned_weight,
        MAX(h.previous_body_weight) FILTER (WHERE h.run_index = 1) AS previous_body_weight,
        MAX(h.previous_body_weight_change) FILTER (WHERE h.run_index = 1) AS previous_body_weight_change,
        jsonb_agg(
            jsonb_build_object(
                'fact_id', 'jra-run:' || h.race_id || ':' || h.target_horse_id,
                'class_fact', h.previous_class_fact,
                'run_index', h.run_index,
                'race_date', h.race_date,
                'racecourse_name', h.venue_name,
                'track', h.track_name,
                'distance', h.distance_meters,
                'class_name', h.previous_class_name
            ) ORDER BY h.run_index
        ) AS recent_contexts
    FROM history_details AS h
    GROUP BY h.target_horse_number, h.target_horse_id
)
SELECT
    t.race_id AS race_code,
    rc.race_date,
    rc.race_name,
    rc.scheduled_start_time,
    rc.venue_name AS current_racecourse_name,
    rc.track_description AS current_track,
    rc.distance_meters AS current_distance,
    current_horse_class.class_name AS current_class_name, current_horse_class.class_fact AS current_class_fact,
    t.horse_age,
    NULL::text AS current_track_condition,
    rc.registered_horses AS declared_entry_count,
    NULL::int AS starter_count,
    t.horse_number,
    t.horse_id,
    t.horse_name,
    t.jockey_name AS current_jockey_name,
    t.trainer_name AS current_trainer_name,
    t.assigned_weight_kg AS current_assigned_weight,
    history.previous_race_code,
    history.previous_race_date,
    history.previous_is_final_result,history.previous_result_status_code,
    history.previous_race_name,
    history.previous_racecourse_name,
    history.previous_track_name,
    history.previous_distance,
    history.previous_class_name, history.previous_class_fact,
    history.previous_track_condition,
    history.previous_field_size,
    history.previous_finish_position,
    history.previous_win_odds,
    history.previous_popularity,
    history.previous_elapsed_seconds,
    history.previous_time_difference_seconds,
    history.previous_last_3f,
    history.previous_corner_1,
    history.previous_corner_2,
    history.previous_corner_3,
    history.previous_corner_4,
    history.previous_jockey_name,
    history.previous_trainer_name,
    history.previous_assigned_weight,
    history.previous_body_weight,
    history.previous_body_weight_change,
    COALESCE(history.recent_contexts, '[]'::jsonb) AS recent_contexts,
    career.facts AS career
FROM target_cards AS t
CROSS JOIN race_context AS rc
{class_join_sql("rc", "current_horse_class", "t.horse_age")}
LEFT JOIN history_summary AS history
  ON history.target_horse_number = t.horse_number
 AND history.target_horse_id = t.horse_id
LEFT JOIN LATERAL (
    SELECT jsonb_build_object(
        'scope', 'stored_complete_jra_before_target_and_now; provisional_can_be_revised',
        'all_stored_runs_searched', TRUE, 'outside_jra_history', 'unknown',
        'stored_jra_starts', COUNT(*),
        'finished_starts', COUNT(*) FILTER (WHERE ch.is_finisher IS TRUE),
        'same_surface_starts', COUNT(*) FILTER (WHERE
            CASE WHEN ch.track_name LIKE '%障害%' THEN 'jump' WHEN ch.track_name LIKE '%芝%' THEN 'turf'
                WHEN ch.track_name LIKE '%ダート%' THEN 'dirt' END =
            CASE WHEN rc.track_name LIKE '%障害%' THEN 'jump' WHEN rc.track_name LIKE '%芝%' THEN 'turf'
                WHEN rc.track_name LIKE '%ダート%' THEN 'dirt' END),
        'same_course_distance_starts', COUNT(*) FILTER (WHERE ch.venue_name = rc.venue_name
            AND ch.track_name = rc.track_name AND ch.distance_meters = rc.distance_meters),
        'same_distance_starts', COUNT(*) FILTER (WHERE ch.distance_meters = rc.distance_meters),
        'same_distance_semantics', 'distance_only; can_include_other_material_or_jump',
        'same_surface_distance_starts', COUNT(*) FILTER (WHERE ch.distance_meters = rc.distance_meters AND
            CASE WHEN ch.track_name LIKE '%障害%' THEN 'jump' WHEN ch.track_name LIKE '%芝%' THEN 'turf'
                WHEN ch.track_name LIKE '%ダート%' THEN 'dirt' END =
            CASE WHEN rc.track_name LIKE '%障害%' THEN 'jump' WHEN rc.track_name LIKE '%芝%' THEN 'turf'
                WHEN rc.track_name LIKE '%ダート%' THEN 'dirt' END),
        'last_same_surface_date', MAX(ch.race_date) FILTER (WHERE
            CASE WHEN ch.track_name LIKE '%障害%' THEN 'jump' WHEN ch.track_name LIKE '%芝%' THEN 'turf'
                WHEN ch.track_name LIKE '%ダート%' THEN 'dirt' END =
            CASE WHEN rc.track_name LIKE '%障害%' THEN 'jump' WHEN rc.track_name LIKE '%芝%' THEN 'turf'
                WHEN rc.track_name LIKE '%ダート%' THEN 'dirt' END),
        'earliest_stored_race_date', MIN(ch.race_date), 'last_stored_race_date', MAX(ch.race_date)
    ) AS facts
    FROM jv_mart.horse_runs ch WHERE ch.horse_id = t.horse_id
      AND {before_target_race_sql("ch", "rc.race_date")} AND substring(ch.race_id FROM 9 FOR 2) BETWEEN '01' AND '10'
      AND (ch.is_final_result IS TRUE OR ch.result_status_code='provisional_complete') AND ch.is_complete_result IS TRUE AND ch.is_starter IS TRUE
) career ON TRUE
ORDER BY t.horse_number
LIMIT :limit
"""


def _make_last_start(row: dict[str, object]) -> RaceLastStart | None:
    """SQLの前走列を条件変化の入れ子構造へ変換する。"""

    race_code = row.get("previous_race_code")
    if not race_code:
        return None
    finish = row.get("previous_finish_position")
    finish_value = int(finish) if finish is not None else None
    finish_raw = f"{finish_value:02d}" if finish_value is not None else None
    elapsed = float(row["previous_elapsed_seconds"]) if row.get("previous_elapsed_seconds") is not None else None
    time_difference = (
        float(row["previous_time_difference_seconds"])
        if row.get("previous_time_difference_seconds") is not None
        else None
    )
    return RaceLastStart(
        fact_id=f"jra-run:{race_code}:{row.get('horse_id')}",
        is_final_result=row.get("previous_is_final_result"),
        result_status_code=row.get("previous_result_status_code"),
        class_fact=row.get("previous_class_fact"),
        run_index=1,
        race_code=str(race_code),
        race_date=row.get("previous_race_date"),
        racecourse_name=_course_name(row.get("previous_racecourse_name")),
        race_name=row.get("previous_race_name"),
        class_name=_class_display(row.get("previous_class_name")),
        track=_track_description(row.get("previous_track_name")),
        distance=int(row["previous_distance"]) if row.get("previous_distance") is not None else None,
        track_condition=row.get("previous_track_condition"),
        field_size=int(row["previous_field_size"]) if row.get("previous_field_size") is not None else None,
        finish_position=FinishPositionFact(
            raw=finish_raw,
            value=finish_value,
            status="available" if finish_value is not None else "unavailable",
        ),
        popularity=int(row["previous_popularity"]) if row.get("previous_popularity") is not None else None,
        win_odds=float(row["previous_win_odds"]) if row.get("previous_win_odds") is not None else None,
        running_time=RunningTimeFact(
            raw=_raw_running_time(elapsed),
            seconds=elapsed,
            formatted=_format_running_time(elapsed),
        ),
        time_difference=TimeDifferenceFact(
            raw=_raw_time_difference(time_difference),
            seconds=time_difference,
        ),
        last_3f=float(row["previous_last_3f"]) if row.get("previous_last_3f") is not None else None,
        corner_positions=CornerPositionsFact(
            first_corner=int(row["previous_corner_1"]) if row.get("previous_corner_1") is not None else None,
            second_corner=int(row["previous_corner_2"]) if row.get("previous_corner_2") is not None else None,
            third_corner=int(row["previous_corner_3"]) if row.get("previous_corner_3") is not None else None,
            fourth_corner=int(row["previous_corner_4"]) if row.get("previous_corner_4") is not None else None,
        ),
        jockey_name=row.get("previous_jockey_name"),
        trainer_name=row.get("previous_trainer_name"),
        assigned_weight=(
            float(row["previous_assigned_weight"]) if row.get("previous_assigned_weight") is not None else None
        ),
        body_weight=(
            f"{int(row['previous_body_weight'])}({int(row['previous_body_weight_change']):+04d})"
            if row.get("previous_body_weight") is not None and row.get("previous_body_weight_change") is not None
            else str(int(row["previous_body_weight"]))
            if row.get("previous_body_weight") is not None
            else None
        ),
    )


def _build_condition_changes(row: dict[str, object], previous: RaceLastStart | None) -> RaceConditionChanges:
    """今回条件と前走条件の差分を算出する。"""

    current_distance = int(row["current_distance"]) if row.get("current_distance") is not None else None
    previous_distance = previous.distance if previous else None
    delta = (
        current_distance - previous_distance
        if current_distance is not None and previous_distance is not None
        else None
    )
    direction = (
        "延長"
        if delta is not None and delta > 0
        else "短縮"
        if delta is not None and delta < 0
        else "同距離"
        if delta == 0
        else None
    )
    current_track = _track_description(row.get("current_track"))
    previous_track = previous.track if previous else None
    current_parts = track_components(current_track)
    previous_parts = track_components(previous_track)
    domains_known = current_parts["domain"] is not None and previous_parts["domain"] is not None
    comparable = current_parts["domain"] == previous_parts["domain"] if domains_known else None
    current_course = _course_name(row.get("current_racecourse_name"))
    previous_course = previous.racecourse_name if previous else None
    current_condition = row.get("current_track_condition")
    previous_condition = previous.track_condition if previous else None
    current_class = _class_display(row.get("current_class_name"))
    previous_class = previous.class_name if previous else None
    class_change = _class_change_payload(current_class, previous_class)
    class_change.update(
        comparable=comparable,
        current_domain=current_parts["domain"],
        previous_domain=previous_parts["domain"],
        comparability_reason="same_race_domain"
        if comparable
        else "flat_jump_domains_differ"
        if comparable is False
        else "domain_unknown",
    )
    if comparable is not True:
        class_change.update(change="incomparable" if comparable is False else "unknown", rank_delta=None)
    interval_days = None
    if previous and previous.race_date and row.get("race_date"):
        previous_date = _as_date(previous.race_date)
        current_date = _as_date(row["race_date"])
        if previous_date and current_date:
            interval_days = (current_date - previous_date).days
    current_weight = float(row["current_assigned_weight"]) if row.get("current_assigned_weight") is not None else None
    previous_weight = previous.assigned_weight if previous else None
    weight_change = (
        current_weight - previous_weight if current_weight is not None and previous_weight is not None else None
    )
    jockey_changed = None if previous is None else row.get("current_jockey_name") != previous.jockey_name
    return RaceConditionChanges(
        distance=ConditionValueChange(
            current=current_distance,
            previous=previous_distance,
            delta=delta,
            direction=direction,
            changed=(delta != 0 if delta is not None else None),
        ),
        surface=_value_change(current_parts["material"], previous_parts["material"]),
        race_domain=_value_change(current_parts["domain"], previous_parts["domain"]),
        course_direction=_value_change(current_parts["direction"], previous_parts["direction"]),
        course_layout=_value_change(current_parts["layout"], previous_parts["layout"]),
        track_configuration=_value_change(current_track, previous_track),
        racecourse=ConditionValueChange(
            current=current_course,
            previous=previous_course,
            changed=(current_course != previous_course if previous_course is not None else None),
        ),
        race_class=RaceClassChange(**class_change),
        track_condition=ConditionValueChange(
            current=current_condition,
            previous=previous_condition,
            changed=(current_condition != previous_condition if previous_condition is not None else None),
        ),
        interval_days=interval_days,
        interval_category=_interval_category(interval_days),
        jockey_changed=jockey_changed,
        assigned_weight_change=weight_change,
        assigned_weight_comparable=comparable,
        distance_comparable=comparable,
    )


def build_race_condition_change_facts_tool(database: JvDataDatabase) -> Callable[..., object]:
    """共有read-only DBを注入したrace_condition_change_facts関数を作る。"""

    async def race_condition_change_facts(
        race_code: RaceCode,
        recent_runs: RecentRuns = 4,
    ) -> RaceConditionChangeFactsOutput:
        """対象レースの各出走馬について、前走からの条件替わり事実を返す。"""

        bounded_recent_runs = max(1, min(recent_runs, 20))
        rows = await fetch_rows(
            database,
            _SQL,
            {"race_code": race_code, "recent_runs": bounded_recent_runs, "limit": 100},
        )
        items: list[RaceConditionChangeItem] = []
        context: RaceContext | None = None
        for row in rows:
            if context is None:
                context = RaceContext(
                    race_code=str(row.get("race_code") or race_code),
                    race_name=row.get("race_name"),
                    race_date=row.get("race_date"),
                    start_time=row.get("scheduled_start_time"),
                    racecourse_name=_course_name(row.get("current_racecourse_name")),
                    track=row.get("current_track"),
                    distance=(int(row["current_distance"]) if row.get("current_distance") is not None else None),
                    class_name=_class_display(row.get("current_class_name")),
                    track_condition=row.get("current_track_condition"),
                    field_size=(int(row["starter_count"]) if row.get("starter_count") is not None else len(rows)),
                    declared_entry_count=(
                        int(row["declared_entry_count"]) if row.get("declared_entry_count") is not None else None
                    ),
                    starter_count=(int(row["starter_count"]) if row.get("starter_count") is not None else None),
                    active_entry_count=len(rows),
                )
            previous = _make_last_start(row)
            contexts = [
                RecentConditionContext(
                    fact_id=value.get("fact_id"),
                    class_fact=value.get("class_fact"),
                    run_index=int(value["run_index"]),
                    race_date=value.get("race_date"),
                    racecourse_name=_course_name(value.get("racecourse_name")),
                    track=_track_description(value.get("track")),
                    distance=(int(value["distance"]) if value.get("distance") is not None else None),
                    class_name=_class_display(value.get("class_name")),
                )
                for value in row.get("recent_contexts", [])
            ]
            items.append(
                RaceConditionChangeItem(
                    horse_number=f"{int(row['horse_number']):02d}",
                    horse_name=row.get("horse_name"),
                    horse_id=str(row["horse_id"]),
                    current_conditions=RaceCurrentConditions(
                        class_fact=row.get("current_class_fact"),
                        jockey_name=row.get("current_jockey_name"),
                        trainer_name=row.get("current_trainer_name"),
                        assigned_weight=(
                            float(row["current_assigned_weight"])
                            if row.get("current_assigned_weight") is not None
                            else None
                        ),
                        racecourse_name=_course_name(row.get("current_racecourse_name")),
                        track=row.get("current_track"),
                        distance=(int(row["current_distance"]) if row.get("current_distance") is not None else None),
                        class_name=_class_display(row.get("current_class_name")),
                        track_condition=row.get("current_track_condition"),
                    ),
                    last_start=previous,
                    condition_changes=_build_condition_changes(row, previous),
                    recent_contexts=contexts,
                    career=row.get("career") or {},
                )
            )
        return RaceConditionChangeFactsOutput(
            race_code=race_code,
            recent_runs_requested=bounded_recent_runs,
            race_context=context,
            items=items,
            count=len(items),
        )

    return race_condition_change_facts
