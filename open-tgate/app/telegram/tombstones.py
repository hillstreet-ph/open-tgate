"""Durable Telegram replay journals on the worker's existing state volume.

Deletion rows contain IDs only; the separate inbox queue contains normalized
live snapshots for personal and bot accounts. Neither queue
contains auth commands, credentials, or session material. SQLite commits precede
event acknowledgement; successful remote writes are acknowledged afterward.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path


class TombstoneJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        connection = sqlite3.connect(self.path, timeout=15)
        self.path.chmod(0o600)
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS pending_deletes ("
            "account_id TEXT NOT NULL, chat_id TEXT NOT NULL, message_id INTEGER NOT NULL, "
            "PRIMARY KEY (account_id, chat_id, message_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS pending_bot_inbox ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS pending_refreshes ("
            "account_id TEXT NOT NULL, chat_id TEXT NOT NULL, message_id INTEGER NOT NULL, "
            "generation INTEGER NOT NULL, pending INTEGER NOT NULL DEFAULT 1, "
            "PRIMARY KEY (account_id, chat_id, message_id))"
        )
        connection.commit()
        return connection

    async def enqueue(self, account_id: str, chat_id: str, message_ids: list[int]) -> None:
        if message_ids:
            await asyncio.to_thread(self._enqueue, account_id, chat_id, message_ids)

    def _enqueue(self, account_id: str, chat_id: str, message_ids: list[int]) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                "INSERT OR IGNORE INTO pending_deletes VALUES (?, ?, ?)",
                [(account_id, chat_id, int(message_id)) for message_id in message_ids],
            )

    async def pending(self, limit: int = 500) -> list[tuple[str, str, int]]:
        return await asyncio.to_thread(self._pending, limit)

    def _pending(self, limit: int) -> list[tuple[str, str, int]]:
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT account_id, chat_id, message_id FROM pending_deletes ORDER BY rowid LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()

    async def acknowledge(self, rows: list[tuple[str, str, int]]) -> None:
        await asyncio.to_thread(self._acknowledge, rows)

    def _acknowledge(self, rows: list[tuple[str, str, int]]) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                "DELETE FROM pending_deletes WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                rows,
            )


class InboxJournal(TombstoneJournal):
    """FIFO normalized inbox snapshots; never raw auth events or commands."""

    async def enqueue_payload(self, payload: dict) -> int:
        return await asyncio.to_thread(self._enqueue_payload, payload)

    def _enqueue_payload(self, payload: dict) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO pending_bot_inbox (payload) VALUES (?)",
                (json.dumps(payload),),
            )
            return cursor.lastrowid

    async def pending_payloads(self, limit: int = 50) -> list[tuple[int, dict]]:
        return await asyncio.to_thread(self._pending_payloads, limit)

    def _pending_payloads(self, limit: int) -> list[tuple[int, dict]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT sequence, payload FROM pending_bot_inbox ORDER BY sequence LIMIT ?",
                (max(1, min(limit, 50)),),
            ).fetchall()
            return [(sequence, json.loads(payload)) for sequence, payload in rows]

    async def acknowledge_payload(self, sequence: int) -> None:
        await asyncio.to_thread(self._acknowledge_payload, sequence)

    def _acknowledge_payload(self, sequence: int) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM pending_bot_inbox WHERE sequence = ?", (sequence,))

    async def checkpoint(self) -> int:
        return await asyncio.to_thread(self._checkpoint)

    def _checkpoint(self) -> int:
        with closing(self._connect()) as connection:
            return connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM pending_bot_inbox").fetchone()[0]

    async def pending_through(self, checkpoint: int) -> bool:
        return await asyncio.to_thread(self._pending_through, checkpoint)

    def _pending_through(self, checkpoint: int) -> bool:
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT 1 FROM pending_bot_inbox WHERE sequence <= ? LIMIT 1", (checkpoint,),
            ).fetchone() is not None

    async def enqueue_refresh(self, account_id: str, chat_id: str, message_id: int) -> int:
        return await asyncio.to_thread(self._enqueue_refresh, account_id, chat_id, message_id)

    def _enqueue_refresh(self, account_id: str, chat_id: str, message_id: int) -> int:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO pending_refreshes VALUES (?, ?, ?, 1, 1) "
                "ON CONFLICT(account_id, chat_id, message_id) DO UPDATE SET "
                "generation = generation + 1, pending = 1",
                (account_id, chat_id, message_id),
            )
            return connection.execute(
                "SELECT generation FROM pending_refreshes WHERE account_id = ? AND chat_id = ? AND message_id = ?",
                (account_id, chat_id, message_id),
            ).fetchone()[0]

    async def pending_refreshes(self, account_id: str, limit: int = 500) -> list[tuple[str, int, int]]:
        return await asyncio.to_thread(self._pending_refreshes, account_id, limit)

    def _pending_refreshes(self, account_id: str, limit: int) -> list[tuple[str, int, int]]:
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT chat_id, message_id, generation FROM pending_refreshes "
                "WHERE account_id = ? AND pending = 1 ORDER BY rowid LIMIT ?",
                (account_id, max(1, min(limit, 500))),
            ).fetchall()

    async def acknowledge_refresh(self, account_id: str, chat_id: str, message_id: int, generation: int) -> None:
        await asyncio.to_thread(self._acknowledge_refresh, account_id, chat_id, message_id, generation)

    def _acknowledge_refresh(self, account_id: str, chat_id: str, message_id: int, generation: int) -> None:
        with closing(self._connect()) as connection, connection:
            # Keep the generation even after acknowledgement: a stale worker
            # cannot acknowledge a later edit that reused a removed row's ID.
            connection.execute(
                "UPDATE pending_refreshes SET pending = 0 WHERE account_id = ? "
                "AND chat_id = ? AND message_id = ? AND generation = ?",
                (account_id, chat_id, message_id, generation),
            )


# Preserve the deployed queue/table and import compatibility across the rename.
BotInboxJournal = InboxJournal
