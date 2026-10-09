"""Offline regressions for inbox checkpoints and TDLib event semantics."""

import asyncio
import json
import time
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import Settings
from app.telegram import sync
from app.telegram.bus import SupabaseBus
from app.telegram.authflow import LoginContext, LoginMode, TdlibParameters
from app.telegram.manager import AccountManager, AccountRuntime
from app.telegram.tombstones import InboxJournal, TombstoneJournal


class Client:
    client_id = 1

    def send(self, request):
        raise AssertionError("Test attempted native TDLib request")


def setup(state_dir=None):
    bus = AsyncMock()
    bus.list_recent_history_chats.return_value = []
    temporary_state = TemporaryDirectory() if state_dir is None else None
    settings = Settings(tdlib_database_directory=str(state_dir or temporary_state.name))
    manager = AccountManager(settings, bus)
    manager._temporary_test_state = temporary_state
    manager._sync_delay = 0
    runtime = AccountRuntime("account-a", Client(), LoginContext(
        LoginMode.PHONE, TdlibParameters(1, "/d", "/f")
    ))
    runtime.last_activity_write = time.time()
    manager._runtimes[runtime.account_id] = runtime
    manager._by_client[1] = runtime
    return manager, runtime, bus


def message(mid=100, **overrides):
    return {"@type": "message", "id": mid, "chat_id": -42, "date": 1700000000,
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
        await manager._backfill_history(runtime)
        patch = bus.patch_chat.await_args.args[2]
        assert "history_cursor" not in patch
        assert patch["history_complete"] is False
        assert "storage" in patch["history_note"]
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


def test_realtime_updates_are_account_scoped_and_cache_eviction_is_not_deletion(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        await manager._process_event({"@type": "updateNewMessage", "@client_id": 1, "message": message()})
        await manager._wait_inbox_persisted()
        assert bus.upsert_messages.await_args.args[0][0]["account_id"] == "account-a"
        deleted = {"@type": "updateDeleteMessages", "@client_id": 1, "chat_id": -42,
                   "message_ids": [100], "is_permanent": False, "from_cache": True}
        await manager._process_event(deleted)
        await manager._wait_inbox_persisted()
        bus.patch_message.assert_not_awaited()
        deleted["is_permanent"] = True
        await manager._process_event(deleted)
        await manager._wait_inbox_persisted()
        await manager._tombstone_task
        bus.upsert_messages.assert_awaited_with([
            {"account_id": "account-a", "chat_id": "-42", "message_id": 100, "deleted": True},
        ])
        await manager._process_event({"@type": "updateChatReadInbox", "@client_id": 1,
                                      "chat_id": -42, "unread_count": 4, "last_read_inbox_message_id": 90})
        await manager._wait_inbox_persisted()
        bus.upsert_chats.assert_awaited_with([{
            "account_id": "account-a", "chat_id": "-42",
            "unread_count": 4, "last_read_inbox_message_id": 90,
        }])
    asyncio.run(run())


def test_chat_creation_persists_summary_and_last_message():
    async def run():
        manager, runtime, bus = setup()
        await manager._process_event({"@type": "updateNewChat", "@client_id": 1,
                                      "chat": {"id": -42, "title": "Team", "last_message": message()}})
        await manager._wait_inbox_persisted()
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
        await manager._wait_inbox_persisted()
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


def test_live_edit_events_coalesce_into_one_complete_message_revision():
    async def run():
        manager, runtime, bus = setup()
        manager._send_and_wait = AsyncMock(return_value=message(
            edit_date=1700000001,
            content={"@type": "messageText", "text": {"text": "edited"}},
        ))
        # Two events queued before the paced read starts coalesce to the
        # latest durable generation without losing either acknowledgement.
        with patch.object(manager, "_start_message_refresh"):
            await manager._process_event({"@type": "updateMessageContent", "@client_id": 1,
                                          "chat_id": -42, "message_id": 100,
                                          "new_content": {"@type": "messageText", "text": {"text": "edited"}}})
            await manager._process_event({"@type": "updateMessageEdited", "@client_id": 1,
                                          "chat_id": -42, "message_id": 100, "edit_date": 1700000001})
        assert await manager._inbox.pending_refreshes("account-a") == [("-42", 100, 2)]
        manager._start_message_refresh(runtime)
        await runtime.message_refresh_task
        await manager._wait_inbox_persisted()
        bus.patch_message.assert_not_awaited()
        manager._send_and_wait.assert_awaited_once()
        assert manager._send_and_wait.await_args.args[1]["@type"] == "getMessage"
        row = bus.upsert_messages.await_args.args[0][0]
        assert row["text"] == "edited"
        assert row["edited_at"] == "2023-11-14T22:13:21+00:00"
        assert row["account_id"] == "account-a"
        await manager._process_event({"@type": "updateChatIsMarkedAsUnread", "@client_id": 1,
                                      "chat_id": -42, "is_marked_as_unread": True})
        await manager._wait_inbox_persisted()
        bus.upsert_chats.assert_awaited_with([{
            "account_id": "account-a", "chat_id": "-42", "is_marked_unread": True,
        }])
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
        await runtime.history_task
        await manager._sync_account_once(runtime)
        await runtime.history_task
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


def test_one_inaccessible_history_chat_does_not_starve_other_chats_or_metadata():
    async def run():
        manager, runtime, bus = setup()
        bus.list_history_chats.return_value = [
            {"chat_id": "-42", "history_cursor": 2 << 20},
            {"chat_id": "-43", "history_cursor": 2 << 20},
        ]
        bus.count_entities.return_value = {"contact": 3}

        async def response(_runtime, request, **kwargs):
            if request["@type"] == "getMe":
                return {"@type": "user", "id": 12}
            if request["@type"] == "loadChats":
                return {"@type": "error", "code": 404}
            if request["@type"] == "getChatHistory":
                if request["chat_id"] == -42:
                    return {"@type": "error", "code": 404, "message": "Not Found"}
                return {"@type": "messages", "messages": []}
            return {"@type": "users", "user_ids": []}

        manager._send_and_wait = AsyncMock(side_effect=response)
        await manager._sync_account(runtime)
        assert runtime.synced is True
        assert runtime.sync_step == "complete"
        await runtime.history_task
        history_calls = [call for call in manager._send_and_wait.await_args_list
                         if call.args[1]["@type"] == "getChatHistory"]
        assert [call.args[1]["chat_id"] for call in history_calls] == [-42, -43]
        failed_patch = bus.patch_chat.await_args_list[0].args[2]
        assert "history_cursor" not in failed_patch
        assert failed_patch["history_complete"] is False
        assert failed_patch["history_synced_at"]
        assert failed_patch["history_note"]
        assert bus.patch_chat.await_args_list[1].args[2]["history_complete"] is True
        assert runtime.synced is True
        assert runtime.sync_step == "complete"
        assert runtime.last_history_pass > 0
    asyncio.run(run())


def test_history_flood_wait_aborts_pass_and_retains_checkpoint():
    async def run():
        manager, runtime, bus = setup()
        bus.list_history_chats.return_value = [
            {"chat_id": "-42", "history_cursor": 2 << 20},
            {"chat_id": "-43", "history_cursor": 2 << 20},
        ]
        manager._send_and_wait = AsyncMock(return_value={
            "@type": "error", "code": 429, "message": "FLOOD_WAIT_60",
        })
        await manager._backfill_history(runtime)
        assert manager._send_and_wait.await_count == 1
        assert manager._send_and_wait.await_args.kwargs["retry_flood"] is False
        assert manager._cooldowns["account-a"] > time.time() + 59
        patch = bus.patch_chat.await_args.args[2]
        assert "history_cursor" not in patch
        assert patch["history_complete"] is False
        assert "cooldown" in patch["history_note"]
    asyncio.run(run())


def test_history_restart_failure_keeps_metadata_ready_and_retries_later():
    async def run():
        manager, runtime, bus = setup()
        bus.restart_history.side_effect = [RuntimeError("history table unavailable"), None]
        bus.count_entities.return_value = {}
        bus.list_history_chats.return_value = []

        async def response(_runtime, request, **kwargs):
            if request["@type"] == "getMe":
                return {"@type": "user", "id": 12}
            if request["@type"] == "loadChats":
                return {"@type": "error", "code": 404}
            return {"@type": "users", "user_ids": []}

        manager._send_and_wait = AsyncMock(side_effect=response)
        await manager._sync_account(runtime)
        assert runtime.synced is True
        assert runtime.sync_step == "complete"
        await runtime.history_task
        assert runtime.history_restarted is False
        await manager._continue_history(runtime)
        assert runtime.history_restarted is True
        assert bus.restart_history.await_count == 2
    asyncio.run(run())


def test_default_read_flood_wait_delays_native_retry_until_deadline():
    async def run():
        manager, runtime, _ = setup()
        sends = []

        def reply(request):
            sends.append(time.monotonic())
            # Resolve the correlation future directly to exercise the read
            # helper independently of the pump's duplicate cooldown guard.
            future = manager._pending_requests[request["@extra"]]
            future.set_result({"@type": "error", "message": "FLOOD_WAIT_1"}
                              if len(sends) == 1 else {"@type": "user", "id": 12})

        runtime.client.send = reply
        result = await manager._send_and_wait(runtime, {"@type": "getMe"})
        assert result["@type"] == "user"
        assert len(sends) == 2
        assert sends[1] - sends[0] >= 0.99
        assert runtime.paused_until == manager._cooldowns["account-a"]
    asyncio.run(run())


def test_edit_during_refresh_fetches_a_second_atomic_current_revision():
    async def run():
        manager, runtime, bus = setup()
        fetching = asyncio.Event()
        release = asyncio.Event()
        reads = 0

        async def snapshot(_runtime, request):
            nonlocal reads
            reads += 1
            if reads == 1:
                fetching.set()
                await release.wait()
            return message(edit_date=1700000000 + reads,
                           content={"@type": "messageText", "text": {"text": f"revision {reads}"}})

        manager._send_and_wait = AsyncMock(side_effect=snapshot)
        event = {"@type": "updateMessageContent", "@client_id": 1,
                 "chat_id": -42, "message_id": 100, "new_content": {}}
        await manager._process_event(event)
        await fetching.wait()
        # A later edit after fetch starts cannot be lost through coalescing.
        await manager._process_event({**event, "@type": "updateMessageEdited", "edit_date": 1700000002})
        release.set()
        await runtime.message_refresh_task
        await manager._wait_inbox_persisted()
        assert reads == 2
        assert [call.args[0][0]["text"] for call in bus.upsert_messages.await_args_list] == [
            "revision 1", "revision 2",
        ]
        assert bus.upsert_messages.await_args.args[0][0]["edited_at"] == "2023-11-14T22:13:22+00:00"
        bus.patch_message.assert_not_awaited()
    asyncio.run(run())


def test_first_server_message_marks_history_complete_but_local_id_stays_pending():
    async def run():
        manager, runtime, bus = setup()
        bus.list_history_chats.return_value = [{"chat_id": "-42", "history_cursor": 1 << 20}]
        manager._send_and_wait = AsyncMock()
        await manager._backfill_history(runtime)
        manager._send_and_wait.assert_not_awaited()
        assert bus.patch_chat.await_args.args[2]["history_complete"] is True
        assert bus.patch_chat.await_args.args[2]["history_note"] is None
    asyncio.run(run())


def test_chat_membership_survives_main_to_archive_move_and_hides_only_after_final_removal():
    async def run():
        manager, runtime, bus = setup()
        await manager._process_event({"@type": "updateNewChat", "@client_id": 1,
                                      "chat": {"id": -42, "title": "Team", "positions": [
                                          {"list": {"@type": "chatListMain"}, "order": 100},
                                      ]}})
        await manager._wait_inbox_persisted()
        assert bus.upsert_chats.await_args.args[0][0]["is_in_main"] is True
        position = {"@type": "updateChatPosition", "@client_id": 1, "chat_id": -42}
        await manager._process_event({**position, "position": {
            "list": {"@type": "chatListMain"}, "order": 0,
        }})
        await manager._wait_inbox_persisted()
        await manager._process_event({**position, "position": {
            "list": {"@type": "chatListArchive"}, "order": 100,
        }})
        await manager._wait_inbox_persisted()
        assert bus.upsert_chats.await_args.args[0][0] == {
            "account_id": "account-a", "chat_id": "-42",
            "is_in_main": False, "is_in_archive": True,
            "is_visible": True, "is_archived": True,
        }
        await manager._process_event({**position, "position": {
            "list": {"@type": "chatListArchive"}, "order": 0,
        }})
        await manager._wait_inbox_persisted()
        assert bus.upsert_chats.await_args.args[0][0]["is_visible"] is False
        # Only membership flags change; durable summary/messages are retained.
        assert all("title" not in call.args[0][0] for call in bus.upsert_chats.await_args_list[1:])
    asyncio.run(run())


def test_unknown_chat_membership_does_not_prematurely_hide_chat():
    async def run():
        manager, runtime, bus = setup()
        bus.get_chat_membership.return_value = None
        await manager._process_event({"@type": "updateChatPosition", "@client_id": 1,
                                      "chat_id": -42, "position": {
                                          "list": {"@type": "chatListMain"}, "order": 0,
                                      }})
        await manager._wait_inbox_persisted()
        assert bus.upsert_chats.await_args.args[0][0] == {"account_id": "account-a", "chat_id": "-42", "is_in_main": False}
        assert "is_visible" not in bus.upsert_chats.await_args.args[0][0]
        archive = sync.normalize_inbox_chat({"id": -42, "positions": [
            {"list": {"@type": "chatListArchive"}, "order": 100},
        ]})
        assert archive["is_in_main"] is False
        assert archive["is_in_archive"] is True
        assert archive["is_visible"] is True
    asyncio.run(run())


def test_contact_snapshot_prunes_only_verified_members_after_success():
    async def run():
        manager, runtime, bus = setup()
        bus.count_entities.return_value = {}
        bus.list_history_chats.return_value = []

        async def response(_runtime, request, **kwargs):
            kind = request["@type"]
            if kind == "getMe":
                return {"@type": "user", "id": 12}
            if kind == "loadChats":
                return {"@type": "error", "code": 404}
            if kind == "getContacts":
                return {"@type": "users", "user_ids": [1, 2]}
            return {"@type": "user", "id": request["user_id"], "is_contact": request["user_id"] == 1}

        manager._send_and_wait = AsyncMock(side_effect=response)
        await manager._sync_account_once(runtime)
        bus.prune_contacts.assert_awaited_once_with("account-a", {"1"})
        contacts = [row for call in bus.upsert_entities.await_args_list for row in call.args[0]]
        assert [(row["kind"], row["tg_id"]) for row in contacts] == [("contact", "1")]
        bus.prune_contacts.reset_mock()
        manager._send_and_wait = AsyncMock(side_effect=[
            {"@type": "user", "id": 12}, {"@type": "error", "code": 404},
            {"@type": "error", "code": 404}, {"@type": "users", "user_ids": [1]}, None,
        ])
        try:
            await manager._sync_account_once(runtime)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Partial contact fetch was accepted")
        bus.prune_contacts.assert_not_awaited()
    asyncio.run(run())


def test_realtime_removed_contact_deletes_stale_contact_classification():
    async def run():
        manager, runtime, bus = setup()
        await manager._process_event({"@type": "updateUser", "@client_id": 1,
                                      "user": {"@type": "user", "id": 12, "is_contact": False,
                                               "is_mutual_contact": False}})
        await manager._wait_inbox_persisted()
        bus.delete_contact.assert_awaited_once_with("account-a", "12")
        assert bus.upsert_entities.await_args.args[0][0]["kind"] == "user"
    asyncio.run(run())


def test_contact_changes_during_snapshot_are_reconciled_before_prune():
    async def run():
        manager, runtime, bus = setup()

        async def response(_runtime, request, **kwargs):
            if request["@type"] == "getContacts":
                return {"@type": "users", "user_ids": [1]}
            # Realtime membership updates race the older getUser snapshot.
            for uid, is_contact in ((1, False), (2, True)):
                await manager._process_event({"@type": "updateUser", "@client_id": 1,
                                              "user": {"@type": "user", "id": uid,
                                                       "is_contact": is_contact}})
            return {"@type": "user", "id": 1, "is_contact": True}

        manager._send_and_wait = AsyncMock(side_effect=response)
        await manager._sync_contacts(runtime)
        await manager._wait_inbox_persisted()
        bus.prune_contacts.assert_awaited_once_with("account-a", {"2"})
        bus.delete_contact.assert_awaited_once_with("account-a", "1")
        latest = {row["tg_id"]: row for call in bus.upsert_entities.await_args_list for row in call.args[0]}
        assert latest["1"]["kind"] == "user"
        assert latest["2"]["kind"] == "contact"
        assert runtime.contact_snapshot_active is False
        assert not runtime.contact_updates
    asyncio.run(run())


def test_contact_changes_during_database_prune_replay_without_blocking_receive_pump():
    async def run():
        manager, runtime, bus = setup()
        pruning = asyncio.Event()
        release = asyncio.Event()
        saved_contacts = {"1"}

        async def prune(_account_id, valid_ids):
            pruning.set()
            await release.wait()
            saved_contacts.intersection_update(valid_ids)

        async def upsert(rows):
            for row in rows:
                if row["kind"] == "contact":
                    saved_contacts.add(row["tg_id"])

        async def delete(_account_id, uid):
            saved_contacts.discard(uid)

        bus.prune_contacts.side_effect = prune
        bus.upsert_entities.side_effect = upsert
        bus.delete_contact.side_effect = delete
        manager._send_and_wait = AsyncMock(side_effect=[
            {"@type": "users", "user_ids": [1]},
            {"@type": "user", "id": 1, "is_contact": True},
        ])
        snapshot = asyncio.create_task(manager._sync_contacts(runtime))
        await pruning.wait()
        for uid, is_contact in ((1, False), (2, True)):
            await manager._process_event({"@type": "updateUser", "@client_id": 1,
                                          "user": {"@type": "user", "id": uid,
                                                   "is_contact": is_contact}})
        assert not manager._inbox_task.done()  # Native pump processes updates during slow prune.
        assert len(await manager._inbox.pending_payloads()) == 3
        release.set()
        await snapshot
        await manager._wait_inbox_persisted()
        assert saved_contacts == {"2"}
        assert runtime.contact_snapshot_active is False
        assert not runtime.contact_updates
    asyncio.run(run())


def test_message_refresh_task_is_cancelled_with_account_lifecycle():
    async def run():
        manager, runtime, _ = setup()
        # Direct pending task avoids native calls while exercising cancellation.
        runtime.message_refresh_task = asyncio.create_task(asyncio.Event().wait())
        runtime.message_refresh_pending[("-42", 100)] = None
        task = runtime.message_refresh_task
        await asyncio.sleep(0)
        await manager._cancel_sync(runtime)
        assert task.cancelled()
        assert runtime.message_refresh_task is None
        assert not runtime.message_refresh_pending
    asyncio.run(run())


def test_contact_pruning_paginates_and_deletes_only_stale_account_contact_rows():
    async def run():
        requests = []

        def handler(request):
            requests.append(request)
            assert request.url.params["account_id"] == "eq.account-a"
            assert request.url.params["kind"] == "eq.contact"
            if request.method == "GET":
                offset = int(request.url.params["offset"])
                rows = ([{"tg_id": str(i)} for i in range(1, 1001)]
                        if offset == 0 else [{"tg_id": "1001"}, {"tg_id": "1002"}])
                return httpx.Response(200, json=rows)
            return httpx.Response(204)

        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with patch("app.telegram.bus.httpx.AsyncClient",
                   side_effect=lambda **kwargs: client_type(transport=transport, **kwargs)):
            bus = SupabaseBus("https://supabase.test", "test-only-key")
            await bus.prune_contacts("account-a", {"2", "1002"})
        gets = [request for request in requests if request.method == "GET"]
        deletes = [request for request in requests if request.method == "DELETE"]
        assert [request.url.params["offset"] for request in gets] == ["0", "1000"]
        assert len(deletes) == 10
        deleted = {value for request in deletes
                   for value in request.url.params["tg_id"][4:-1].split(",")}
        assert "2" not in deleted and "1002" not in deleted
        assert deleted == {str(i) for i in range(1, 1003)} - {"2", "1002"}
    asyncio.run(run())


def test_message_provenance_tags_current_and_history_without_mutating_rows_or_sparse_patches():
    async def run():
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(201)

        raw = {"account_id": "account-a", **sync.normalize_message(message(edit_date=1700000001))}
        original_meta = dict(raw["meta"])
        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with patch("app.telegram.bus.httpx.AsyncClient",
                   side_effect=lambda **kwargs: client_type(transport=transport, **kwargs)):
            bus = SupabaseBus("https://supabase.test", "test-only-key")
            await bus.upsert_messages([raw])
            await bus.upsert_messages([raw], history=True)
            await bus.patch_message("account-a", "-42", 100, {"deleted": True})
        assert bodies[0][0]["meta"]["_mirror_source"] == "current"
        assert bodies[1][0]["meta"]["_mirror_source"] == "history"
        assert bodies[0][0]["edited_at"] == bodies[1][0]["edited_at"]
        assert raw["meta"] == original_meta
        assert "meta" not in bodies[2][0]
    asyncio.run(run())


def test_cached_chat_last_message_is_conservative_history_provenance():
    async def run():
        manager, runtime, bus = setup()
        await manager._process_event({"@type": "updateNewChat", "@client_id": 1,
                                      "chat": {"id": -42, "last_message": message(edit_date=1700000001)}})
        await manager._wait_inbox_persisted()
        assert bus.upsert_messages.await_args.kwargs["history"] is True
        await manager._process_event({"@type": "updateChatLastMessage", "@client_id": 1,
                                      "chat_id": -42, "last_message": message(edit_date=1700000001)})
        await manager._wait_inbox_persisted()
        assert bus.upsert_messages.await_args.kwargs["history"] is True
    asyncio.run(run())


def test_inventory_completes_before_slow_history_and_keeps_one_background_task():
    async def run():
        manager, runtime, bus = setup()
        entered = asyncio.Event()
        release = asyncio.Event()
        bus.count_entities.return_value = {"contact": 3}

        async def response(_runtime, request, **kwargs):
            if request["@type"] == "getMe":
                return {"@type": "user", "id": 12}
            if request["@type"] == "loadChats":
                return {"@type": "error", "code": 404}
            return {"@type": "users", "user_ids": []}

        async def history(_runtime):
            entered.set()
            await release.wait()

        manager._send_and_wait = AsyncMock(side_effect=response)
        manager._continue_history = AsyncMock(side_effect=history)
        await asyncio.wait_for(manager._sync_account(runtime), timeout=1)
        assert runtime.synced is True
        assert runtime.sync_step == "complete"
        assert bus.update_account.await_args.args[1]["entity_counts"] == {"contact": 3}
        task = runtime.history_task
        await entered.wait()
        assert task and not task.done()
        await manager._sync_account_once(runtime)
        assert runtime.history_task is task
        assert manager._continue_history.await_count == 1
        release.set()
        await task
    asyncio.run(run())


def test_recent_gap_has_independent_cursor_and_advances_watermark_only_after_complete():
    async def run():
        manager, runtime, bus = setup()
        shift = 1 << 20
        chat = {"chat_id": "-42", "recent_cursor": 0, "recent_boundary": 200 * shift,
                "recent_head": 0, "latest_synced_message_id": 200 * shift,
                "history_cursor": 100 * shift, "history_complete": False}
        manager._send_and_wait = AsyncMock(side_effect=[
            {"@type": "messages", "messages": [message(1000 * shift), message(900 * shift)]},
            {"@type": "messages", "messages": [message(800 * shift), message(199 * shift)]},
        ])
        await manager._backfill_recent_chat(runtime, chat)
        first = bus.patch_chat.await_args.args[2]
        assert first["recent_cursor"] == 900 * shift
        assert first["recent_head"] == 1000 * shift
        assert first["recent_complete"] is False
        assert "latest_synced_message_id" not in first
        assert "history_cursor" not in first and "history_complete" not in first
        await manager._backfill_recent_chat(runtime, {**chat, **first})
        bus.complete_recent_history.assert_awaited_once_with("account-a", "-42", 1000 * shift)
        assert bus.patch_chat.await_count == 1  # No nonatomic completion PATCH.
        assert manager._send_and_wait.await_args_list[1].args[1]["from_message_id"] == 899 * shift
        assert all(call.kwargs["history"] is True for call in bus.upsert_messages.await_args_list)
    asyncio.run(run())


def test_recent_startup_does_not_reset_or_starve_older_history_cursor():
    async def run():
        manager, runtime, bus = setup()
        shift = 1 << 20
        stored = {"chat_id": "-42", "history_cursor": 100 * shift, "history_complete": False,
                  "recent_cursor": 900 * shift, "recent_complete": False,
                  "recent_boundary": 200 * shift, "recent_head": 1000 * shift,
                  "latest_synced_message_id": 200 * shift}
        old_starts = []
        recent_starts = []

        async def prepare(_account_id):
            # Pending catch-up survives startup; the RPC queues a newer scan.
            stored["recent_restart_pending"] = True

        async def save(_account_id, _chat_id, changes):
            stored.update(changes)

        async def recent_rows(*_args):
            recent_starts.append(stored["recent_cursor"])
            return [dict(stored)]

        async def old_rows(*_args):
            old_starts.append(stored["history_cursor"])
            return [dict(stored)]

        async def response(_runtime, request, **kwargs):
            anchor = request["from_message_id"]
            ids = [1100 * shift, 1000 * shift] if anchor == 0 else [anchor]
            return {"@type": "messages", "messages": [message(mid) for mid in ids]}

        bus.restart_history.side_effect = prepare
        bus.patch_chat.side_effect = save
        bus.list_recent_history_chats.side_effect = recent_rows
        bus.list_history_chats.side_effect = old_rows
        manager._send_and_wait = AsyncMock(side_effect=response)
        await manager._continue_history(runtime)
        assert stored["history_cursor"] == 97 * shift
        # A second runtime represents another worker startup/offline gap.
        runtime.history_restarted = False
        await manager._continue_history(runtime)
        assert old_starts == [100 * shift, 97 * shift]
        assert stored["history_cursor"] == 94 * shift
        assert recent_starts == [900 * shift, 899 * shift]
        assert stored["recent_cursor"] == 898 * shift
        assert stored["recent_boundary"] == 200 * shift
        assert stored["latest_synced_message_id"] == 200 * shift
    asyncio.run(run())


def test_recent_preparation_uses_rpc_and_sends_no_old_cursor_reset():
    async def run():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(204)

        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with patch("app.telegram.bus.httpx.AsyncClient",
                   side_effect=lambda **kwargs: client_type(transport=transport, **kwargs)):
            await SupabaseBus("https://supabase.test", "test-only-key").restart_history("account-a")
        assert len(requests) == 1
        assert requests[0].url.path == "/rest/v1/rpc/open_tgate_prepare_recent_history"
        assert json.loads(requests[0].content) == {"account": "account-a"}
    asyncio.run(run())


def test_inventory_loads_partial_main_and_archive_pages_until_both_lists_are_exhausted():
    async def run():
        manager, runtime, bus = setup()
        counts = {"chatListMain": 0, "chatListArchive": 0}
        bus.count_entities.return_value = {"user": 10}

        async def response(_runtime, request, **kwargs):
            kind = request["@type"]
            if kind == "getMe":
                return {"@type": "user", "id": 12}
            if kind == "loadChats":
                list_name = request["chat_list"]["@type"]
                counts[list_name] += 1
                maximum = 6 if list_name == "chatListMain" else 4
                if counts[list_name] > maximum:
                    return {"@type": "error", "code": 404, "message": "Not Found"}
                # Each partial OK loads only one chat, far fewer than limit200.
                chat_id = counts[list_name] + (100 if list_name == "chatListArchive" else 0)
                await manager._process_event({"@type": "updateNewChat", "@client_id": 1,
                                              "chat": {"id": chat_id, "title": f"Chat {chat_id}",
                                                       "positions": [{"list": {"@type": list_name}, "order": 1}]}})
                return {"@type": "ok"}
            if kind == "getContacts":
                # Contacts and completion must wait for both exhaustion markers.
                assert counts == {"chatListMain": 7, "chatListArchive": 5}
                return {"@type": "users", "user_ids": []}
            raise AssertionError(f"Unexpected inventory request: {kind}")

        manager._send_and_wait = AsyncMock(side_effect=response)
        await manager._sync_account(runtime)
        assert counts == {"chatListMain": 7, "chatListArchive": 5}
        assert bus.upsert_chats.await_count == 10
        ids = {call.args[0][0]["chat_id"] for call in bus.upsert_chats.await_args_list}
        assert ids == {str(i) for i in range(1, 7)} | {str(i) for i in range(101, 105)}
        assert runtime.synced is True
        assert runtime.sync_step == "complete"
        assert bus.update_account.await_args.args[1]["entity_counts"] == {"user": 10}
        await manager._cancel_sync(runtime)
    asyncio.run(run())


def test_chat_list_error_is_not_inventory_exhaustion_or_completion():
    async def run():
        manager, runtime, bus = setup()
        manager._send_and_wait = AsyncMock(side_effect=[
            {"@type": "user", "id": 12},
            {"@type": "ok"},
            {"@type": "error", "code": 403, "message": "Chat access unavailable"},
        ])
        try:
            await manager._sync_account_once(runtime)
        except RuntimeError as error:
            assert str(error) == "chat_list_sync_incomplete"
        else:
            raise AssertionError("Non-exhaustion list error was accepted as completion")
        assert runtime.synced is False
        assert runtime.sync_step == "chats"
        assert runtime.history_task is None
        assert not any(call.args[1].get("sync_step") == "complete"
                       for call in bus.update_account.await_args_list)
        assert not any(call.args[1]["@type"] == "getContacts"
                       for call in manager._send_and_wait.await_args_list)
    asyncio.run(run())


def test_pending_recent_gap_survives_restart_then_catches_newer_top_gap():
    async def run():
        manager, runtime, bus = setup()
        shift = 1 << 20
        stored = {"chat_id": "-42", "recent_cursor": 500 * shift,
                  "recent_head": 1000 * shift, "recent_boundary": 200 * shift,
                  "latest_synced_message_id": 200 * shift, "recent_complete": False,
                  "recent_restart_pending": False, "history_complete": True}

        async def prepare(_account_id):
            stored["recent_restart_pending"] = True

        async def save(_account_id, _chat_id, changes):
            stored.update(changes)

        async def complete(_account_id, _chat_id, head):
            # Atomic 00500 completion contract; Main tests its actual SQL.
            stored["latest_synced_message_id"] = head
            if stored["recent_restart_pending"]:
                stored.update(recent_cursor=0, recent_head=0, recent_boundary=head,
                              recent_complete=False, recent_restart_pending=False)
            else:
                stored["recent_complete"] = True

        async def pending(*_args):
            return [dict(stored)] if not stored["recent_complete"] else []

        bus.restart_history.side_effect = prepare
        bus.patch_chat.side_effect = save
        bus.complete_recent_history.side_effect = complete
        bus.list_recent_history_chats.side_effect = pending
        bus.list_history_chats.return_value = []  # Oldest history was already complete.
        manager._send_and_wait = AsyncMock(side_effect=[
            {"@type": "messages", "messages": [message(400 * shift)]},
            {"@type": "messages", "messages": [message(199 * shift)]},
            {"@type": "messages", "messages": [message(1500 * shift), message(1000 * shift)]},
        ])
        await manager._continue_history(runtime)
        assert stored["recent_cursor"] == 400 * shift
        assert stored["latest_synced_message_id"] == 200 * shift
        # A further restart must continue the middle, not fetch newest again.
        runtime.history_restarted = False
        await manager._continue_history(runtime)
        assert stored["latest_synced_message_id"] == 1000 * shift
        assert stored["recent_cursor"] == 0
        assert stored["recent_boundary"] == 1000 * shift
        assert stored["recent_complete"] is False
        assert stored["recent_restart_pending"] is False
        await manager._continue_history(runtime)
        assert stored["latest_synced_message_id"] == 1500 * shift
        assert stored["recent_complete"] is True
        assert [call.args[1]["from_message_id"] for call in manager._send_and_wait.await_args_list] == [
            499 * shift, 399 * shift, 0,
        ]
        assert all("recent_complete" not in call.args[2] or call.args[2]["recent_complete"] is False
                   for call in bus.patch_chat.await_args_list)
    asyncio.run(run())


def test_deletion_journal_replays_complete_failed_batch_after_worker_restart(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        failed = asyncio.Event()

        async def unavailable(rows):
            assert {row["message_id"] for row in rows} == {100, 200, 300}
            assert all(row["deleted"] is True for row in rows)
            failed.set()
            raise httpx.HTTPStatusError("temporary outage", request=httpx.Request("POST", "https://supabase.test"),
                                        response=httpx.Response(503))

        bus.upsert_messages.side_effect = unavailable
        await manager._process_event({"@type": "updateDeleteMessages", "@client_id": 1,
                                      "chat_id": -42, "message_ids": [100, 200, 300], "is_permanent": True})
        await failed.wait()
        assert len(await manager._tombstones.pending()) == 3
        manager._tombstone_task.cancel()
        await asyncio.gather(manager._tombstone_task, return_exceptions=True)
        # A new manager/journal instance replays IDs, never message text/secrets.
        restarted, _, recovered_bus = setup(tmp_path)
        reopened = TombstoneJournal(manager._tombstones.path)
        assert {row[2] for row in await reopened.pending()} == {100, 200, 300}
        restarted._start_tombstone_drain()
        await restarted._tombstone_task
        recovered_bus.upsert_messages.assert_awaited_once()
        rows = recovered_bus.upsert_messages.await_args.args[0]
        assert {row["message_id"] for row in rows} == {100, 200, 300}
        assert all(set(row) == {"account_id", "chat_id", "message_id", "deleted"} for row in rows)
        assert await reopened.pending() == []
        bus.patch_message.assert_not_awaited()
    asyncio.run(run())


def test_deletion_journal_keeps_unacknowledged_ids_when_flusher_is_cancelled(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocked(_rows):
            entered.set()
            await release.wait()

        bus.upsert_messages.side_effect = blocked
        await manager._process_event({"@type": "updateDeleteMessages", "@client_id": 1,
                                      "chat_id": -42, "message_ids": [100, 200], "is_permanent": True})
        await entered.wait()
        # Login lifecycle cancellation doesn't clear independent deletion IDs.
        await manager._cancel_sync(runtime)
        assert len(await manager._tombstones.pending()) == 2
        manager._tombstone_task.cancel()
        await asyncio.gather(manager._tombstone_task, return_exceptions=True)
        assert len(await TombstoneJournal(manager._tombstones.path).pending()) == 2
    asyncio.run(run())


def test_recent_completion_uses_atomic_rpc_with_scope_and_head():
    async def run():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(204)

        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with patch("app.telegram.bus.httpx.AsyncClient",
                   side_effect=lambda **kwargs: client_type(transport=transport, **kwargs)):
            await SupabaseBus("https://supabase.test", "test-only-key").complete_recent_history("account-a", "-42", 100)
        assert len(requests) == 1
        assert requests[0].url.path == "/rest/v1/rpc/open_tgate_complete_recent_history"
        assert json.loads(requests[0].content) == {"account": "account-a", "chat": "-42", "head": 100}
    asyncio.run(run())


def test_bot_chat_with_empty_positions_remains_visible(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        runtime.ctx.mode = LoginMode.BOT
        await manager._process_event({"@type": "updateNewChat", "@client_id": 1,
                                      "chat": {"id": -42, "title": "Bot inbox", "positions": []}})
        await manager._inbox_task
        row = bus.upsert_chats.await_args.args[0][0]
        assert row["is_visible"] is True
        assert row["is_in_main"] is False and row["is_in_archive"] is False
        await manager._process_event({"@type": "updateChatPosition", "@client_id": 1,
                                      "chat_id": -42, "position": {
                                          "list": {"@type": "chatListMain"}, "order": 0,
                                      }})
        await manager._inbox_task
        assert bus.upsert_chats.await_args.args[0][0]["is_visible"] is True
    asyncio.run(run())


@pytest.mark.parametrize("mode", [LoginMode.BOT, LoginMode.PHONE])
def test_inbox_journal_replays_discoverable_chat_messages_and_newer_summary_fifo_after_restart(tmp_path, mode):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        runtime.ctx.mode = mode
        runtime.synced = True
        # Neither completed history lane revisits this chat during the outage.
        bus.list_history_chats.return_value = []
        bus.list_recent_history_chats.return_value = []
        runtime.ctx.code = "test-login-command-not-in-journal"
        failure = asyncio.Event()

        async def unavailable(_rows):
            failure.set()
            raise RuntimeError("PostgREST temporarily unavailable")

        bus.upsert_entities.side_effect = unavailable
        await manager._process_event({"@type": "updateNewChat", "@client_id": 1,
                                      "chat": {"id": -42, "title": "Old title", "positions": [{
                                                   "list": {"@type": "chatListMain"}, "order": 1,
                                               }],
                                               "last_message": message(100)}})
        await failure.wait()
        await manager._process_event({"@type": "updateNewMessage", "@client_id": 1,
                                      "message": message(101, content={
                                          "@type": "messageText", "text": {"text": "new message"},
                                      })})
        await manager._process_event({"@type": "updateChatTitle", "@client_id": 1,
                                      "chat_id": -42, "title": "Newest title"})
        await manager._process_event({"@type": "updateChatLastMessage", "@client_id": 1,
                                      "chat_id": -42, "last_message": message(102, content={
                                          "@type": "messageText", "text": {"text": "latest preview"},
                                      })})
        manager._inbox_task.cancel()
        await asyncio.gather(manager._inbox_task, return_exceptions=True)
        journal = InboxJournal(manager._tombstones.path)
        pending = await journal.pending_payloads()
        assert len(pending) == 4
        # Last-message body and preview share one atomic journal transaction.
        assert pending[-1][1]["messages"][0]["message_id"] == 102
        assert pending[-1][1]["chats"][0]["last_message"] == "latest preview"
        serialized = json.dumps(pending)
        assert "test-login-command-not-in-journal" not in serialized
        assert "@client_id" not in serialized and "authorization_state" not in serialized
        restarted, _, replay_bus = setup(tmp_path)
        chats = {}
        messages = {}

        async def save_chats(rows):
            for row in rows:
                chats.setdefault(row["chat_id"], {}).update(row)

        async def save_messages(rows, **kwargs):
            for row in rows:
                messages[(row["chat_id"], row["message_id"])] = row

        replay_bus.upsert_chats.side_effect = save_chats
        replay_bus.upsert_messages.side_effect = save_messages
        restarted._start_inbox_drain()
        await restarted._inbox_task
        assert chats["-42"]["title"] == "Newest title"
        assert chats["-42"]["last_message"] == "latest preview"
        assert chats["-42"]["is_visible"] is True
        assert {key[1] for key in messages} == {100, 101, 102}
        assert await journal.pending_payloads() == []
        restarted._send_and_wait = AsyncMock()
        await restarted._backfill_history(runtime)
        await restarted._backfill_recent_history(runtime)
        restarted._send_and_wait.assert_not_awaited()
        assert journal.path.stat().st_mode & 0o777 == 0o600
    asyncio.run(run())


def test_bot_message_alone_creates_visible_discoverable_summary(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        runtime.ctx.mode = LoginMode.BOT
        await manager._process_event({"@type": "updateNewMessage", "@client_id": 1,
                                      "message": message(101)})
        await manager._inbox_task
        chat = bus.upsert_chats.await_args.args[0][0]
        assert chat["account_id"] == "account-a" and chat["chat_id"] == "-42"
        assert chat["is_visible"] is True and chat["last_message"] == "hello"
        assert bus.upsert_messages.await_args.args[0][0]["message_id"] == 101
        assert bus.upsert_messages.await_args.kwargs["history"] is False
    asyncio.run(run())


@pytest.mark.parametrize("mode", [LoginMode.BOT, LoginMode.PHONE])
def test_fetched_edit_is_journaled_before_remote_write_and_survives_cancellation(tmp_path, mode):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        runtime.ctx.mode = mode
        writing = asyncio.Event()
        release = asyncio.Event()

        async def blocked(_rows, **kwargs):
            writing.set()
            await release.wait()

        bus.upsert_messages.side_effect = blocked
        manager._send_and_wait = AsyncMock(return_value=message(
            edit_date=1700000001, content={"@type": "messageText", "text": {"text": "edited bot message"}},
        ))
        await manager._process_event({"@type": "updateMessageEdited", "@client_id": 1,
                                      "chat_id": -42, "message_id": 100, "edit_date": 1700000001})
        await runtime.message_refresh_task
        await writing.wait()
        manager._inbox_task.cancel()
        await asyncio.gather(manager._inbox_task, return_exceptions=True)
        reopened = InboxJournal(manager._tombstones.path)
        payload = (await reopened.pending_payloads())[0][1]
        assert payload["messages"][0]["text"] == "edited bot message"
        assert payload["messages"][0]["edited_at"] == "2023-11-14T22:13:21+00:00"
        assert "history_messages" not in payload
        restarted, _, recovered_bus = setup(tmp_path)
        restarted._start_inbox_drain()
        await restarted._inbox_task
        assert recovered_bus.upsert_messages.await_args.args[0][0]["text"] == "edited bot message"
        assert await reopened.pending_payloads() == []
    asyncio.run(run())


def test_terminal_refresh_errors_do_not_retry_or_starve_other_message_ids():
    async def run():
        manager, runtime, bus = setup()
        manager._send_and_wait = AsyncMock(side_effect=[
            {"@type": "error", "code": 400}, {"@type": "error", "code": 401},
            {"@type": "error", "code": 403}, {"@type": "error", "code": 404}, message(105),
        ])
        for message_id in range(101, 106):
            await manager._schedule_message_refresh(runtime, "-42", message_id)
        await asyncio.wait_for(runtime.message_refresh_task, timeout=1)
        await manager._wait_inbox_persisted()
        assert manager._send_and_wait.await_count == 5
        assert not runtime.message_refresh_pending
        bus.upsert_messages.assert_awaited_once()
        assert bus.upsert_messages.await_args.args[0][0]["message_id"] == 105
    asyncio.run(run())


def test_personal_hidden_membership_survives_outage_replay_without_forced_visibility(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        failed = asyncio.Event()

        async def unavailable(_rows):
            failed.set()
            raise RuntimeError("temporary outage")

        bus.upsert_chats.side_effect = unavailable
        await manager._process_event({"@type": "updateNewChat", "@client_id": 1,
                                      "chat": {"id": -42, "title": "Left chat", "positions": []}})
        await failed.wait()
        await manager._process_event({"@type": "updateNewMessage", "@client_id": 1,
                                      "message": message(101)})
        await manager._process_event({"@type": "updateChatTitle", "@client_id": 1,
                                      "chat_id": -42, "title": "Latest title"})
        manager._inbox_task.cancel()
        await asyncio.gather(manager._inbox_task, return_exceptions=True)
        pending = await manager._inbox.pending_payloads()
        assert all(row.get("is_visible") is not True
                   for _, payload in pending for row in payload.get("chats", []))
        restarted, _, recovered_bus = setup(tmp_path)
        summary = {}

        async def persist(rows):
            for row in rows:
                summary.update(row)

        recovered_bus.upsert_chats.side_effect = persist
        restarted._start_inbox_drain()
        await restarted._inbox_task
        assert summary["is_visible"] is False
        assert summary["is_in_main"] is False and summary["is_in_archive"] is False
        assert summary["title"] == "Latest title" and summary["last_message"] == "hello"
        assert recovered_bus.upsert_messages.await_args.args[0][0]["message_id"] == 101
    asyncio.run(run())


def test_inventory_waits_for_remote_chat_persistence_before_counts_and_completion(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        writing, release = asyncio.Event(), asyncio.Event()

        async def persist(rows):
            writing.set()
            await release.wait()

        async def response(_runtime, request, **kwargs):
            if request["@type"] == "getMe":
                return {"@type": "user", "id": 12}
            if request["@type"] == "loadChats":
                return {"@type": "error", "code": 404}
            if request["@type"] == "getContacts":
                return {"@type": "users", "user_ids": []}
            raise AssertionError(request)

        bus.upsert_chats.side_effect = persist
        manager._send_and_wait = AsyncMock(side_effect=response)
        await manager._process_event({"@type": "updateNewChat", "@client_id": 1,
                                      "chat": {"id": -42, "title": "Team"}})
        await writing.wait()
        inventory = asyncio.create_task(manager._sync_account(runtime))
        # Observe the checkpoint read, rather than relying on scheduler timing.
        checking = asyncio.Event()
        original = manager._inbox.pending_through

        async def pending(checkpoint):
            result = await original(checkpoint)
            checking.set()
            return result

        manager._inbox.pending_through = pending
        await checking.wait()
        assert runtime.sync_step == "contacts" and runtime.synced is False
        bus.count_entities.assert_not_awaited()
        release.set()
        await inventory
        assert runtime.synced is True and runtime.sync_step == "complete"
        bus.count_entities.assert_awaited_once()
        await manager._cancel_sync(runtime)
    asyncio.run(run())


def test_inventory_checkpoint_does_not_wait_for_later_live_events(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        first_write, release_first = asyncio.Event(), asyncio.Event()
        second_write, release_second = asyncio.Event(), asyncio.Event()
        checkpoint_read = asyncio.Event()

        async def persist(rows):
            if rows[0]["title"] == "First":
                first_write.set()
                await release_first.wait()
            else:
                second_write.set()
                await release_second.wait()

        bus.upsert_chats.side_effect = persist
        await manager._queue_inbox({"chats": [{"account_id": "account-a", "chat_id": "-42", "title": "First"}]})
        await first_write.wait()
        original = manager._inbox.checkpoint

        async def checkpoint():
            result = await original()
            checkpoint_read.set()
            return result

        manager._inbox.checkpoint = checkpoint
        barrier = asyncio.create_task(manager._wait_inbox_persisted())
        await checkpoint_read.wait()
        await manager._queue_inbox({"chats": [{"account_id": "account-a", "chat_id": "-42", "title": "Later"}]})
        release_first.set()
        await second_write.wait()
        await asyncio.wait_for(barrier, 1)
        assert not manager._inbox_task.done()
        release_second.set()
        await manager._inbox_task
    asyncio.run(run())


def test_unknown_personal_position_intents_survive_lookup_outage_and_replay_in_order(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        failed = asyncio.Event()

        async def unavailable(*args):
            failed.set()
            raise RuntimeError("membership storage unavailable")

        bus.get_chat_membership.side_effect = unavailable
        event = {"@type": "updateChatPosition", "@client_id": 1, "chat_id": -42}
        await manager._process_event({**event, "position": {
            "list": {"@type": "chatListMain"}, "order": 0,
        }})
        await failed.wait()
        await manager._process_event({**event, "position": {
            "list": {"@type": "chatListArchive"}, "order": 100,
        }})
        manager._inbox_task.cancel()
        await asyncio.gather(manager._inbox_task, return_exceptions=True)
        pending = await manager._inbox.pending_payloads()
        assert len(pending) == 2
        assert pending[0][1]["chat_positions"][0]["enabled"] is False
        restarted, _, recovered_bus = setup(tmp_path)
        summary = {"is_in_main": True, "is_in_archive": False, "is_visible": True}

        async def stored(*args):
            return dict(summary)

        async def save(rows):
            summary.update(rows[0])

        recovered_bus.get_chat_membership.side_effect = stored
        recovered_bus.upsert_chats.side_effect = save
        restarted._start_inbox_drain()
        await restarted._inbox_task
        assert summary["is_in_main"] is False
        assert summary["is_in_archive"] is True and summary["is_visible"] is True
        assert await manager._inbox.pending_payloads() == []
    asyncio.run(run())


def test_large_healthy_inventory_checkpoint_has_no_total_or_per_payload_deadline(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        # More than 75 paced payloads exceed 30s at the production 0.4s pace.
        for index in range(80):
            await manager._inbox.enqueue_payload({"chats": [{
                "account_id": "account-a", "chat_id": str(index), "title": str(index),
            }]})
        manager._sync_delay = 0.001
        with patch("app.telegram.manager.asyncio.wait_for", side_effect=AssertionError("unexpected inventory deadline")):
            await manager._wait_inbox_persisted()
        assert bus.upsert_chats.await_count == 80
        assert await manager._inbox.pending_payloads() == []
        await manager._inbox_task
    asyncio.run(run())


def test_inventory_checkpoint_cancellation_retains_pending_rows(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        writing = asyncio.Event()

        async def blocked(rows):
            writing.set()
            await asyncio.Event().wait()

        bus.upsert_chats.side_effect = blocked
        await manager._queue_inbox({"chats": [{
            "account_id": "account-a", "chat_id": "-42", "title": "Pending",
        }]})
        await writing.wait()
        barrier = asyncio.create_task(manager._wait_inbox_persisted())
        barrier.cancel()
        result = await asyncio.gather(barrier, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert len(await manager._inbox.pending_payloads()) == 1
        assert not manager._inbox_task.done()
        manager._inbox_task.cancel()
        await asyncio.gather(manager._inbox_task, return_exceptions=True)
        restarted, _, replay_bus = setup(tmp_path)
        await restarted._wait_inbox_persisted()
        assert replay_bus.upsert_chats.await_args.args[0][0]["title"] == "Pending"
        await restarted._inbox_task
    asyncio.run(run())


@pytest.mark.parametrize("mode", [LoginMode.PHONE, LoginMode.BOT])
@pytest.mark.parametrize("is_contact", [True, False])
def test_live_contact_membership_is_durable_through_storage_outage_and_restart(tmp_path, mode, is_contact):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        runtime.ctx.mode = mode
        failed = asyncio.Event()

        async def unavailable(rows):
            failed.set()
            raise RuntimeError("contact storage offline")

        bus.upsert_entities.side_effect = unavailable
        await manager._process_event({"@type": "updateUser", "@client_id": 1,
                                      "user": {"@type": "user", "id": 12,
                                               "first_name": "Latest contact name", "is_contact": is_contact}})
        await failed.wait()
        manager._inbox_task.cancel()
        await asyncio.gather(manager._inbox_task, return_exceptions=True)
        restarted, _, recovered_bus = setup(tmp_path)
        contacts = set() if is_contact else {"12"}

        async def upsert(rows):
            for row in rows:
                if row["kind"] == "contact":
                    contacts.add(row["tg_id"])

        async def delete(account, uid):
            contacts.discard(uid)

        recovered_bus.upsert_entities.side_effect = upsert
        recovered_bus.delete_contact.side_effect = delete
        await restarted._wait_inbox_persisted()
        await restarted._inbox_task
        row = recovered_bus.upsert_entities.await_args.args[0][0]
        assert row["title"] == "Latest contact name"
        assert contacts == ({"12"} if is_contact else set())
        assert await manager._inbox.pending_payloads() == []
    asyncio.run(run())


def test_authoritative_contact_snapshot_replays_after_older_delta_before_newer_delta(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        failed = asyncio.Event()

        async def unavailable(rows):
            failed.set()
            raise RuntimeError("contact storage offline")

        bus.upsert_entities.side_effect = unavailable
        await manager._process_event({"@type": "updateUser", "@client_id": 1,
                                      "user": {"@type": "user", "id": 1, "is_contact": True}})
        await failed.wait()
        manager._send_and_wait = AsyncMock(return_value={"@type": "users", "user_ids": []})
        await manager._sync_contacts(runtime)
        await manager._process_event({"@type": "updateUser", "@client_id": 1,
                                      "user": {"@type": "user", "id": 2, "is_contact": True}})
        manager._inbox_task.cancel()
        await asyncio.gather(manager._inbox_task, return_exceptions=True)
        pending = await manager._inbox.pending_payloads()
        assert len(pending) == 3
        assert pending[1][1]["contact_snapshots"] == [{"account_id": "account-a", "keep_ids": []}]
        bus.prune_contacts.assert_not_awaited()
        restarted, _, recovered_bus = setup(tmp_path)
        saved_contacts = {"stale"}
        actions = []

        async def save(rows):
            for row in rows:
                if row["kind"] == "contact":
                    saved_contacts.add(row["tg_id"])
                    actions.append(("add", row["tg_id"]))

        async def prune(account, valid):
            actions.append(("prune", set(valid)))
            saved_contacts.intersection_update(valid)

        recovered_bus.upsert_entities.side_effect = save
        recovered_bus.prune_contacts.side_effect = prune
        await restarted._wait_inbox_persisted()
        await restarted._inbox_task
        assert actions == [("add", "1"), ("prune", set()), ("add", "2")]
        assert saved_contacts == {"2"}  # Older pending addition cannot resurrect after prune.
        assert await manager._inbox.pending_payloads() == []
    asyncio.run(run())


def test_contact_delta_during_partial_snapshot_is_durable_without_authoritative_prune(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        bus.upsert_entities.side_effect = RuntimeError("offline")

        async def response(_runtime, request, **kwargs):
            if request["@type"] == "getContacts":
                return {"@type": "users", "user_ids": [1]}
            await manager._process_event({"@type": "updateUser", "@client_id": 1,
                                          "user": {"@type": "user", "id": 2, "is_contact": True}})
            return None

        manager._send_and_wait = AsyncMock(side_effect=response)
        with pytest.raises(RuntimeError, match="contact_sync_incomplete"):
            await manager._sync_contacts(runtime)
        manager._inbox_task.cancel()
        await asyncio.gather(manager._inbox_task, return_exceptions=True)
        pending = await manager._inbox.pending_payloads()
        assert pending and all("contact_snapshots" not in payload for _, payload in pending)
        assert runtime.contact_snapshot_active is False
        restarted, _, recovered_bus = setup(tmp_path)
        await restarted._wait_inbox_persisted()
        await restarted._inbox_task
        recovered_bus.prune_contacts.assert_not_awaited()
        assert recovered_bus.upsert_entities.await_args.args[0][0]["tg_id"] == "2"
        assert await manager._inbox.pending_payloads() == []
    asyncio.run(run())


def test_contact_delta_during_local_snapshot_commit_follows_snapshot_and_latest_fields_win(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        committing, release = asyncio.Event(), asyncio.Event()
        original = manager._inbox.enqueue_payload

        async def enqueue(payload):
            if payload.get("contact_snapshots"):
                committing.set()
                await release.wait()
            return await original(payload)

        manager._inbox.enqueue_payload = enqueue
        manager._send_and_wait = AsyncMock(side_effect=[
            {"@type": "users", "user_ids": [1]},
            {"@type": "user", "id": 1, "first_name": "Old snapshot name", "is_contact": True},
        ])
        # Keep replay stopped so the exact durable order can be inspected.
        with patch.object(manager, "_start_inbox_drain"):
            snapshot = asyncio.create_task(manager._sync_contacts(runtime))
            await committing.wait()
            delta = asyncio.create_task(manager._process_event({
                "@type": "updateUser", "@client_id": 1,
                "user": {"@type": "user", "id": 1, "first_name": "Newest name", "is_contact": False},
            }))
            await asyncio.sleep(0)
            assert not delta.done()  # Snapshot owns the local FIFO commit lock.
            release.set()
            await snapshot
            await delta
        pending = await manager._inbox.pending_payloads()
        assert pending[0][1]["contact_snapshots"][0]["keep_ids"] == ["1"]
        assert pending[0][1]["entities"][0]["title"] == "Old snapshot name"
        assert all(payload["entities"][0]["title"] == "Newest name" for _, payload in pending[1:])
        saved_rows, contacts = {}, set()

        async def save(rows):
            for row in rows:
                saved_rows[row["tg_id"]] = row
                if row["kind"] == "contact":
                    contacts.add(row["tg_id"])

        async def prune(account, keep):
            contacts.intersection_update(keep)

        async def delete(account, uid):
            contacts.discard(uid)

        bus.upsert_entities.side_effect = save
        bus.prune_contacts.side_effect = prune
        bus.delete_contact.side_effect = delete
        await manager._wait_inbox_persisted()
        await manager._inbox_task
        assert saved_rows["1"]["title"] == "Newest name"
        assert saved_rows["1"]["kind"] == "user" and contacts == set()
    asyncio.run(run())


@pytest.mark.parametrize("mode", [LoginMode.PHONE, LoginMode.BOT])
def test_refresh_intent_survives_runtime_cancellation_and_authorized_restart(tmp_path, mode):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        runtime.ctx.mode = mode
        fetching = asyncio.Event()

        async def unavailable(_runtime, request):
            fetching.set()
            await asyncio.Event().wait()

        manager._send_and_wait = AsyncMock(side_effect=unavailable)
        await manager._process_event({"@type": "updateMessageEdited", "@client_id": 1,
                                      "chat_id": -42, "message_id": 100, "edit_date": 1700000001})
        await fetching.wait()
        assert await manager._inbox.pending_refreshes("account-a") == [("-42", 100, 1)]
        await manager._cancel_sync(runtime)
        assert not runtime.message_refresh_pending
        assert await manager._inbox.pending_refreshes("account-a") == [("-42", 100, 1)]
        restarted, restored, replay_bus = setup(tmp_path)
        restored.ctx.mode = mode
        restored.synced = True  # Inventory already complete; only native readiness is needed.
        restarted._send_and_wait = AsyncMock(return_value=message(
            edit_date=1700000001, content={"@type": "messageText", "text": {"text": "Recovered edit"}},
        ))
        await restarted._handle_event({"@type": "updateAuthorizationState", "@client_id": 1,
                                       "authorization_state": {"@type": "authorizationStateReady"}})
        await restored.message_refresh_task
        await restarted._wait_inbox_persisted()
        assert replay_bus.upsert_messages.await_args.args[0][0]["text"] == "Recovered edit"
        assert await restarted._inbox.pending_refreshes("account-a") == []
        assert not restored.message_refresh_pending
        await restarted._inbox_task
    asyncio.run(run())


@pytest.mark.parametrize("first_terminal", [False, True])
def test_newer_edit_generation_survives_old_snapshot_or_terminal_ack_and_restart(tmp_path, first_terminal):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        first_fetch, release_first, second_fetch = asyncio.Event(), asyncio.Event(), asyncio.Event()
        reads = 0

        async def snapshot(_runtime, request):
            nonlocal reads
            reads += 1
            if reads == 1:
                first_fetch.set()
                await release_first.wait()
                if first_terminal:
                    return {"@type": "error", "code": 404}
                return message(edit_date=1700000001)
            second_fetch.set()
            await asyncio.Event().wait()

        manager._send_and_wait = AsyncMock(side_effect=snapshot)
        await manager._schedule_message_refresh(runtime, "-42", 100)
        await first_fetch.wait()
        await manager._schedule_message_refresh(runtime, "-42", 100)
        release_first.set()
        await second_fetch.wait()
        assert await manager._inbox.pending_refreshes("account-a") == [("-42", 100, 2)]
        await manager._reload_message_refreshes(runtime)
        assert not runtime.message_refresh_pending  # Poll does not duplicate the active generation.
        await manager._cancel_sync(runtime)
        if manager._inbox_task:
            await manager._inbox_task
        restarted, restored, recovered_bus = setup(tmp_path)
        restarted._send_and_wait = AsyncMock(return_value=message(
            edit_date=1700000002, content={"@type": "messageText", "text": {"text": "Newest edit"}},
        ))
        await restarted._reload_message_refreshes(restored)
        await restored.message_refresh_task
        await restarted._wait_inbox_persisted()
        assert recovered_bus.upsert_messages.await_args.args[0][0]["text"] == "Newest edit"
        assert await restarted._inbox.pending_refreshes("account-a") == []
        await restarted._inbox_task
    asyncio.run(run())


def test_refresh_acknowledgement_requires_durable_complete_snapshot_and_generation_is_never_reused(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        committing = asyncio.Event()
        original = manager._queue_inbox

        async def blocked(payload):
            committing.set()
            await asyncio.Event().wait()
            await original(payload)

        manager._queue_inbox = blocked
        manager._send_and_wait = AsyncMock(return_value=message(edit_date=1700000001))
        await manager._schedule_message_refresh(runtime, "-42", 100)
        await committing.wait()
        assert await manager._inbox.pending_refreshes("account-a") == [("-42", 100, 1)]
        assert await manager._inbox.pending_payloads() == []
        await manager._cancel_sync(runtime)
        # Explicit terminal acknowledgement clears pending but retains its generation.
        await manager._inbox.acknowledge_refresh("account-a", "-42", 100, 1)
        assert await manager._inbox.pending_refreshes("account-a") == []
        assert await manager._inbox.enqueue_refresh("account-a", "-42", 100) == 2
        await manager._inbox.acknowledge_refresh("account-a", "-42", 100, 1)
        assert await manager._inbox.pending_refreshes("account-a") == [("-42", 100, 2)]
        assert await manager._inbox.pending_refreshes("different-account") == []
    asyncio.run(run())


@pytest.mark.parametrize("error", [{"@type": "error", "code": 500},
                                    {"@type": "error", "code": 429, "message": "FLOOD_WAIT_10"}])
def test_transient_or_flood_refresh_errors_remain_durable(tmp_path, error):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        retrying = asyncio.Event()
        reads = 0

        async def read(_runtime, request):
            nonlocal reads
            reads += 1
            if reads == 1:
                return error
            retrying.set()
            await asyncio.Event().wait()

        manager._send_and_wait = AsyncMock(side_effect=read)
        await manager._schedule_message_refresh(runtime, "-42", 100)
        await retrying.wait()
        await manager._cancel_sync(runtime)
        assert await manager._inbox.pending_refreshes("account-a") == [("-42", 100, 1)]
        bus.upsert_messages.assert_not_awaited()
    asyncio.run(run())


@pytest.mark.parametrize("mode", [LoginMode.PHONE, LoginMode.BOT])
def test_live_bot_contact_projection_keeps_names_current_and_removal_retains_bot(tmp_path, mode):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        runtime.ctx.mode = mode
        stored = {("contact", "12"): {"title": "Old contact"}}

        async def save(rows):
            for row in rows:
                stored[(row["kind"], row["tg_id"])] = row

        async def delete(account, uid):
            stored.pop(("contact", uid), None)

        bus.upsert_entities.side_effect = save
        bus.delete_contact.side_effect = delete
        user = {"@type": "user", "id": 12, "type": {"@type": "userTypeBot"},
                "first_name": "New bot name", "username": "new_bot", "is_contact": True}
        await manager._process_event({"@type": "updateUser", "@client_id": 1, "user": user})
        await manager._wait_inbox_persisted()
        assert stored[("contact", "12")]["title"] == "New bot name"
        assert stored[("contact", "12")]["username"] == "new_bot"
        assert stored[("bot", "12")]["meta"]["is_bot"] is True
        await manager._process_event({"@type": "updateUser", "@client_id": 1,
                                      "user": {**user, "first_name": "Removed bot", "is_contact": False}})
        await manager._wait_inbox_persisted()
        assert ("contact", "12") not in stored
        assert stored[("bot", "12")]["title"] == "Removed bot"
        await manager._inbox_task
    asyncio.run(run())


def test_bot_contact_changed_during_snapshot_preserves_contact_projection_and_latest_details(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        user = {"@type": "user", "id": 12, "type": {"@type": "userTypeBot"},
                "first_name": "Newest bot", "username": "latest_bot", "is_contact": True}

        async def response(_runtime, request, **kwargs):
            if request["@type"] == "getContacts":
                return {"@type": "users", "user_ids": [12]}
            await manager._process_event({"@type": "updateUser", "@client_id": 1, "user": user})
            return {**user, "first_name": "Old bot", "username": "old_bot"}

        manager._send_and_wait = AsyncMock(side_effect=response)
        await manager._sync_contacts(runtime)
        await manager._wait_inbox_persisted()
        bus.prune_contacts.assert_awaited_once_with("account-a", {"12"})
        latest = {row["kind"]: row for call in bus.upsert_entities.await_args_list for row in call.args[0]}
        assert set(latest) == {"bot", "contact"}
        assert all(row["title"] == "Newest bot" and row["username"] == "latest_bot" for row in latest.values())
        await manager._inbox_task
    asyncio.run(run())


def test_connection_state_and_activity_replay_fifo_after_outage_restart(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        failed = asyncio.Event()

        async def unavailable(account, patch):
            failed.set()
            raise RuntimeError("activity storage offline")

        bus.update_account.side_effect = unavailable
        await manager._process_event({"@type": "updateConnectionState", "@client_id": 1,
                                      "state": {"@type": "connectionStateConnecting"}})
        await failed.wait()
        runtime.last_activity_write = 0
        await manager._process_event({"@type": "updateNewMessage", "@client_id": 1, "message": message(101)})
        await manager._process_event({"@type": "updateConnectionState", "@client_id": 1,
                                      "state": {"@type": "connectionStateReady"}})
        manager._inbox_task.cancel()
        await asyncio.gather(manager._inbox_task, return_exceptions=True)
        pending = await manager._inbox.pending_payloads()
        observed = [activity for _, payload in pending for activity in payload.get("activity", [])]
        assert len(observed) == 3
        timestamps = [activity["last_activity_at"] for activity in observed]
        assert timestamps == sorted(timestamps)
        assert all(set(activity) <= {"account_id", "connection_state", "last_activity_at"} for activity in observed)
        restarted, _, recovered_bus = setup(tmp_path)
        state, applied = {}, []

        async def save(account, patch):
            assert account == "account-a"
            applied.append(patch)
            state.update(patch)

        recovered_bus.update_account.side_effect = save
        await restarted._wait_inbox_persisted()
        await restarted._inbox_task
        assert state["connection_state"] == "connectionStateReady"
        assert state["last_activity_at"] == timestamps[-1]
        assert [patch["last_activity_at"] for patch in applied] == timestamps
        assert await manager._inbox.pending_payloads() == []
    asyncio.run(run())


def test_unknown_last_message_reopens_completed_recent_lane_and_known_transition_reconciles_again(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        shift = 1 << 20
        runtime.synced = True
        runtime.history_restarted = True
        requested = False

        async def request(account, chat):
            nonlocal requested
            assert (account, chat) == ("account-a", "-42")
            requested = True

        async def recent(account, limit):
            if requested:
                return [{"chat_id": "-42", "recent_cursor": 0, "recent_head": 0,
                         "recent_boundary": 100 * shift}]
            return []  # Both lanes were complete before TDLib lost last-message state.

        bus.request_recent_history.side_effect = request
        bus.list_recent_history_chats.side_effect = recent
        bus.list_history_chats.return_value = []
        event = {"@type": "updateChatLastMessage", "@client_id": 1, "chat_id": -42, "last_message": None}
        await manager._process_event(event)
        await manager._wait_inbox_persisted()
        bus.request_recent_history.assert_awaited_once_with("account-a", "-42")
        await manager._process_event(event)
        await manager._wait_inbox_persisted()
        assert bus.request_recent_history.await_count == 1  # Repeated unknowns do not reset active progress.
        manager._send_and_wait = AsyncMock(return_value={"@type": "messages", "messages": [
            message(120 * shift), message(110 * shift), message(100 * shift),
        ]})
        await manager._backfill_recent_history(runtime)
        assert {row["message_id"] for row in bus.upsert_messages.await_args.args[0]} == {120 * shift, 110 * shift, 100 * shift}
        bus.complete_recent_history.assert_awaited_once_with("account-a", "-42", 120 * shift)
        bus.restart_history.assert_not_awaited()
        await manager._process_event({**event, "last_message": message(130 * shift)})
        await manager._wait_inbox_persisted()
        assert bus.request_recent_history.await_count == 2
        assert "-42" not in runtime.unknown_last_message
        await manager._process_event({**event, "last_message": message(140 * shift)})
        await manager._wait_inbox_persisted()
        assert bus.request_recent_history.await_count == 2
        await manager._inbox_task
    asyncio.run(run())


def test_targeted_recent_request_replays_after_storage_failure_and_restart(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        failed = asyncio.Event()

        async def unavailable(account, chat):
            failed.set()
            raise RuntimeError("recent request storage offline")

        bus.request_recent_history.side_effect = unavailable
        await manager._process_event({"@type": "updateChatLastMessage", "@client_id": 1,
                                      "chat_id": -42, "last_message": None})
        await failed.wait()
        manager._inbox_task.cancel()
        await asyncio.gather(manager._inbox_task, return_exceptions=True)
        payload = (await manager._inbox.pending_payloads())[0][1]
        assert payload["recent_requests"] == [{"account_id": "account-a", "chat_id": "-42"}]
        restarted, _, recovered_bus = setup(tmp_path)
        await restarted._wait_inbox_persisted()
        await restarted._inbox_task
        recovered_bus.request_recent_history.assert_awaited_once_with("account-a", "-42")
        assert recovered_bus.upsert_chats.await_args.args[0][0]["last_message"] == ""
        assert await manager._inbox.pending_payloads() == []
    asyncio.run(run())


def test_bot_unknown_last_message_never_requests_unsupported_history(tmp_path):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        runtime.ctx.mode = LoginMode.BOT
        for last in (None, message(101)):
            await manager._process_event({"@type": "updateChatLastMessage", "@client_id": 1,
                                          "chat_id": -42, "last_message": last})
        await manager._wait_inbox_persisted()
        bus.request_recent_history.assert_not_awaited()
        assert not runtime.unknown_last_message
        await manager._inbox_task
    asyncio.run(run())


def test_bus_targeted_recent_request_uses_atomic_account_and_chat_rpc():
    async def run():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(204)

        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        with patch("app.telegram.bus.httpx.AsyncClient",
                   side_effect=lambda **kwargs: client_type(transport=transport, **kwargs)):
            await SupabaseBus("https://supabase.test", "test-only-key").request_recent_history("account-a", "-42")
        assert len(requests) == 1
        assert requests[0].url.path == "/rest/v1/rpc/open_tgate_request_recent_history"
        assert json.loads(requests[0].content) == {"account": "account-a", "chat": "-42"}
    asyncio.run(run())


@pytest.mark.parametrize("known_event", ["updateChatLastMessage", "updateNewChat"])
def test_rehydrated_null_chat_remembers_unknown_interval_after_startup_catchup_completes(tmp_path, known_event):
    async def run():
        manager, runtime, bus = setup(tmp_path)
        await manager._process_event({"@type": "updateNewChat", "@client_id": 1,
                                      "chat": {"id": -42, "title": "Recovered chat", "last_message": None}})
        await manager._wait_inbox_persisted()
        assert runtime.unknown_last_message == {"-42"}
        bus.request_recent_history.assert_not_awaited()  # Startup already prepares its recent lane.
        runtime.synced = True
        runtime.history_restarted = True
        bus.list_recent_history_chats.return_value = []  # Startup caught up while last message remained unknown.
        event = {"@type": known_event, "@client_id": 1}
        if known_event == "updateChatLastMessage":
            event.update(chat_id=-42, last_message=message(101))
        else:
            event["chat"] = {"id": -42, "title": "Recovered chat", "last_message": message(101)}
        await manager._process_event(event)
        await manager._wait_inbox_persisted()
        bus.request_recent_history.assert_awaited_once_with("account-a", "-42")
        assert not runtime.unknown_last_message
        await manager._inbox_task
    asyncio.run(run())
