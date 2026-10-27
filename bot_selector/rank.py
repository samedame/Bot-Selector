"""Cross-bot ranking and the pick.

Why this is a separate step from grade.py
-----------------------------------------
Grades are bot-relative, so a 100 from a weak bot and a 100 from a strong bot mean different things. Ranking
on grade alone would just reward whichever bot grades most generously. So the pick is made on EXPECTED EDGE:

    expected = (blend(documented edge, this bot's live results) + validated-level adjustment)
               x credibility x execution

    blend        = (k * documented + n * live_mean) / (k + n)     k = prior_weight, n = live closed trades
    credibility  = (evidence_pts / 35) x (price_pts / 20)          the bot's own fleet-audit scores
    execution    = execution score / 100                            stale or unsized alerts earn nothing

The grade GATES (tier D never picked) and is shown next to every pick; it is not what orders the board.
All inputs live in bots.json so every term can be audited and changed.
"""
from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Optional

from .grade import grade_setup
from .model import Decision, Edge, Scored, Setup
from .registry import Bot, Registry


def build_edge(bot: Bot, hist: list[dict], level_adj: float, exec_score: float, k0: float) -> Edge:
    exec_factor = max(0.0, min(1.0, exec_score / 100.0))
    if bot.claimed_edge is None:
        return Edge(None, None, 0, None, bot.credibility, level_adj, exec_factor, None)
    usable = [r["outcome"] for r in hist if r.get("unit", "roi") == "roi"]
    n = len(usable)
    live = mean(usable) if n else None
    blended = ((k0 * bot.claimed_edge + n * live) / (k0 + n)) if n else bot.claimed_edge
    expected = (blended + level_adj) * bot.credibility * exec_factor
    return Edge(bot.claimed_edge, live, n, blended, bot.credibility, level_adj, exec_factor, expected)


def score_setup(bot: Bot, s: Setup, hist: list[dict], now: datetime, cfg: dict, halted: set[str]) -> Scored:
    g, adj = grade_setup(bot, s, hist, now, cfg.get("disc_shrink_k", 15))
    edge = build_edge(bot, hist, adj, g.exec_score, cfg.get("prior_weight", 30))
    reasons: list[str] = []
    if bot.status != "live":
        reasons.append(f"{bot.status}" + (f" (replaced by {bot.superseded_by})" if bot.superseded_by else "")
                       + (f": {bot.status_reason}" if bot.status_reason else ""))
    if bot.kind != "trade":
        reasons.append("standing allocation, not a single trade")
    if bot.id in halted:
        reasons.append("manually halted in state/overrides.json")
    if s.closed:
        reasons.append("the bot has already exited this position")
    if now > s.valid_until:
        reasons.append("expired")
    reasons += g.disqualifiers
    if g.grade < cfg.get("min_grade_to_pick", 50) and not g.disqualifiers:
        reasons.append(f"grade {g.grade} is below the {cfg.get('min_grade_to_pick', 50)} needed to be picked")
    if edge.expected is None:
        reasons.append("no documented per-trade edge for this bot (set claimed_edge in bots.json)")
    elif edge.expected <= 0:
        reasons.append("expected edge is not positive once the bot's live results are blended in")
    return Scored(setup=s, grade=g, edge=edge, eligible=not reasons, reasons=reasons)


def decide(registry: Registry, collected: dict[str, tuple[list[Setup], list[str]]],
           histories: dict[str, list[dict]], now: datetime, previous_key: Optional[str] = None,
           halted: Optional[set[str]] = None) -> Decision:
    halted = halted or set()
    cfg = registry.defaults
    board: list[Scored] = []
    sidelined: list[dict] = []
    for bot_id, (setups, notes) in collected.items():
        bot = registry.get(bot_id)
        live = [x for x in setups if not x.closed and now <= x.valid_until]
        for s in live:
            board.append(score_setup(bot, s, histories.get(bot_id, []), now, cfg, halted))
        if not live:
            why = notes[0] if notes else "nothing actionable"
            if bot.status != "live" and bot.status_reason:
                why = f"{bot.status}: {bot.status_reason}"
            sidelined.append({"bot": bot_id, "label": bot.label, "status": bot.status, "why": why})
    eligible = sorted([b for b in board if b.eligible], key=lambda b: (b.edge.expected, b.grade.grade), reverse=True)
    board.sort(key=lambda b: (b.eligible, b.edge.expected if b.edge.expected is not None else -9, b.grade.grade),
               reverse=True)
    pick = eligible[0] if eligible else None
    kept = False
    margin = cfg.get("switch_margin", 0.25)
    if pick and previous_key:
        prev = next((b for b in eligible if b.setup.key == previous_key), None)
        if prev is not None and prev is not pick and pick.edge.expected < prev.edge.expected * (1 + margin):
            pick, kept = prev, True
    runner = next((b for b in eligible if b is not pick), None)
    return Decision(ts=now, pick=pick, runner_up=runner, board=board, sidelined=sidelined, kept_previous=kept)
