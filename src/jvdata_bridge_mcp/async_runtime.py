"""アプリ起動時の非同期イベントループ。"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run_async(operation: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """WindowsではPsycopgに対応するSelectorループで処理を実行する。"""

    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    with asyncio.Runner(loop_factory=loop_factory) as runner:
        return runner.run(operation())
