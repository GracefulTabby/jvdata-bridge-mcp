"""時系列バックテスト済み指標を確率・総合分析で共有するSQL断片。"""

from __future__ import annotations

from typing import Final

INDICATOR_MODEL_VERSION: Final[str] = "temporal-linear-v3-speed-2026-holdout"
# 市場なしモデルは、現行係数から historical_market 項だけを外した診断用の派生値。
# 独立した再学習・再校正を済ませたモデルではないため、API上でも別バージョンとして明示する。
MARKET_FREE_MODEL_VERSION: Final[str] = "temporal-linear-v3-speed-2026-holdout-market-free-diagnostic"
MARKET_FREE_MODEL_DESCRIPTION: Final[str] = (
    "診断用市場なし派生モデル。speed_qualityを含む現行市場ありモデルからhistorical_market項だけを除外した診断値で、"
    "独立再学習・再校正済みではない。"
)

DEFAULT_INDICATOR_WEIGHTS: Final[dict[str, float]] = {
    # 学習期間: 2021〜2025年。設定選択: 2023〜2024年。2026年は開発で参照済みの評価期間。
    "aptitude_weight": 0.576935,
    "recent_form_weight": 0.573728,
    "distance_aptitude_weight": 0.042949,
    "course_aptitude_weight": 0.144058,
    "closing_weight": 0.126436,
    "time_quality_weight": 0.809266,
    "historical_market_weight": 0.871966,
    "jockey_form_weight": 0.891236,
    "trainer_form_weight": 0.479686,
    "horse_jockey_form_weight": 0.228294,
}
# 速度の重みは固定の別定数とし、任意重みを受け付ける指標と区別する。
SPEED_QUALITY_WEIGHT: Final[float] = 0.567713


def indicator_feature_ctes(historical_cutoff_date_expression: str) -> str:
    """対象馬の事前履歴を、同レース内で比較可能な0〜1指標へ変換するCTEを返す。"""

    return f"""
WITH target AS MATERIALIZED (
    SELECT
        r.race_date,
        r.venue_code,
        r.distance_meters,
        r.track_code,
        {historical_cutoff_date_expression} AS historical_cutoff_date
    FROM jv_data.races AS r
    WHERE r.race_id = :race_code
), target_cards AS MATERIALIZED (
    SELECT
        c.horse_number,
        c.horse_id,
        c.horse_name,
        c.jockey_id,
        c.trainer_id,
        c.assigned_weight_kg,
        t.race_date,
        t.venue_code,
        t.distance_meters,
        t.track_code,
        t.historical_cutoff_date
    FROM jv_mart.race_cards AS c
    CROSS JOIN target AS t
    WHERE c.race_id = :race_code


), speed_features AS MATERIALIZED (
    -- 対象レース・履歴排他日・モデル版・馬の4キーで事前計算値を読む。
    -- read modelのrawは減衰加重平均なので、v3モデルの事前値(+1/+2)を戻す。
    SELECT
        cards.horse_id,
        (stored.speed_quality_raw * recency.weight_total + 1.0)
            / (recency.weight_total + 2.0) AS speed_quality_raw,
        stored.speed_quality_coverage AS speed_quality_coverage_raw,
        stored.speed_baseline_level
    FROM target_cards AS cards
    JOIN jv_mart.horse_speed_quality AS stored
      ON stored.race_id = :race_code
     AND stored.race_date = cards.race_date
     AND stored.horse_id = cards.horse_id
     -- The read model version defines aggregation strictly before the target race date.
     AND stored.model_version = '{INDICATOR_MODEL_VERSION}'
    JOIN (VALUES
        (0, 0.0::double precision),
        (1, 1.0::double precision),
        (2, 1.72::double precision),
        (3, 2.2384::double precision),
        (4, 2.611648::double precision),
        (5, 2.88038656::double precision)
    ) AS recency(observation_count, weight_total)
      ON recency.observation_count = stored.speed_observation_count
), target_jockeys AS MATERIALIZED (
    SELECT DISTINCT jockey_id, historical_cutoff_date
    FROM target_cards
    WHERE jockey_id IS NOT NULL
), jockey_ranked_entries AS MATERIALIZED (
    SELECT
        target_jockey.jockey_id,
        e.race_id,
        e.race_date,
        e.horse_id,
        ROW_NUMBER() OVER (
            PARTITION BY target_jockey.jockey_id
            ORDER BY e.race_date DESC, e.race_id DESC
        ) AS recency_rank
    FROM target_jockeys AS target_jockey
    JOIN jv_mart.entry_performances AS e ON e.jockey_id = target_jockey.jockey_id
    WHERE e.race_date < target_jockey.historical_cutoff_date
      AND e.race_date >= target_jockey.historical_cutoff_date - INTERVAL '730 days'
), jockey_features AS MATERIALIZED (
    SELECT
        limited.jockey_id,
        (
            COALESCE(SUM(
                (history_race.starters - result.finish_position)::double precision
                / NULLIF(history_race.starters - 1, 0)
                * POWER(0.985, limited.recency_rank - 1)
            ) FILTER (WHERE result.finish_position IS NOT NULL), 0.0) + 4.0
        ) / (
            COALESCE(SUM(POWER(0.985, limited.recency_rank - 1))
                FILTER (WHERE result.finish_position IS NOT NULL), 0.0) + 8.0
        ) AS jockey_form_raw
    FROM jockey_ranked_entries AS limited
    JOIN jv_data.races AS history_race ON history_race.race_id = limited.race_id
    LEFT JOIN LATERAL (
        SELECT rr.finish_position
        FROM jv_mart.race_results AS rr
        WHERE rr.race_id = limited.race_id
          AND rr.horse_id = limited.horse_id
          AND rr.is_final_result
          AND rr.is_complete_result
          AND rr.is_starter
          AND rr.is_finisher
        LIMIT 1
    ) AS result ON TRUE
    WHERE limited.recency_rank <= 30
    GROUP BY limited.jockey_id
), target_trainers AS MATERIALIZED (
    SELECT DISTINCT trainer_id, historical_cutoff_date
    FROM target_cards
    WHERE trainer_id IS NOT NULL
), trainer_ranked_entries AS MATERIALIZED (
    SELECT
        target_trainer.trainer_id,
        e.race_id,
        e.race_date,
        e.horse_id,
        ROW_NUMBER() OVER (
            PARTITION BY target_trainer.trainer_id
            ORDER BY e.race_date DESC, e.race_id DESC
        ) AS recency_rank
    FROM target_trainers AS target_trainer
    JOIN jv_mart.entry_performances AS e ON e.trainer_id = target_trainer.trainer_id
    WHERE e.race_date < target_trainer.historical_cutoff_date
      AND e.race_date >= target_trainer.historical_cutoff_date - INTERVAL '1095 days'
), trainer_features AS MATERIALIZED (
    SELECT
        limited.trainer_id,
        (
            COALESCE(SUM(
                (history_race.starters - result.finish_position)::double precision
                / NULLIF(history_race.starters - 1, 0)
                * POWER(0.99, limited.recency_rank - 1)
            ) FILTER (WHERE result.finish_position IS NOT NULL), 0.0) + 6.0
        ) / (
            COALESCE(SUM(POWER(0.99, limited.recency_rank - 1))
                FILTER (WHERE result.finish_position IS NOT NULL), 0.0) + 12.0
        ) AS trainer_form_raw
    FROM trainer_ranked_entries AS limited
    JOIN jv_data.races AS history_race ON history_race.race_id = limited.race_id
    LEFT JOIN LATERAL (
        SELECT rr.finish_position
        FROM jv_mart.race_results AS rr
        WHERE rr.race_id = limited.race_id
          AND rr.horse_id = limited.horse_id
          AND rr.is_final_result
          AND rr.is_complete_result
          AND rr.is_starter
          AND rr.is_finisher
        LIMIT 1
    ) AS result ON TRUE
    WHERE limited.recency_rank <= 50
    GROUP BY limited.trainer_id
), raw_features AS MATERIALIZED (
    SELECT
        c.*,
        career.starts,
        career.wins,
        career.top3_finishes,
        career.average_finish_position,
        career.average_win_odds,
        career.average_last_three_furlongs,
        (career.top3_finishes + 1.25) / (career.starts + 5.0) AS aptitude_raw,
        career.distance_aptitude_raw,
        career.course_aptitude_raw,
        recent.recent_form_raw,
        recent.closing_raw,
        recent.time_quality_raw,
        recent.historical_market_raw,
        speed.speed_quality_raw AS speed_quality_raw,
        speed.speed_quality_coverage_raw AS speed_quality_coverage_raw,
        -- 履歴なしの事前計算行はraw=0.5、coverage=0、階層なしで格納される。
        COALESCE(speed.speed_baseline_level, 'unavailable') AS speed_baseline_level,
        COALESCE(jockey.jockey_form_raw, 0.5) AS jockey_form_raw,
        COALESCE(trainer.trainer_form_raw, 0.5) AS trainer_form_raw,
        horse_jockey.horse_jockey_form_raw,
        1.0 - EXP(-career.starts / 5.0) AS history_reliability_raw
    FROM target_cards AS c
    CROSS JOIN LATERAL (
        SELECT
            COUNT(h.race_id)::bigint AS starts,
            COUNT(h.race_id) FILTER (WHERE h.finish_position = 1)::bigint AS wins,
            COUNT(h.race_id) FILTER (WHERE h.finish_position <= 3)::bigint AS top3_finishes,
            AVG(h.finish_position) AS average_finish_position,
            AVG(h.final_win_odds) AS average_win_odds,
            AVG(h.last_three_furlongs_seconds) AS average_last_three_furlongs,
            (
                COALESCE(SUM(
                    (history_race.starters - h.finish_position)::double precision
                    / NULLIF(history_race.starters - 1, 0)
                ) FILTER (
                    WHERE LEFT(COALESCE(h.track_code, ''), 1) = LEFT(COALESCE(c.track_code, ''), 1)
                      AND ROUND(h.distance_meters / 200.0) * 200 = ROUND(c.distance_meters / 200.0) * 200
                ), 0.0) + 2.5
            ) / (
                COUNT(h.race_id) FILTER (
                    WHERE LEFT(COALESCE(h.track_code, ''), 1) = LEFT(COALESCE(c.track_code, ''), 1)
                      AND ROUND(h.distance_meters / 200.0) * 200 = ROUND(c.distance_meters / 200.0) * 200
                ) + 5.0
            ) AS distance_aptitude_raw,
            (
                COALESCE(SUM(
                    (history_race.starters - h.finish_position)::double precision
                    / NULLIF(history_race.starters - 1, 0)
                ) FILTER (
                    WHERE h.venue_code = c.venue_code
                      AND LEFT(COALESCE(h.track_code, ''), 1) = LEFT(COALESCE(c.track_code, ''), 1)
                ), 0.0) + 3.0
            ) / (
                COUNT(h.race_id) FILTER (
                    WHERE h.venue_code = c.venue_code
                      AND LEFT(COALESCE(h.track_code, ''), 1) = LEFT(COALESCE(c.track_code, ''), 1)
                ) + 6.0
            ) AS course_aptitude_raw
        FROM jv_mart.horse_runs AS h
        LEFT JOIN LATERAL (
            SELECT r.starters
            FROM jv_data.races AS r
            WHERE r.race_id = h.race_id
            LIMIT 1
        ) AS history_race ON TRUE
        WHERE h.horse_id = c.horse_id
          AND h.race_date < c.historical_cutoff_date
          AND h.is_final_result
          AND h.is_complete_result
          AND h.is_starter
          AND h.is_finisher
    ) AS career
    CROSS JOIN LATERAL (
        SELECT
            (
                COALESCE(SUM(weighted.finish_quality * weighted.recency_weight), 0.0) + 0.75
            ) / (COALESCE(SUM(weighted.recency_weight), 0.0) + 1.5) AS recent_form_raw,
            (
                COALESCE(SUM(weighted.closing_quality * weighted.recency_weight)
                    FILTER (WHERE weighted.closing_quality IS NOT NULL), 0.0) + 1.0
            ) / (
                COALESCE(SUM(weighted.recency_weight)
                    FILTER (WHERE weighted.closing_quality IS NOT NULL), 0.0) + 2.0
            ) AS closing_raw,
            (
                COALESCE(SUM(weighted.time_quality * weighted.recency_weight)
                    FILTER (WHERE weighted.time_quality IS NOT NULL), 0.0) + 1.0
            ) / (
                COALESCE(SUM(weighted.recency_weight)
                    FILTER (WHERE weighted.time_quality IS NOT NULL), 0.0) + 2.0
            ) AS time_quality_raw,
            (
                COALESCE(SUM(weighted.market_quality * weighted.recency_weight)
                    FILTER (WHERE weighted.market_quality IS NOT NULL), 0.0) + 1.0
            ) / (
                COALESCE(SUM(weighted.recency_weight)
                    FILTER (WHERE weighted.market_quality IS NOT NULL), 0.0) + 2.0
            ) AS historical_market_raw
        FROM (
            SELECT
                recent_history.*,
                CASE recent_history.recency_rank
                    WHEN 1 THEN 1.0
                    WHEN 2 THEN 0.72
                    WHEN 3 THEN 0.5184
                    WHEN 4 THEN 0.373248
                    ELSE 0.26873856
                END AS recency_weight,
                (recent_history.starters - recent_history.finish_position)::double precision
                    / NULLIF(recent_history.starters - 1, 0) AS finish_quality,
                peer_rank.closing_quality,
                CASE
                    WHEN recent_history.time_difference_seconds IS NULL THEN NULL
                    ELSE EXP(-GREATEST(recent_history.time_difference_seconds, 0.0))
                END AS time_quality,
                CASE
                    WHEN recent_history.final_win_popularity IS NULL THEN NULL
                    ELSE (recent_history.starters - recent_history.final_win_popularity)::double precision
                        / NULLIF(recent_history.starters - 1, 0)
                END AS market_quality
            FROM (
                SELECT
                    h.race_id,
                    h.finish_position,
                    h.last_three_furlongs_seconds,
                    h.time_difference_seconds,
                    h.final_win_popularity,
                    history_race.starters,
                    ROW_NUMBER() OVER (ORDER BY h.race_date DESC, h.race_id DESC) AS recency_rank
                FROM jv_mart.horse_runs AS h
                LEFT JOIN LATERAL (
                    SELECT r.starters
                    FROM jv_data.races AS r
                    WHERE r.race_id = h.race_id
                    LIMIT 1
                ) AS history_race ON TRUE
                WHERE h.horse_id = c.horse_id
                  AND h.race_date < c.historical_cutoff_date
                  AND h.is_final_result
                  AND h.is_complete_result
                  AND h.is_starter
                  AND h.is_finisher
                ORDER BY h.race_date DESC, h.race_id DESC
                LIMIT 5
            ) AS recent_history
            LEFT JOIN LATERAL (
                SELECT
                    CASE
                        WHEN COUNT(peer.horse_id) FILTER (WHERE peer.last_three_furlongs_seconds IS NOT NULL) < 2
                          OR recent_history.last_three_furlongs_seconds IS NULL THEN NULL
                        ELSE (
                            COUNT(peer.horse_id) FILTER (
                                WHERE peer.last_three_furlongs_seconds > recent_history.last_three_furlongs_seconds
                            )
                            + 0.5 * GREATEST(
                                COUNT(peer.horse_id) FILTER (
                                    WHERE peer.last_three_furlongs_seconds = recent_history.last_three_furlongs_seconds
                                ) - 1,
                                0
                            )
                        )::double precision / NULLIF(
                            COUNT(peer.horse_id) FILTER (WHERE peer.last_three_furlongs_seconds IS NOT NULL) - 1,
                            0
                        )
                    END AS closing_quality
                FROM jv_mart.race_results AS peer
                WHERE peer.race_id = recent_history.race_id
                  AND peer.is_final_result
                  AND peer.is_complete_result
                  AND peer.is_starter
                  AND peer.is_finisher
            ) AS peer_rank ON TRUE
        ) AS weighted
    ) AS recent
    LEFT JOIN jockey_features AS jockey ON jockey.jockey_id = c.jockey_id
    LEFT JOIN trainer_features AS trainer ON trainer.trainer_id = c.trainer_id
    JOIN speed_features AS speed ON speed.horse_id = c.horse_id
    CROSS JOIN LATERAL (
        SELECT
            (
                COALESCE(SUM(history.finish_quality * POWER(0.9, history.recency_rank - 1)), 0.0) + 2.0
            ) / (COALESCE(SUM(POWER(0.9, history.recency_rank - 1)), 0.0) + 4.0) AS horse_jockey_form_raw
        FROM (
            SELECT
                (history_race.starters - result.finish_position)::double precision
                    / NULLIF(history_race.starters - 1, 0) AS finish_quality,
                limited.recency_rank
            FROM (
                SELECT
                    recent_entries.*,
                    ROW_NUMBER() OVER (ORDER BY recent_entries.race_date DESC, recent_entries.race_id DESC)
                        AS recency_rank
                FROM (
                    SELECT e.race_id, e.race_date, e.horse_id
                    FROM jv_mart.entry_performances AS e
                    WHERE e.horse_id = c.horse_id
                      AND e.jockey_id = c.jockey_id
                      AND e.race_date < c.historical_cutoff_date
                      AND e.race_date >= c.historical_cutoff_date - INTERVAL '1095 days'
                    ORDER BY e.race_date DESC, e.race_id DESC
                    LIMIT 20
                ) AS recent_entries
            ) AS limited
            LEFT JOIN LATERAL (
                SELECT r.starters
                FROM jv_data.races AS r
                WHERE r.race_id = limited.race_id
                LIMIT 1
            ) AS history_race ON TRUE
            LEFT JOIN LATERAL (
                SELECT rr.finish_position
                FROM jv_mart.race_results AS rr
                WHERE rr.race_id = limited.race_id
                  AND rr.horse_id = limited.horse_id
                  AND rr.is_final_result
                  AND rr.is_complete_result
                  AND rr.is_starter
                  AND rr.is_finisher
                LIMIT 1
            ) AS result ON TRUE
            WHERE result.finish_position IS NOT NULL
        ) AS history
    ) AS horse_jockey
), component_scores AS MATERIALIZED (
    SELECT
        f.*,
        PERCENT_RANK() OVER (ORDER BY f.aptitude_raw) AS aptitude_score,
        PERCENT_RANK() OVER (ORDER BY f.recent_form_raw) AS recent_form_score,
        PERCENT_RANK() OVER (ORDER BY f.distance_aptitude_raw) AS distance_aptitude_score,
        PERCENT_RANK() OVER (ORDER BY f.course_aptitude_raw) AS course_aptitude_score,
        PERCENT_RANK() OVER (ORDER BY f.closing_raw) AS closing_score,
        PERCENT_RANK() OVER (ORDER BY f.time_quality_raw) AS time_quality_score,
        PERCENT_RANK() OVER (ORDER BY f.historical_market_raw) AS historical_market_score,
        PERCENT_RANK() OVER (ORDER BY f.jockey_form_raw) AS jockey_form_score,
        PERCENT_RANK() OVER (ORDER BY f.trainer_form_raw) AS trainer_form_score,
        PERCENT_RANK() OVER (ORDER BY f.horse_jockey_form_raw) AS horse_jockey_form_score,
        PERCENT_RANK() OVER (ORDER BY f.speed_quality_raw) AS speed_quality_score
    FROM raw_features AS f
)
"""
