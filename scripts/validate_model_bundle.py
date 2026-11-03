"""同梱JSONだけを検証する。DB、学習環境、ネットワークへの接続は不要。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jvdata_bridge_mcp.ml.artifacts import _decode, bundled_model_directory


def main() -> None:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else bundled_model_directory()
    unexpected = sorted(
        path.name
        for path in folder.iterdir()
        if path.name not in {"README.md", "prediction-model.json", "prediction-model.manifest.json"}
    )
    if unexpected:
        raise ValueError(f"unexpected files in model bundle: {unexpected}")
    if not (folder / "prediction-model.json").exists() and not (folder / "prediction-model.manifest.json").exists():
        if len(sys.argv) > 1:
            raise ValueError("specified bundle is missing")
        print(json.dumps({"valid": True, "model": "bundled_coefficients", "additional_bundle": False}))
        return
    manifest, model = _decode(
        (folder / "prediction-model.manifest.json").read_bytes(), (folder / "prediction-model.json").read_bytes()
    )
    if not model.get("provenance", {}).get("periods"):
        raise ValueError("training, selection, calibration and evaluation provenance required")
    print(json.dumps({"valid": True, "model_version": manifest["model_version"], "sha256": manifest["sha256"]}))


if __name__ == "__main__":
    main()
