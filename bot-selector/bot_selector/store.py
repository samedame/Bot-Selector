"""Append-only decision log + the one-line 'current board' pointer. Plain files so BotDeck can read them."""
from __future__ import annotations

import json
import os
from typing import Optional


class Store:
    def __init__(self, directory: str):
        self.dir = directory
        os.makedirs(directory, exist_ok=True)
        self.board_path = os.path.join(directory, "board.json")
        self.log_path = os.path.join(directory, "decisions.jsonl")

    def load_board(self) -> dict:
        if not os.path.exists(self.board_path):
            return {}
        with open(self.board_path) as fh:
            text = fh.read().strip()
        return json.loads(text) if text else {}

    def save_board(self, board: dict) -> None:
        tmp = self.board_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(board, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.board_path)

    def append_decision(self, rec: dict) -> None:
        with open(self.log_path, "a") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")

    def read_decisions(self) -> list[dict]:
        if not os.path.exists(self.log_path):
            return []
        out = []
        with open(self.log_path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def rewrite_decisions(self, rows: list[dict]) -> None:
        tmp = self.log_path + ".tmp"
        with open(tmp, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        os.replace(tmp, self.log_path)
