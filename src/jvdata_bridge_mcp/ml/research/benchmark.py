"""モデルを個別プロセスで測る。DB特徴量取得は別の測定範囲。"""

from __future__ import annotations

import argparse
import gc
import importlib
import json
import statistics
import time
import tracemalloc
from pathlib import Path

from jvdata_bridge_mcp.ml.distribution import softmax
from jvdata_bridge_mcp.ml.inference import predict_probabilities


def measure(model_path: Path, rows_path: Path) -> dict[str, object]:
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    gc.collect()
    tracemalloc.start()
    model = json.loads(model_path.read_text(encoding="utf-8"))
    allocated, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    def predict() -> list[float]:
        if model.get("kind") == "bundled_linear":
            scores = [sum(float(row[key]) * value for key, value in model["weights"].items()) for row in rows]
            return softmax(scores, model.get("temperature", 1))
        return predict_probabilities(model, rows)

    predict()
    durations = []
    for _ in range(10):
        start = time.perf_counter()
        predict()
        durations.append((time.perf_counter() - start) * 1000)
    memory = importlib.import_module("psutil").Process().memory_info()
    return {
        "model_bytes": model_path.stat().st_size,
        "runners": len(rows),
        "inference_median_ms": statistics.median(durations),
        "json_retained_python_bytes": allocated,
        "json_parse_peak_python_bytes": peak,
        "process_rss_bytes": memory.rss,
        "process_peak_working_set_bytes": getattr(memory, "peak_wset", None),
        "scope": "isolated Python process; model JSON and score/softmax inference; database extraction excluded",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--rows", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(measure(args.model, args.rows)))


if __name__ == "__main__":
    main()
