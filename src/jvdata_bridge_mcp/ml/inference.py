"""特徴量契約を検証し、入力と較正を適用するJSON推論。"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from jvdata_bridge_mcp.ml import model as original
from jvdata_bridge_mcp.ml.distribution import softmax
from jvdata_bridge_mcp.ml.expanded_features import FEATURE_NAMES, FEATURE_VERSION
from jvdata_bridge_mcp.ml.features import FEATURE_NAMES as BASE_NAMES
from jvdata_bridge_mcp.ml.features import FEATURE_VERSION as BASE_VERSION
from jvdata_bridge_mcp.ml.features import number


def validate_model(payload: Mapping[str, Any]) -> None:
    if payload.get("feature_version") == BASE_VERSION:
        original.validate_model(payload)
        return
    if payload.get("feature_version") != FEATURE_VERSION or tuple(payload.get("features", ())) != FEATURE_NAMES:
        raise ValueError("unsupported extended feature contract")
    preprocessing = payload.get("preprocessing", {})
    if not set(preprocessing.get("constant_features", ())) <= set(FEATURE_NAMES):
        raise ValueError("unknown constant feature")
    if not set(preprocessing.get("categorical_values", {})) <= set(FEATURE_NAMES):
        raise ValueError("unknown categorical feature")
    if len(payload.get("impute", ())) != len(FEATURE_NAMES) or not all(math.isfinite(x) for x in payload["impute"]):
        raise ValueError("invalid extended imputation")
    projected = dict(payload)
    projected.update(
        {"feature_version": BASE_VERSION, "features": list(BASE_NAMES), "impute": payload["impute"][: len(BASE_NAMES)]}
    )
    projected["preprocessing"] = {
        **preprocessing,
        "constant_features": [name for name in preprocessing.get("constant_features", ()) if name in BASE_NAMES],
        "categorical_values": {
            name: values for name, values in preprocessing.get("categorical_values", {}).items() if name in BASE_NAMES
        },
    }
    original.validate_model(projected)
    slope = payload.get("reliability_temperature_slope", 0)
    if not math.isfinite(slope) or abs(slope) > 1:
        raise ValueError("invalid reliability calibration")


def reliability(rows: Sequence[Mapping[str, Any]]) -> float:
    return math.fsum(math.exp(-max(0, number(row.get("history_count"), 0)) / 3) for row in rows) / len(rows)


def effective_temperature(
    payload: Mapping[str, Any], field_size: int, rows: Sequence[Mapping[str, Any]] | None = None
) -> float:
    temperature = original.effective_temperature(payload, field_size)
    slope = float(payload.get("reliability_temperature_slope", 0))
    if slope:
        if not rows or len(rows) != field_size:
            raise ValueError("reliability calibration requires the complete field")
        temperature *= math.exp(slope * reliability(rows))
    return temperature


def predict_utilities(payload: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[float]:
    if payload.get("feature_version") == BASE_VERSION:
        return original.predict_utilities(payload, rows)
    validate_model(payload)
    names = tuple(payload["features"])
    model = payload["estimator"]
    prep = payload.get("preprocessing", {})
    native_missing = prep.get("missing") == "native"
    constants = set(prep.get("constant_features", ()))
    result = []
    for row in rows:
        values = [
            median if name in constants else number(row.get(name), math.nan if native_missing else median)
            for name, median in zip(names, payload["impute"], strict=True)
        ]
        if model["kind"] == "lightgbm":
            for name, allowed in prep.get("categorical_values", {}).items():
                index = names.index(name)
                if original.category_token(values[index]) not in allowed:
                    values[index] = math.nan
            score = math.fsum(original._lgb_tree(tree["tree_structure"], values) for tree in model["tree_info"])
        elif model["kind"] == "linear":
            score = float(model["intercept"]) + math.fsum(
                w * (x - center) / scale
                for w, x, center, scale in zip(model["weights"], values, model["center"], model["scale"], strict=True)
            )
        else:
            numeric = [original._cat_float32(value) for value in values]
            score = 0.0
            for tree in model["oblivious_trees"]:
                index = 0
                for bit, split in enumerate(tree.get("splits") or []):
                    if split["split_type"] == "FloatFeature":
                        right = numeric[int(split["float_feature_index"])] > float(split["border"])
                    elif split["split_type"] == "CategorySet":
                        right = original.category_token(values[int(split["feature_index"])]) in split["values"]
                    else:
                        raise ValueError("unsupported CatBoost split")
                    if right:
                        index |= 1 << bit
                score += float(tree["leaf_values"][index])
            scale, bias = model["scale_and_bias"]
            score = score * float(scale) + float(bias[0])
        if not math.isfinite(score):
            raise ValueError("nonfinite prediction")
        result.append(score)
    return result


def predict_probabilities(payload: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[float]:
    return softmax(predict_utilities(payload, rows), effective_temperature(payload, len(rows), rows))
