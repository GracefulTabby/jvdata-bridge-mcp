"""Git管理して同梱した採用済みJSONと、任意のローカル学習モデルを読む。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

from jvdata_bridge_mcp.ml.inference import validate_model

MAX_MODEL_BYTES = 32 * 1024 * 1024


def bundled_model_directory() -> Path:
    return Path(__file__).with_name("models")


def model_cache() -> Path:
    parent = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return parent / "jvdata-bridge-mcp" / "models"


def _decode(manifest_bytes: bytes, model_bytes: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    if len(manifest_bytes) > 100_000 or len(model_bytes) > MAX_MODEL_BYTES:
        raise ValueError("model asset exceeds size limit")
    manifest = json.loads(manifest_bytes)
    if manifest.get("format") != "jvdata-model-manifest-v1" or manifest.get("asset") != "prediction-model.json":
        raise ValueError("invalid model manifest")
    if manifest.get("sha256") != hashlib.sha256(model_bytes).hexdigest() or manifest.get("size_bytes") != len(
        model_bytes
    ):
        raise ValueError("model checksum or length mismatch")
    payload = json.loads(model_bytes)
    validate_model(payload)
    if payload.get("adoption", {}).get("approved_by_empirical_gate") is not True:
        raise ValueError("research candidate has not passed the adoption gate")
    if (
        payload["adoption"].get("exact_baseline_required") is not True
        or payload["adoption"].get("walk_forward_gate") is not True
    ):
        raise ValueError("exact SQL baseline and walk-forward approval are required")
    if payload["adoption"].get("independent_final_gate") is not True:
        raise ValueError("uninspected independent final evaluation is required")
    if manifest.get("model_version") != payload.get("model_version"):
        raise ValueError("manifest model version mismatch")
    return manifest, payload


def install_bytes(manifest_bytes: bytes, model_bytes: bytes) -> dict[str, Any]:
    manifest, _ = _decode(manifest_bytes, model_bytes)
    root = model_cache()
    destination = root / manifest["sha256"]
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in (("manifest.json", manifest_bytes), ("prediction-model.json", model_bytes)):
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        temporary.replace(destination / name)
    with tempfile.NamedTemporaryFile(dir=root, delete=False) as handle:
        pointer = Path(handle.name)
        handle.write(json.dumps({"sha256": manifest["sha256"]}).encode())
    pointer.replace(root / "active.json")
    _load_cached.cache_clear()
    return {"installed": True, "model_version": manifest["model_version"], "sha256": manifest["sha256"]}


@lru_cache(maxsize=2)
def _load_cached(root: str, digest: str, mtime: int, size: int) -> dict[str, Any]:
    directory = Path(root) / digest
    if size > MAX_MODEL_BYTES:
        raise ValueError("model exceeds size limit")
    manifest, payload = _decode(
        (directory / "manifest.json").read_bytes(), (directory / "prediction-model.json").read_bytes()
    )
    if manifest["sha256"] != digest:
        raise ValueError("active model digest mismatch")
    return payload


def load_active_model() -> dict[str, Any] | None:
    root = model_cache()
    if not (root / "active.json").exists():
        bundled = bundled_model_directory()
        model_file = bundled / "prediction-model.json"
        manifest_file = bundled / "prediction-model.manifest.json"
        if not model_file.exists() and not manifest_file.exists():
            return None
        return _load_bundle(str(bundled), model_file.stat().st_mtime_ns, manifest_file.stat().st_mtime_ns)
    pointer = json.loads((root / "active.json").read_text(encoding="utf-8"))
    digest = str(pointer.get("sha256", ""))
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid active model digest")
    file = root / digest / "prediction-model.json"
    stat = file.stat()
    return _load_cached(str(root), digest, stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=2)
def _load_bundle(root: str, model_mtime: int, manifest_mtime: int) -> dict[str, Any]:
    folder = Path(root)
    model = folder / "prediction-model.json"
    manifest = folder / "prediction-model.manifest.json"
    if model.stat().st_size > MAX_MODEL_BYTES or manifest.stat().st_size > 100_000:
        raise ValueError("model bundle exceeds size limit")
    return _decode(manifest.read_bytes(), model.read_bytes())[1]


def prepare_bundle(model: Path, output: Path) -> dict[str, Any]:
    from jvdata_bridge_mcp.ml._development import archive_directory

    output = archive_directory(output)
    payload = json.loads(model.read_bytes())
    content = (json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    manifest = {
        "format": "jvdata-model-manifest-v1",
        "asset": "prediction-model.json",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "model_version": payload["model_version"],
    }
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _decode(encoded, content)
    (output / "prediction-model.json").write_bytes(content)
    (output / "prediction-model.manifest.json").write_bytes(encoded)
    return manifest


def model_status() -> dict[str, Any]:
    try:
        payload = load_active_model()
    except (ValueError, OSError, KeyError, TypeError) as error:
        return {"status": "invalid_asset", "error": type(error).__name__, "network_fetch_during_inference": False}
    if payload is None:
        return {
            "status": "not_installed",
            "production": "bundled_linear",
            "distribution": "git_versioned_package",
            "source": "bundled_coefficients",
            "setup_required": False,
            "network_fetch_during_inference": False,
        }
    return {
        "status": "ready",
        "distribution": "git_versioned_package",
        "source": "local_install" if (model_cache() / "active.json").exists() else "bundled",
        "setup_required": False,
        "model_version": payload["model_version"],
        "feature_version": payload["feature_version"],
        "feature_recipe": payload.get("feature_recipe", "reference"),
        "preprocessing": payload.get("preprocessing"),
        "calibration_status": payload["calibration_status"],
        "provenance": payload.get("provenance"),
        "market_included": False,
        "network_fetch_during_inference": False,
    }
