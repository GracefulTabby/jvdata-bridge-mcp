"""canonicalなMCP公開関数を、カテゴリ順に組み立てる。"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from mcp.server.fastmcp.resources.types import TextResource

from jvdata_bridge_mcp.database import JvDataDatabase
from jvdata_bridge_mcp.knowledge_resources import load_knowledge_resources
from jvdata_bridge_mcp.sql_gateway import JvDataSqlGateway, JvDataSqlGatewayProtocol
from jvdata_bridge_mcp.tools.bloodline.distance_bias import build_bloodline_distance_bias_tool
from jvdata_bridge_mcp.tools.bloodline.form_profile import build_bloodline_form_profile_tool
from jvdata_bridge_mcp.tools.bloodline.pedigree import build_bloodline_fetch_pedigree_tool
from jvdata_bridge_mcp.tools.bloodline.scoreboard import build_bloodline_scoreboard_tool
from jvdata_bridge_mcp.tools.course.advantage import build_course_advantage_analyzer_tool
from jvdata_bridge_mcp.tools.course.distance_insight import build_course_distance_insight_tool
from jvdata_bridge_mcp.tools.course.frame_style_stats import build_course_frame_style_stats_tool
from jvdata_bridge_mcp.tools.horse.compare import build_compare_horses_tool
from jvdata_bridge_mcp.tools.horse.details import build_get_horse_details_tool
from jvdata_bridge_mcp.tools.horse.history import build_analyze_horse_race_history_tool
from jvdata_bridge_mcp.tools.horse.performance import build_analyze_horse_performance_tool
from jvdata_bridge_mcp.tools.horse.search import build_search_horses_tool
from jvdata_bridge_mcp.tools.jockey.condition_stats import build_get_jockey_stats_tool
from jvdata_bridge_mcp.tools.jockey.profile import build_get_jockey_profile_tool
from jvdata_bridge_mcp.tools.jockey.recent_form import build_jockey_recent_form_tool
from jvdata_bridge_mcp.tools.jockey.top import build_get_top_jockeys_tool
from jvdata_bridge_mcp.tools.knowledge import build_knowledge_list_tool, build_knowledge_read_tool
from jvdata_bridge_mcp.tools.misc.all_tables import build_sql_get_all_tables_tool
from jvdata_bridge_mcp.tools.misc.composite import build_get_composite_analysis_tool
from jvdata_bridge_mcp.tools.misc.custom_sql import build_sql_execute_custom_query_tool
from jvdata_bridge_mcp.tools.misc.table_schema import build_sql_get_table_schema_tool
from jvdata_bridge_mcp.tools.odds.table import build_odds_table_tool
from jvdata_bridge_mcp.tools.pace.beneficiaries import build_race_pace_identify_beneficiaries_tool
from jvdata_bridge_mcp.tools.pace.course_profile import build_race_pace_course_profile_tool
from jvdata_bridge_mcp.tools.pace.jockey_tactics import build_race_pace_jockey_tactics_tool
from jvdata_bridge_mcp.tools.pace.running_styles import build_race_pace_running_styles_tool
from jvdata_bridge_mcp.tools.prediction.features import build_get_horse_feature_matrix_tool
from jvdata_bridge_mcp.tools.prediction.probabilities import build_predict_race_probabilities_tool
from jvdata_bridge_mcp.tools.prediction.scenarios import build_simulate_race_scenarios_tool
from jvdata_bridge_mcp.tools.prediction.status import build_prediction_model_status_tool
from jvdata_bridge_mcp.tools.race.condition_changes import build_race_condition_change_facts_tool
from jvdata_bridge_mcp.tools.race.entries import build_entries_list_tool
from jvdata_bridge_mcp.tools.race.evidence import build_race_evidence_collector_tool
from jvdata_bridge_mcp.tools.race.graded import build_get_graded_races_tool
from jvdata_bridge_mcp.tools.race.live_context import build_race_forecast_context_tool
from jvdata_bridge_mcp.tools.race.opponent_class import build_race_opponent_class_facts_tool
from jvdata_bridge_mcp.tools.race.recent_form import build_race_recent_form_facts_tool
from jvdata_bridge_mcp.tools.race.results import build_get_race_results_tool
from jvdata_bridge_mcp.tools.race.search import build_search_races_tool
from jvdata_bridge_mcp.tools.race.similar import build_analyze_similar_races_tool
from jvdata_bridge_mcp.tools.race.summary import build_race_summary_tool
from jvdata_bridge_mcp.tools.race.track_condition import build_track_condition_summary_tool
from jvdata_bridge_mcp.tools.trainer.compare import build_compare_race_trainers_tool
from jvdata_bridge_mcp.tools.trainer.distance_specialty import build_analyze_trainer_distance_specialty_tool
from jvdata_bridge_mcp.tools.trainer.performance import build_analyze_trainer_performance_tool
from jvdata_bridge_mcp.tools.training.condition_summary import build_training_condition_summary_tool
from jvdata_bridge_mcp.tools.training.entry_detail import build_training_entry_detail_tool
from jvdata_bridge_mcp.tools.training.evaluation import build_race_training_evaluation_tool
from jvdata_bridge_mcp.tools.training.intensity import build_training_intensity_analysis_tool

ToolFunction = Callable[..., object]

CANONICAL_TOOL_NAMES: tuple[str, ...] = (
    # Race (12)
    "race_summary",
    "entries_list",
    "track_condition_summary",
    "search_races",
    "get_race_results",
    "get_graded_races",
    "race_evidence_collector",
    "analyze_similar_races",
    "race_recent_form_facts",
    "race_opponent_class_facts",
    "race_condition_change_facts",
    "race_forecast_context",
    # Horse (5)
    "search_horses",
    "get_horse_details",
    "compare_horses",
    "analyze_horse_race_history",
    "analyze_horse_performance",
    # Odds (1)
    "odds_table",
    # Jockey (4)
    "get_jockey_profile",
    "get_jockey_stats",
    "get_top_jockeys",
    "jockey_recent_form",
    # Pace (4)
    "race_pace_running_styles",
    "race_pace_identify_beneficiaries",
    "race_pace_jockey_tactics",
    "race_pace_course_profile",
    # Prediction (5)
    "prediction_model_status",
    "get_horse_feature_matrix",
    "simulate_race_scenarios",
    "predict_race_probabilities",
    "get_composite_analysis",
    # Bloodline (4)
    "bloodline_scoreboard",
    "bloodline_fetch_pedigree",
    "bloodline_distance_bias",
    "bloodline_form_profile",
    # Course (3)
    "course_advantage_analyzer",
    "course_distance_insight",
    "course_frame_style_stats",
    # Training (4)
    "race_training_evaluation",
    "training_condition_summary",
    "training_entry_detail",
    "training_intensity_analysis",
    # Trainer (3)
    "analyze_trainer_performance",
    "compare_race_trainers",
    "analyze_trainer_distance_specialty",
    # SQL (3)
    "sql_execute_custom_query",
    "sql_get_all_tables",
    "sql_get_table_schema",
    # Knowledge (2)
    "knowledge_list",
    "knowledge_read",
)


def build_all_tools(
    database: JvDataDatabase,
    sql_gateway: JvDataSqlGatewayProtocol | None = None,
    *,
    knowledge_resources: Sequence[TextResource] | None = None,
) -> tuple[ToolFunction, ...]:
    """対象レース基準の公開面を、固定順で構築する。"""

    resolved_sql_gateway = sql_gateway or JvDataSqlGateway(database)
    resources = load_knowledge_resources() if knowledge_resources is None else knowledge_resources
    tools: tuple[ToolFunction, ...] = (
        # Race (12)
        build_race_summary_tool(database),
        build_entries_list_tool(database),
        build_track_condition_summary_tool(database),
        build_search_races_tool(database),
        build_get_race_results_tool(database),
        build_get_graded_races_tool(database),
        build_race_evidence_collector_tool(database),
        build_analyze_similar_races_tool(database),
        build_race_recent_form_facts_tool(database),
        build_race_opponent_class_facts_tool(database),
        build_race_condition_change_facts_tool(database),
        build_race_forecast_context_tool(database),
        # Horse (5)
        build_search_horses_tool(database),
        build_get_horse_details_tool(database),
        build_compare_horses_tool(database),
        build_analyze_horse_race_history_tool(database),
        build_analyze_horse_performance_tool(database),
        # Odds (1)
        build_odds_table_tool(database),
        # Jockey (4)
        build_get_jockey_profile_tool(database),
        build_get_jockey_stats_tool(database),
        build_get_top_jockeys_tool(database),
        build_jockey_recent_form_tool(database),
        # Pace (4)
        build_race_pace_running_styles_tool(database),
        build_race_pace_identify_beneficiaries_tool(database),
        build_race_pace_jockey_tactics_tool(database),
        build_race_pace_course_profile_tool(database),
        # Prediction (5)
        build_prediction_model_status_tool(database),
        build_get_horse_feature_matrix_tool(database),
        build_simulate_race_scenarios_tool(database),
        build_predict_race_probabilities_tool(database),
        build_get_composite_analysis_tool(database),
        # Bloodline (4)
        build_bloodline_scoreboard_tool(database),
        build_bloodline_fetch_pedigree_tool(database),
        build_bloodline_distance_bias_tool(database),
        build_bloodline_form_profile_tool(database),
        # Course (3)
        build_course_advantage_analyzer_tool(database),
        build_course_distance_insight_tool(database),
        build_course_frame_style_stats_tool(database),
        # Training (4)
        build_race_training_evaluation_tool(database),
        build_training_condition_summary_tool(database),
        build_training_entry_detail_tool(database),
        build_training_intensity_analysis_tool(database),
        # Trainer (3)
        build_analyze_trainer_performance_tool(database),
        build_compare_race_trainers_tool(database),
        build_analyze_trainer_distance_specialty_tool(database),
        # SQL (3)
        build_sql_execute_custom_query_tool(database, resolved_sql_gateway),
        build_sql_get_all_tables_tool(database, resolved_sql_gateway),
        build_sql_get_table_schema_tool(database, resolved_sql_gateway),
        # Knowledge (2)
        build_knowledge_list_tool(resources),
        build_knowledge_read_tool(resources),
    )
    names = tuple(tool.__name__ for tool in tools)
    if names != CANONICAL_TOOL_NAMES:
        raise RuntimeError("canonical toolset names or order changed unexpectedly")
    return tools
