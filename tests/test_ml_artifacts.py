"""配布アセットの整合性と、学習依存を必要としないJSON推論。"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from jvdata_bridge_mcp.ml import artifacts
from jvdata_bridge_mcp.ml.features import FEATURE_NAMES, FEATURE_VERSION
from jvdata_bridge_mcp.ml.model import MODEL_FORMAT, predict_utilities


def _model() -> dict[str, object]:
    n = len(FEATURE_NAMES)
    return {
        "format": MODEL_FORMAT,
        "feature_version": FEATURE_VERSION,
        "features": list(FEATURE_NAMES),
        "market_included": False,
        "model_version": "synthetic-only",
        "impute": [0.0] * n,
        "temperature": 1.0,
        "stage_powers": [1.0, 1.0],
        "calibration_status": "synthetic_test",
        "adoption": {
            "approved_by_empirical_gate": True,
            "exact_baseline_required": True,
            "walk_forward_gate": True,
            "independent_final_gate": True,
        },
        "estimator": {
            "kind": "linear",
            "intercept": 0,
            "weights": [1.0] + [0.0] * (n - 1),
            "center": [0.0] * n,
            "scale": [1.0] * n,
        },
    }


def _asset(payload: dict[str, object]) -> tuple[bytes, bytes]:
    content = json.dumps(payload).encode()
    manifest = {
        "format": "jvdata-model-manifest-v1",
        "asset": "prediction-model.json",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "model_version": payload["model_version"],
    }
    return json.dumps(manifest).encode(), content


def test_checksum_and_adoption_gate_reject_invalid_models() -> None:
    model = _model()
    manifest, content = _asset(model)
    with pytest.raises(ValueError, match="checksum"):
        artifacts._decode(manifest, content + b" ")
    model["adoption"] = {"approved_by_empirical_gate": False}
    with pytest.raises(ValueError, match="adoption"):
        artifacts._decode(*_asset(model))
    model["adoption"] = {"approved_by_empirical_gate": True}
    with pytest.raises(ValueError, match="exact SQL"):
        artifacts._decode(*_asset(model))
    model["adoption"] = {
        "approved_by_empirical_gate": True,
        "exact_baseline_required": True,
        "walk_forward_gate": True,
    }
    with pytest.raises(ValueError, match="independent final"):
        artifacts._decode(*_asset(model))


@pytest.mark.parametrize("kind", ["lightgbm", "catboost"])
def test_portable_categories_and_native_missing_have_explicit_branches(kind: str) -> None:
    model = _model()
    index = FEATURE_NAMES.index("venue")
    model["preprocessing"] = {"missing": "native", "categorical_values": {"venue": ["1", "2", "3", "__missing__"]}}
    if kind == "lightgbm":
        model["estimator"] = {
            "kind": kind,
            "tree_info": [
                {
                    "tree_structure": {
                        "split_index": 0,
                        "split_feature": index,
                        "decision_type": "==",
                        "threshold": "1||3",
                        "default_left": False,
                        "left_child": {"leaf_value": 2},
                        "right_child": {"leaf_value": -1},
                    }
                }
            ],
        }
        expected = [2, -1, -1, -1]
    else:
        model["estimator"] = {
            "kind": kind,
            "scale_and_bias": [1, [0]],
            "oblivious_trees": [
                {
                    "splits": [{"split_type": "CategorySet", "feature_index": index, "values": ["1", "__missing__"]}],
                    "leaf_values": [-1, 2],
                }
            ],
        }
        expected = [2, -1, -1, 2]
    assert predict_utilities(model, [{"venue": value} for value in (1, 2, 99, None)]) == expected


def test_catboost_numeric_splits_quantize_float32_without_rounding_category_tokens() -> None:
    model = _model()
    model["preprocessing"] = {"missing": "native", "categorical_values": {"venue": ["1.00000001"]}}
    model["estimator"] = {
        "kind": "catboost",
        "scale_and_bias": [1, [0]],
        "oblivious_trees": [
            {
                "splits": [{"split_type": "FloatFeature", "float_feature_index": 0, "border": 1.0}],
                "leaf_values": [0, 1],
            },
            {
                "splits": [
                    {
                        "split_type": "CategorySet",
                        "feature_index": FEATURE_NAMES.index("venue"),
                        "values": ["1.00000001"],
                    }
                ],
                "leaf_values": [0, 2],
            },
        ],
    }
    rows = [{FEATURE_NAMES[0]: value, "venue": 1.00000001} for value in (1 + 1e-8, 1 + 1e-6, None, 1e100)]
    assert predict_utilities(model, rows) == [2, 3, 2, 3]


def test_offline_review_uses_float32_splits_and_field_size_calibration() -> None:
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    from jvdata_bridge_mcp.ml.research.evaluation import score_frozen

    model = _model()
    model["field_temperature_slope"] = 0.3
    model["estimator"] = {
        "kind": "catboost",
        "scale_and_bias": [1, [0]],
        "oblivious_trees": [
            {
                "splits": [{"split_type": "FloatFeature", "float_feature_index": 0, "border": 1.0}],
                "leaf_values": [0, 1],
            }
        ],
    }
    values = [1 + 1e-8, 1 + 1e-6, 0, 0, 1, 1 + 1e-8, 1 + 1e-6, 2]
    frame = pd.DataFrame({"race_id": ["a"] * 3 + ["b"] * 5, FEATURE_NAMES[0]: values})
    p = score_frozen(model, frame)
    first_temperature = math.exp(0.3 * (3 - 12) / 6)
    second_temperature = math.exp(0.3 * (5 - 12) / 6)
    assert p[:3].sum() == pytest.approx(1)
    assert p[3:].sum() == pytest.approx(1)
    assert p[0] == pytest.approx(1 / (2 + math.exp(1 / first_temperature)))
    assert p[-1] == pytest.approx(math.exp(1 / second_temperature) / (3 + 2 * math.exp(1 / second_temperature)))
    assert np.isfinite(p).all()


def test_explicit_local_install_is_atomic_and_inference_never_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(artifacts, "model_cache", lambda: tmp_path)
    assert artifacts.load_active_model() is None
    artifacts.install_bytes(*_asset(_model()))
    loaded = artifacts.load_active_model()
    assert loaded is not None
    assert predict_utilities(loaded, [{FEATURE_NAMES[0]: 2.0}, {FEATURE_NAMES[0]: float("nan")}]) == [2, 0]
    assert artifacts.model_status()["network_fetch_during_inference"] is False
    assert artifacts.model_status()["status"] == "ready"


def test_packaged_json_is_used_without_setup_and_local_training_install_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "package"
    bundle.mkdir()
    monkeypatch.setattr(artifacts, "bundled_model_directory", lambda: bundle)
    monkeypatch.setattr(artifacts, "model_cache", lambda: tmp_path / "cache")
    manifest, content = _asset(_model())
    (bundle / "prediction-model.manifest.json").write_bytes(manifest)
    (bundle / "prediction-model.json").write_bytes(content)
    assert artifacts.load_active_model()["model_version"] == "synthetic-only"
    status = artifacts.model_status()
    assert status["source"] == "bundled" and status["setup_required"] is False
    assert not (tmp_path / "cache").exists()

    local = _model()
    local["model_version"] = "local-synthetic"
    artifacts.install_bytes(*_asset(local))
    assert artifacts.load_active_model()["model_version"] == "local-synthetic"
    assert artifacts.model_status()["source"] == "local_install"


def test_bundle_checksum_uses_canonical_lf_bytes_for_git_and_all_operating_systems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jvdata_bridge_mcp.ml import _development as dataset

    source = tmp_path / "synthetic.json"
    source.write_bytes(json.dumps(_model(), indent=2).replace("\n", "\r\n").encode())
    output = tmp_path / "bundle"
    output.mkdir()
    monkeypatch.setattr(dataset, "archive_directory", lambda path: path)
    manifest = artifacts.prepare_bundle(source, output)
    content = (output / "prediction-model.json").read_bytes()
    assert b"\r" not in content and content.endswith(b"\n")
    assert manifest["sha256"] == hashlib.sha256(content).hexdigest()
    _, payload = artifacts._decode((output / "prediction-model.manifest.json").read_bytes(), content)
    assert predict_utilities(payload, [{FEATURE_NAMES[0]: 2.0}]) == [2]


@pytest.mark.parametrize("bad_model", [b"{}", None])
def test_invalid_or_incomplete_packaged_json_cannot_silently_switch_to_bundled_model(
    bad_model: bytes | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(artifacts, "bundled_model_directory", lambda: tmp_path)
    monkeypatch.setattr(artifacts, "model_cache", lambda: tmp_path / "cache")
    manifest, _ = _asset(_model())
    (tmp_path / "prediction-model.manifest.json").write_bytes(manifest)
    if bad_model is not None:
        (tmp_path / "prediction-model.json").write_bytes(bad_model)
    with pytest.raises((ValueError, OSError)):
        artifacts.load_active_model()
    assert artifacts.model_status()["status"] == "invalid_asset"


@pytest.mark.parametrize("kind", ["lightgbm", "catboost"])
def test_numeric_tree_export_respects_thresholds_and_imputation(kind: str) -> None:
    model = _model()
    if kind == "lightgbm":
        model["estimator"] = {
            "kind": kind,
            "tree_info": [
                {
                    "tree_structure": {
                        "split_index": 0,
                        "split_feature": 0,
                        "decision_type": "<=",
                        "threshold": 0.5,
                        "default_left": True,
                        "left_child": {"leaf_value": 1.0},
                        "right_child": {"leaf_value": 3.0},
                    }
                }
            ],
        }
    else:
        model["estimator"] = {
            "kind": kind,
            "scale_and_bias": [1, [0]],
            "oblivious_trees": [
                {
                    "splits": [{"split_type": "FloatFeature", "float_feature_index": 0, "border": 0.5}],
                    "leaf_values": [1.0, 3.0],
                }
            ],
        }
    actual = predict_utilities(model, [{FEATURE_NAMES[0]: v} for v in (0.5, 0.6, float("nan"))])
    assert actual == [1.0, 3.0, 1.0]
