"""Regression tests for rate limits and account lifetime boundaries."""
import asyncio
import time
from unittest.mock import AsyncMock
from app.config import Settings
from app.telegram.authflow import LoginContext, LoginMode, TdlibParameters, parse_flood_wait_seconds
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
    runtime = AccountRuntime('a', Client(1), LoginContext(LoginMode.PHONE, TdlibParameters(1, '/d', '/f')))
    manager._runtimes['a'] = runtime
    manager._by_client[1] = runtime
    return manager, runtime, bus


def test_correlated_flood_wait_blocks_only_affected_account():
    async def run():
        manager, runtime, bus = setup()
        future = asyncio.get_running_loop().create_future()
        manager._pending_requests['r'] = future
        manager._request_owners['r'] = runtime
        await manager._handle_event({'@client_id': 1, '@extra': 'r', '@type': 'error',
                                     'code': 429, 'message': 'FLOOD_WAIT_60'})
        assert future.done()
        assert manager._cooldowns['a'] > time.time() + 59
        waiting = asyncio.create_task(manager._send_and_wait(runtime, {'@type': 'getMe'}))
        await asyncio.sleep(0)
        assert runtime.client.sent == []
        await manager.apply_command({'id': 'command', 'account_id': 'a', 'action': 'start_qr'})
        assert bus.mark_command.call_args.args[1] == 'error'
        other = AccountRuntime('b', Client(2), runtime.ctx)
        manager._runtimes['b'] = other
        await manager._wait_to_send(other)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    asyncio.run(run())


def test_auth_state_deferred_during_cooldown():
    async def run():
        manager, runtime, _ = setup()
        manager._cooldowns['a'] = time.time() + 60
        await manager._handle_event({'@client_id': 1, '@type': 'authorizationStateWaitTdlibParameters'})
        assert runtime.deferred_state is not None
        assert runtime.client.sent == []
    asyncio.run(run())


def test_logout_cancels_sync_and_pending_requests():
    async def run():
        manager, runtime, bus = setup()
        runtime.sync_task = asyncio.create_task(manager._send_and_wait(runtime, {'@type': 'getMe'}))
        task = runtime.sync_task
        await asyncio.sleep(0)
        assert manager._pending_requests
        await manager.apply_command({'id': 'command', 'account_id': 'a', 'action': 'logout'})
        assert task.cancelled()
        assert manager._pending_requests == {}
        assert manager._request_owners == {}
        assert runtime.client.sent[-1] == {'@type': 'logOut'}
        assert not bus.update_account.called
    asyncio.run(run())


def test_old_runtime_cannot_send_after_replacement():
    async def run():
        manager, runtime, _ = setup()
        manager._runtimes.pop('a')
        try:
            await manager._send_and_wait(runtime, {'@type': 'getMe'})
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError('stale runtime was allowed to send')
        assert runtime.client.sent == []
    asyncio.run(run())


def test_flood_wait_formats():
    for value in ('FLOOD_WAIT_15', 'FLOOD_PREMIUM_WAIT_15', 'Too Many Requests: retry after 15'):
        assert parse_flood_wait_seconds({'@type': 'error', 'message': value}) == 15


def test_read_only_sync_retries_flood_wait_then_continues():
    async def run():
        manager, runtime, _ = setup()
        manager._send_once = AsyncMock(side_effect=[
            {'@type': 'error', 'message': 'FLOOD_WAIT_1'}, {'@type': 'user', 'id': 1}])
        result = await manager._send_and_wait(runtime, {'@type': 'getMe'})
        assert result['@type'] == 'user'
        assert manager._send_once.await_count == 2
    asyncio.run(run())


def test_closed_runtime_is_reaped_even_if_database_is_down():
    async def run():
        manager, runtime, bus = setup()
        bus.update_account.side_effect = RuntimeError('offline')
        try:
            await manager._handle_event({'@client_id': 1, '@type': 'authorizationStateClosed'})
        except RuntimeError:
            pass
        assert 'a' not in manager._runtimes
        assert 1 not in manager._by_client
    asyncio.run(run())


def test_bot_sync_never_calls_user_only_methods():
    async def run():
        manager, runtime, bus = setup()
        runtime.ctx.mode = LoginMode.BOT
        manager._sync_delay = 0
        manager._send_and_wait = AsyncMock(return_value={'@type': 'user', 'id': 123,
                                                       'type': {'@type': 'userTypeBot'}})
        bus.count_entities.return_value = {'bot': 1}
        await manager._sync_account(runtime)
        assert manager._send_and_wait.await_count == 1
        assert manager._send_and_wait.call_args.args[1]['@type'] == 'getMe'
        assert bus.update_account.call_args.args[1]['sync_step'] == 'complete'
    asyncio.run(run())


def test_failed_login_request_exposes_restart_action():
    async def run():
        manager, runtime, bus = setup()
        runtime.ctx.sent.add('phone')
        await manager._handle_event({'@client_id': 1, '@type': 'error',
                                     'code': 400, 'message': 'PHONE_NUMBER_INVALID'})
        assert bus.update_account.call_args.args[1]['status'] == 'error'
    asyncio.run(run())


def test_correlated_cooldown_response_does_not_wait_for_database():
    async def run():
        manager, runtime, bus = setup()
        bus.update_account.side_effect = RuntimeError('offline')
        future = asyncio.get_running_loop().create_future()
        manager._pending_requests['r'] = future
        await manager._handle_event({'@client_id': 1, '@extra': 'r', '@type': 'error',
                                     'code': 429, 'message': 'FLOOD_WAIT_30'})
        assert future.result()['code'] == 429
        assert manager._cooldowns['a'] > time.time()
        assert not bus.update_account.called
    asyncio.run(run())


def test_revoke_bot_logs_out_and_resets_account():
    async def run():
        manager, runtime, bus = setup()
        await manager.apply_command({'id': 'command', 'account_id': 'a', 'action': 'revoke_bot'})
        assert runtime.client.sent[-1] == {'@type': 'logOut'}
        assert bus.update_account.call_args.args[1]['status'] == 'logged_out'
        assert bus.mark_command.call_args.args[1] == 'done'
    asyncio.run(run())


def test_revoke_bot_resets_account_without_a_live_runtime():
    async def run():
        manager, _, bus = setup()
        manager._runtimes.pop('a')
        manager._by_client.pop(1)
        await manager.apply_command({'id': 'command', 'account_id': 'a', 'action': 'revoke_bot'})
        assert bus.update_account.call_args.args[1]['status'] == 'logged_out'
        assert bus.mark_command.call_args.args[1] == 'done'
    asyncio.run(run())


def test_start_bot_token_opens_bot_runtime():
    async def run():
        manager, _, bus = setup()
        manager._runtimes.pop('a')
        manager._by_client.pop(1)
        created = AccountRuntime('a', Client(9), LoginContext(LoginMode.BOT, TdlibParameters(1, '/d', '/f')))

        async def fake_new_runtime(account_id, mode):
            created.ctx.mode = mode
            manager._runtimes[account_id] = created
            manager._by_client[created.client.client_id] = created
            return created

        manager._new_runtime = AsyncMock(side_effect=fake_new_runtime)
        await manager.apply_command({'id': 'command', 'account_id': 'a', 'action': 'start_bot_token',
                                     'payload': {'bot_token': '123:abc'}})
        assert manager._new_runtime.call_args.args[1] is LoginMode.BOT
        assert manager._runtimes['a'].ctx.bot_token == '123:abc'
        assert bus.update_account.call_args.args[1]['account_type'] == 'bot'
    asyncio.run(run())

