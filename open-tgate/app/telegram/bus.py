"""Supabase-backed command/event bus between the API and the TDLib worker.

The public ``/app`` console and the admin API cannot talk to the worker
directly — the worker owns the TDLib session volume and has no public port. They
coordinate through a small set of RLS-protected tables in Supabase:

* ``public.open_tgate_tg_accounts`` — one row per connected account and its live
  login/sync status (operator-readable).
* ``public.open_tgate_login_commands`` — operator → worker instructions
  (start_phone / start_qr / submit_code / submit_password / logout). Secrets
  (code, password) ride here only transiently and are cleared by the worker the
  instant they are consumed.
* ``public.open_tgate_tg_entities`` — the synced contacts/groups/channels/bots/
  files (operator-readable).

This module maps :class:`~app.telegram.authflow.Decision` values to account-row
patches (a pure function, unit-tested) and provides a thin PostgREST client used
by the worker with the Supabase *secret* key. TDLib session material is NEVER
written here.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from .authflow import Decision, LoginStatus

log = logging.getLogger("open-tgate.bus")

# Login-command actions accepted from the API.
ACTION_START_PHONE = "start_phone"
ACTION_START_QR = "start_qr"
ACTION_SUBMIT_CODE = "submit_code"
ACTION_SUBMIT_PASSWORD = "submit_password"
ACTION_LOGOUT = "logout"
ACTION_START_BOT_TOKEN = "start_bot_token"
ACTION_REVOKE_BOT = "revoke_bot"
ACTION_RESEND_CODE = "resend_code"

# Backwards-compatible alias kept in step with the production CHECK constraint
# (the bot action is ``start_bot_token``, never the legacy ``start_bot``).
ACTION_START_BOT = ACTION_START_BOT_TOKEN

VALID_ACTIONS = frozenset(
    {
        ACTION_START_PHONE,
        ACTION_START_QR,
        ACTION_SUBMIT_CODE,
        ACTION_SUBMIT_PASSWORD,
        ACTION_LOGOUT,
        ACTION_START_BOT_TOKEN,
        ACTION_REVOKE_BOT,
        ACTION_RESEND_CODE,
    }
)


def account_patch_from_decision(decision: Decision) -> dict[str, Any]:
    """Map a login :class:`Decision` to the columns to patch on an account row.

    ``qr_link`` is only overwritten when the decision carries a fresh link, so a
    transient state that omits it does not wipe a link the operator is scanning.
    """

    patch: dict[str, Any] = {"status": decision.status.value}
    patch["needs"] = decision.needs
    patch["last_error"] = decision.error
    if decision.qr_link is not None:
        patch["qr_link"] = decision.qr_link
    if decision.status is LoginStatus.AUTHORIZED:
        # Login finished: clear the QR link and any pending prompt.
        patch["qr_link"] = None
        patch["needs"] = None
    return patch


def build_command_row(account_id: str, action: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalise an operator login command into a queue row.

    Raises :class:`ValueError` for an unknown action or a payload missing the
    field that action requires. The returned row is what gets inserted into
    ``open_tgate_login_commands`` with ``status='pending'``.
    """

    if action not in VALID_ACTIONS:
        raise ValueError(f"unknown action: {action!r}")
    payload = payload or {}
    normalized: dict[str, Any] = {}

    if action == ACTION_START_PHONE:
        phone = str(payload.get("phone_number", "")).strip()
        if not phone.startswith("+") or len(phone) < 8:
            raise ValueError("phone_number must be E.164, e.g. +15551234567")
        normalized["phone_number"] = phone
    elif action == ACTION_SUBMIT_CODE:
        code = str(payload.get("code", "")).strip()
        if not code.isdigit() or not (3 <= len(code) <= 8):
            raise ValueError("code must be the 3-8 digit login code")
        normalized["code"] = code
    elif action == ACTION_SUBMIT_PASSWORD:
        password = str(payload.get("password", ""))
        if not password:
            raise ValueError("password is required for two-step verification")
        normalized["password"] = password
    elif action == ACTION_START_BOT_TOKEN:
        bot_token = str(payload.get("bot_token", "")).strip()
        if not bot_token or ":" not in bot_token:
            raise ValueError("bot_token must be a valid Telegram bot token (e.g. 123456:ABC-DEF…)")
        normalized["bot_token"] = bot_token
    # start_qr, logout, and revoke_bot take no payload.

    return {
        "account_id": account_id,
        "action": action,
        "payload": normalized or None,
        "status": "pending",
    }


class SupabaseBus:
    """Minimal PostgREST client for the worker (uses the Supabase secret key)."""

    def __init__(self, base_url: str, secret_key: str, *, timeout: float = 15.0) -> None:
        self._rest = f"{base_url.rstrip('/')}/rest/v1"
        self._timeout = timeout
        self._headers = {
            "apikey": secret_key,
            "authorization": f"Bearer {secret_key}",
            "content-type": "application/json",
        }

    async def list_authorized_accounts(self) -> list[dict[str, Any]]:
        """Return already-authorized accounts (id + type) so the worker can
        reopen their persistent TDLib sessions after a restart/redeploy.

        Both ``authorized`` (phone/QR logins) and ``bot_authorized`` (bot-token
        logins) are terminal authorized states in the account-status vocabulary
        (see ``20260929120000_open_tgate_account_vocab_forward.sql``), so a bot
        account's persistent session is rehydrated too.

        A metadata-sync failure can also leave an already-connected account in
        ``error``. Reopen only those with a persisted Telegram profile and a
        failed sync step; TDLib then determines whether the session is still
        authorized. Reopening never replaces or clears the session database.
        """

        url = (
            f"{self._rest}/open_tgate_tg_accounts"
            "?or=(status.in.(authorized,bot_authorized),"
            "and(status.eq.error,sync_step.eq.error,tg_user_id.not.is.null))"
            "&select=id,account_type"
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json()

    async def claim_pending_commands(self, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch queued commands oldest-first for the worker to execute."""

        url = (
            f"{self._rest}/open_tgate_login_commands"
            f"?status=eq.pending&order=created_at.asc&limit={limit}"
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json()

    async def mark_command(self, command_id: str, status: str, error: str | None = None) -> None:
        url = f"{self._rest}/open_tgate_login_commands?id=eq.{command_id}"
        body = {"status": status, "error": error, "payload": None, "consumed_at": datetime.now(UTC).isoformat()}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(url, headers=self._headers, content=json.dumps(body))
            resp.raise_for_status()

    async def update_account(self, account_id: str, patch: dict[str, Any]) -> None:
        url = f"{self._rest}/open_tgate_tg_accounts?id=eq.{account_id}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(url, headers=self._headers, content=json.dumps(patch))
            resp.raise_for_status()

    async def upsert_entities(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        url = f"{self._rest}/open_tgate_tg_entities?on_conflict=account_id,kind,tg_id"
        headers = {**self._headers, "prefer": "resolution=merge-duplicates"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=headers, content=json.dumps(rows))
            resp.raise_for_status()

    async def delete_contact(self, account_id: str, tg_id: str) -> None:
        """Remove only stale contact classification, retaining other entities."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.delete(
                f"{self._rest}/open_tgate_tg_entities", headers=self._headers,
                params={"account_id": f"eq.{account_id}", "kind": "eq.contact", "tg_id": f"eq.{tg_id}"},
            )
            resp.raise_for_status()

    async def prune_contacts(self, account_id: str, contact_ids: set[str]) -> None:
        """Reconcile a fully verified contact snapshot without oversized URLs."""
        saved_ids: set[str] = set()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            offset = 0
            while True:
                resp = await client.get(
                    f"{self._rest}/open_tgate_tg_entities", headers=self._headers,
                    params={"account_id": f"eq.{account_id}", "kind": "eq.contact",
                            "select": "tg_id", "order": "tg_id.asc", "limit": 1000, "offset": offset},
                )
                resp.raise_for_status()
                rows = resp.json()
                saved_ids.update(str(row["tg_id"]) for row in rows)
                if len(rows) < 1000:
                    break
                offset += 1000
            stale_ids = sorted(saved_ids - contact_ids)
            for start in range(0, len(stale_ids), 100):
                resp = await client.delete(
                    f"{self._rest}/open_tgate_tg_entities", headers=self._headers,
                    params={"account_id": f"eq.{account_id}", "kind": "eq.contact",
                            "tg_id": f"in.({','.join(stale_ids[start:start + 100])})"},
                )
                resp.raise_for_status()

    async def _upsert_inbox(self, table: str, keys: str, rows: list[dict]) -> None:
        if not rows:
            return
        headers = {**self._headers, "prefer": "resolution=merge-duplicates"}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._rest}/{table}?on_conflict={keys}",
                headers=headers, json=rows,
            )
            resp.raise_for_status()

    async def upsert_chats(self, rows: list[dict]) -> None:
        await self._upsert_inbox("open_tgate_tg_chats", "account_id,chat_id", rows)

    async def get_chat_membership(self, account_id: str, chat_id: str) -> dict | None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._rest}/open_tgate_tg_chats", headers=self._headers,
                params={"account_id": f"eq.{account_id}", "chat_id": f"eq.{chat_id}",
                        "select": "is_in_main,is_in_archive", "limit": 1},
            )
            resp.raise_for_status()
            rows = resp.json()
            return rows[0] if rows else None

    async def upsert_messages(self, rows: list[dict], *, history: bool = False) -> None:
        # Only full snapshots carry provenance. Sparse deletion patches must
        # retain existing metadata rather than replacing it with a source tag.
        snapshots = []
        for row in rows:
            snapshot = dict(row)
            if "meta" in row or history:
                snapshot["meta"] = {
                    **(row.get("meta") or {}),
                    "_mirror_source": "history" if history else "current",
                }
            snapshots.append(snapshot)
        await self._upsert_inbox(
            "open_tgate_tg_messages", "account_id,chat_id,message_id", snapshots
        )

    async def patch_chat(self, account_id: str, chat_id: str, patch: dict) -> None:
        # Sparse upsert: read/title updates can arrive before updateNewChat.
        await self.upsert_chats([{"account_id": account_id, "chat_id": chat_id, **patch}])

    async def patch_message(
        self, account_id: str, chat_id: str, message_id: int, patch: dict
    ) -> None:
        # A sparse edit/delete arriving ahead of backfill must survive as a row.
        await self.upsert_messages([{
            "account_id": account_id, "chat_id": chat_id,
            "message_id": message_id, **patch,
        }])

    async def list_history_chats(self, account_id: str, limit: int = 20) -> list[dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._rest}/open_tgate_tg_chats", headers=self._headers,
                params={"account_id": f"eq.{account_id}", "history_complete": "eq.false",
                        "is_visible": "eq.true",
                        "select": "chat_id,history_cursor,history_complete",
                        "order": "history_synced_at.asc.nullsfirst,chat_id.asc",
                        "limit": min(max(limit, 1), 20)},
            )
            resp.raise_for_status()
            return resp.json()

    async def restart_history(self, account_id: str) -> None:
        """Prepare the recent catch-up lane without resetting older history.

        The migration's RPC preserves unfinished recent cursors and old history
        checkpoints, staging a fresh newest scan after pending catch-up finishes.
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._rest}/rpc/open_tgate_prepare_recent_history", headers=self._headers,
                json={"account": account_id},
            )
            resp.raise_for_status()

    async def list_recent_history_chats(self, account_id: str, limit: int = 10) -> list[dict]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._rest}/open_tgate_tg_chats", headers=self._headers,
                params={"account_id": f"eq.{account_id}", "recent_complete": "eq.false",
                        "is_visible": "eq.true",
                        "select": "chat_id,recent_cursor,recent_complete,recent_boundary,recent_head,latest_synced_message_id,recent_restart_pending",
                        "order": "recent_synced_at.asc.nullsfirst,chat_id.asc",
                        "limit": min(max(limit, 1), 10)},
            )
            resp.raise_for_status()
            return resp.json()

    async def request_recent_history(self, account_id: str, chat_id: str) -> None:
        """Reopen one recent lane atomically, preserving an active catch-up cursor."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._rest}/rpc/open_tgate_request_recent_history", headers=self._headers,
                json={"account": account_id, "chat": chat_id},
            )
            resp.raise_for_status()

    async def complete_recent_history(self, account_id: str, chat_id: str, head: int) -> None:
        """Atomically commit the covered watermark and any queued newest scan."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._rest}/rpc/open_tgate_complete_recent_history", headers=self._headers,
                json={"account": account_id, "chat": chat_id, "head": head},
            )
            resp.raise_for_status()

    async def count_entities(self, account_id: str) -> dict[str, int]:
        """Return per-kind entity counts for one account.

        Uses PostgREST's ``select`` with ``count`` — one query per kind to keep
        the response small.  Falls back to an empty dict on failure.
        """

        kinds = ["user", "contact", "bot", "group", "channel", "file"]
        counts: dict[str, int] = {}
        try:
            for kind in kinds:
                url = (
                    f"{self._rest}/open_tgate_tg_entities"
                    f"?account_id=eq.{account_id}&kind=eq.{kind}"
                    f"&select=tg_id"
                )
                headers = {**self._headers, "prefer": "count=exact", "range-unit": "items"}
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.head(url, headers=headers)
                    resp.raise_for_status()
                    cr = resp.headers.get("content-range", "")
                    # content-range: 0-N/TOTAL  or  */TOTAL  or  */0
                    total_str = cr.rsplit("/", 1)[-1] if "/" in cr else "0"
                    total = int(total_str) if total_str.isdigit() else 0
                    if total > 0:
                        counts[kind] = total
        except Exception:  # noqa: BLE001
            pass
        return counts
