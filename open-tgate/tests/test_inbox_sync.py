"""Offline regressions for inbox checkpoints and TDLib event semantics."""

import asyncio
from unittest.mock import AsyncMock

from app.config import Settings
from app.telegram import sync
from app.telegram.authflow import LoginContext, LoginMode, TdlibParameters
from app.telegram.manager import AccountManager, AccountRuntime


class Client:
    client_id = 1

    def send(self, request):
        raise AssertionError("Test attempted native TDLib request")


def setup():
    bus = AsyncMock()
    manager = AccountManager(Settings(), bus)
    manager._sync_delay = 0
    runtime = AccountRuntime("account-a", Client(), LoginContext(
        LoginMode.PHONE, TdlibParameters(1, "/d", "/f")
    ))
    manager._runtimes[runtime.account_id] = runtime
    manager._by_client[1] = runtime
    return manager, runtime, bus


def message(mid=100, **overrides):
    return {"id": mid, "chat_id": -42, "date": 1700000000,
            "content": {"@type": "messageText", "text": {"text": "hello"}},
            "sender_id": {"@type": "messageSenderUser", "user_id": 12}, **overrides}


def test_normalization_captures_caption_without_file_bytes_or_session_paths():
    row = sync.normalize_message(message(
        content={"@type": "messageDocument", "caption": {"text": "caption"},
                 "document": {"document": {"local": {"path": "/secret/file"}}}}
    ))
    assert row["chat_id"] == "-42"
    assert row["sender_id"] == "12"
    assert row["text"] == "caption"
    assert row["content_type"] == "messageDocument"
    assert row["sent_at"] == "2023-11-14T22:13:20+00:00"
    assert "deleted" not in row  # History never revives deletion tombstones.
    assert "/secret/file" not in str(row)
    chat = sync.normalize_inbox_chat({"id": -42, "last_message": message()})
    assert chat["last_message"] == "hello"
    assert "history_cursor" not in chat


def test_paced_history_continues_cursor_without_treating_short_pages_as_complete():
    async def run():
        manager, runtime, bus = setup()
        shift = 1 << 20
        bus.list_history_chats.return_value = [{"chat_id": "-42", "history_cursor": 100 * shift}]
        manager._send_and_wait = AsyncMock(side_effect=[
            {"@type": "messages", "messages": [message(90 * shift)]},
            {"@type": "messages", "messages": [message(80 * shift)]},
            {"@type": "messages", "messages": [message(70 * shift)]},
        ])
        await manager._backfill_history(runtime)
        calls = manager._send_and_wait.await_args_list
        assert [call.args[1]["from_message_id"] for call in calls] == [99 * shift, 89 * shift, 79 * shift]
        assert all(call.args[1]["limit"] <= 100 for call in calls)
        assert bus.patch_chat.await_args.args[2]["history_cursor"] == 70 * shift
        assert bus.patch_chat.await_args.args[2]["history_complete"] is False
        assert bus.upsert_messages.await_args_list[0].args[0][0]["account_id"] == "account-a"
        assert bus.upsert_messages.await_args_list[0].args[0][0]["message_id"] == 90 * shift
    asyncio.run(run())


def test_history_checkpoint_is_not_committed_before_messages_persist():
    async def run():
        manager, runtime, bus = setup()
        bus.list_history_chats.return_value = [{"chat_id": "-42", "history_cursor": 0}]
        bus.upsert_messages.side_effect = RuntimeError("offline")
        manager._send_and_wait = AsyncMock(return_value={"@type": "messages", "messages": [message()]})
        try:
            await manager._backfill_history(runtime)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Storage failure suppressed")
        bus.patch_chat.assert_not_awaited()
    asyncio.run(run())


def test_empty_history_marks_complete_and_bots_never_query_user_history():
    async def run():
        manager, runtime, bus = setup()
        bus.list_history_chats.return_value = [{"chat_id": "-42", "history_cursor": 2 << 20}]
        manager._send_and_wait = AsyncMock(return_value={"@type": "messages", "messages": []})
        await manager._backfill_history(runtime)
        assert bus.patch_chat.await_args.args[2]["history_complete"] is True
        runtime.ctx.mode = LoginMode.BOT
        bus.list_history_chats.reset_mock()
        await manager._backfill_history(runtime)
        bus.list_history_chats.assert_not_awaited()
    asyncio.run(run())


def test_realtime_updates_are_account_scoped_and_cache_eviction_is_not_deletion():
    async def run():
        manager, runtime, bus = setup()
        await manager._process_event({"@type": "updateNewMessage", "@client_id": 1, "message": message()})
        assert bus.upsert_messages.await_args.args[0][0]["account_id"] == "account-a"
        deleted = {"@type": "updateDeleteMessages", "@client_id": 1, "chat_id": -42,
                   "message_ids": [100], "is_permanent": False, "from_cache": True}
        await manager._process_event(deleted)
        bus.patch_message.assert_not_awaited()
        deleted["is_permanent"] = True
        await manager._process_event(deleted)
        bus.patch_message.assert_awaited_once_with("account-a", "-42", 100, {"deleted": True})
        await manager._process_event({"@type": "updateChatReadInbox", "@client_id": 1,
                                      "chat_id": -42, "unread_count": 4, "last_read_inbox_message_id": 90})
        bus.patch_chat.assert_awaited_with("account-a", "-42", {
            "unread_count": 4, "last_read_inbox_message_id": 90,
        })
    asyncio.run(run())


def test_chat_creation_persists_summary_and_last_message():
    async def run():
        manager, runtime, bus = setup()
        await manager._process_event({"@type": "updateNewChat", "@client_id": 1,
                                      "chat": {"id": -42, "title": "Team", "last_message": message()}})
        assert bus.upsert_entities.await_args.args[0][0]["account_id"] == "account-a"
        assert bus.upsert_chats.await_args.args[0][0]["title"] == "Team"
        assert bus.upsert_messages.await_args.args[0][0]["message_id"] == 100
    asyncio.run(run())


def test_orphan_read_error_does_not_overwrite_authorized_login_state():
    async def run():
        manager, runtime, bus = setup()
        await manager._process_event({"@type": "error", "@client_id": 1,
                                      "@extra": "sync-timeout", "code": 404, "message": "Not Found"})
        bus.update_account.assert_not_awaited()
    asyncio.run(run())


def test_connection_state_is_durable():
    async def run():
        manager, runtime, bus = setup()
        await manager._process_event({"@type": "updateConnectionState", "@client_id": 1,
                                      "state": {"@type": "connectionStateReady"}})
        assert bus.update_account.await_args.args[1]["connection_state"] == "connectionStateReady"
        assert bus.update_account.await_args.args[1]["last_activity_at"]
    asyncio.run(run())


def test_logout_cancels_background_history_without_replacing_session():
    async def run():
        manager, runtime, bus = setup()
        runtime.history_task = asyncio.create_task(asyncio.Event().wait())
        task = runtime.history_task
        await asyncio.sleep(0)
        await manager._cancel_sync(runtime)
        assert task.cancelled()
        assert runtime.history_task is None
        assert manager._runtimes["account-a"] is runtime
        bus.update_account.assert_not_awaited()
    asyncio.run(run())


def test_live_edit_and_unread_events_only_patch_their_own_fields():
    async def run():
        manager, runtime, bus = setup()
        await manager._process_event({"@type": "updateMessageContent", "@client_id": 1,
                                      "chat_id": -42, "message_id": 100,
                                      "new_content": {"@type": "messageText", "text": {"text": "edited"}}})
        bus.patch_message.assert_awaited_with("account-a", "-42", 100,
                                             {"text": "edited", "content_type": "messageText"})
        await manager._process_event({"@type": "updateMessageEdited", "@client_id": 1,
                                      "chat_id": -42, "message_id": 100, "edit_date": 1700000000})
        bus.patch_message.assert_awaited_with("account-a", "-42", 100,
                                             {"edited_at": "2023-11-14T22:13:20+00:00"})
        await manager._process_event({"@type": "updateChatIsMarkedAsUnread", "@client_id": 1,
                                      "chat_id": -42, "is_marked_as_unread": True})
        bus.patch_chat.assert_awaited_with("account-a", "-42", {"is_marked_unread": True})
        bus.upsert_messages.assert_not_awaited()
    asyncio.run(run())


def test_history_anchors_never_construct_invalid_local_message_ids():
    shift = 1 << 20
    assert sync.previous_history_anchor(100 * shift) == 99 * shift
    assert sync.previous_history_anchor(2 * shift) == shift
    assert sync.previous_history_anchor(shift) is None
    assert sync.previous_history_anchor(shift + 1) is None  # unsent ID
    assert sync.previous_history_anchor(shift + 2) is None  # local ID
    assert sync.previous_history_anchor(0) is None


def test_local_history_boundary_remains_pending_with_explanation():
    async def run():
        manager, runtime, bus = setup()
        bus.list_history_chats.return_value = [{"chat_id": "-42", "history_cursor": (1 << 20) + 2}]
        manager._send_and_wait = AsyncMock()
        await manager._backfill_history(runtime)
        manager._send_and_wait.assert_not_awaited()
        assert bus.patch_chat.await_args.args[2]["history_complete"] is False
        assert "no safe earlier" in bus.patch_chat.await_args.args[2]["history_note"]
    asyncio.run(run())


def test_startup_history_reset_happens_once_after_valid_profile_and_preserves_auth():
    async def run():
        manager, runtime, bus = setup()
        bus.list_history_chats.return_value = []
        bus.count_entities.return_value = {}

        async def response(_runtime, request, **kwargs):
            if request["@type"] == "getMe":
                return {"@type": "user", "id": 12}
            if request["@type"] == "loadChats":
                return {"@type": "error", "code": 404}
            return {"@type": "users", "user_ids": []}

        manager._send_and_wait = AsyncMock(side_effect=response)
        await manager._sync_account_once(runtime)
        await manager._sync_account_once(runtime)
        bus.restart_history.assert_awaited_once_with("account-a")
        assert runtime.history_restarted is True
        assert manager._runtimes["account-a"] is runtime
        assert not bus.mark_command.called
    asyncio.run(run())


def test_invalid_profile_never_resets_history_checkpoint():
    async def run():
        manager, runtime, bus = setup()
        manager._send_and_wait = AsyncMock(return_value=None)
        try:
            await manager._sync_account_once(runtime)
        except RuntimeError:
            pass
        bus.restart_history.assert_not_awaited()
        assert runtime.history_restarted is False
    asyncio.run(run())
