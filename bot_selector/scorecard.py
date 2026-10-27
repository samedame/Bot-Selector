"""Does the selector actually add anything? Measured, not assumed.

For every logged decision, once the bots' own ledgers report how each candidate ended, compare the outcome of
the alert the selector picked with the average outcome of the other alerts that were on the board at that
moment. Only bots whose ledgers carry a premium-ROI outcome can be compared (the Apex family today).

Until there are enough decisions the verdict is INSUFFICIENT DATA. That is the expected state for weeks.
"""
from __future__ import annotations

import random
from statistics import mean
from typing import Optional

MIN_N = 30


def attach_outcomes(decisions: list[dict], histories: dict[str, list[dict]]) -> int:
    """Fill candidate['outcome'] from each bot's closed-trade ledger. Returns how many were newly resolved."""
    index: dict[tuple, float] = {}
    for bot_id, rows in histories.items():
        for r in rows:
            if r.get("unit") != "roi":
                continue
            index[(bot_id, r.get("date"), r.get("ticker"), r.get("level"), r.get("minute"), r.get("mode"))] = r["outcome"]
    resolved = 0
    for d in decisions:
        cands = [d.get("pick")] + list(d.get("board") or [])
        for c in cands:
            if not c or c.get("outcome") is not None:
                continue
            k = (c["bot"], c["ts"][:10], c.get("symbol"), c.get("level"), c.get("minute"), c.get("mode"))
            if k in index:
                c["outcome"] = index[k]
                resolved += 1
    return resolved


def _boot_ci(xs: list[float], reps: int = 2000, seed: int = 7) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(mean(rng.choice(xs) for _ in range(n)) for _ in range(reps))
    return means[int(0.05 * reps)], means[int(0.95 * reps)]


def scorecard(decisions: list[dict]) -> dict:
    diffs: list[float] = []
    picked: list[float] = []
    for d in decisions:
        pick = d.get("pick")
        if not pick or pick.get("outcome") is None:
            continue
        key = pick["key"]
        others = [c["outcome"] for c in (d.get("board") or [])
                  if c and c["key"] != key and c.get("outcome") is not None]
        if not others:
            continue
        diffs.append(pick["outcome"] - mean(others))
        picked.append(pick["outcome"])
    out: dict = {"n": len(diffs), "min_n": MIN_N, "decisions_logged": len(decisions)}
    if not diffs:
        out["verdict"] = "INSUFFICIENT DATA: no decision yet has a pick and at least one alternative with a known outcome"
        return out
    lo, hi = _boot_ci(diffs)
    out.update({"mean_pick_roi": mean(picked), "mean_advantage": mean(diffs), "ci90": [lo, hi],
                "share_pick_beat_alternatives": sum(1 for x in diffs if x > 0) / len(diffs)})
    if len(diffs) < MIN_N:
        out["verdict"] = f"INSUFFICIENT DATA: {len(diffs)} comparable decisions, need {MIN_N}"
    elif lo > 0:
        out["verdict"] = "ADDS VALUE: the pick beat the alternatives and the 90% interval excludes zero"
    elif hi < 0:
        out["verdict"] = "HURTS: the pick underperformed the alternatives; stop using it"
    else:
        out["verdict"] = "NOT DEMONSTRATED: the 90% interval includes zero"
    return out
