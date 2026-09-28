"""Export already-synced Telegram entities into a Notion database.

Open-TGate mirrors each connected account's contacts, groups, channels, bots
and files into Supabase (see ``app.telegram.sync``). This module is a **thin,
read-only downstream consumer** of that Supabase data: it reads entity rows and
upserts them, one Notion page per entity, into a Notion database that an AI
agent can use as a knowledge base.

Design constraints (intentional):

* **Isolation.** Nothing in ``app.telegram`` imports this module, so the export
  can never touch the ban-safe sync path. It runs out-of-band.
* **Read-only on Telegram.** It only reads Supabase rows already produced by
  sync; it never talks to TDLib and never sends anything to Telegram.
* **Idempotent.** Each entity has a stable ``external_key`` used to find an
  existing Notion page and update it in place instead of creating duplicates.
* **Pure core.** The row→Notion mapping is pure functions, unit-tested without
  any network, so the mapping rules are verifiable in CI without credentials.
"""

from __future__ import annotations

from typing import Any

import httpx

_NOTION_VERSION = "2022-06-28"
_NOTION_API = "https://api.notion.com/v1"


def external_key(entity: dict[str, Any]) -> str:
    """Return a stable, unique key for an entity across (account, kind, tg_id).

    The same Telegram entity can legitimately appear under multiple accounts, so
    the account id is part of the key. This key is written to the Notion page and
    used to locate it again for idempotent updates.
    """

    account = str(entity.get("account_id") or "").strip()
    kind = str(entity.get("kind") or "").strip()
    tg_id = str(entity.get("tg_id") or "").strip()
    return f"{account}:{kind}:{tg_id}"


def _rich_text(value: str | None) -> list[dict[str, Any]]:
    text = (value or "").strip()
    if not text:
        return []
    # Notion caps rich_text content at 2000 characters per item.
    return [{"type": "text", "text": {"content": text[:2000]}}]


def _title_for(entity: dict[str, Any]) -> str:
    """Human title for the Notion page, falling back gracefully."""

    title = (entity.get("title") or "").strip()
    if title:
        return title
    username = (entity.get("username") or "").strip()
    if username:
        return f"@{username}"
    tg_id = str(entity.get("tg_id") or "").strip()
    kind = str(entity.get("kind") or "entity").strip()
    return f"{kind}:{tg_id}" if tg_id else kind


def entity_to_notion_properties(entity: dict[str, Any]) -> dict[str, Any]:
    """Map a Supabase entity row to a Notion ``properties`` payload.

    Property schema (all created as expected in the target database):
      * ``Name``        — title
      * ``Kind``        — rich_text (contact/user/group/channel/bot/file)
      * ``Username``    — rich_text
      * ``Telegram ID`` — rich_text
      * ``Account``     — rich_text (the owning account id)
      * ``Key``         — rich_text (the idempotency ``external_key``)
    """

    username = entity.get("username")
    username_text = f"@{username}" if username else ""
    return {
        "Name": {
            "title": [{"type": "text", "text": {"content": _title_for(entity)[:2000]}}]
        },
        "Kind": {"rich_text": _rich_text(str(entity.get("kind") or ""))},
        "Username": {"rich_text": _rich_text(username_text)},
        "Telegram ID": {"rich_text": _rich_text(str(entity.get("tg_id") or ""))},
        "Account": {"rich_text": _rich_text(str(entity.get("account_id") or ""))},
        "Key": {"rich_text": _rich_text(external_key(entity))},
    }


class NotionExporter:
    """Upsert synced entities from Supabase into a Notion knowledge base.

    Instantiated with explicit credentials so it is trivial to unit-test the
    pure parts and to construct from settings at the call site (never imported
    by the sync worker).
    """

    def __init__(
        self,
        *,
        notion_token: str,
        notion_database_id: str,
        supabase_url: str,
        supabase_secret_key: str,
        timeout: float = 20.0,
    ) -> None:
        self._token = notion_token
        self._database_id = notion_database_id
        self._supabase_url = supabase_url.rstrip("/")
        self._supabase_key = supabase_secret_key
        self._timeout = timeout

    def _notion_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _supabase_headers(self) -> dict[str, str]:
        return {
            "apikey": self._supabase_key,
            "Authorization": f"Bearer {self._supabase_key}",
            "Content-Type": "application/json",
        }

    def fetch_entities(
        self, *, limit: int = 500
    ) -> list[dict[str, Any]]:  # pragma: no cover - network
        """Read entity rows from Supabase (service key, read-only)."""

        url = f"{self._supabase_url}/rest/v1/open_tgate_tg_entities"
        params = {
            "select": "account_id,kind,tg_id,title,username,meta",
            "order": "kind.asc",
            "limit": str(limit),
        }
        resp = httpx.get(
            url, headers=self._supabase_headers(), params=params, timeout=self._timeout
        )
        resp.raise_for_status()
        return resp.json() or []

    def _find_page_id(self, key: str) -> str | None:  # pragma: no cover - network
        url = f"{_NOTION_API}/databases/{self._database_id}/query"
        payload = {
            "filter": {"property": "Key", "rich_text": {"equals": key}},
            "page_size": 1,
        }
        resp = httpx.post(
            url, headers=self._notion_headers(), json=payload, timeout=self._timeout
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        return results[0]["id"] if results else None

    def upsert_entity(
        self, entity: dict[str, Any]
    ) -> str:  # pragma: no cover - network
        """Create or update a single Notion page for an entity. Returns 'created'/'updated'."""

        properties = entity_to_notion_properties(entity)
        page_id = self._find_page_id(external_key(entity))
        if page_id:
            resp = httpx.patch(
                f"{_NOTION_API}/pages/{page_id}",
                headers=self._notion_headers(),
                json={"properties": properties},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return "updated"
        resp = httpx.post(
            f"{_NOTION_API}/pages",
            headers=self._notion_headers(),
            json={
                "parent": {"database_id": self._database_id},
                "properties": properties,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return "created"

    def run_once(
        self, *, limit: int = 500
    ) -> dict[str, int]:  # pragma: no cover - network
        """Export up to ``limit`` synced entities into Notion. Returns a summary."""

        entities = self.fetch_entities(limit=limit)
        summary = {"created": 0, "updated": 0, "failed": 0}
        for entity in entities:
            try:
                outcome = self.upsert_entity(entity)
                summary[outcome] += 1
            except httpx.HTTPError:
                summary["failed"] += 1
        return summary
