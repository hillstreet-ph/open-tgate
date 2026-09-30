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
