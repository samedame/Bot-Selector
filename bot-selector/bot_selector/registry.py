"""Loads bots.json and applies the per-bot overrides in state/overrides.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class Bot:
    id: str
    label: str
    channel: Optional[str]
    repo: str
    adapter: str
    status: str
    kind: str
    params: dict
    validity_s: int
    grace_s: int
    claimed_edge: Optional[float]
    edge_unit: str
    edge_source: str
    evidence_pts: float
    price_pts: float
    setup_weight: float
    discriminator: dict
    exec_factors: list
    status_reason: str = ""
    superseded_by: Optional[str] = None
    credibility_override: Optional[float] = None
    branch: str = "main"
    raw: dict = field(default_factory=dict)

    @property
    def credibility(self) -> float:
        """Probability-like haircut on a documented edge: evidence quality x price realism."""
        if self.credibility_override is not None:
            return float(self.credibility_override)
        return max(0.0, min(1.0, (self.evidence_pts / 35.0) * (self.price_pts / 20.0)))


@dataclass
class Registry:
    defaults: dict
    bots: dict[str, Bot]

    def get(self, bot_id: str) -> Bot:
        return self.bots[bot_id]


def load_registry(path: Optional[str] = None, overrides_path: Optional[str] = None) -> Registry:
    path = path or os.path.join(ROOT, "bots.json")
    with open(path) as fh:
        data = json.load(fh)
    overrides: dict[str, Any] = {}
    overrides_path = overrides_path or os.path.join(ROOT, "state", "overrides.json")
    if os.path.exists(overrides_path):
        with open(overrides_path) as fh:
            text = fh.read().strip()
        overrides = json.loads(text) if text else {}
    bots: dict[str, Bot] = {}
    for raw in data["bots"]:
        raw = {**raw, **overrides.get("bots", {}).get(raw["id"], {})}
        bots[raw["id"]] = Bot(
            id=raw["id"], label=raw["label"], channel=raw.get("channel"), repo=raw["repo"],
            adapter=raw["adapter"], status=raw["status"], kind=raw.get("kind", "trade"),
            params=raw.get("params", {}), validity_s=int(raw.get("validity_s", 600)),
            grace_s=int(raw.get("grace_s", 150)), claimed_edge=raw.get("claimed_edge"),
            edge_unit=raw.get("edge_unit", ""), edge_source=raw.get("edge_source", ""),
            evidence_pts=float(raw.get("evidence_pts", 0)), price_pts=float(raw.get("price_pts", 0)),
            setup_weight=float(raw.get("setup_weight", 0)), discriminator=raw.get("discriminator", {}),
            exec_factors=raw.get("exec_factors", []), status_reason=raw.get("status_reason", ""),
            superseded_by=raw.get("superseded_by"), credibility_override=raw.get("credibility_override"),
            branch=raw.get("branch", "main"),
            raw=raw,
        )
    defaults = {**data.get("defaults", {}), **overrides.get("defaults", {})}
    return Registry(defaults=defaults, bots=bots)


def halted_bots(overrides_path: Optional[str] = None) -> set[str]:
    """Manual kill switch: state/overrides.json -> {"halt": ["chronos"]}. Wins over everything."""
    overrides_path = overrides_path or os.path.join(ROOT, "state", "overrides.json")
    if not os.path.exists(overrides_path):
        return set()
    with open(overrides_path) as fh:
        text = fh.read().strip()
    return set(json.loads(text).get("halt", [])) if text else set()
