"""APEX-CHRONOS, APEX-SNIPER (0DTE options) and APEX-OMEGA (NQ futures).

All three commit a per-day 'sent' ledger the moment an alert posts, plus a trades.csv of closed trades.
That makes both the live board and the bot's own history readable straight from the repo.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from ..model import Setup
from ..registry import Bot
from ..timeutil import minute_to_dt, parse_iso
from .common import fnum, load_csv, load_json, need


def collect(bot: Bot, src, now, cfg) -> tuple[list[Setup], list[str]]:
    d = now.date().isoformat()
    path = f"{bot.params.get('state_dir', 'state')}/sent-{d}.json"
    raw = src.read(bot.repo, path)
    if raw is None:
        return [], [f"no {path} yet (bot has not alerted today)"]
    data = load_json(raw, {})
    if bot.adapter == "omega":
        return _omega(bot, data, now)
    return _apex(bot, data, now)


def _apex(bot: Bot, data: dict, now) -> tuple[list[Setup], list[str]]:
    entries, exits = [], []
    for key, rec in data.items():
        parts = key.split("|")
        if len(parts) < 5:
            continue
        kind = parts[1]
        item = {"key": key, "date": parts[0], "ticker": parts[2], "module": parts[3],
                "minute": int(parts[4]), "mode": parts[5] if len(parts) > 5 else None, "rec": rec}
        if kind not in ("ENTRY", "EXIT"):
            continue
        t_raw = rec.get("t") if isinstance(rec, dict) else None
        if t_raw and parse_iso(t_raw) > now:
            continue                       # had not happened yet at `now`
        (entries if kind == "ENTRY" else exits).append(item)
    setups: list[Setup] = []
    notes: list[str] = []
    skipped_late = 0
    for e in entries:
        rec = e["rec"]
        if not rec.get("posted", False):
            skipped_late += 1          # the bot itself declined to post it (too late); never actionable
            continue
        ts = parse_iso(rec["t"])
        closed = any(
            x["ticker"] == e["ticker"] and x["module"] == e["module"] and x["mode"] == e["mode"]
            and parse_iso(x["rec"]["t"]) >= ts for x in exits
        )
        mode = f" [{e['mode']}]" if e["mode"] else ""
        setups.append(Setup(
            bot_id=bot.id, key=e["key"], ts=ts, valid_until=ts + timedelta(seconds=bot.validity_s),
            label=f"{e['ticker']} {e['module']}{mode}", symbol=e["ticker"], level=e["module"],
            features={"late_s": fnum(rec.get("late_s")), "minute": e["minute"], "mode": e["mode"],
                      "ticker": e["ticker"], "expiry_verified": None},
            contracts=None, closed=closed,
        ))
    if skipped_late:
        notes.append(f"{skipped_late} alert(s) the bot itself withheld as too late")
    return setups, notes


def _omega(bot: Bot, data, now) -> tuple[list[Setup], list[str]]:
    setups: list[Setup] = []
    def when(item: str):
        return minute_to_dt(now.date(), int(item.split("|")[3]))
    exits = [s for s in data if isinstance(s, str) and "|EXIT|" in s and when(s) <= now]
    for s in data:
        if not isinstance(s, str) or "|ENTRY|" not in s:
            continue
        parts = s.split("|")
        pillar, minute = parts[2], int(parts[3])
        ts = minute_to_dt(now.date(), minute)
        if ts > now:
            continue
        closed = any(x.split("|")[2] == pillar for x in exits)
        setups.append(Setup(
            bot_id=bot.id, key=s, ts=ts, valid_until=ts + timedelta(seconds=bot.validity_s),
            label=f"NQ {pillar}", symbol="NQ", level=pillar,
            features={"minute": minute, "late_s": 0.0, "data_age_s": 600.0},
            closed=closed,
        ))
    return setups, []


def history(bot: Bot, src) -> list[dict]:
    """The bot's own closed trades: [{'level', 'outcome', 'date', 'ticker', 'minute', 'mode'}]."""
    raw = need(src, bot.repo, bot.params.get("ledger", "ledger/trades.csv"))
    rows = load_csv(raw)
    out: list[dict] = []
    if bot.adapter == "omega":
        for r in rows:
            o = fnum(r.get("net_r"))
            if o is not None:
                out.append({"level": r.get("pillar"), "outcome": o, "date": r.get("date"),
                            "ticker": "NQ", "minute": None, "mode": None, "unit": "R"})
        return out
    field = bot.params.get("module_field") or "module"
    mode_field = bot.params.get("mode_field")
    for r in rows:
        o = fnum(r.get("roi"))
        if o is None:
            continue
        m = fnum(r.get("m_entry"))
        out.append({"level": r.get(field), "outcome": o, "date": r.get("date"), "ticker": r.get("ticker"),
                    "minute": int(m) if m is not None else None,
                    "mode": r.get(mode_field) if mode_field else None, "unit": "roi"})
    return out
