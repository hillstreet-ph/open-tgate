"""Pure normalisation of TDLib chat/user/file objects into entity rows.

The synchronisation path is deliberately dependency-free (no AI, no Notion, no
network in this module) and read-only: it turns the objects TDLib already holds
in its local database into flat rows that mirror what the official Telegram app
shows after login — contacts, groups, channels, bots and files.

Everything here is a pure function so the classification rules can be unit
tested against representative TDLib payloads.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

EntityKind = str  # one of: user, contact, bot, group, channel, file

# Sync steps in execution order — used by the dashboard progress stepper.
SYNC_STEPS: list[str] = [
    "profile",
    "chats",
    "archived",
    "contacts",
    "complete",
]


def mask_phone(phone: str | None) -> str | None:
    """Mask a phone number for storage/display, keeping only a country hint and
    the last two digits (e.g. ``+15551234567`` → ``+1•••••67``).

    Full phone numbers are personal data; we never persist them in the clear.
    """

    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 4:
        return "•" * len(digits)
    lead = ("+" + digits[0]) if phone.strip().startswith("+") else digits[:1]
    return f"{lead}{'•' * max(3, len(digits) - 3)}{digits[-2:]}"


def classify_chat(chat: dict[str, Any]) -> EntityKind:
    """Classify a TDLib ``chat`` object into an Open-TGate entity kind."""

    chat_type = (chat.get("type") or {}).get("@type", "")
    if chat_type == "chatTypeBasicGroup":
        return "group"
    if chat_type == "chatTypeSupergroup":
        return "channel" if (chat.get("type") or {}).get("is_channel") else "group"
    if chat_type == "chatTypeSecret":
        return "user"
    # chatTypePrivate and anything unknown fall through to a 1:1 user chat.
    return "user"


def _extract_last_message_date(chat: dict[str, Any]) -> str | None:
    """Extract the last message date from a TDLib chat as an ISO timestamp."""
    last_msg = chat.get("last_message")
    if not last_msg:
        return None
    date_epoch = last_msg.get("date")
    if not date_epoch or not isinstance(date_epoch, (int, float)):
        return None
    try:
        return _dt.datetime.fromtimestamp(date_epoch, tz=_dt.timezone.utc).isoformat()
    except (OSError, ValueError):
        return None


def _is_archived_chat(chat: dict[str, Any]) -> bool:
    """Return True if the chat belongs to the Archive folder."""
    positions = chat.get("positions") or []
    for pos in positions:
        clist = pos.get("list", {})
        if isinstance(clist, dict) and clist.get("@type") == "chatListArchive":
            return True
    return False


def normalize_chat(chat: dict[str, Any]) -> dict[str, Any]:
    """Turn a TDLib ``chat`` object into a flat entity row."""

    chat_type_raw = (chat.get("type") or {}).get("@type", "")
    photo = chat.get("photo")
    small_photo_id = None
    if photo and isinstance(photo, dict):
        small = photo.get("small")
        if isinstance(small, dict):
            remote = small.get("remote")
            if isinstance(remote, dict):
                small_photo_id = remote.get("id")

    return {
        "kind": classify_chat(chat),
        "tg_id": str(chat.get("id", "")),
        "title": chat.get("title") or "",
        "username": None,
        "is_archived": _is_archived_chat(chat),
        "last_message_date": _extract_last_message_date(chat),
        "meta": {
            "chat_type": chat_type_raw,
            "has_photo": bool(photo),
            "small_photo_id": small_photo_id,
            "unread_count": chat.get("unread_count", 0),
            "member_count": (chat.get("type") or {}).get("member_count"),
            "is_marked_unread": bool(chat.get("is_marked_as_unread")),
        },
    }


def normalize_user(user: dict[str, Any]) -> dict[str, Any]:
    """Turn a TDLib ``user`` object into a flat entity row.

    A user is classified as ``bot`` when its type is ``userTypeBot``, otherwise
    ``contact`` when Telegram marks it a mutual/known contact, else ``user``.
    """

    user_type = (user.get("type") or {}).get("@type", "")
    if user_type == "userTypeBot":
        kind: EntityKind = "bot"
    elif user.get("is_contact") or user.get("is_mutual_contact"):
        kind = "contact"
    else:
        kind = "user"

    first = user.get("first_name") or ""
    last = user.get("last_name") or ""
    title = (first + " " + last).strip() or (user.get("username") or "")
    usernames = user.get("usernames") or {}
    active = usernames.get("active_usernames") if isinstance(usernames, dict) else None
    username = user.get("username") or (active[0] if active else None)

    return {
        "kind": kind,
        "tg_id": str(user.get("id", "")),
        "title": title,
        "username": username,
        "meta": {
            "is_bot": user_type == "userTypeBot",
            "is_contact": bool(user.get("is_contact")),
            "is_mutual_contact": bool(user.get("is_mutual_contact")),
            "is_verified": bool(user.get("is_verified")),
            "is_premium": bool(user.get("is_premium")),
            "phone_masked": mask_phone(user.get("phone_number")),
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
        },
    }


def user_projections(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain user type and contact membership as independent entity projections."""
    meta = row.get("meta") or {}
    primary = {**row, "kind": "bot"} if meta.get("is_bot") else row
    rows = [primary]
    if (meta.get("is_contact") or meta.get("is_mutual_contact")) and primary["kind"] != "contact":
        rows.append({**primary, "kind": "contact"})
    return rows


def normalize_file(document: dict[str, Any], *, chat_id: int | str | None = None) -> dict[str, Any]:
    """Turn a TDLib file-bearing object (``document``/``photo``/``audio`` …)
    into a flat ``file`` entity row. Only metadata is captured; file *bytes*
    are never uploaded off the worker by the sync path.
    """

    file = document.get("document") or document.get("file") or {}
    remote = (file.get("remote") or {}) if isinstance(file, dict) else {}
    return {
        "kind": "file",
        "tg_id": str(remote.get("unique_id") or file.get("id") or ""),
        "title": document.get("file_name") or document.get("caption", {}).get("text", "") or "",
        "username": None,
        "meta": {
            "mime_type": document.get("mime_type") or "",
            "size": (file.get("size") if isinstance(file, dict) else None) or 0,
            "chat_id": str(chat_id) if chat_id is not None else None,
        },
    }


def extract_profile(me: dict[str, Any]) -> dict[str, Any]:
    """Extract profile fields from a TDLib ``user`` object (result of getMe).

    Returns the patch for ``open_tgate_tg_accounts``, not an entity row.
    """

    first = me.get("first_name") or ""
    last = me.get("last_name") or ""
    usernames = me.get("usernames") or {}
    active = usernames.get("active_usernames") if isinstance(usernames, dict) else None
    username = me.get("username") or (active[0] if active else None)

    return {
        "tg_user_id": str(me.get("id", "")),
        "tg_first_name": first,
        "tg_last_name": last,
        "tg_username": username,
        "phone_masked": mask_phone(me.get("phone_number")),
    }


def summarize_counts(entities: list[dict[str, Any]]) -> dict[str, int]:
    """Aggregate a list of entity rows into per-kind counts for the UI."""

    counts: dict[str, int] = {}
    for entity in entities:
        kind = entity.get("kind", "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def epoch_timestamp(value: Any) -> str | None:
    """Normalize TDLib's UTC epoch seconds, rejecting absent/invalid dates."""
    if not isinstance(value, (int, float)) or not value:
        return None
    try:
        return _dt.datetime.fromtimestamp(value, tz=_dt.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def normalize_content(content: dict[str, Any]) -> dict[str, Any]:
    """Store text/caption and a minimal type; never store local paths or bytes."""
    formatted = content.get("text") or content.get("caption") or {}
    text = formatted.get("text", "") if isinstance(formatted, dict) else ""
    return {"text": text, "content_type": content.get("@type") or "messageUnknown"}


def normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    sender = message.get("sender_id") or {}
    return {
        "chat_id": str(message["chat_id"]),
        "message_id": message["id"],
        **normalize_content(message.get("content") or {}),
        "sender_id": str(sender.get("user_id") or sender.get("chat_id") or ""),
        "is_outgoing": bool(message.get("is_outgoing")),
        "sent_at": epoch_timestamp(message.get("date")),
        "edited_at": epoch_timestamp(message.get("edit_date")),
        "meta": {"sender_type": sender.get("@type"),
                 "message_thread_id": message.get("message_thread_id", 0)},
    }


def normalize_inbox_chat(chat: dict[str, Any]) -> dict[str, Any]:
    """Chat summary; omit history checkpoints so updates cannot reset backfill."""
    last = chat.get("last_message") or {}
    row = {
        "chat_id": str(chat["id"]),
        "title": chat.get("title") or "",
        "kind": classify_chat(chat),
        "unread_count": chat.get("unread_count", 0),
        "last_message": normalize_content(last.get("content") or {})["text"],
        "last_message_at": epoch_timestamp(last.get("date")),
        "is_archived": _is_archived_chat(chat),
        "is_marked_unread": bool(chat.get("is_marked_as_unread")),
        "last_read_inbox_message_id": chat.get("last_read_inbox_message_id", 0),
        "last_read_outbox_message_id": chat.get("last_read_outbox_message_id", 0),
        "synced_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    if "positions" in chat:
        row.update(membership_patch(chat_membership(chat["positions"])))
    return row


def chat_membership(positions: list[dict]) -> set[str]:
    """Full known main/archive membership, ignoring inactive positions."""
    return {(position.get("list") or {}).get("@type") for position in positions
            if position.get("order") and (position.get("list") or {}).get("@type")
            in ("chatListMain", "chatListArchive")}


def membership_patch(membership: set[str]) -> dict[str, bool]:
    """Visibility is independent from archive state; rows are never removed."""
    main = "chatListMain" in membership
    archive = "chatListArchive" in membership
    return {"is_in_main": main, "is_in_archive": archive,
            "is_visible": main or archive, "is_archived": archive}


def previous_history_anchor(message_id: int) -> int | None:
    """Exclusive anchor for a TDLib server ID; no arithmetic on local IDs.

    TDLib MessageId.h encodes server IDs as server_id << 20. Offset 0 is
    inclusive, so use the preceding server ID rather than subtracting one
    (which TDLib rejects). Local/unsent IDs cannot use this conversion safely.
    At the first server ID there is no valid previous server anchor; the caller
    can mark exhaustion there. Local/unsent IDs remain pending.
    """
    server_id_shift = 1 << 20
    if message_id > server_id_shift and message_id % server_id_shift == 0:
        return message_id - server_id_shift
    return None
