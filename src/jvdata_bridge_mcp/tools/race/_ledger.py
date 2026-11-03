"""結果と出馬表に共通する、異常区分からの状態表示。"""


def runner_status(row: dict[str, object], *, pre_race: bool = False) -> str:
    code = str(row.get("abnormality_code") or "0")
    name = str(row.get("abnormality_name") or "")
    if code in {"2", "3"} or "除外" in name:
        return "excluded"
    if code == "1" or "取消" in name:
        return "withdrawn"
    if pre_race:
        return "entry_listed"
    if code == "4" or "競走中止" in name:
        return "did_not_finish"
    if code == "5" or "失格" in name:
        return "disqualified"
    if row.get("is_final_result") and row.get("is_finisher"):
        return "finished" if code == "0" else "finished_with_abnormality"
    if (
        row.get("is_complete_result")
        and row.get("result_status_code") == "provisional_complete"
        and row.get("is_finisher")
    ):
        return "provisional_finished" if code == "0" else "provisional_finished_with_abnormality"
    return "not_confirmed"
