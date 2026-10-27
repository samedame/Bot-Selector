"""The per-bot 1-100 grade: how good is THIS alert, for THIS bot, measured against that bot's own history.

Two components, always reported separately:

  setup      0-100  where the alert's validated discriminator ranks among the bot's own closed trades.
                    Only discriminators the project's out-of-sample work supports are used
                    (alert-confidence-design-2026-10-26.md). A bot with none is graded on execution alone.
  execution  0-100  mechanical: is the alert fresh, sized, priced against a real quote, on a real expiry.

A 100 means 'as good as this bot's alerts get'. It is NOT comparable across bots and NOT a win probability;
rank.py is what turns grades into a cross-bot comparison.
"""
from __future__ import annotations

from statistics import mean
from typing import Optional

from .model import Grade, Setup
from .registry import Bot

TIERS = [(85, "A"), (70, "B"), (50, "C"), (0, "D")]
DISQ_CAP = 49


def tier_for(score: float) -> str:
    for floor, name in TIERS:
        if score >= floor:
            return name
    return "D"


# --------------------------------------------------------------------- execution factors
# Each returns (score 0..1 or None, drag text or None, disqualifier text or None).

def _lateness(bot: Bot, s: Setup, now):
    late = s.features.get("late_s")
    if late is None:
        return 0.5, "lateness unknown", None
    if s.features.get("age_exempt"):
        age = 0.0
    else:
        age = max(0.0, (now - s.ts).total_seconds())
    total = float(late) + age
    grace, window = float(bot.grace_s), float(max(bot.validity_s, bot.grace_s + 1))
    if total <= grace:
        return 1.0, None, None
    if total >= window:
        return 0.0, None, f"alert is {total / 60:.0f} min old, past its {window / 60:.0f} min window"
    score = 1.0 - 0.8 * (total - grace) / (window - grace)
    return score, f"{total / 60:.1f} min since the alert fired", None


def _age(bot: Bot, s: Setup, now):
    a = s.features.get("data_age_s")
    if a is None:
        return 0.5, "data age unknown", None
    if a <= 150:
        return 1.0, None, None
    return 0.2, f"price feed is {a / 60:.0f} min delayed", None


def _sizing(bot: Bot, s: Setup, now):
    if s.contracts is None:
        return None, None, None             # configuration gap, identical on every alert: noted, not scored
    if s.contracts == 0:
        return 0.0, None, "sizing resolves to 0 contracts at this account size"
    return (0.85 if s.contracts == 1 else 1.0), None, None


def _expiry(bot: Bot, s: Setup, now):
    v = s.features.get("expiry_verified")
    if v is True:
        return 1.0, None, None
    if v is False:
        return 0.0, None, "the instrument does not exist as specified"
    return 0.5, "same-day expiry NOT verified against a real chain - may not be tradeable as written", None


def _pricing(bot: Bot, s: Setup, now):
    p = s.features.get("pct_of_model")
    if p is None:
        return 0.4, "no real quote checked against the model price", None
    score = max(0.0, min(1.0, (p - 0.2) / 0.6))
    return score, (f"real credit is only {p:.0%} of the model's" if p < 0.5 else None), None


def _gate(bot: Bot, s: Setup, now):
    m = s.features.get("gate_margin")
    if m is None:
        return 0.5, "regime-gate margin unknown", None
    if m < 0.05:
        return 0.4, f"regime gate passed by a thin {m:.1%}", None
    return (0.8 if m < 0.15 else 1.0), None, None


def _killswitch(bot: Bot, s: Setup, now):
    k = s.features.get("killswitch")
    if k is None:
        return None, None, None
    if k:
        return 0.0, None, "the bot's kill switch is tripped"
    return 1.0, None, None


def _gonogo(bot: Bot, s: Setup, now):
    d = s.features.get("gonogo")
    if d is None:
        return None, None, None
    if str(d).upper().startswith("NO"):
        return 0.0, None, "the bot's own go/no-go said NO-GO for today"
    return 1.0, None, None


FACTORS = {"gonogo": _gonogo, "lateness": _lateness, "age": _age, "sizing": _sizing, "expiry": _expiry,
           "pricing": _pricing, "gate": _gate, "killswitch": _killswitch}


def execution(bot: Bot, s: Setup, now) -> tuple[float, list[str], list[str], list[str]]:
    scores, drags, disq, notes = [], [], [], []
    for name in bot.exec_factors:
        sc, drag, dq = FACTORS[name](bot, s, now)
        if sc is None:
            if name == "sizing":
                notes.append("account size not visible to the selector, so sizing is not scored")
            continue
        scores.append(sc)
        if drag:
            drags.append(drag)
        if dq:
            disq.append(dq)
    if not bot.exec_factors:
        return 100.0, drags, disq, notes
    if not scores:
        return 50.0, drags, disq, notes
    return 100.0 * mean(scores), drags, disq, notes


# --------------------------------------------------------------------- setup component
def setup_component(bot: Bot, s: Setup, hist: list[dict], k: float) -> tuple[Optional[float], float, str]:
    """Returns (setup score 0-100 or None, level adjustment in the bot's own outcome units, note)."""
    disc = bot.discriminator or {}
    if not disc.get("validated") or bot.setup_weight <= 0:
        return None, 0.0, ""
    static = disc.get("static_scores")
    if static is not None:
        return (float(static[s.level]) if s.level in static else None), 0.0, "documented ranking, no ledger to adjust edge"
    rows = [r for r in hist if r.get("level") is not None]
    if len(rows) < 8:
        return None, 0.0, f"only {len(rows)} closed trades in this bot's own history, too few to rank a setup"
    overall = mean(r["outcome"] for r in rows)
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["level"], []).append(r["outcome"])
    shrunk = {lv: (len(v) * mean(v) + k * overall) / (len(v) + k) for lv, v in by.items()}
    mine = shrunk.get(s.level, overall)
    values = [shrunk[r["level"]] for r in rows]
    score = 100.0 * sum(1 for v in values if v <= mine) / len(values)
    note = (f"{s.level}: {len(by.get(s.level, []))} trades, shrunk mean {mine:+.3f} vs bot average {overall:+.3f}"
            if s.level in by else f"{s.level}: never seen in this bot's history, scored as average")
    return max(1.0, score), mine - overall, note


# --------------------------------------------------------------------- the grade
def grade_setup(bot: Bot, s: Setup, hist: list[dict], now, k: float = 15.0) -> tuple[Grade, float]:
    ex, drags, disq, notes = execution(bot, s, now)
    setup_score, adj, snote = setup_component(bot, s, hist, k)
    if snote:
        notes.append(snote)
    w = bot.setup_weight if setup_score is not None else 0.0
    blended = ex if setup_score is None else (w * setup_score + (100.0 - w) * ex) / 100.0
    if disq:
        blended = min(blended, DISQ_CAP)
    g = int(max(1, min(100, round(blended))))
    return Grade(setup_score=None if setup_score is None else round(setup_score, 1),
                 exec_score=round(ex, 1), grade=g, tier=tier_for(g),
                 disqualifiers=disq, drags=drags, notes=notes), adj
