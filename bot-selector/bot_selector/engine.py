"""Collects every bot's setups and history, then asks rank.decide for the pick."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from .adapters import COLLECT, history_for
from .model import Decision, Setup
from .rank import decide
from .registry import Registry, halted_bots
from .sources import SourceError

HISTORY_TTL_S = 1800


class Engine:
    def __init__(self, registry: Registry, src, overrides_path: Optional[str] = None):
        self.registry = registry
        self.src = src
        self.overrides_path = overrides_path
        self._hist: dict[str, tuple[float, list[dict]]] = {}
        self.errors: dict[str, str] = {}
        if hasattr(src, "branches"):
            src.branches.update({b.repo: b.branch for b in registry.bots.values()})

    def collect(self, now: datetime) -> dict[str, tuple[list[Setup], list[str]]]:
        out: dict[str, tuple[list[Setup], list[str]]] = {}
        self.errors = {}
        for bot in self.registry.bots.values():
            fn = COLLECT[bot.adapter]
            try:
                out[bot.id] = fn(bot, self.src, now, self.registry.defaults)
            except SourceError as e:
                self.errors[bot.id] = str(e)
                out[bot.id] = ([], [f"could not read {bot.repo}: {e}"])
            except (KeyError, ValueError, TypeError) as e:
                # A bot whose file format drifted must never take the selector down or fake a pick.
                self.errors[bot.id] = f"{type(e).__name__}: {e}"
                out[bot.id] = ([], [f"could not parse {bot.repo} ({type(e).__name__}: {e})"])
        return out

    def histories(self, now: Optional[datetime] = None) -> dict[str, list[dict]]:
        """Each bot's own closed trades from BEFORE today. Same-day rows are excluded on purpose: in a replay
        they would leak the outcome being predicted, and live they are the least settled rows anyway."""
        out: dict[str, list[dict]] = {}
        for bot in self.registry.bots.values():
            cached = self._hist.get(bot.id)
            if cached and time.time() - cached[0] < HISTORY_TTL_S:
                out[bot.id] = cached[1]
                continue
            try:
                rows = history_for(bot, self.src)
            except (SourceError, KeyError, ValueError, TypeError) as e:
                self.errors.setdefault(bot.id, f"history: {e}")
                rows = cached[1] if cached else []
            self._hist[bot.id] = (time.time(), rows)
            out[bot.id] = rows
        if now is not None:
            cut = now.date().isoformat()
            out = {k: [r for r in v if (r.get("date") or "") < cut] for k, v in out.items()}
        return out

    def decide(self, now: datetime, previous_key: Optional[str] = None) -> Decision:
        collected = self.collect(now)
        return decide(self.registry, collected, self.histories(now), now, previous_key,
                      halted_bots(self.overrides_path))
