"""配布モデルのJSON実行部。pickle、実行コード、学習ライブラリをロードしない。"""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence
from typing import Any

from jvdata_bridge_mcp.ml.distribution import softmax
from jvdata_bridge_mcp.ml.features import FEATURE_NAMES, FEATURE_RECIPES, FEATURE_VERSION, number

MODEL_FORMAT = "jvdata-market-free-json-v1"


def validate_model(payload: Mapping[str, Any]) -> None:
    if payload.get("format") != MODEL_FORMAT or payload.get("feature_version") != FEATURE_VERSION:
        raise ValueError("unsupported model or feature contract")
    if tuple(payload.get("features", ())) != FEATURE_NAMES:
        raise ValueError("feature order mismatch")
    if payload.get("market_included") is not False:
        raise ValueError("ability model must be market-free")
    if payload.get("feature_recipe", "reference") not in FEATURE_RECIPES:
        raise ValueError("unknown feature recipe")
    preprocessing = payload.get("preprocessing", {})
    if preprocessing.get("missing", "median") not in {"median", "native"}:
        raise ValueError("unknown missing-value policy")
    if not set(preprocessing.get("constant_features", ())) <= set(FEATURE_NAMES):
        raise ValueError("unknown constant feature")
    if not set(preprocessing.get("categorical_values", {})) <= set(FEATURE_NAMES):
        raise ValueError("unknown categorical feature")
    if payload.get("estimator", {}).get("kind") not in {"linear", "lightgbm", "catboost"}:
        raise ValueError("unsupported estimator")
    if len(payload.get("impute", ())) != len(FEATURE_NAMES) or not all(math.isfinite(x) for x in payload["impute"]):
        raise ValueError("invalid imputation values")
    if not math.isfinite(payload.get("temperature", 0)) or payload["temperature"] <= 0:
        raise ValueError("invalid calibration temperature")
    slope = payload.get("field_temperature_slope", 0)
    if not math.isfinite(slope) or abs(slope) > 2:
        raise ValueError("invalid field-size calibration slope")
    if len(payload.get("stage_powers", ())) != 2 or any(
        not math.isfinite(x) or x <= 0 for x in payload["stage_powers"]
    ):
        raise ValueError("invalid stage calibration")


def _lgb_tree(tree: Mapping[str, Any], values: Sequence[float]) -> float:
    node = tree
    while "split_index" in node:
        value = values[int(node["split_feature"])]
        decision = node.get("decision_type")
        if decision == "==":
            left = (
                bool(node["default_left"])
                if not math.isfinite(value) or value < 0
                else int(value) in {int(v) for v in str(node["threshold"]).split("||")}
            )
        elif decision == "<=":
            missing_type = node.get("missing_type", "NaN")
            if not math.isfinite(value) and missing_type == "None":
                value = 0.0
            missing = not math.isfinite(value) or (missing_type == "Zero" and value == 0)
            left = bool(node["default_left"]) if missing else value <= float(node["threshold"])
        else:
            raise ValueError("unsupported LightGBM split")
        node = node["left_child"] if left else node["right_child"]
    return float(node["leaf_value"])


def predict_utilities(payload: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[float]:
    validate_model(payload)
    model = payload["estimator"]
    preprocessing = payload.get("preprocessing", {})
    native_missing = preprocessing.get("missing") == "native"
    constant = set(preprocessing.get("constant_features", ()))
    categorical = preprocessing.get("categorical_values", {})
    results = []
    for row in rows:
        values = [
            median if k in constant else number(row.get(k), float("nan") if native_missing else median)
            for k, median in zip(FEATURE_NAMES, payload["impute"], strict=True)
        ]
        if model["kind"] == "lightgbm":
            for key, allowed in categorical.items():
                index = FEATURE_NAMES.index(key)
                if category_token(values[index]) not in allowed:
                    values[index] = float("nan")
        if model["kind"] == "linear":
            score = float(model["intercept"]) + sum(
                w * (x - center) / scale
                for x, center, scale, w in zip(values, model["center"], model["scale"], model["weights"], strict=True)
            )
        elif model["kind"] == "lightgbm":
            score = sum(_lgb_tree(tree["tree_structure"], values) for tree in model["tree_info"])
        else:
            # CatBoostの数値入力は推論前にfloat32へ量子化される。境界付近もネイティブと揃える。
            numeric = [_cat_float32(value) for value in values]
            score = 0.0
            for tree in model["oblivious_trees"]:
                index = 0
                for bit, split in enumerate(tree.get("splits") or []):
                    if split["split_type"] == "FloatFeature":
                        right = numeric[int(split["float_feature_index"])] > float(split["border"])
                    elif split["split_type"] == "CategorySet":
                        right = category_token(values[int(split["feature_index"])]) in split["values"]
                    else:
                        raise ValueError("unsupported CatBoost split")
                    if right:
                        index |= 1 << bit
                score += float(tree["leaf_values"][index])
            scale, bias = model["scale_and_bias"]
            score = score * float(scale) + float(bias[0])
        if not math.isfinite(score):
            raise ValueError("nonfinite model prediction")
        results.append(score)
    return results


def category_token(value: float) -> str:
    return format(value, ".12g") if math.isfinite(value) else "__missing__"


def _cat_float32(value: float) -> float:
    try:
        return struct.unpack("f", struct.pack("f", value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def predict_probabilities(payload: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[float]:
    return softmax(predict_utilities(payload, rows), effective_temperature(payload, len(rows)))


def effective_temperature(payload: Mapping[str, Any], field_size: int) -> float:
    return float(payload["temperature"]) * math.exp(
        float(payload.get("field_temperature_slope", 0)) * (field_size - 12) / 6
    )
