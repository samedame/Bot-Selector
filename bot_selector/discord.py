"""Discord webhook delivery with retry. A failed post is raised, never swallowed (the one gap the fleet audit
found in the condor bot was a single unretried post)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional

UA = {"User-Agent": "bot-selector (github actions)", "Content-Type": "application/json"}


class DiscordError(RuntimeError):
    pass


def post(text: str, webhook: Optional[str] = None, tries: int = 4) -> bool:
    webhook = webhook or os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("[discord] DISCORD_WEBHOOK_URL not set; message below was not sent\n" + text)
        return False
    body = json.dumps({"content": text[:1990]}).encode()
    last: Optional[Exception] = None
    for i in range(tries):
        try:
            req = urllib.request.Request(webhook, data=body, headers=UA, method="POST")
            with urllib.request.urlopen(req, timeout=15):
                return True
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                try:
                    wait = float(json.loads(e.read().decode()).get("retry_after", 2))
                except Exception:
                    wait = 2.0
                time.sleep(min(wait, 10))
                continue
            if 400 <= e.code < 500:
                break
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(2 ** i)
    raise DiscordError(f"discord post failed: {last}")
