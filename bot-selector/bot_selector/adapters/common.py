from __future__ import annotations

import csv
import io
import json
from typing import Any, Optional

from ..sources import SourceError


def load_json(text: Optional[str], default: Any = None) -> Any:
    if text is None or not text.strip():
        return default
    return json.loads(text)


def load_csv(text: Optional[str]) -> list[dict]:
    if not text or not text.strip():
        return []
    return list(csv.DictReader(io.StringIO(text)))


def fnum(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def need(src, repo: str, path: str) -> str:
    """Read a file that MUST exist. A missing required file is a configuration or path problem and must never
    look like 'the bot had nothing to say today'."""
    text = src.read(repo, path)
    if text is None:
        raise SourceError(f"{repo}/{path} not found (wrong path, wrong branch, or no read access)")
    return text
