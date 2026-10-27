"""Where bot files come from.

LocalSource reads checked-out repos (tests, replay, local runs).
GitHubSource reads the committed files through the contents API with ETag caching, so a poll that finds
nothing new costs no rate limit. It needs a READ-ONLY token for the private repos (secret GH_READ_TOKEN).
Nothing here ever writes to a bot repo.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Optional


class SourceError(RuntimeError):
    pass


class Source:
    def read(self, repo: str, path: str) -> Optional[str]:  # pragma: no cover - interface
        raise NotImplementedError


class LocalSource(Source):
    """repo 'samedame/Apex_Chronos' -> directory under `root` (case-insensitive match on the repo name)."""

    def __init__(self, root: str, mapping: Optional[dict[str, str]] = None):
        self.root = root
        self.mapping = {k.lower(): v for k, v in (mapping or {}).items()}

    def _dir(self, repo: str) -> Optional[str]:
        if repo.lower() in self.mapping:
            return self.mapping[repo.lower()]
        name = repo.split("/")[-1].lower()
        for cand in (name, os.path.join("samedame", name)):
            p = os.path.join(self.root, cand)
            if os.path.isdir(p):
                return p
        if os.path.isdir(self.root):
            for entry in os.listdir(self.root):
                if entry.lower() == name and os.path.isdir(os.path.join(self.root, entry)):
                    return os.path.join(self.root, entry)
        return None

    def read(self, repo: str, path: str) -> Optional[str]:
        d = self._dir(repo)
        if d is None:
            return None
        p = os.path.join(d, path)
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8") as fh:
            return fh.read()


class GitHubSource(Source):
    def __init__(self, token: Optional[str] = None, branch: str = "main", timeout: float = 20.0,
                 cache_path: Optional[str] = None):
        self.token = token or os.environ.get("GH_READ_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.branch = branch
        self.branches: dict[str, str] = {}      # repo -> default branch, filled from the registry
        self.timeout = timeout
        self.cache_path = cache_path
        self._etags: dict[str, str] = {}
        self._bodies: dict[str, str] = {}
        self.calls = 0
        self.not_modified = 0
        if cache_path and os.path.exists(cache_path):
            try:
                blob = json.load(open(cache_path))
                self._etags, self._bodies = blob.get("etags", {}), blob.get("bodies", {})
            except (OSError, ValueError):
                pass

    def save_cache(self) -> None:
        if self.cache_path:
            with open(self.cache_path, "w") as fh:
                json.dump({"etags": self._etags, "bodies": self._bodies}, fh)

    def read(self, repo: str, path: str) -> Optional[str]:
        url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={self.branches.get(repo, self.branch)}"
        headers = {"Accept": "application/vnd.github.raw", "User-Agent": "bot-selector",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if url in self._etags:
            headers["If-None-Match"] = self._etags[url]
        req = urllib.request.Request(url, headers=headers)
        last: Optional[Exception] = None
        for attempt in range(3):
            try:
                self.calls += 1
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                    etag = resp.headers.get("ETag")
                    if etag:
                        self._etags[url] = etag
                        self._bodies[url] = body
                    return body
            except urllib.error.HTTPError as e:
                if e.code == 304 and url in self._bodies:
                    self.not_modified += 1
                    return self._bodies[url]
                if e.code == 404:
                    return None
                if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
                    raise SourceError(f"GitHub rate limit hit reading {repo}/{path}") from e
                last = e
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
            time.sleep(1.5 * (attempt + 1))
        raise SourceError(f"could not read {repo}/{path}: {last}")
