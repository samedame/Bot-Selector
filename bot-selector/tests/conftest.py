import json
import os
import shutil
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot_selector.engine import Engine  # noqa: E402
from bot_selector.registry import load_registry  # noqa: E402
from bot_selector.sources import LocalSource  # noqa: E402
from bot_selector.timeutil import ET  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "repos")


def et(s: str) -> datetime:
    """'2026-10-27 09:50:30' -> aware Eastern datetime."""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)


@pytest.fixture
def registry(tmp_path):
    # an empty overrides file so a developer's local state/overrides.json can never change a test
    ov = tmp_path / "overrides.json"
    ov.write_text("{}")
    return load_registry(overrides_path=str(ov))


@pytest.fixture
def repos(tmp_path):
    """A writable copy of the real-data fixtures, so a test can edit a state file."""
    dst = tmp_path / "repos"
    shutil.copytree(FIX, dst)
    return str(dst)


@pytest.fixture
def engine(registry, repos, tmp_path):
    return Engine(registry, LocalSource(repos), overrides_path=str(tmp_path / "overrides.json"))


def write(repos, rel, obj, raw=False):
    p = os.path.join(repos, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write(obj if raw else json.dumps(obj))
