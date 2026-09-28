"""Unit tests for the Telegram login state machine, entity sync, and bus.

These cover the pure, security-critical logic that drives phone/QR login and
read-only account sync without needing a real ``libtdjson`` or network.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.security import require_admin
from app.telegram import bus, sync
from app.telegram.authflow import (
    Decision,
    LoginContext,
    LoginMode,
    LoginStatus,
    TdlibParameters,
    parse_flood_wait_seconds,
    plan,
)

API_HASH = "test-hash"


def _ctx(mode: LoginMode, **kw) -> LoginContext:
    params = TdlibParameters(api_id=123, database_directory="/d", files_directory="/f")
    return LoginContext(mode=mode, parameters=params, **kw)


# ---------------------------------------------------------------------------
# authflow — phone login
# ---------------------------------------------------------------------------
def test_wait_parameters_sends_set_parameters_with_secret_injected():
    d = plan({"@type": "authorizationStateWaitTdlibParameters"}, _ctx(LoginMode.PHONE), api_hash=API_HASH)
    assert d.status is LoginStatus.INITIALIZING
    assert d.request["@type"] == "setTdlibParameters"
    assert d.request["api_id"] == 123
    assert d.request["api_hash"] == API_HASH
    assert d.request["use_secret_chats"] is False


def test_phone_without_number_asks_for_phone():
    d = plan({"@type": "authorizationStateWaitPhoneNumber"}, _ctx(LoginMode.PHONE), api_hash=API_HASH)
    assert d.status is LoginStatus.AWAITING_PHONE
    assert d.needs == "phone_number"
    assert d.request is None


def test_phone_with_number_sends_phone_then_waits_for_code():
    ctx = _ctx(LoginMode.PHONE, phone_number="+15551234567")
    d = plan({"@type": "authorizationStateWaitPhoneNumber"}, ctx, api_hash=API_HASH)
    assert d.request["@type"] == "setAuthenticationPhoneNumber"
    assert d.request["phone_number"] == "+15551234567"
    assert d.needs == "code"
    # Re-emission of the same state must not resend the phone number.
    again = plan({"@type": "authorizationStateWaitPhoneNumber"}, ctx, api_hash=API_HASH)
    assert again.request is None
    assert again.status is LoginStatus.AWAITING_CODE


def test_code_submission():
    ctx = _ctx(LoginMode.PHONE, code="12345")
    d = plan({"@type": "authorizationStateWaitCode"}, ctx, api_hash=API_HASH)
    assert d.request == {"@type": "checkAuthenticationCode", "code": "12345"}
    # Without a code, the machine waits.
    waiting = plan({"@type": "authorizationStateWaitCode"}, _ctx(LoginMode.PHONE), api_hash=API_HASH)
    assert waiting.request is None
    assert waiting.needs == "code"


def test_password_submission_for_2fa():
    ctx = _ctx(LoginMode.PHONE, password="hunter2")
    d = plan({"@type": "authorizationStateWaitPassword"}, ctx, api_hash=API_HASH)
    assert d.request == {"@type": "checkAuthenticationPassword", "password": "hunter2"}
    waiting = plan({"@type": "authorizationStateWaitPassword"}, _ctx(LoginMode.PHONE), api_hash=API_HASH)
    assert waiting.needs == "password"
    assert waiting.request is None


# ---------------------------------------------------------------------------
# authflow — QR login
# ---------------------------------------------------------------------------
def test_qr_requests_qr_authentication_once():
    ctx = _ctx(LoginMode.QR)
    first = plan({"@type": "authorizationStateWaitPhoneNumber"}, ctx, api_hash=API_HASH)
    assert first.request == {"@type": "requestQrCodeAuthentication", "other_user_ids": []}
    assert first.needs == "qr_scan"
    second = plan({"@type": "authorizationStateWaitPhoneNumber"}, ctx, api_hash=API_HASH)
    assert second.request is None


def test_qr_link_surfaced_for_display():
    d = plan(
        {"@type": "authorizationStateWaitOtherDeviceConfirmation", "link": "tg://login?token=abc"},
        _ctx(LoginMode.QR),
        api_hash=API_HASH,
    )
    assert d.status is LoginStatus.AWAITING_QR_SCAN
    assert d.qr_link == "tg://login?token=abc"


def test_bot_token_authentication_is_single_use():
    token = "123456789:" + "a" * 35
    ctx = _ctx(LoginMode.BOT, bot_token=token)
    first = plan({"@type": "authorizationStateWaitPhoneNumber"}, ctx, api_hash=API_HASH)
    assert first.request == {"@type": "checkAuthenticationBotToken", "token": token}
    second = plan({"@type": "authorizationStateWaitPhoneNumber"}, ctx, api_hash=API_HASH)
    assert second.request is None


def test_bot_without_token_fails_closed():
    decision = plan({"@type": "authorizationStateWaitPhoneNumber"}, _ctx(LoginMode.BOT), api_hash=API_HASH)
    assert decision.status is LoginStatus.ERROR
    assert decision.error == "bot_token_required"


# ---------------------------------------------------------------------------
# authflow — terminal / refused states
# ---------------------------------------------------------------------------
def test_ready_is_authorized():
    assert plan({"@type": "authorizationStateReady"}, _ctx(LoginMode.QR), api_hash=API_HASH).status is (
        LoginStatus.AUTHORIZED
    )


def test_registration_is_refused():
    d = plan({"@type": "authorizationStateWaitRegistration"}, _ctx(LoginMode.PHONE), api_hash=API_HASH)
    assert d.status is LoginStatus.ERROR
    assert d.error == "account_not_registered"


def test_email_login_is_unsupported():
    d = plan({"@type": "authorizationStateWaitEmailAddress"}, _ctx(LoginMode.PHONE), api_hash=API_HASH)
    assert d.error == "email_login_unsupported"


def test_flood_wait_parsing():
    assert parse_flood_wait_seconds({"@type": "error", "message": "Too Many Requests: retry after 42"}) == 42
    assert parse_flood_wait_seconds({"@type": "error", "message": "PHONE_CODE_INVALID"}) is None
    assert parse_flood_wait_seconds({"@type": "ok"}) is None


# ---------------------------------------------------------------------------
# sync — entity normalisation
# ---------------------------------------------------------------------------
def test_mask_phone_hides_the_middle():
    masked = sync.mask_phone("+15551234567")
    assert masked.startswith("+1")
    assert masked.endswith("67")
    assert "5551234" not in masked
    assert sync.mask_phone(None) is None


def test_classify_chat_types():
    assert sync.classify_chat({"type": {"@type": "chatTypeBasicGroup"}}) == "group"
    assert sync.classify_chat({"type": {"@type": "chatTypeSupergroup", "is_channel": True}}) == "channel"
    assert sync.classify_chat({"type": {"@type": "chatTypeSupergroup", "is_channel": False}}) == "group"
    assert sync.classify_chat({"type": {"@type": "chatTypePrivate"}}) == "user"


def test_normalize_user_variants():
    bot = sync.normalize_user({"id": 7, "type": {"@type": "userTypeBot"}, "username": "mybot"})
    assert bot["kind"] == "bot"
    assert bot["username"] == "mybot"
    contact = sync.normalize_user(
        {"id": 8, "type": {"@type": "userTypeRegular"}, "is_contact": True, "first_name": "Ada", "last_name": "L"}
    )
    assert contact["kind"] == "contact"
    assert contact["title"] == "Ada L"
    stranger = sync.normalize_user(
        {"id": 9, "type": {"@type": "userTypeRegular"}, "usernames": {"active_usernames": ["ghost"]}}
    )
    assert stranger["kind"] == "user"
    assert stranger["username"] == "ghost"


def test_normalize_user_masks_phone():
    row = sync.normalize_user({"id": 1, "type": {"@type": "userTypeRegular"}, "phone_number": "+15551234567"})
    assert "5551234" not in (row["meta"]["phone_masked"] or "")


def test_summarize_counts():
    counts = sync.summarize_counts(
        [{"kind": "contact"}, {"kind": "contact"}, {"kind": "channel"}]
    )
    assert counts == {"contact": 2, "channel": 1}


# ---------------------------------------------------------------------------
# bus — command building + decision mapping
# ---------------------------------------------------------------------------
def test_build_command_row_validates_phone():
    row = bus.build_command_row("acc", "start_phone", {"phone_number": "+15551234567"})
    assert row["action"] == "start_phone"
    assert row["status"] == "pending"
    assert row["payload"]["phone_number"] == "+15551234567"


def test_build_command_row_rejects_bad_phone_and_action():
    import pytest

    with pytest.raises(ValueError):
        bus.build_command_row("acc", "start_phone", {"phone_number": "5551234"})
    with pytest.raises(ValueError):
        bus.build_command_row("acc", "not_a_real_action", {})


def test_build_command_row_code_and_qr():
    assert bus.build_command_row("acc", "submit_code", {"code": "12345"})["payload"]["code"] == "12345"
    assert bus.build_command_row("acc", "start_qr", None)["payload"] is None


def test_build_command_row_accepts_bot_token_without_logging_it():
    token = "123456789:" + "b" * 35
    row = bus.build_command_row("acc", "start_bot", {"bot_token": token})
    assert row["action"] == "start_bot"
    assert row["payload"] == {"bot_token": token}


def test_build_command_row_rejects_malformed_bot_token():
    import pytest

    with pytest.raises(ValueError):
        bus.build_command_row("acc", "start_bot", {"bot_token": "not-a-token"})


def test_account_patch_authorized_clears_qr_and_needs():
    patch = bus.account_patch_from_decision(Decision(status=LoginStatus.AUTHORIZED, qr_link="x", needs="code"))
    assert patch["status"] == "authorized"
    assert patch["qr_link"] is None
    assert patch["needs"] is None


def test_account_patch_qr_scan_keeps_link():
    patch = bus.account_patch_from_decision(
        Decision(status=LoginStatus.AWAITING_QR_SCAN, qr_link="tg://login?token=z", needs="qr_scan")
    )
    assert patch["qr_link"] == "tg://login?token=z"
    assert patch["needs"] == "qr_scan"


# ---------------------------------------------------------------------------
# API — admin gate + validation
# ---------------------------------------------------------------------------
client = TestClient(app)


def test_telegram_endpoints_require_admin():
    assert client.post("/api/v1/telegram/accounts", json={"label": "main"}).status_code == 401
    assert client.patch("/api/v1/telegram/accounts/a", json={"label": "renamed"}).status_code == 401
    assert client.post(
        "/api/v1/telegram/commands", json={"account_id": "a", "action": "start_qr"}
    ).status_code == 401


def test_enqueue_rejects_bad_action_before_network():
    app.dependency_overrides[require_admin] = lambda: None
    try:
        r = client.post("/api/v1/telegram/commands", json={"account_id": "a", "action": "nope"})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_enqueue_valid_but_supabase_unconfigured_returns_503():
    app.dependency_overrides[require_admin] = lambda: None
    try:
        r = client.post(
            "/api/v1/telegram/commands",
            json={"account_id": "a", "action": "start_phone", "payload": {"phone_number": "+15551234567"}},
        )
        assert r.status_code == 503
    finally:
        app.dependency_overrides.pop(require_admin, None)


# ---------------------------------------------------------------------------
# knowledge — isolated Notion export (pure mapping, no network)
# ---------------------------------------------------------------------------
def test_notion_external_key_is_stable_per_account_kind_and_id():
    from app.knowledge import external_key

    entity = {"account_id": "acc-1", "kind": "contact", "tg_id": "42"}
    assert external_key(entity) == "acc-1:contact:42"
    # The same Telegram id under a different account is a different key.
    other = {"account_id": "acc-2", "kind": "contact", "tg_id": "42"}
    assert external_key(other) != external_key(entity)


def test_notion_properties_map_expected_fields():
    from app.knowledge import entity_to_notion_properties

    props = entity_to_notion_properties(
        {"account_id": "acc-1", "kind": "channel", "tg_id": "99", "title": "News", "username": "news"}
    )
    assert props["Name"]["title"][0]["text"]["content"] == "News"
    assert props["Kind"]["rich_text"][0]["text"]["content"] == "channel"
    assert props["Username"]["rich_text"][0]["text"]["content"] == "@news"
    assert props["Telegram ID"]["rich_text"][0]["text"]["content"] == "99"
    assert props["Account"]["rich_text"][0]["text"]["content"] == "acc-1"
    assert props["Key"]["rich_text"][0]["text"]["content"] == "acc-1:channel:99"


def test_notion_title_falls_back_to_username_then_id():
    from app.knowledge import entity_to_notion_properties

    # No title but a username -> @username.
    p1 = entity_to_notion_properties({"account_id": "a", "kind": "bot", "tg_id": "7", "username": "helper"})
    assert p1["Name"]["title"][0]["text"]["content"] == "@helper"
    # No title, no username -> kind:tg_id.
    p2 = entity_to_notion_properties({"account_id": "a", "kind": "file", "tg_id": "3"})
    assert p2["Name"]["title"][0]["text"]["content"] == "file:3"


def test_notion_export_module_is_isolated_from_sync():
    """The Telegram sync path must never import the knowledge exporters.

    Checks actual ``import`` statements (via the AST) rather than raw text, so
    the sync module's docstring promise ("no Notion") does not trip the guard.
    """
    import ast
    import inspect

    import app.telegram.sync as sync_mod

    tree = ast.parse(inspect.getsource(sync_mod))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any("knowledge" in name or "notion" in name.lower() for name in imported)
