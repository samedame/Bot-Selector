"""Data types shared by every part of the selector. Stdlib only."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional


@dataclass
class Setup:
    """One actionable alert from one bot, normalised."""
    bot_id: str
    key: str                       # stable id, unique per alert
    ts: datetime                   # when the bot raised it (tz-aware)
    valid_until: datetime          # after this the alert is stale
    label: str                     # human text, e.g. "NVDA Phase1A_GapTrap_Single0DTE"
    symbol: Optional[str] = None
    level: Optional[str] = None    # value of the bot's discriminator (module, setup, etf, pillar ...)
    features: dict[str, Any] = field(default_factory=dict)
    contracts: Optional[int] = None
    notes: list[str] = field(default_factory=list)
    closed: bool = False           # the bot has already exited it


@dataclass
class Grade:
    """Per-bot 1-100 grade. Bot-relative: 100 means 'as good as this bot's alerts get'."""
    setup_score: Optional[float]   # 0-100 rank among this bot's own history, None if no validated factor
    exec_score: float              # 0-100, mechanical actionability
    grade: int                     # 1-100 blend, capped at 49 when disqualified
    tier: str                      # A / B / C / D
    disqualifiers: list[str] = field(default_factory=list)
    drags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Edge:
    """Why the cross-bot number is what it is. Every term is shown to the user."""
    claimed: Optional[float]
    live_mean: Optional[float]
    live_n: int
    blended: Optional[float]
    credibility: float
    level_adj: float               # validated-discriminator adjustment from the bot's own ledger
    exec_factor: float
    expected: Optional[float]      # fraction of capital committed, per trade


@dataclass
class Scored:
    setup: Setup
    grade: Grade
    edge: Edge
    eligible: bool
    reasons: list[str] = field(default_factory=list)   # why NOT eligible (empty when eligible)


@dataclass
class Decision:
    ts: datetime
    pick: Optional[Scored]
    runner_up: Optional[Scored]
    board: list[Scored]
    sidelined: list[dict]          # bots with nothing actionable, and why
    kept_previous: bool = False    # pick retained by the switch margin
    headline: str = ""

    def to_log(self) -> dict:
        def s(x: Optional[Scored]):
            if x is None:
                return None
            return {
                "bot": x.setup.bot_id, "key": x.setup.key, "label": x.setup.label,
                "ts": x.setup.ts.isoformat(), "symbol": x.setup.symbol, "level": x.setup.level,
                "minute": x.setup.features.get("minute"), "mode": x.setup.features.get("mode"),
                "grade": x.grade.grade, "tier": x.grade.tier,
                "setup_score": x.grade.setup_score, "exec_score": x.grade.exec_score,
                "expected_edge": x.edge.expected, "eligible": x.eligible, "reasons": x.reasons,
            }
        return {
            "ts": self.ts.isoformat(),
            "pick": s(self.pick),
            "runner_up": s(self.runner_up),
            "board": [s(b) for b in self.board],
            "kept_previous": self.kept_previous,
        }


def asdict_safe(obj) -> dict:
    return asdict(obj)
