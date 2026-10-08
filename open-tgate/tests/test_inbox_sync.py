"""Offline regressions for inbox checkpoints and TDLib event semantics."""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import httpx

from app.config import Settings
from app.telegram import sync
from app.telegram.bus import SupabaseBus
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


def test_live_edit_events_coalesce_into_one_complete_message_revision():
    async def run():
        manager, runtime, bus = setup()
        manager._send_and_wait = AsyncMock(return_value=message(
            edit_date=1700000001,
            content={"@type": "messageText", "text": {"text": "edited"}},
        ))
        await manager._process_event({"@type": "updateMessageContent", "@client_id": 1,
                                      "chat_id": -42, "message_id": 100,
                                      "new_content": {"@type": "messageText", "text": {"text": "edited"}}})
        await manager._process_event({"@type": "updateMessageEdited", "@client_id": 1,
                                      "chat_id": -42, "message_id": 100, "edit_date": 1700000001})
        await runtime.message_refresh_task
        bus.patch_message.assert_not_awaited()
        manager._send_and_wait.assert_awaited_once()
        assert manager._send_and_wait.await_args.args[1]["@type"] == "getMessage"
        row = bus.upsert_messages.await_args.args[0][0]
        assert row["text"] == "edited"
        assert row["edited_at"] == "2023-11-14T22:13:21+00:00"
        assert row["account_id"] == "account-a"
        await manager._process_event({"@type": "updateChatIsMarkedAsUnread", "@client_id": 1,
                                      "chat_id": -42, "is_marked_as_unread": True})
        bus.patch_chat.assert_awaited_with("account-a", "-42", {"is_marked_unread": True})
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
        assert bus.upsert_chats.await_args.args[0][0]["is_in_main"] is True
        position = {"@type": "updateChatPosition", "@client_id": 1, "chat_id": -42}
        await manager._process_event({**position, "position": {
            "list": {"@type": "chatListMain"}, "order": 0,
        }})
        await manager._process_event({**position, "position": {
            "list": {"@type": "chatListArchive"}, "order": 100,
        }})
        assert bus.patch_chat.await_args.args[2] == {
            "is_in_main": False, "is_in_archive": True,
            "is_visible": True, "is_archived": True,
        }
        await manager._process_event({**position, "position": {
            "list": {"@type": "chatListArchive"}, "order": 0,
        }})
        assert bus.patch_chat.await_args.args[2]["is_visible"] is False
        # Only membership flags change; durable summary/messages are retained.
        assert all("title" not in call.args[2] for call in bus.patch_chat.await_args_list)
    asyncio.run(run())


def test_unknown_chat_membership_does_not_prematurely_hide_chat():
    async def run():
        manager, runtime, bus = setup()
        bus.get_chat_membership.return_value = None
        await manager._process_event({"@type": "updateChatPosition", "@client_id": 1,
                                      "chat_id": -42, "position": {
                                          "list": {"@type": "chatListMain"}, "order": 0,
                                      }})
        assert bus.patch_chat.await_args.args[2] == {"is_in_main": False}
        assert "is_visible" not in bus.patch_chat.await_args.args[2]
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
        bus.prune_contacts.assert_awaited_once_with("account-a", {"2"})
        bus.delete_contact.assert_awaited_once_with("account-a", "1")
        assert bus.upsert_entities.await_args.args[0][0]["tg_id"] == "2"
        assert bus.upsert_entities.await_args.args[0][0]["kind"] == "contact"
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
        assert not snapshot.done()  # Native pump processed updates despite slow DB.
        assert set(runtime.contact_updates) == {"1", "2"}
        release.set()
        await snapshot
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
