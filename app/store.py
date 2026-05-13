from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Optional

from .models import GameState


class GameStore:
    def __init__(self, db_path: str | Path = "jeopardy.db") -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_code TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def save(self, state: GameState) -> GameState:
        payload = state.model_dump_json()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_code, state_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_code) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (state.session_code, payload),
            )
        return state

    def get(self, session_code: str) -> GameState:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM sessions WHERE session_code = ?",
                (session_code.upper(),),
            ).fetchone()
        if row is None:
            raise KeyError(session_code)
        return GameState.model_validate(json.loads(row[0]))

    def latest(self) -> Optional[GameState]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state_json FROM sessions ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return GameState.model_validate(json.loads(row[0]))

    def update(self, session_code: str, mutator: Callable[[GameState], GameState]) -> GameState:
        with self._lock:
            state = self.get(session_code)
            state = mutator(state)
            return self.save(state)
