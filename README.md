# bot-selector

Watches every alert your bots post, grades each one 1-100 against **that bot's own standard**, and names the single setup it thinks is the best one to trade right now (or says **NO TRADE**). It never places orders and never writes to any bot repo.

## Read this first: what it can and can't claim

- Your own research (premium-sleeve README) found a bot-switcher **NO-GO**. This build does not overturn that. It is a ranking / second opinion with a built-in test of whether it adds value.
- The **1-100 grade is bot-relative**. A 90 from Chronos and a 90 from IBS do not mean the same thing, and neither is a win probability. The grade gates (min 50) and is displayed; it is **not** what ranks bots against each other.
- The cross-bot pick is ranked on **expected edge per trade**:

  ```
  blend    = (30 * documented_edge + n * live_mean) / (30 + n)      # live results pull the claim toward reality
  expected = (blend + setup_adj) * credibility * (execution / 100)
  credibility = (evidence_pts / 35) * (price_pts / 20)              # from the 2026-10-25 fleet audit
  ```
  The ordering is a heuristic and is very sensitive to the documented edges in `bots.json`.
- A pick only changes if the new one beats the old by 25% (no flip-flopping), and expired / exited / halted / disqualified alerts are never picked.
- It reads **committed** bot state once a minute, so a pick appears 1-2 minutes after the bot's alert. For 0DTE this is a second opinion, not a faster signal.
- **Replay 2026-10-15..10-27 (Chronos, Sniper, Omega only, no look-ahead):** 39 pick changes, n=5 comparable decisions with outcomes, pick vs alternatives on the board = -29 points, 90% CI includes zero -> **INSUFFICIENT DATA**. That is noise-level. It shows no demonstrated value yet; the forward scorecard decides.
- Chronos, Sniper and Omega live results are below their documented edges, and the selector reflects that through the blend.

## Grade (1-100, per bot)

`grade = setup (only where out-of-sample support exists) + execution (mechanical)`, disqualifiers cap at 49.

| Bot | Setup component | Weight |
|---|---|---|
| Chronos | percentile of module vs its own ledger, shrunk k=15 | 35 |
| Omega | pillar | 20 |
| dipcalls | static SPY 100 / QQQ 70 / IWM 40 | 10 |
| scanner | per its own ranking | 30 |
| Sniper, IBS, spy-bps, shadow | **none** (research found no usable discriminator) | 0 |

Execution factors: lateness, alert age, sizing (0 contracts = disqualified), expiry verified, price vs model, go/no-go gate, kill-switch. Missing input is neutral, never a free 100. Tiers: A >= 85, B >= 70, C >= 50, D < 50.

## Who can be picked

Needs: status `live`, kind `trade`, not halted, not closed, not expired, no disqualifiers, a documented edge, and expected edge > 0.

| Bot | Status | Pickable? |
|---|---|---|
| chronos, sniper, ibs | live | yes |
| premium-sleeve | live, credibility forced to 0.25 (unaudited) | **no, `claimed_edge` is null** |
| dipcalls | live | **no, `claimed_edge` is null** |
| omega | paper (no real-time feed) | no |
| condor, spybps | superseded by premium-sleeve | no |
| scanner | superseded, unreadable | no |
| frontier5, momentum, shadow | portfolio / paper | no (status only) |

**Decisions you need to make:** a `claimed_edge` for premium-sleeve and dipcalls (otherwise they are graded and shown but can never be the pick), whether Omega goes live, whether the superseded bots stay superseded. Edit `bots.json`; edge numbers should come from your own verdict docs, not from hope.

## Forward test (the part that matters)

- Every pick change is appended to `state/decisions.jsonl`.
- `eod` matches outcomes from each bot's own ledger and posts the scorecard: pick vs the alternatives that were on the board at the same time, bootstrap 90% CI.
- Returns **INSUFFICIENT DATA until n >= 30** comparable decisions. Verdicts: ADDS VALUE / NOT DEMONSTRATED / HURTS, decided by whether the CI excludes zero.
- Rules are frozen for the test. Do not tune `bots.json` edges after seeing outcomes.
- No look-ahead anywhere: live and replay use only alerts posted by `now`, and grading history is ledger rows dated before today.

## Setup

1. Create repo `samedame/bot-selector`, upload these files.
2. Secrets:
   - `GH_READ_TOKEN`: fine-grained PAT, **read-only Contents** on the bot repos.
   - `DISCORD_WEBHOOK_URL`: webhook for a new channel (e.g. `#best-setup`).
3. Scheduler: GitHub native cron runs hours late on your account, so add this job to the `open-scheduler` Worker:
   ```json
   {"id": "selector-session", "repo": "samedame/bot-selector", "workflow": "selector-session.yml",
    "ref": "main", "at_et": "09:20", "grace_min": 30, "watchdog_min": 4,
    "fallback_message": "bot-selector did not start - no best-setup picks today"}
   ```
   The workflow also has DST-safe backup crons (13:25 / 14:25 UTC) with an ET guard.
4. **Prove the wiring:** Actions -> `selector-selftest` -> Run workflow (works any time of day). It posts one line per bot to Discord: `ok`, `skip` (superseded bots) or `FAIL` with the reason.
5. `selector-eod.yml` runs the scorecard at 16:40 ET.
6. Manual kill: put a bot id in `state/overrides.json`, e.g. `{"halt": ["chronos"]}`. It wins over everything.

## Commands

```
python -m bot_selector once                     # rank right now, print only
python -m bot_selector live                     # market-hours loop (what the workflow runs)
python -m bot_selector digest                   # fleet status + board
python -m bot_selector selftest                 # read every bot once, any time of day; posts ok/FAIL per bot to Discord
python -m bot_selector eod                      # resolve outcomes, post scorecard
python -m bot_selector scorecard
python -m bot_selector replay --local ROOT --start 2026-10-15 --end 2026-10-27 --out state/replay.json
python -m pytest tests -q                       # 70 tests
```

## Known gaps

- **THE SCHEDULER JOB MUST EXIST (setup step 3).** As of 2026-11-04 no run has ever been started by the `open-scheduler` Worker: every `selector-session` run was GitHub's own backup cron, starting between 15:00 and 17:30 ET, so the selector has never watched a morning session. If it starts after 09:45 ET it now posts `SELECTOR STARTED LATE` to Discord. That message means the Worker job is missing or not firing.
- **Failures are loud now.** The loop logs every poll to the Actions log, posts `SELECTOR READ PROBLEM` to Discord when a live/paper bot can't be read (once per distinct problem), survives a Discord outage (it retries the post next poll instead of crashing), and commits state with a bot identity. Before 2026-11-04 the end-of-session commit failed with "Author identity unknown", so `state/board.json` and `state/decisions.jsonl` were never saved and the EOD scorecard had nothing to score.

- **The GitHub reader was not exercised against the live API.** The build sandbox got `403` from api.github.com, so `GitHubSource` is covered by unit tests only. First run: check `bot-errors` / the board file's `errors` for read failures. A missing required file is raised as an error on purpose; it never looks like a quiet day.
- Sizing / account capital is not scored (the selector can't see per-alert cost), only 0-contract disqualification.
- Scanner is unreadable; Omega, FRONTIER-5 and momentum are not pickable.
- GitHub Actions session limit is 6h; the loop hands off, and stops at 16:05 ET.
- Branch is per bot (`branch` in `bots.json`, default `main`; dipcalls repo uses `master`).

## Session turned off (2026-11-13)

`selector-session` is OFF by default. The live loop used about 42% of the account's private Actions minutes (1,805 of ~4,260, 1-12 Nov) and its own replay found no value (n=5, pick 29 points worse than the alternatives, CI includes zero). The six native cron lines are commented out and the job needs the repository variable `SELECTOR_SESSION_ENABLED=true`, which also blocks the Cloudflare Worker's dispatch. `selector-eod` (the scorecard) is unchanged. Remove the `selector-session` job from the Worker table when convenient.
