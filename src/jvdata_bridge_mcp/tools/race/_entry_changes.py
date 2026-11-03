"""発表時刻付きAV/JCを、既往証拠を消さず現在の出走情報へ重ねる。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from jvdata_bridge_mcp.tools._source_timing import SourceTiming, source_datetime, source_timing

ENTRY_CHANGES_SQL = """
SELECT DISTINCT ON (source_record_type, horse_number, announced_at)
       race_id, source_record_type, change_type, horse_number, horse_name,
       announced_at, artifact_hash, reason_code, reason_name,
       jockey_before_id, jockey_before_name, jockey_after_id, jockey_after_name,
       weight_before_kg, weight_after_kg
FROM jv_mart.race_changes
WHERE race_id=:race_code AND source_record_type IN ('AV','JC')
  AND (announced_at IS NULL OR announced_at<=CAST(:analysis_local AS timestamp))
  AND (announced_at IS NULL OR CAST(:scheduled_start_local AS timestamp) IS NULL
       OR announced_at<CAST(:scheduled_start_local AS timestamp))
ORDER BY source_record_type,horse_number,announced_at,artifact_hash DESC
"""


class EntryChangeObservation(BaseModel):
    source_record_type: str
    change_type: str
    horse_number: int | None = None
    horse_name: str | None = None
    announced_at: str | None = None
    artifact_hash: str | None = None
    reason_code: str | None = None
    reason_name: str | None = None
    jockey_before_id: str | None = None
    jockey_before_name: str | None = None
    jockey_after_id: str | None = None
    jockey_after_name: str | None = None
    weight_before_kg: float | None = None
    weight_after_kg: float | None = None
    applied: bool
    unapplied_reason: str | None = None
    source: str = "jv_mart.race_changes"
    source_timing: SourceTiming


class CurrentRunner(BaseModel):
    horse_number: int
    horse_id: str
    horse_name: str
    jockey_id: str | None = None
    jockey_name: str | None = None
    assigned_weight_kg: float | None = None
    runner_status: Literal["no_withdrawal_notice", "withdrawn", "excluded", "withdrawn_or_excluded"] = (
        "no_withdrawal_notice"
    )
    betting_eligible: bool | None = Field(
        default=None, description="確認済みAVはfalse。通知なしを購入可能の保証にしない"
    )
    withdrawal_notice: EntryChangeObservation | None = None
    jockey_change_notice: EntryChangeObservation | None = None


class CurrentEntryObservations(BaseModel):
    runners: list[CurrentRunner]
    changes: list[EntryChangeObservation]
    confirmed_nonrunner_count: int
    unverified_notice_count: int
    coverage_status: Literal["notices_available", "no_matching_notices", "unverified_notices_only"]
    upstream_coverage_verified: bool = False
    history_entries_removed: bool = False
    limitations: list[str] = Field(
        default_factory=lambda: [
            "No withdrawal notice is not proof that all announcements have been ingested.",
            "AV identifies a nonrunner; withdrawal versus exclusion is left unresolved when the source does not distinguish them.",
            "Unknown announcement times are explicit and are not applied to the current roster.",
            "Horse history and model rosters remain declared entries; current runners and jockey changes are a separate observation.",
        ]
    )


def current_entry_observations(
    entries: list[dict[str, object]],
    rows: list[dict[str, object]],
    *,
    analysis_at: str,
    scheduled_start_at: str | None,
) -> CurrentEntryObservations:
    """時刻を確認した通知だけを適用し、発表不明と未収録を区別する。"""

    runners = {
        int(e["horse_number"]): CurrentRunner(
            **{
                k: v
                for k, v in e.items()
                if k in {"horse_number", "horse_id", "horse_name", "jockey_id", "jockey_name", "assigned_weight_kg"}
            }
        )
        for e in entries
    }
    now, start = source_datetime(analysis_at), source_datetime(scheduled_start_at)
    if now is None:
        raise ValueError("analysis_at requires a timestamp")
    notices = []

    def change_order(row: dict[str, object]) -> tuple[float, str]:
        announced = source_datetime(row.get("announced_at"))
        return (
            announced.timestamp() if announced else float("-inf"),
            str(row.get("artifact_hash") or ""),
        )

    # Official timestamps are naive JST; the parser also handles explicit offsets in supplied rows.
    for row in sorted(rows, key=change_order):
        announced = source_datetime(row.get("announced_at"))
        number = row.get("horse_number")
        kind = str(row.get("source_record_type") or "")
        if kind not in {"AV", "JC"}:
            continue
        if announced is not None and (announced > now or (start is not None and announced >= start)):
            continue
        reason = (
            "announcement_time_unknown"
            if announced is None
            else (
                "horse_not_in_declared_entries"
                if number not in runners
                else ("replacement_jockey_unknown" if kind == "JC" and not row.get("jockey_after_id") else None)
            )
        )
        notice = EntryChangeObservation(
            **{
                k: v
                for k, v in row.items()
                if k in EntryChangeObservation.model_fields
                and k not in {"source_timing", "applied", "unapplied_reason"}
            },
            applied=reason is None,
            unapplied_reason=reason,
            source_timing=source_timing(analysis_at, published_at=row.get("announced_at")),
        )
        notices.append(notice)
        if not notice.applied:
            continue
        runner = runners[number]
        if kind == "AV":
            name = str(row.get("reason_name") or "")
            runner.runner_status = (
                "excluded" if "除外" in name else "withdrawn" if "取消" in name else "withdrawn_or_excluded"
            )
            runner.betting_eligible = False
            runner.withdrawal_notice = notice
        else:
            runner.jockey_id, runner.jockey_name = notice.jockey_after_id, notice.jockey_after_name
            if notice.weight_after_kg is not None:
                runner.assigned_weight_kg = notice.weight_after_kg
            runner.jockey_change_notice = notice
    applied = any(n.applied for n in notices)
    return CurrentEntryObservations(
        runners=list(runners.values()),
        changes=notices,
        confirmed_nonrunner_count=sum(r.betting_eligible is False for r in runners.values()),
        unverified_notice_count=sum(not n.applied for n in notices),
        coverage_status="notices_available"
        if applied
        else "unverified_notices_only"
        if notices
        else "no_matching_notices",
    )
