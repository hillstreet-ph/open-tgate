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
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS mirror_sessions ("
                           "generation TEXT PRIMARY KEY, account_id TEXT NOT NULL, "
                           "identity TEXT, state TEXT NOT NULL DEFAULT 'waiting')")
        connection.execute("CREATE TABLE IF NOT EXISTS mirror_slots ("
                           "account_id TEXT PRIMARY KEY, current_generation TEXT NOT NULL, identity TEXT)")
        # Unscoped deployed rows cannot safely inherit a newly observed identity.
        # Keep them quarantined on disk rather than attributing them by slot ID.
        for table in ("pending_deletes", "pending_refreshes"):
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if columns and "session_generation" not in columns:
                connection.execute(f"ALTER TABLE {table} RENAME TO unfenced_{table}")
        connection.execute("CREATE TABLE IF NOT EXISTS pending_deletes ("
                           "account_id TEXT NOT NULL, chat_id TEXT NOT NULL, message_id INTEGER NOT NULL, "
                           "session_generation TEXT NOT NULL, "
                           "PRIMARY KEY (account_id, chat_id, message_id, session_generation))")
        connection.execute("CREATE TABLE IF NOT EXISTS pending_refreshes ("
                           "account_id TEXT NOT NULL, chat_id TEXT NOT NULL, message_id INTEGER NOT NULL, "
                           "generation INTEGER NOT NULL, pending INTEGER NOT NULL DEFAULT 1, "
                           "session_generation TEXT NOT NULL, "
                           "PRIMARY KEY (account_id, chat_id, message_id, session_generation))")
        connection.execute("CREATE TABLE IF NOT EXISTS pending_bot_inbox ("
                           "sequence INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL, "
                           "account_id TEXT, session_generation TEXT)")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(pending_bot_inbox)")}
        for column in ("account_id", "session_generation"):
            if column not in columns:
                connection.execute(f"ALTER TABLE pending_bot_inbox ADD COLUMN {column} TEXT")
        connection.commit()
        return connection

    @staticmethod
    def _eligible(alias: str) -> str:
        return (
            f" JOIN mirror_sessions source ON source.generation = {alias}.session_generation "
            f"AND source.account_id = {alias}.account_id "
            f"JOIN mirror_slots slot ON slot.account_id = {alias}.account_id "
            "JOIN mirror_sessions active ON active.generation = slot.current_generation "
            "AND active.account_id = slot.account_id "
            "AND active.state = 'approved' AND source.state = 'approved' "
            "AND active.identity = source.identity AND slot.identity = source.identity "
        )

    async def open_session(self, account_id: str, generation: str) -> None:
        await asyncio.to_thread(self._open_session, account_id, generation)

    def _open_session(self, account_id: str, generation: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT OR IGNORE INTO mirror_sessions(generation, account_id) VALUES (?, ?)",
                               (generation, account_id))
            connection.execute("INSERT INTO mirror_slots(account_id, current_generation) VALUES (?, ?) "
                               "ON CONFLICT(account_id) DO UPDATE SET current_generation = excluded.current_generation",
                               (account_id, generation))

    async def note_identity(self, account_id: str, generation: str, identity: str) -> bool:
        return await asyncio.to_thread(self._note_identity, account_id, generation, identity)

    def _note_identity(self, account_id: str, generation: str, identity: str) -> bool:
        with closing(self._connect()) as connection, connection:
            slot = connection.execute("SELECT identity, current_generation FROM mirror_slots WHERE account_id = ?",
                                      (account_id,)).fetchone()
            if not slot or slot[1] != generation:
                return False
            valid = slot[0] is None or slot[0] == identity
            connection.execute("UPDATE mirror_sessions SET identity = ?, state = ? WHERE generation = ?",
                               (identity, "waiting" if valid else "rejected", generation))
            return valid

    async def approve_session(self, account_id: str, generation: str, identity: str) -> bool:
        return await asyncio.to_thread(self._approve_session, account_id, generation, identity)

    def _approve_session(self, account_id: str, generation: str, identity: str) -> bool:
        with closing(self._connect()) as connection, connection:
            slot = connection.execute("SELECT identity, current_generation FROM mirror_slots WHERE account_id = ?",
                                      (account_id,)).fetchone()
            if not slot or slot[1] != generation or (slot[0] is not None and slot[0] != identity):
                return False
            connection.execute("UPDATE mirror_slots SET identity = ? WHERE account_id = ?", (identity, account_id))
            connection.execute("UPDATE mirror_sessions SET identity = ?, state = 'approved' WHERE generation = ?",
                               (identity, generation))
            # A previous process may have observed getMe during a database outage.
            # The now-confirmed same identity also validates those staged records;
            # preauth generations with no observed identity remain quarantined.
            connection.execute("UPDATE mirror_sessions SET state = 'approved' "
                               "WHERE account_id = ? AND identity = ? AND state = 'waiting'",
                               (account_id, identity))
            return True

    async def reject_session(self, generation: str) -> None:
        await asyncio.to_thread(self._reject_session, generation)

    def _reject_session(self, generation: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE mirror_sessions SET state = 'rejected' WHERE generation = ?", (generation,))

    async def enqueue(self, account_id: str, chat_id: str, message_ids: list[int], session_generation: str = "unfenced") -> None:
        if message_ids:
            await asyncio.to_thread(self._enqueue, account_id, chat_id, message_ids, session_generation)

    def _enqueue(self, account_id: str, chat_id: str, message_ids: list[int], session_generation: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                "INSERT OR IGNORE INTO pending_deletes VALUES (?, ?, ?, ?)",
                [(account_id, chat_id, int(message_id), session_generation) for message_id in message_ids],
            )

    async def pending(self, limit: int = 500) -> list[tuple[str, str, int, str]]:
        return await asyncio.to_thread(self._pending, limit)

    def _pending(self, limit: int) -> list[tuple[str, str, int, str]]:
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT d.account_id, d.chat_id, d.message_id, d.session_generation FROM pending_deletes d "
                + self._eligible("d") + "ORDER BY d.rowid LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()

    async def acknowledge(self, rows: list[tuple[str, str, int, str]]) -> None:
        await asyncio.to_thread(self._acknowledge, rows)

    def _acknowledge(self, rows: list[tuple[str, str, int, str]]) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                "DELETE FROM pending_deletes WHERE account_id = ? AND chat_id = ? AND message_id = ? AND session_generation = ?",
                rows,
            )


class InboxJournal(TombstoneJournal):
    """FIFO normalized inbox snapshots; never raw auth events or commands."""

    async def enqueue_payload(self, payload: dict) -> int:
        return await asyncio.to_thread(self._enqueue_payload, payload)

    def _enqueue_payload(self, payload: dict) -> int:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "INSERT INTO pending_bot_inbox (payload, account_id, session_generation) VALUES (?, ?, ?)",
                (json.dumps(payload), payload.get("_account_id"), payload.get("_session_generation")),
            )
            return cursor.lastrowid

    async def pending_payloads(self, limit: int = 50) -> list[tuple[int, dict]]:
        return await asyncio.to_thread(self._pending_payloads, limit)

    def _pending_payloads(self, limit: int) -> list[tuple[int, dict]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT p.sequence, p.payload FROM pending_bot_inbox p " + self._eligible("p") + "ORDER BY p.sequence LIMIT ?",
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
                "SELECT 1 FROM pending_bot_inbox p " + self._eligible("p") + "WHERE p.sequence <= ? LIMIT 1", (checkpoint,),
            ).fetchone() is not None

    async def enqueue_refresh(self, account_id: str, chat_id: str, message_id: int, session_generation: str) -> int:
        return await asyncio.to_thread(self._enqueue_refresh, account_id, chat_id, message_id, session_generation)

    def _enqueue_refresh(self, account_id: str, chat_id: str, message_id: int, session_generation: str) -> int:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO pending_refreshes VALUES (?, ?, ?, 1, 1, ?) "
                "ON CONFLICT(account_id, chat_id, message_id, session_generation) DO UPDATE SET "
                "generation = generation + 1, pending = 1",
                (account_id, chat_id, message_id, session_generation),
            )
            return connection.execute(
                "SELECT generation FROM pending_refreshes WHERE account_id = ? AND chat_id = ? AND message_id = ? AND session_generation = ?",
                (account_id, chat_id, message_id, session_generation),
            ).fetchone()[0]

    async def pending_refreshes(self, account_id: str, limit: int = 500, *, include_sessions: bool = False) -> list[tuple]:
        return await asyncio.to_thread(self._pending_refreshes, account_id, limit, include_sessions)

    def _pending_refreshes(self, account_id: str, limit: int, include_sessions: bool) -> list[tuple]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT r.chat_id, r.message_id, r.generation, r.session_generation FROM pending_refreshes r "
                + self._eligible("r") + "WHERE r.account_id = ? AND r.pending = 1 ORDER BY r.rowid LIMIT ?",
                (account_id, max(1, min(limit, 500))),
            ).fetchall()
            return rows if include_sessions else [row[:3] for row in rows]

    async def acknowledge_refresh(self, account_id: str, chat_id: str, message_id: int, generation: int, session_generation: str) -> None:
        await asyncio.to_thread(self._acknowledge_refresh, account_id, chat_id, message_id, generation, session_generation)

    def _acknowledge_refresh(self, account_id: str, chat_id: str, message_id: int, generation: int, session_generation: str) -> None:
        with closing(self._connect()) as connection, connection:
            # Keep the generation even after acknowledgement: a stale worker
            # cannot acknowledge a later edit that reused a removed row's ID.
            connection.execute(
                "UPDATE pending_refreshes SET pending = 0 WHERE account_id = ? "
                "AND chat_id = ? AND message_id = ? AND generation = ? AND session_generation = ?",
                (account_id, chat_id, message_id, generation, session_generation),
            )


# Preserve the deployed queue/table and import compatibility across the rename.
BotInboxJournal = InboxJournal
