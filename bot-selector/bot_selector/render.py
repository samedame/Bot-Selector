"""Plain-text rendering for Discord and the console. Every pick shows its arithmetic."""
from __future__ import annotations

from .model import Decision, Scored

LIMIT = 1900


def _pct(x, d=2):
    return "n/a" if x is None else f"{x * 100:+.{d}f}%"


def edge_line(b: Scored) -> str:
    e = b.edge
    if e.expected is None:
        return "Expected edge: n/a (no documented per-trade edge for this bot)"
    live = "no live trades yet" if e.live_n == 0 else f"live {_pct(e.live_mean, 1)} over {e.live_n} trades"
    adj = f" {e.level_adj * 100:+.1f}pt for this setup type," if abs(e.level_adj) >= 0.0005 else ""
    return (f"Expected edge {_pct(e.expected)} per $ committed = (documented {_pct(e.claimed, 1)} / {live}"
            f" -> blended {_pct(e.blended, 1)},{adj}) x credibility {e.credibility:.2f} x execution {e.exec_factor:.2f}")


def grade_line(b: Scored) -> str:
    g = b.grade
    setup = "n/a" if g.setup_score is None else f"{g.setup_score:.0f}"
    return f"Grade {g.grade}/100 for this bot (setup {setup} - execution {g.exec_score:.0f}) - tier {g.tier}"


def pick_block(b: Scored, held: bool = False) -> str:
    bot = b.setup.bot_id
    lines = [f"{'KEEPING' if held else 'TRADE THIS'}: {bot.upper()} - {b.setup.label}",
             grade_line(b), edge_line(b)]
    if b.grade.drags:
        lines.append("Biggest drag: " + "; ".join(b.grade.drags[:2]))
    lines.append("Valid until " + b.setup.valid_until.strftime("%H:%M ET"))
    return "\n".join(lines)


def render(d: Decision, registry=None) -> str:
    out: list[str] = []
    if d.pick is None:
        out.append("NO TRADE - nothing on the board clears the bar right now.")
    else:
        out.append(pick_block(d.pick, d.kept_previous))
        if d.kept_previous:
            out.append("(a stronger alert exists but does not beat this one by the switch margin)")
        if d.runner_up is not None:
            r = d.runner_up
            out.append(f"Runner-up: {r.setup.bot_id.upper()} {r.setup.label} - grade {r.grade.grade}, "
                       f"edge {_pct(r.edge.expected)}")
    rest = [b for b in d.board if b is not d.pick and b is not d.runner_up]
    if rest:
        out.append("\nAlso on the board (not pickable):")
        for b in rest[:8]:
            why = b.reasons[0] if b.reasons else "eligible"
            out.append(f"- {b.setup.bot_id.upper()} {b.setup.label} - grade {b.grade.grade} ({b.grade.tier}): {why[:110]}")
    if d.sidelined:
        out.append("\nNothing to act on from: " + ", ".join(f"{s['bot']} ({s['status']})" for s in d.sidelined))
    text = "\n".join(out)
    return text if len(text) <= LIMIT else text[: LIMIT - 20] + "\n...(truncated)"


def digest(d: Decision, registry) -> str:
    """The morning/status view: what each bot is, what it can currently contribute, and why not if nothing."""
    lines = [f"BOT SELECTOR - {d.ts:%a %b %d %H:%M ET}", ""]
    lines.append(render(d, registry))
    lines.append("\nFleet:")
    for s in d.sidelined:
        lines.append(f"- {s['label']}: {s['status']} - {s['why'][:120]}")
    text = "\n".join(lines)
    return text if len(text) <= LIMIT else text[: LIMIT - 20] + "\n...(truncated)"
