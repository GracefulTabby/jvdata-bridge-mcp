"""時系列研究用の前処理・早期停止・カテゴリ対応JSONへの変換。"""

from __future__ import annotations

import json
import math
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize
from threadpoolctl import threadpool_limits

from jvdata_bridge_mcp.ml.features import FEATURE_NAMES
from jvdata_bridge_mcp.ml.model import category_token, predict_utilities
from jvdata_bridge_mcp.ml.research.common import groups, model_payload

CATEGORY_NAMES = ("venue", "grade", "dirt")


@dataclass(frozen=True)
class Trial:
    name: str
    family: str
    missing: str = "median"
    categorical: bool = False
    leaves_or_depth: int = 31
    regularization: float = 10
    min_leaf: int = 150
    learning_rate: float = 0.04
    iterations: int = 1200
    feature_recipe: str = "reference"
    feature_subset: str = "all"
    rolling_years: int = 0
    half_life_years: float = 0
    ranking: bool = False
    early_stopping: bool = True


def constant_features(subset: str) -> list[str]:
    if subset == "all":
        return []
    if subset == "no_pace":
        return [name for name in FEATURE_NAMES if name.startswith("course_pace_")]
    if subset == "raw_only":
        return [name for name in FEATURE_NAMES if name.endswith("_score")]
    raise ValueError("unknown feature subset")


def fit_preprocessing(
    frame: Any,
    trial: Trial,
    feature_names: tuple[str, ...] = FEATURE_NAMES,
    disabled_features: tuple[str, ...] = (),
) -> tuple[list[float], dict[str, Any]]:
    values = frame[list(feature_names)].to_numpy(dtype=float)
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        medians = np.nan_to_num(np.nanmedian(values, axis=0), nan=0).tolist()
    categories = (
        {name: sorted({category_token(float(value)) for value in frame[name]}) for name in CATEGORY_NAMES}
        if trial.categorical
        else {}
    )
    return medians, {
        "missing": trial.missing,
        "categorical_values": categories,
        "constant_features": list(dict.fromkeys([*constant_features(trial.feature_subset), *disabled_features])),
    }


def prepared_matrix(frame: Any, payload: dict[str, Any], *, catboost: bool = False) -> np.ndarray:
    names = tuple(payload["features"])
    values = frame[list(names)].to_numpy(dtype=float).copy()
    medians = np.asarray(payload["impute"])
    preprocessing = payload["preprocessing"]
    if preprocessing["missing"] == "median":
        values = np.where(np.isfinite(values), values, medians)
    for name in preprocessing["constant_features"]:
        values[:, names.index(name)] = medians[names.index(name)]
    if catboost and preprocessing["categorical_values"]:
        result = values.astype(object)
        for name in preprocessing["categorical_values"]:
            index = names.index(name)
            result[:, index] = [category_token(value) for value in values[:, index]]
        return result
    for name, allowed in preprocessing["categorical_values"].items():
        index = names.index(name)
        values[:, index] = [value if category_token(value) in allowed else np.nan for value in values[:, index]]
    return values


def race_loss(frame: Any, scores: np.ndarray) -> tuple[float, float]:
    sizes = groups(frame)
    starts = np.r_[0, np.cumsum(sizes)[:-1]]
    maximum = np.maximum.reduceat(scores, starts)
    exponentials = np.exp(scores - np.repeat(maximum, sizes))
    denominator = np.add.reduceat(exponentials, starts)
    p = exponentials / np.repeat(denominator, sizes)
    labels = frame.win.to_numpy(dtype=float).copy()
    labels /= np.repeat(np.add.reduceat(labels, starts), sizes)
    ll = np.mean(-np.add.reduceat(labels * np.log(np.maximum(p, 1e-15)), starts))
    brier = np.mean(np.add.reduceat((p - labels) ** 2, starts) / sizes)
    return float(ll), float(brier)


def training_weights(frame: Any, half_life: float) -> tuple[np.ndarray, np.ndarray]:
    sizes = groups(frame)
    dates = frame.groupby("race_id", sort=False).race_date.first()
    if half_life:
        import pandas as pd

        age = (pd.to_datetime(frame.race_date.max()) - pd.to_datetime(dates)).dt.days.to_numpy() / 365.25
        race_weights = np.exp2(-age / half_life)
    else:
        race_weights = np.ones(len(sizes))
    race_weights /= race_weights.mean()
    return race_weights, np.repeat(race_weights / sizes, sizes)


def fit_linear(x: np.ndarray, frame: Any, trial: Trial) -> dict[str, Any]:
    center, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1
    z = (x - center) / scale
    sizes = groups(frame)
    starts = np.r_[0, np.cumsum(sizes)[:-1]]
    labels = frame.win.to_numpy(dtype=float).copy()
    labels /= np.repeat(np.add.reduceat(labels, starts), sizes)
    race_weights, _ = training_weights(frame, trial.half_life_years)

    def objective(weights: np.ndarray) -> tuple[float, np.ndarray]:
        score = z @ weights
        maximum = np.maximum.reduceat(score, starts)
        exponentials = np.exp(score - np.repeat(maximum, sizes))
        total = np.add.reduceat(exponentials, starts)
        p = exponentials / np.repeat(total, sizes)
        loss = np.mean(race_weights * (np.log(total) + maximum - np.add.reduceat(labels * score, starts)))
        gradient = z.T @ ((p - labels) * np.repeat(race_weights, sizes)) / len(sizes)
        return float(loss + trial.regularization * (weights @ weights)), gradient + 2 * trial.regularization * weights

    with threadpool_limits(limits=3):
        fitted = minimize(objective, np.zeros(x.shape[1]), jac=True, method="L-BFGS-B", options={"maxiter": 400})
    if not fitted.success:
        raise ValueError(f"linear fitting failed: {fitted.message}")
    return {
        "kind": "linear",
        "intercept": 0,
        "weights": fitted.x.tolist(),
        "center": center.tolist(),
        "scale": scale.tolist(),
    }


def export_catboost(
    model: Any, preprocessing: dict[str, Any], feature_names: tuple[str, ...] = FEATURE_NAMES
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "model.json"
        model.save_model(str(path), format="json")
        dump = json.loads(path.read_text(encoding="utf-8"))
    float_indices = {
        feature["feature_index"]: feature["flat_feature_index"] for feature in dump["features_info"]["float_features"]
    }
    cat_indices = {
        feature["feature_index"]: feature["flat_feature_index"]
        for feature in dump["features_info"].get("categorical_features", [])
    }
    probes, probe_tokens = [], []
    if cat_indices:
        prototype: list[Any] = [0.0] * len(feature_names)
        for flat in cat_indices.values():
            prototype[flat] = preprocessing["categorical_values"][feature_names[flat]][0]
        for cat_index, flat in cat_indices.items():
            for token in preprocessing["categorical_values"][feature_names[flat]]:
                row = prototype.copy()
                row[flat] = token
                probes.append(row)
                probe_tokens.append((cat_index, token))
        leaves = model.calc_leaf_indexes(probes)
    else:
        leaves = np.empty((0, 0))
    trees = []
    for tree_index, tree in enumerate(dump["oblivious_trees"]):
        splits = []
        for bit, split in enumerate(tree.get("splits") or []):
            if split["split_type"] == "FloatFeature":
                splits.append(
                    {
                        "split_type": "FloatFeature",
                        "float_feature_index": float_indices[split["float_feature_index"]],
                        "border": split["border"],
                    }
                )
            elif split["split_type"] == "OneHotFeature":
                cat_index = split["cat_feature_index"]
                tokens = [
                    token
                    for i, (cat, token) in enumerate(probe_tokens)
                    if cat == cat_index and ((int(leaves[i, tree_index]) >> bit) & 1)
                ]
                splits.append({"split_type": "CategorySet", "feature_index": cat_indices[cat_index], "values": tokens})
            else:
                raise ValueError(
                    "categorical target-statistic splits are not portable; use bounded one-hot categories"
                )
        trees.append({"splits": splits, "leaf_values": tree["leaf_values"]})
    return {"kind": "catboost", "oblivious_trees": trees, "scale_and_bias": dump["scale_and_bias"]}


def fit_trial(
    train: Any,
    stopping: Any,
    trial: Trial,
    *,
    iterations: int | None = None,
    feature_names: tuple[str, ...] = FEATURE_NAMES,
    feature_version: str | None = None,
    disabled_features: tuple[str, ...] = (),
) -> tuple[Any, dict[str, Any], int]:
    medians, preprocessing = fit_preprocessing(train, trial, feature_names, disabled_features)
    payload = model_payload(trial.name, {}, np.asarray(medians))
    payload["features"] = list(feature_names)
    if feature_version is not None:
        payload["feature_version"] = feature_version
    payload.update({"feature_recipe": trial.feature_recipe, "preprocessing": preprocessing, "trial": asdict(trial)})
    x = prepared_matrix(train, payload, catboost=trial.family == "catboost")
    xs = prepared_matrix(stopping, payload, catboost=trial.family == "catboost")
    labels = train.win.to_numpy(dtype=float)
    sizes = groups(train)
    race_weights, row_weights = training_weights(train, trial.half_life_years)
    total = iterations or trial.iterations
    if trial.family == "linear":
        model = fit_linear(x, train, trial)
        estimator, best = model, 0
    elif trial.family == "lightgbm":
        import lightgbm as lgb

        model = (lgb.LGBMRanker if trial.ranking else lgb.LGBMClassifier)(
            objective="lambdarank" if trial.ranking else "binary",
            metric="None",
            n_estimators=total,
            num_leaves=trial.leaves_or_depth,
            learning_rate=trial.learning_rate,
            min_child_samples=trial.min_leaf,
            reg_lambda=trial.regularization,
            n_jobs=3,
            verbosity=-1,
            random_state=617,
            deterministic=True,
            force_col_wise=True,
        )

        def metric(actual: np.ndarray, predictions: np.ndarray) -> tuple[str, float, bool]:
            scores = (
                predictions
                if trial.ranking
                else np.log(np.clip(predictions, 1e-12, 1 - 1e-12) / np.clip(1 - predictions, 1e-12, 1))
            )
            return "race_log_loss", race_loss(stopping, scores)[0], False

        target = np.maximum(0, 4 - train.finish.to_numpy()).astype(int) if trial.ranking else labels
        stop_target = (
            np.maximum(0, 4 - stopping.finish.to_numpy()).astype(int) if trial.ranking else stopping.win.to_numpy()
        )
        kwargs = {"group": sizes, "eval_group": [groups(stopping)]} if trial.ranking else {}
        model.fit(
            x,
            target,
            sample_weight=row_weights,
            eval_set=[(xs, stop_target)],
            eval_metric=metric,
            categorical_feature=[feature_names.index(name) for name in preprocessing["categorical_values"]],
            callbacks=[]
            if iterations or not trial.early_stopping
            else [lgb.early_stopping(80, first_metric_only=True, verbose=False)],
            **kwargs,
        )
        best = int(model.best_iteration_ or total)
        estimator = {"kind": "lightgbm", "tree_info": model.booster_.dump_model()["tree_info"]}
    else:
        from catboost import CatBoostRanker, Pool

        model = CatBoostRanker(
            iterations=total,
            depth=trial.leaves_or_depth,
            learning_rate=trial.learning_rate,
            l2_leaf_reg=trial.regularization,
            thread_count=3,
            random_seed=617,
            loss_function="QuerySoftMax",
            one_hot_max_size=32,
            nan_mode="Min",
            verbose=False,
            allow_writing_files=False,
            bootstrap_type="No",
        )
        categories = [feature_names.index(name) for name in preprocessing["categorical_values"]]
        pool = Pool(
            x,
            labels,
            group_id=np.repeat(np.arange(len(sizes)), sizes),
            group_weight=np.repeat(race_weights, sizes),
            cat_features=categories,
        )
        stop_sizes = groups(stopping)
        stop_pool = Pool(
            xs,
            stopping.win.to_numpy(),
            group_id=np.repeat(np.arange(len(stop_sizes)), stop_sizes),
            cat_features=categories,
        )
        model.fit(
            pool,
            eval_set=stop_pool,
            early_stopping_rounds=None if iterations or not trial.early_stopping else 80,
            use_best_model=not bool(iterations) and trial.early_stopping,
        )
        best = int(model.tree_count_)
        estimator = export_catboost(model, preprocessing, feature_names)
    payload["estimator"] = estimator
    payload["model_version"] = f"optimized-{trial.name}-{trial.feature_recipe}"
    return model, payload, best


def native_scores(model: Any, payload: dict[str, Any], frame: Any) -> np.ndarray:
    kind = payload["estimator"]["kind"]
    values = prepared_matrix(frame, payload, catboost=kind == "catboost")
    if kind == "linear":
        return ((values - np.asarray(model["center"])) / np.asarray(model["scale"])) @ np.asarray(model["weights"])
    if kind == "lightgbm":
        return model.predict(values, raw_score=True)
    return np.asarray(model.predict(values))


def check_export(model: Any, payload: dict[str, Any], frame: Any) -> float:
    probes = frame.iloc[:64].copy()
    altered = probes.iloc[:3].copy()
    altered.loc[:, "venue"] = 99
    altered.loc[:, "speed_quality_raw"] = np.nan
    import pandas as pd

    boundary_rows, seen = [], set()
    if payload["estimator"]["kind"] == "catboost":
        for tree in payload["estimator"]["oblivious_trees"]:
            for split in tree.get("splits") or []:
                if split["split_type"] != "FloatFeature":
                    continue
                key = (split["float_feature_index"], split["border"])
                if key in seen:
                    continue
                seen.add(key)
                border = float(split["border"])
                with np.errstate(over="ignore", invalid="ignore"):
                    step = float(np.nextafter(np.float32(border), np.float32(np.inf))) - border
                if math.isfinite(step) and step > 0:
                    row = probes.iloc[0].copy()
                    row[payload["features"][split["float_feature_index"]]] = border + step / 4
                    boundary_rows.append(row)
                if len(boundary_rows) >= 100:
                    break
            if len(boundary_rows) >= 100:
                break
    probes = pd.concat([probes, altered, pd.DataFrame(boundary_rows)], ignore_index=True)
    native = native_scores(model, payload, probes)
    if tuple(payload["features"]) == FEATURE_NAMES:
        portable = np.asarray(predict_utilities(payload, probes.to_dict("records")))
    else:
        from jvdata_bridge_mcp.ml.inference import predict_utilities as expanded_utilities

        portable = np.asarray(expanded_utilities(payload, probes.to_dict("records")))
    error = float(np.max(np.abs(native - portable)))
    if not math.isfinite(error) or error > 1e-5:
        raise ValueError(f"native/JSON mismatch: {error}")
    return error
