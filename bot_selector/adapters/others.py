"""Condor, IBS, IBS Dip Calls, SPY bull put spread, Premium sleeve, and the status-only bots."""
from __future__ import annotations

import re
from datetime import date, time, timedelta

from ..model import Setup
from ..registry import Bot
from ..timeutil import CLOSE, at_et, next_weekday, parse_iso
from .common import fnum, load_csv, load_json, need

_NO_HISTORY: list[dict] = []


# ---------------------------------------------------------------- condor
def collect_condor(bot: Bot, src, now, cfg):
    st = load_json(need(src, bot.repo, bot.params["state"]), {})
    pos = (st or {}).get("open_position")
    today = now.date().isoformat()
    ks = (st or {}).get("kill_switch") or {}
    if not pos or pos.get("date") != today:
        return [], ["no condor entered today"]
    ts = parse_iso(pos["entry_time"])
    checks = [c for c in (st.get("credit_checks") or []) if c.get("date") == today]
    pct = fnum(checks[-1].get("pct_of_model")) if checks else None
    if pct is not None:
        pct = pct / 100.0 if pct > 3 else pct          # the bot stores percent (6.1 = 6.1%); accept a ratio too
    quote = pos.get("chain_quote") or {}
    plan = (load_json(src.read(bot.repo, bot.params.get("pipeline", "")), {}) or {}).get("plans", {}).get(today, {})
    gate = ((plan.get("gonogo") or {}).get("gate")) or {}
    rv, med = fnum(gate.get("rv20_prior_day")), fnum(gate.get("rv20_median_prior_day"))
    margin = (med - rv) / med if (rv is not None and med) else None
    decision = (plan.get("gonogo") or {}).get("decision") or (plan.get("staged") or {}).get("decision")
    return [Setup(
        bot_id=bot.id, key=f"condor|{today}", ts=ts, valid_until=ts + timedelta(seconds=bot.validity_s),
        label=f"{pos.get('ticker', 'SPY')} 0DTE iron condor {pos.get('short_put_strike')}/{pos.get('short_call_strike')}",
        symbol=pos.get("ticker", "SPY"), level=None,
        features={"late_s": 0.0,
                  "pct_of_model": pct, "real_quote": bool(quote.get("fresh")) if quote else None,
                  "gate_margin": margin, "killswitch": bool(ks.get("tripped")), "gonogo": decision,
                  "expiry_verified": True if pos.get("listed_strikes") else None},
        contracts=None,
    )], []


# ---------------------------------------------------------------- IBS callout
_BUY = re.compile(r"^\s*([A-Z]{2,5}):\s*close\s+([\d.]+)\s+IBS\s+([\d.]+)\s+\[BUY signal\]", re.M)


def collect_ibs(bot: Bot, src, now, cfg):
    st = load_json(need(src, bot.repo, bot.params["state"]), {})
    ev = (st or {}).get("last_evening") or {}
    bar, text = ev.get("bar"), ev.get("text", "")
    if not bar:
        return [], ["no evening signal on record"]
    bar_d = date.fromisoformat(bar)
    ts = at_et(bar_d, time(17, 15))
    nxt = next_weekday(bar_d)
    until = at_et(nxt, time(10, 0))
    out = []
    for sym, close, ibs in _BUY.findall(text):
        out.append(Setup(
            bot_id=bot.id, key=f"ibs|{bar}|{sym}", ts=ts, valid_until=until,
            label=f"{sym} IBS {float(ibs):.2f} buy at the {nxt:%a %b %d} open", symbol=sym, level=None,
            features={"late_s": 0.0, "age_exempt": True, "ibs": float(ibs), "close": float(close)}, contracts=None,
        ))
    return out, ([] if out else [f"no BUY in the {bar} evening list"])


# ---------------------------------------------------------------- IBS Dip Calls
def collect_dipcalls(bot: Bot, src, now, cfg):
    rows = load_csv(need(src, bot.repo, bot.params["signals"]))
    if not rows:
        return [], ["signals log empty"]
    last_run = rows[-1]["run_time"]
    batch = [r for r in rows if r["run_time"] == last_run]
    ts = parse_iso(last_run)
    if ts.date() != now.date():
        return [], [f"last run was {ts.date()}, none today"]
    thr = float(bot.params.get("ibs_entry", 0.10))
    out = []
    for r in batch:
        ibs = fnum(r.get("ibs"))
        if ibs is None or ibs >= thr:
            continue
        out.append(Setup(
            bot_id=bot.id, key=f"dipcalls|{last_run}|{r['etf']}", ts=ts,
            valid_until=at_et(ts.date(), CLOSE), label=f"{r['etf']} dip call, IBS {ibs:.2f}",
            symbol=r["etf"], level=r["etf"],
            features={"late_s": 0.0, "ibs": ibs, "expiry_verified": True}, contracts=None,
        ))
    return out, ([] if out else [f"IBS above {thr:.2f} on every ETF at the last run"])


# ---------------------------------------------------------------- SPY bull put spread
def collect_spybps(bot: Bot, src, now, cfg):
    st = ((load_json(need(src, bot.repo, bot.params["state"]), {}) or {}).get("state") or {})
    pos = st.get("position")
    today = now.date().isoformat()
    if not pos or pos.get("entry_date") != today:
        return [], ["no spread opened today"]
    ts = at_et(now.date(), time(9, 45))
    return [Setup(
        bot_id=bot.id, key=f"spybps|{today}", ts=ts, valid_until=at_et(now.date(), CLOSE),
        label=f"SPY bull put spread {pos['short_k']}/{pos['long_k']} exp {pos['expiry_date']}", symbol="SPY",
        level=None, features={"late_s": 0.0, "age_exempt": True, "max_loss": pos.get("maxloss")}, contracts=int(pos.get("n", 0)),
    )], []


# ---------------------------------------------------------------- Premium sleeve
def collect_sleeve(bot: Bot, src, now, cfg):
    positions = load_json(need(src, bot.repo, bot.params["positions"]), []) or []
    eq = load_json(need(src, bot.repo, bot.params["equity"]), {}) or {}
    today = now.date().isoformat()
    ts = at_et(now.date(), time(15, 40))
    out = []
    for p in positions:
        if p.get("opened") != today or p.get("status") != "open":
            continue
        out.append(Setup(
            bot_id=bot.id, key=f"sleeve|{p['id']}", ts=ts, valid_until=at_et(now.date(), CLOSE),
            label=f"SPY {p['short_strike']}/{p['long_strike']}P exp {p['expiry']} credit {p['credit']}",
            symbol="SPY", level=None,
            features={"late_s": 0.0, "killswitch": bool(eq.get("halted")), "max_loss": p.get("max_loss_per_contract")},
            contracts=p.get("contracts"),
        ))
    return out, ([] if out else ["no sleeve entry today"])


# ---------------------------------------------------------------- status-only bots
def collect_portfolio(bot: Bot, src, now, cfg):
    st = load_json(need(src, bot.repo, bot.params["state"]), {}) or {}
    stamp = st.get("last_post_utc") or st.get("generated_at") or st.get("last_rebalanced_month") or "no timestamp"
    return [], [f"standing allocation, last update {stamp}; never competes for a single-trade pick"]


def collect_unreadable(bot: Bot, src, now, cfg):
    return [], [bot.status_reason or "alerts are not committed anywhere this selector can read"]


def history_none(bot: Bot, src):
    return _NO_HISTORY
