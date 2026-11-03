"""ローカル開発成果物の保存先とJSON入出力。"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def archive_directory(path: Path) -> Path:
    """実データ、モデル、評価結果はGit管理外archiveのみに書く。"""
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()).resolve()
    archive = root / "archive"
    resolved = path.resolve()
    if not resolved.is_relative_to(archive) or resolved == archive:
        raise ValueError("development output must be a subdirectory of archive/")
    archive.mkdir(exist_ok=True)
    ignore = archive / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", encoding="utf-8")
    if "*" not in ignore.read_text(encoding="utf-8-sig").splitlines():
        raise ValueError("archive/.gitignore must contain *")
    if subprocess.check_output(["git", "ls-files", "archive"], cwd=root, text=True).strip():
        raise ValueError("archive contains tracked files")
    checked = subprocess.run(["git", "check-ignore", "--quiet", str(resolved / "probe.json")], cwd=root, check=False)
    if checked.returncode:
        raise ValueError("output is not ignored by Git")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str, allow_nan=False), encoding="utf-8")


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
