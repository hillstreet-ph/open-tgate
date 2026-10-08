"""Regression tests for rate limits and account lifetime boundaries."""

import asyncio
import time
from unittest.mock import AsyncMock
from app.config import Settings
from app.telegram.authflow import (
    Decision,
    LoginContext,
    LoginMode,
    LoginStatus,
    TdlibParameters,
    parse_flood_wait_seconds,
)
from app.telegram.manager import AccountManager, AccountRuntime


class Client:
    def __init__(self, client_id):
        self.client_id = client_id
        self.sent = []

    def send(self, request):
        self.sent.append(request)


def setup():
    bus = AsyncMock()
    manager = AccountManager(Settings(), bus)
    runtime = AccountRuntime(
        "a", Client(1), LoginContext(LoginMode.PHONE, TdlibParameters(1, "/d", "/f"))
    )
    manager._runtimes["a"] = runtime
    manager._by_client[1] = runtime
    return manager, runtime, bus


def test_correlated_flood_wait_blocks_only_affected_account():
    async def run():
        manager, runtime, bus = setup()
        future = asyncio.get_running_loop().create_future()
        manager._pending_requests["r"] = future
        manager._request_owners["r"] = runtime
        await manager._handle_event(
            {
                "@client_id": 1,
                "@extra": "r",
                "@type": "error",
                "code": 429,
                "message": "FLOOD_WAIT_60",
            }
        )
        assert future.done()
        assert manager._cooldowns["a"] > time.time() + 59
        waiting = asyncio.create_task(
            manager._send_and_wait(runtime, {"@type": "getMe"})
        )
        await asyncio.sleep(0)
        assert runtime.client.sent == []
        await manager.apply_command(
            {"id": "command", "account_id": "a", "action": "start_qr"}
        )
        assert bus.mark_command.call_args.args[1] == "error"
        other = AccountRuntime("b", Client(2), runtime.ctx)
        manager._runtimes["b"] = other
        await manager._wait_to_send(other)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)

    asyncio.run(run())


def test_auth_state_deferred_during_cooldown():
    async def run():
        manager, runtime, _ = setup()
        manager._cooldowns["a"] = time.time() + 60
        await manager._handle_event(
            {"@client_id": 1, "@type": "authorizationStateWaitTdlibParameters"}
        )
        assert runtime.deferred_state is not None
        assert runtime.client.sent == []

    asyncio.run(run())


def test_logout_cancels_sync_and_pending_requests():
    async def run():
        manager, runtime, bus = setup()
        runtime.sync_task = asyncio.create_task(
            manager._send_and_wait(runtime, {"@type": "getMe"})
        )
        task = runtime.sync_task
        await asyncio.sleep(0)
        assert manager._pending_requests
        await manager.apply_command(
            {"id": "command", "account_id": "a", "action": "logout"}
        )
        assert task.cancelled()
        assert manager._pending_requests == {}
        assert manager._request_owners == {}
        assert runtime.client.sent[-1] == {"@type": "logOut"}
        assert not bus.update_account.called

    asyncio.run(run())


def test_old_runtime_cannot_send_after_replacement():
    async def run():
        manager, runtime, _ = setup()
        manager._runtimes.pop("a")
        try:
            await manager._send_and_wait(runtime, {"@type": "getMe"})
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("stale runtime was allowed to send")
        assert runtime.client.sent == []

    asyncio.run(run())


def test_flood_wait_formats():
    for value in (
        "FLOOD_WAIT_15",
        "FLOOD_PREMIUM_WAIT_15",
        "Too Many Requests: retry after 15",
    ):
        assert parse_flood_wait_seconds({"@type": "error", "message": value}) == 15


def test_read_only_sync_retries_flood_wait_then_continues():
    async def run():
        manager, runtime, _ = setup()
        manager._send_once = AsyncMock(
            side_effect=[
                {"@type": "error", "message": "FLOOD_WAIT_1"},
                {"@type": "user", "id": 1},
            ]
        )
        result = await manager._send_and_wait(runtime, {"@type": "getMe"})
        assert result["@type"] == "user"
        assert manager._send_once.await_count == 2

    asyncio.run(run())


def test_closed_runtime_is_reaped_even_if_database_is_down():
    async def run():
        manager, runtime, bus = setup()
        bus.update_account.side_effect = RuntimeError("offline")
        try:
            await manager._handle_event(
                {"@client_id": 1, "@type": "authorizationStateClosed"}
            )
        except RuntimeError:
            pass
        assert "a" not in manager._runtimes
        assert 1 not in manager._by_client

    asyncio.run(run())


def test_bot_sync_never_calls_user_only_methods():
    async def run():
        manager, runtime, bus = setup()
        runtime.ctx.mode = LoginMode.BOT
        manager._sync_delay = 0
        manager._send_and_wait = AsyncMock(
            return_value={"@type": "user", "id": 123, "type": {"@type": "userTypeBot"}}
        )
        bus.count_entities.return_value = {"bot": 1}
        await manager._sync_account(runtime)
        assert manager._send_and_wait.await_count == 1
        assert manager._send_and_wait.call_args.args[1]["@type"] == "getMe"
        assert bus.update_account.call_args.args[1]["sync_step"] == "complete"

    asyncio.run(run())


def test_sync_retries_transient_failure_then_marks_account_synced():
    async def run():
        manager, runtime, _ = setup()
        manager._sync_account_once = AsyncMock(
            side_effect=[RuntimeError("temporary Supabase failure"), None]
        )
        manager._sleep_for_sync_retry = AsyncMock()

        await manager._sync_account(runtime)

        assert manager._sync_account_once.await_count == 2
        manager._sleep_for_sync_retry.assert_awaited_once_with(5.0)
        assert runtime.synced is True

    asyncio.run(run())


def test_sync_retry_wait_respects_telegram_cooldown():
    async def run():
        manager, runtime, _ = setup()
        manager._sync_account_once = AsyncMock(
            side_effect=[RuntimeError("temporary TDLib error"), None]
        )
        manager._sleep_for_sync_retry = AsyncMock()
        manager._cooldowns["a"] = time.time() + 40

        await manager._sync_account(runtime)

        wait = manager._sleep_for_sync_retry.await_args.args[0]
        assert wait >= 39
        assert runtime.synced is True

    asyncio.run(run())


def test_sync_failure_after_retry_budget_is_visible_and_retryable():
    async def run():
        manager, runtime, _ = setup()
        manager._sync_account_once = AsyncMock(
            side_effect=RuntimeError("temporary error")
        )
        manager._sleep_for_sync_retry = AsyncMock()

        await manager._sync_account(runtime)

        assert manager._sync_account_once.await_count == 3
        assert manager._sleep_for_sync_retry.await_count == 2
        assert runtime.synced is False
        assert runtime.sync_step == "error"
        assert (
            "Reconnect to retry"
            in manager._bus.update_account.await_args.args[1]["last_error"]
        )

    asyncio.run(run())


def test_duplicate_authorized_updates_do_not_start_parallel_syncs():
    async def run():
        manager, runtime, _ = setup()
        release = asyncio.Event()

        async def hold_sync(_runtime):
            await release.wait()

        manager._sync_account = AsyncMock(side_effect=hold_sync)
        ready = {"@client_id": 1, "@type": "authorizationStateReady"}
        await manager._handle_event(ready)
        await asyncio.sleep(0)
        active_task = runtime.sync_task
        await manager._handle_event(ready)

        assert runtime.sync_task is active_task
        assert manager._sync_account.await_count == 1
        assert runtime.synced is False
        release.set()
        await active_task

    asyncio.run(run())


def test_completed_retry_budget_waits_for_reconnect_before_retrying():
    async def run():
        manager, runtime, _ = setup()
        runtime.sync_step = "error"
        manager._sync_account = AsyncMock()

        await manager._handle_event(
            {"@client_id": 1, "@type": "authorizationStateReady"}
        )

        manager._sync_account.assert_not_awaited()

    asyncio.run(run())


def test_failed_login_request_exposes_restart_action():
    async def run():
        manager, runtime, bus = setup()
        runtime.ctx.sent.add("phone")
        await manager._handle_event(
            {
                "@client_id": 1,
                "@type": "error",
                "code": 400,
                "message": "PHONE_NUMBER_INVALID",
            }
        )
        assert bus.update_account.call_args.args[1]["status"] == "error"

    asyncio.run(run())


def test_correlated_cooldown_response_does_not_wait_for_database():
    async def run():
        manager, runtime, bus = setup()
        bus.update_account.side_effect = RuntimeError("offline")
        future = asyncio.get_running_loop().create_future()
        manager._pending_requests["r"] = future
        await manager._handle_event(
            {
                "@client_id": 1,
                "@extra": "r",
                "@type": "error",
                "code": 429,
                "message": "FLOOD_WAIT_30",
            }
        )
        assert future.result()["code"] == 429
        assert manager._cooldowns["a"] > time.time()
        assert not bus.update_account.called

    asyncio.run(run())


def test_revoke_bot_logs_out_and_resets_account():
    async def run():
        manager, runtime, bus = setup()
        await manager.apply_command(
            {"id": "command", "account_id": "a", "action": "revoke_bot"}
        )
        assert runtime.client.sent[-1] == {"@type": "logOut"}
        assert bus.update_account.call_args.args[1]["status"] == "logged_out"
        assert bus.mark_command.call_args.args[1] == "done"

    asyncio.run(run())


def test_revoke_bot_resets_account_without_a_live_runtime():
    async def run():
        manager, _, bus = setup()
        manager._runtimes.pop("a")
        manager._by_client.pop(1)
        await manager.apply_command(
            {"id": "command", "account_id": "a", "action": "revoke_bot"}
        )
        assert bus.update_account.call_args.args[1]["status"] == "logged_out"
        assert bus.mark_command.call_args.args[1] == "done"

    asyncio.run(run())


def test_start_bot_token_opens_bot_runtime():
    async def run():
        manager, _, bus = setup()
        manager._runtimes.pop("a")
        manager._by_client.pop(1)
        created = AccountRuntime(
            "a", Client(9), LoginContext(LoginMode.BOT, TdlibParameters(1, "/d", "/f"))
        )

        async def fake_new_runtime(account_id, mode):
            created.ctx.mode = mode
            manager._runtimes[account_id] = created
            manager._by_client[created.client.client_id] = created
            return created

        manager._new_runtime = AsyncMock(side_effect=fake_new_runtime)
        await manager.apply_command(
            {
                "id": "command",
                "account_id": "a",
                "action": "start_bot_token",
                "payload": {"bot_token": "123:abc"},
            }
        )
        assert manager._new_runtime.call_args.args[1] is LoginMode.BOT
        assert manager._runtimes["a"].ctx.bot_token == "123:abc"
        assert bus.update_account.call_args.args[1]["account_type"] == "bot"

    asyncio.run(run())


def test_resend_code_asks_tdlib_to_resend():
    async def run():
        manager, runtime, bus = setup()
        await manager.apply_command(
            {"id": "command", "account_id": "a", "action": "resend_code"}
        )
        assert runtime.client.sent[-1] == {"@type": "resendAuthenticationCode"}
        assert bus.update_account.call_args.args[1]["needs"] == "code"
        assert bus.mark_command.call_args.args[1] == "done"

    asyncio.run(run())


def test_resend_code_without_runtime_reports_error():
    async def run():
        manager, _, bus = setup()
        manager._runtimes.pop("a")
        manager._by_client.pop(1)
        await manager.apply_command(
            {"id": "command", "account_id": "a", "action": "resend_code"}
        )
        assert bus.mark_command.call_args.args[1] == "error"

    asyncio.run(run())


def test_bot_status_uses_bot_vocabulary():
    manager, runtime, _ = setup()
    runtime.ctx.mode = LoginMode.BOT
    runtime.ctx.bot_token = "123:abc"
    assert (
        manager._account_patch(runtime, Decision(status=LoginStatus.INITIALIZING))[
            "status"
        ]
        == "validating_token"
    )
    assert (
        manager._account_patch(runtime, Decision(status=LoginStatus.AUTHORIZED))[
            "status"
        ]
        == "bot_authorized"
    )
    # Personal accounts keep the standard vocabulary.
    runtime.ctx.mode = LoginMode.PHONE
    assert (
        manager._account_patch(runtime, Decision(status=LoginStatus.AUTHORIZED))[
            "status"
        ]
        == "authorized"
    )
