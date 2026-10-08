"""Pure TDLib authorization state machine (phone, QR, and bot login).

This module contains **no I/O**. It maps a TDLib ``authorizationState`` (plus the
inputs an operator has supplied so far) to the next request that should be sent
to TDLib and to a coarse, UI-facing status. Keeping it pure makes the tricky
login handshake fully unit-testable without a real ``libtdjson`` or network.

Supported login modes:

* ``phone`` — ``setAuthenticationPhoneNumber`` → ``checkAuthenticationCode`` →
  (optional 2FA) ``checkAuthenticationPassword``.
* ``qr`` — ``requestQrCodeAuthentication`` → the operator scans the emitted
  ``tg://login`` link on an already-authorized device →
  (optional 2FA) ``checkAuthenticationPassword``.
* ``bot`` — ``checkAuthenticationBotToken`` using an operator-supplied token.
  The token is consumed immediately and is never retained in the account row.

We deliberately refuse ``authorizationStateWaitRegistration`` (creating a brand
new Telegram account) — Open-TGate connects to *existing* accounts only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LoginMode(str, Enum):
    PHONE = "phone"
    QR = "qr"
    BOT = "bot"


class LoginStatus(str, Enum):
    """Coarse, UI-facing status for a login attempt."""

    INITIALIZING = "initializing"
    AWAITING_PHONE = "awaiting_phone"
    AWAITING_QR_SCAN = "awaiting_qr_scan"
    AWAITING_CODE = "awaiting_code"
    AWAITING_PASSWORD = "awaiting_password"
    AUTHORIZED = "authorized"
    LOGGED_OUT = "logged_out"
    ERROR = "error"


@dataclass
class TdlibParameters:
    """Non-secret bootstrap parameters for a TDLib client.

    ``api_hash`` is a secret and is supplied at send time by the caller, never
    stored on this dataclass or logged.
    """

    api_id: int
    database_directory: str
    files_directory: str
    use_test_dc: bool = False
    device_model: str = "Open-TGate"
    application_version: str = "1.0.0"
    system_language_code: str = "en"


@dataclass
class LoginContext:
    """Everything the state machine needs to decide the next step.

    The operator-supplied secrets (``code``, ``password``) are consumed by the
    machine and should be nulled by the caller immediately after the emitted
    request is dispatched — they must never be persisted.
    """

    mode: LoginMode
    parameters: TdlibParameters
    phone_number: str | None = None
    code: str | None = None
    password: str | None = None
    bot_token: str | None = None
    # Inputs already dispatched to TDLib, so we don't resend them on repeated
    # emissions of the same state.
    sent: set[str] = field(default_factory=set)


@dataclass
class Decision:
    """The machine's output for a single authorization-state observation."""

    status: LoginStatus
    request: dict | None = None
    # A ``tg://login?token=...`` link to render as a QR code, when present.
    qr_link: str | None = None
    # A short machine-readable reason the flow needs operator input or failed.
    needs: str | None = None
    error: str | None = None


def _set_parameters_request(p: TdlibParameters, api_hash: str) -> dict:
    return {
        "@type": "setTdlibParameters",
        "use_test_dc": p.use_test_dc,
        "database_directory": p.database_directory,
        "files_directory": p.files_directory,
        "use_file_database": True,
        "use_chat_info_database": True,
        "use_message_database": True,
        "use_secret_chats": False,
        "api_id": p.api_id,
        "api_hash": api_hash,
        "system_language_code": p.system_language_code,
        "device_model": p.device_model,
        "application_version": p.application_version,
    }


def plan(state: dict, ctx: LoginContext, *, api_hash: str) -> Decision:
    """Return the :class:`Decision` for one ``authorizationState`` observation.

    ``state`` is the parsed ``authorizationState`` object from TDLib (the value
    carried by an ``updateAuthorizationState`` update, or a direct query
    result). ``api_hash`` is injected here so the secret never lives on
    :class:`LoginContext`.
    """

    state_type = state.get("@type", "")

    if state_type == "authorizationStateWaitTdlibParameters":
        return Decision(
            status=LoginStatus.INITIALIZING,
            request=_set_parameters_request(ctx.parameters, api_hash),
        )

    if state_type == "authorizationStateWaitPhoneNumber":
        if ctx.mode is LoginMode.BOT:
            if not ctx.bot_token:
                return Decision(status=LoginStatus.ERROR, needs="bot_token", error="bot_token_required")
            if "bot_token" in ctx.sent:
                return Decision(status=LoginStatus.INITIALIZING)
            ctx.sent.add("bot_token")
            return Decision(
                status=LoginStatus.INITIALIZING,
                request={"@type": "checkAuthenticationBotToken", "token": ctx.bot_token},
            )
        if ctx.mode is LoginMode.QR:
            if "qr" in ctx.sent:
                return Decision(status=LoginStatus.AWAITING_QR_SCAN, needs="qr_scan")
            ctx.sent.add("qr")
            return Decision(
                status=LoginStatus.AWAITING_QR_SCAN,
                request={"@type": "requestQrCodeAuthentication", "other_user_ids": []},
                needs="qr_scan",
            )
        # Phone mode.
        if not ctx.phone_number:
            return Decision(status=LoginStatus.AWAITING_PHONE, needs="phone_number")
        if "phone" in ctx.sent:
            return Decision(status=LoginStatus.AWAITING_CODE, needs="code")
        ctx.sent.add("phone")
        return Decision(
            status=LoginStatus.AWAITING_CODE,
            request={
                "@type": "setAuthenticationPhoneNumber",
                "phone_number": ctx.phone_number,
            },
            needs="code",
        )

    if state_type == "authorizationStateWaitOtherDeviceConfirmation":
        # QR handshake: TDLib emits the link to render as a QR code. It rotates,
        # so always surface the newest link.
        return Decision(
            status=LoginStatus.AWAITING_QR_SCAN,
            qr_link=state.get("link"),
            needs="qr_scan",
        )

    if state_type == "authorizationStateWaitCode":
        if not ctx.code:
            return Decision(status=LoginStatus.AWAITING_CODE, needs="code")
        if "code" in ctx.sent:
            return Decision(status=LoginStatus.AWAITING_CODE, needs="code")
        ctx.sent.add("code")
        return Decision(
            status=LoginStatus.AWAITING_CODE,
            request={"@type": "checkAuthenticationCode", "code": ctx.code},
        )

    if state_type == "authorizationStateWaitPassword":
        if not ctx.password:
            return Decision(status=LoginStatus.AWAITING_PASSWORD, needs="password")
        if "password" in ctx.sent:
            return Decision(status=LoginStatus.AWAITING_PASSWORD, needs="password")
        ctx.sent.add("password")
        return Decision(
            status=LoginStatus.AWAITING_PASSWORD,
            request={
                "@type": "checkAuthenticationPassword",
                "password": ctx.password,
            },
        )

    if state_type in ("authorizationStateWaitEmailAddress", "authorizationStateWaitEmailCode"):
        return Decision(
            status=LoginStatus.ERROR,
            error="email_login_unsupported",
            needs="email",
        )

    if state_type == "authorizationStateWaitRegistration":
        # Open-TGate connects existing accounts only; it never registers a new
        # Telegram account on the user's behalf.
        return Decision(
            status=LoginStatus.ERROR,
            error="account_not_registered",
        )

    if state_type == "authorizationStateReady":
        return Decision(status=LoginStatus.AUTHORIZED)

    if state_type in ("authorizationStateLoggingOut", "authorizationStateClosing"):
        return Decision(status=LoginStatus.LOGGED_OUT)

    if state_type == "authorizationStateClosed":
        return Decision(status=LoginStatus.LOGGED_OUT)

    # Unknown / not-yet-handled state: stay put and let the caller keep reading.
    return Decision(status=LoginStatus.INITIALIZING)


# TDLib error payloads carry ``code`` + ``message``. A FLOOD_WAIT looks like
# ``{"@type":"error","code":429,"message":"Too Many Requests: retry after 42"}``.
def parse_flood_wait_seconds(error: dict) -> int | None:
    """Return the seconds to back off for a FLOOD_WAIT-style error, else None."""

    if error.get("@type") != "error":
        return None
    message = str(error.get("message", ""))
    import re

    match = re.search(r"(?:FLOOD_WAIT_|FLOOD_PREMIUM_WAIT_)(\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    marker = "retry after "
    idx = message.lower().find(marker)
    if idx == -1:
        return None
    tail = message[idx + len(marker):].strip()
    digits = ""
    for ch in tail:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


# Map well-known Telegram/TDLib auth error codes to an operator-facing message
# that says what is wrong AND how to fix it. The console shows ``last_error``
# verbatim, so a raw code like ``API_ID_INVALID`` is replaced here with guidance
# an operator can act on without reading TDLib internals. Unknown errors fall
# through to the raw message so nothing is hidden.
_OPERATOR_ERROR_MESSAGES: dict[str, str] = {
    "API_ID_INVALID": (
        "Telegram rejected the app credentials (API_ID_INVALID). Set a valid "
        "TELEGRAM_API_ID and TELEGRAM_API_HASH (from https://my.telegram.org) "
        "on the worker, then start the login again."
    ),
    "API_ID_PUBLISHED_FLOOD": (
        "This Telegram api_id is rate-limited for new logins "
        "(API_ID_PUBLISHED_FLOOD). Use a dedicated api_id/api_hash from "
        "https://my.telegram.org and retry later."
    ),
    "PHONE_NUMBER_INVALID": (
        "The phone number is not a valid Telegram number "
        "(PHONE_NUMBER_INVALID). Check the country code and digits, then start "
        "the login again."
    ),
    "PHONE_NUMBER_BANNED": (
        "This Telegram phone number is banned (PHONE_NUMBER_BANNED) and cannot "
        "be logged in."
    ),
    "PHONE_CODE_INVALID": (
        "The login code was incorrect (PHONE_CODE_INVALID). Submit the code "
        "again."
    ),
    "PHONE_CODE_EXPIRED": (
        "The login code expired (PHONE_CODE_EXPIRED). Start the login again to "
        "request a fresh code."
    ),
    "PASSWORD_HASH_INVALID": (
        "The two-step verification password was incorrect "
        "(PASSWORD_HASH_INVALID). Submit the password again."
    ),
    "ACCESS_TOKEN_INVALID": (
        "The bot token is invalid (ACCESS_TOKEN_INVALID). Check the token from "
        "@BotFather and start the bot login again."
    ),
    "ACCESS_TOKEN_EXPIRED": (
        "The bot token has expired (ACCESS_TOKEN_EXPIRED). Reissue it in "
        "@BotFather and start the bot login again."
    ),
    "SESSION_PASSWORD_NEEDED": (
        "This account has two-step verification enabled. Submit the account "
        "password to finish logging in."
    ),
}


def operator_error_message(raw: str | None) -> str:
    """Return an actionable operator message for a raw TDLib error string.

    Matches a known Telegram error code anywhere in ``raw`` (TDLib wraps codes
    in longer text) and returns guidance; otherwise returns the raw message so
    unknown failures are still surfaced, never swallowed.
    """

    text = (raw or "").strip()
    if not text:
        return text
    upper = text.upper()
    for code, friendly in _OPERATOR_ERROR_MESSAGES.items():
        if code in upper:
            return friendly
    return text
