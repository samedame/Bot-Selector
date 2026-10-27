"""Adapter dispatch: bot.adapter name -> (collect, history)."""
from __future__ import annotations

from . import apex, others

COLLECT = {
    "apex": apex.collect, "omega": apex.collect,
    "condor": others.collect_condor, "ibs": others.collect_ibs, "dipcalls": others.collect_dipcalls,
    "spybps": others.collect_spybps, "sleeve": others.collect_sleeve,
    "portfolio": others.collect_portfolio, "unreadable": others.collect_unreadable,
}
HISTORY = {"apex": apex.history, "omega": apex.history}


def history_for(bot, src):
    fn = HISTORY.get(bot.adapter)
    return fn(bot, src) if fn else []
