"""Multi-account TDLib runtime: drives login and read-only auto-sync.

The manager owns the single native ``td_receive`` pump (TDLib multiplexes every
client onto one global receive queue) and routes each event to the right
:class:`AccountRuntime` by ``@client_id``. Per account it feeds the pure
:mod:`~app.telegram.authflow` state machine, dispatches the emitted request, and
mirrors login status to Supabase via :class:`~app.telegram.bus.SupabaseBus`.

Ban-safety measures baked in here:

* **Update-driven, not polling** — we react to TDLib updates instead of hot
  looping requests.
* **FLOOD_WAIT is honoured** — a ``retry after N`` error parks that account for
  ``N`` seconds instead of retrying immediately.
* **Paced sync** — chats/contacts are hydrated with a small delay between calls
  and a bounded in-flight window, mimicking an official client's cold start.
* **Read-only** — no send path exists here; outbound stays gated by
  ``EXTERNAL_SEND_ENABLED`` elsewhere.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from dataclasses import dataclass, field

from ..config import Settings
from . import bus, sync
from .authflow import (
    LoginContext,
    LoginMode,
    LoginStatus,
    TdlibParameters,
    parse_flood_wait_seconds,
    plan,
)
from .tdjson import TdJsonClient, receive_any, set_log_verbosity

log = logging.getLogger("open-tgate.manager")


@dataclass
class AccountRuntime:
    """In-memory state for one connecting/connected Telegram account."""

    account_id: str
    client: TdJsonClient
    ctx: LoginContext
    status: LoginStatus = LoginStatus.INITIALIZING
    synced: bool = False
    sync_task: asyncio.Task | None = field(default=None, repr=False)
    deferred_state: dict | None = field(default=None, repr=False)
    # Epoch seconds until which this account is parked due to FLOOD_WAIT.
    paused_until: float = 0.0
    pending_chat_ids: list[int] = field(default_factory=list)
    # Collected entity rows during sync (flushed to Supabase in batches).
    _entity_buffer: list[dict] = field(default_factory=list)


class AccountManager:
    """Owns every :class:`AccountRuntime` and the shared receive pump."""

    def __init__(self, settings: Settings, message_bus: bus.SupabaseBus) -> None:
        self._settings = settings
        self._bus = message_bus
        self._runtimes: dict[str, AccountRuntime] = {}
        self._by_client: dict[int, AccountRuntime] = {}
        # Clients told to close but not yet confirmed closed. They still hold the
        # per-account TDLib database lock, so a replacement for the same account
        # must wait for closure before reopening that directory. Tracked by
        # account_id and (for reaping the Closed event in the pump) by client_id.
        self._closing: dict[str, TdJsonClient] = {}
        self._closing_ids: dict[int, str] = {}
        self._sync_delay = max(0.2, float(getattr(settings, "sync_pacing_seconds", 0.4)))
        self._entity_batch_size = 50
        self._pending_requests: dict[str, asyncio.Future[dict]] = {}
        self._request_owners: dict[str, AccountRuntime] = {}
        self._cooldowns: dict[str, float] = {}

    # ---- lifecycle -----------------------------------------------------

    async def _new_runtime(self, account_id: str, mode: LoginMode) -> AccountRuntime:
        existing = self._runtimes.pop(account_id, None)
        replacing = existing is not None
        if existing is not None:
            await self._cancel_sync(existing)
            # Move the outgoing client to the closing set and ask it to close.
            self._by_client.pop(existing.client.client_id, None)
            existing.client.send({"@type": "close"})
            self._closing[account_id] = existing.client
            self._closing_ids[existing.client.client_id] = account_id
        # If a client for this account is still closing (this call's, or a prior
        # attempt that timed out), the per-account TDLib database lock is still
        # held; wait for authorizationStateClosed before reopening the directory.
        closing = self._closing.get(account_id)
        if closing is not None:
            if not await self._drain_until_closed(closing):
                # Still not closed after the bounded wait: the tombstone stays so
                # the operator's retry keeps waiting for it instead of racing the
                # lock. Abort this attempt (the command is marked error).
                log.warning(
                    "account %s: previous TDLib client did not confirm close in time; "
                    "aborting re-login so it is retried after the old client closes", account_id,
                )
                raise RuntimeError("previous login session is still closing; please retry in a moment")
            # _drain_until_closed cleared the tombstone on observing Closed.
        # Enforce the account cap AFTER any drain, but not for a direct
        # replacement (it reuses the slot its own outgoing runtime just vacated).
        # A retry after a timed-out replacement has no live runtime to reuse and
        # another account may have taken the freed slot, so it must be checked.
        if not replacing and len(self._runtimes) >= self._settings.max_login_accounts:
            raise RuntimeError("maximum connected-account limit reached")
        client = TdJsonClient(self._settings.tdlib_library_path)
        params = TdlibParameters(
            api_id=int(self._settings.telegram_api_id or 0),
            database_directory=f"{self._settings.tdlib_database_directory}/{account_id}",
            files_directory=f"{self._settings.tdlib_files_directory}/{account_id}",
        )
        runtime = AccountRuntime(account_id=account_id, client=client, ctx=LoginContext(mode=mode, parameters=params))
        self._runtimes[account_id] = runtime
        self._by_client[client.client_id] = runtime
        # A TDLib client created via td_create_client_id() stays idle and emits
        # nothing until it receives its first request. Kick it so TDLib starts
        # the auth handshake (emits updateAuthorizationState) and the login flow
        # actually begins; without this the account never leaves 'pending'.
        self._kick(runtime)
        log.info("account %s: runtime created (mode=%s), kicked auth flow", account_id, mode.value)
        return runtime

    # ---- command handling ---------------------------------------------

    async def apply_command(self, command: dict) -> None:
        """Apply one queued login command to the relevant account runtime."""

        account_id = command.get("account_id")
        action = command.get("action")
        payload = command.get("payload") or {}
        if not account_id or action not in bus.VALID_ACTIONS:
            await self._bus.mark_command(command["id"], "error", "invalid_command")
            return

        try:
            if self._cooldowns.get(account_id, 0) > time.time():
                await self._bus.mark_command(command["id"], "error", "telegram_cooldown_active; wait before retrying")
                return
            if action == bus.ACTION_START_PHONE:
                runtime = await self._new_runtime(account_id, LoginMode.PHONE)
                runtime.ctx.phone_number = payload.get("phone_number")
                await self._bus.update_account(account_id, {"account_type": "user"})
            elif action == bus.ACTION_START_QR:
                await self._new_runtime(account_id, LoginMode.QR)
                await self._bus.update_account(account_id, {"account_type": "user"})
            elif action == bus.ACTION_START_BOT:
                runtime = await self._new_runtime(account_id, LoginMode.BOT)
                runtime.ctx.bot_token = payload.get("bot_token")
                await self._bus.update_account(account_id, {"account_type": "bot"})
            elif action == bus.ACTION_SUBMIT_CODE:
                runtime = self._runtimes.get(account_id)
                if runtime is None:
                    await self._bus.mark_command(command["id"], "error", "no active login; start the login again")
                    return
                runtime.ctx.code = payload.get("code")
                runtime.ctx.sent.discard("code")
                # TDLib is parked at authorizationStateWaitCode and emits no new
                # event on its own, so re-kick it to re-evaluate now that the
                # code is set (plan() will send checkAuthenticationCode).
                self._kick(runtime)
            elif action == bus.ACTION_SUBMIT_PASSWORD:
                runtime = self._runtimes.get(account_id)
                if runtime is None:
                    await self._bus.mark_command(command["id"], "error", "no active login; start the login again")
                    return
                runtime.ctx.password = payload.get("password")
                runtime.ctx.sent.discard("password")
                self._kick(runtime)  # re-evaluate authorizationStateWaitPassword
            elif action == bus.ACTION_RESEND_CODE:
                runtime = self._runtimes.get(account_id)
                if runtime is None:
                    await self._bus.mark_command(command["id"], "error", "no active login; start the login again")
                    return
                # Telegram only re-sends when TDLib is actually waiting for the
                # code; otherwise this is a harmless no-op the console shows as
                # "code already sent".
                if runtime.ctx.mode is LoginMode.BOT:
                    await self._bus.mark_command(command["id"], "error", "resend_code is only for phone logins")
                    return
                runtime.client.send({"@type": "resendAuthenticationCode"})
                await self._bus.update_account(account_id, {"needs": "code", "last_error": None})
            elif action == bus.ACTION_LOGOUT:
                runtime = self._runtimes.get(account_id)
                if runtime:
                    await self._cancel_sync(runtime)
                    runtime.client.send({"@type": "logOut"})
            elif action == bus.ACTION_REVOKE_BOT:
                # Revoking a bot is a logout plus an immediate account reset, so
                # the console leaves 'bot_authorized' even if the client was
                # already gone or the logOut never reaches a Closed event.
                runtime = self._runtimes.get(account_id)
                if runtime:
                    await self._cancel_sync(runtime)
                    runtime.client.send({"@type": "logOut"})
                await self._bus.update_account(
                    account_id,
                    {"status": "logged_out", "needs": None, "qr_link": None, "last_error": None},
                )
            await self._bus.mark_command(command["id"], "done")
        except Exception as exc:  # noqa: BLE001 - report and keep the loop alive
            log.exception("Command %s failed", command.get("id"))
            await self._bus.mark_command(command["id"], "error", str(exc)[:200])

    async def _cancel_sync(self, runtime: AccountRuntime) -> None:
        task = runtime.sync_task
        runtime.sync_task = None
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for key, owner in list(self._request_owners.items()):
            if owner is runtime:
                future = self._pending_requests.pop(key, None)
                self._request_owners.pop(key, None)
                if future and not future.done():
                    future.cancel()

    async def _wait_to_send(self, runtime: AccountRuntime) -> None:
        while self._cooldowns.get(runtime.account_id, 0) > time.time():
            await asyncio.sleep(min(1.0, self._cooldowns[runtime.account_id] - time.time()))
        if self._runtimes.get(runtime.account_id) is not runtime:
            raise asyncio.CancelledError

    # ---- event pump ----------------------------------------------------

    def _kick(self, runtime: AccountRuntime) -> None:
        """Ask TDLib for the current auth state so ``plan`` can act on it."""

        if self._cooldowns.get(runtime.account_id, 0) <= time.time():
            runtime.client.send({"@type": "getAuthorizationState"})

    async def _handle_event(self, event: dict) -> None:
        client_id = event.get("@client_id")
        runtime = self._by_client.get(client_id) if client_id is not None else None
        if runtime is None:
            return

        etype = event.get("@type")
        wait = parse_flood_wait_seconds(event)
        if wait is not None:
            runtime.paused_until = max(self._cooldowns.get(runtime.account_id, 0), time.time() + wait)
            self._cooldowns[runtime.account_id] = runtime.paused_until
        extra_id = event.get("@extra")
        if extra_id:
            future = self._pending_requests.pop(str(extra_id), None)
            self._request_owners.pop(str(extra_id), None)
            if future is not None and not future.done():
                future.set_result(event)
                return
        if etype == "updateConnectionState":
            cs = (event.get("state") or {}).get("@type")
            log.info("account %s: connection state %s", runtime.account_id, cs)
            return
        if etype == "error":
            wait = parse_flood_wait_seconds(event)
            if wait:
                runtime.paused_until = time.time() + wait
                log.warning("Account %s FLOOD_WAIT %ss", runtime.account_id, wait)
                await self._bus.update_account(runtime.account_id, {"status": "error", "needs": None, "last_error": f"Telegram cooldown: retry after {wait} seconds"})
            else:
                log.warning("Account %s TDLib error: %s", runtime.account_id, str(event.get("message"))[:200])
                await self._bus.update_account(runtime.account_id, {"status": "error", "needs": None, "last_error": str(event.get("message"))[:200]})
            return

        state = event.get("authorization_state") if etype == "updateAuthorizationState" else (
            event if etype and etype.startswith("authorizationState") else None
        )
        if state is None:
            return

        if state.get("@type") in ("authorizationStateLoggingOut", "authorizationStateClosing", "authorizationStateClosed"):
            await self._cancel_sync(runtime)
        elif self._cooldowns.get(runtime.account_id, 0) > time.time():
            runtime.deferred_state = state
            return
        decision = plan(state, runtime.ctx, api_hash=self._settings.telegram_api_hash)
        log.info(
            "account %s: auth state %s -> status=%s needs=%s%s",
            runtime.account_id, state.get("@type"), decision.status.value,
            decision.needs, (" error=" + decision.error) if decision.error else "",
        )
        runtime.status = decision.status
        if decision.request is not None:
            runtime.client.send(decision.request)
            # Clear each secret ONLY once the request that actually consumes it
            # is dispatched. An earlier auth state (e.g. WaitTdlibParameters,
            # which only sends setTdlibParameters) must not wipe a bot token /
            # code / password before TDLib asks for it — otherwise plan() sees
            # no token at WaitPhoneNumber and ends a bot login with
            # bot_token_required.
            req_type = decision.request.get("@type")
            if req_type == "checkAuthenticationBotToken":
                runtime.ctx.bot_token = None
            elif req_type == "checkAuthenticationCode":
                runtime.ctx.code = None
            elif req_type == "checkAuthenticationPassword":
                runtime.ctx.password = None
        if state.get("@type") == "authorizationStateClosed":
            # The client closed on its own (e.g. after logout). Drop the runtime
            # so a later login opens a fresh client instead of sending a second
            # close to an already-closed one and waiting forever for a Closed
            # event that will not come (which would strand a tombstone and make
            # every future login for this account fail with "still closing").
            self._runtimes.pop(runtime.account_id, None)
            self._by_client.pop(runtime.client.client_id, None)
            await self._bus.update_account(runtime.account_id, bus.account_patch_from_decision(decision))
            return

        await self._bus.update_account(
            runtime.account_id, self._account_patch(runtime, decision)
        )

        if decision.status is LoginStatus.AUTHORIZED and not runtime.synced:
            runtime.synced = True
            runtime.sync_task = asyncio.create_task(self._sync_account(runtime))

    @staticmethod
    def _account_patch(runtime: AccountRuntime, decision) -> dict:
        """Map a decision to an account patch, using the bot-specific vocabulary.

        Bot logins reach the same ``authorized`` TDLib state as personal ones,
        but the console and the DB distinguish them: a bot ends at
        ``bot_authorized`` (and shows ``validating_token`` while TDLib is
        checking the token). Keeping the two vocabularies in step is what makes
        the bot login/revoke UI reachable instead of always reading ``authorized``.
        """

        patch = bus.account_patch_from_decision(decision)
        if runtime.ctx.mode is LoginMode.BOT:
            if decision.status is LoginStatus.AUTHORIZED:
                patch["status"] = "bot_authorized"
            elif decision.status is LoginStatus.INITIALIZING and runtime.ctx.bot_token:
                patch["status"] = "validating_token"
        return patch

    # ---- comprehensive read-only entity sync ----------------------------

    async def _set_sync_step(self, runtime: AccountRuntime, step: str, extra: dict | None = None) -> None:
        """Update the sync_step column and any extra fields on the account row."""
        patch: dict = {"sync_step": step}
        if extra:
            patch.update(extra)
        try:
            await self._bus.update_account(runtime.account_id, patch)
        except Exception:  # noqa: BLE001
            log.exception("Failed to set sync_step=%s for %s", step, runtime.account_id)

    async def _flush_entities(self, runtime: AccountRuntime, rows: list[dict]) -> None:
        """Upsert entity rows to Supabase in batches."""
        for row in rows:
            row["account_id"] = runtime.account_id
        # Batch upsert.
        for i in range(0, len(rows), self._entity_batch_size):
            batch = rows[i : i + self._entity_batch_size]
            try:
                await self._bus.upsert_entities(batch)
            except Exception:  # noqa: BLE001
                log.exception("Entity upsert failed for %s (batch %d)", runtime.account_id, i)
                raise
            await asyncio.sleep(self._sync_delay * 0.5)

    async def _send_and_wait(self, runtime: AccountRuntime, request: dict, *, timeout: float = 30.0) -> dict | None:
        # Retry read-only sync requests after Telegram's deadline; never replay auth secrets.
        for _ in range(3):
            response = await self._send_once(runtime, dict(request), timeout=timeout)
            if response is None or parse_flood_wait_seconds(response) is None:
                return response
        return response

    async def _send_once(self, runtime: AccountRuntime, request: dict, *, timeout: float = 30.0) -> dict | None:
        """Send a TDLib request via the extra-request path and wait for a typed
        response on the ``@extra`` correlation id. Returns None on timeout.

        This is a blocking helper for one-shot requests during sync. The main
        receive pump continues to run; we just filter for our ``@extra`` marker.
        """
        import uuid as _uuid

        await self._wait_to_send(runtime)
        extra_id = f"sync-{_uuid.uuid4().hex[:12]}"
        request["@extra"] = extra_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._pending_requests[extra_id] = future
        self._request_owners[extra_id] = runtime
        runtime.client.send(request)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            self._pending_requests.pop(extra_id, None)
            log.warning("Sync request timed out for %s: %s", runtime.account_id, request.get("@type"))
            return None
        finally:
            self._pending_requests.pop(extra_id, None)
            self._request_owners.pop(extra_id, None)

    async def _ingest_update(self, runtime: AccountRuntime, event: dict) -> None:
        """Normalise and upsert a single chat/user update event."""
        etype = event.get("@type")
        rows: list[dict] = []
        if etype in ("user", "updateUser"):
            user_obj = event.get("user") if etype == "updateUser" else event
            if user_obj:
                rows.append(sync.normalize_user(user_obj))
        elif etype in ("chat", "updateNewChat"):
            chat_obj = event.get("chat") if etype == "updateNewChat" else event
            if chat_obj:
                rows.append(sync.normalize_chat(chat_obj))
        if rows:
            for row in rows:
                row["account_id"] = runtime.account_id
            try:
                await self._bus.upsert_entities(rows)
            except Exception:  # noqa: BLE001
                log.exception("Ingest update failed for %s", runtime.account_id)

    async def _sync_account(self, runtime: AccountRuntime) -> None:
        """Comprehensive read-only sync after login, paced to mimic a cold start.

        Sync steps:
        1. **profile** — ``getMe`` → extract name, username, masked phone
        2. **chats** — ``loadChats`` (main list) → entities from updateNewChat
        3. **archived** — ``loadChats`` (archive list) → entities
        4. **contacts** — ``getContacts`` → user entities
        5. **complete** — compute entity_counts, mark sync finished
        """

        account_id = runtime.account_id
        log.info("Starting comprehensive sync for account %s", account_id)

        try:
            # ── Step 1: Profile ─────────────────────────────────────────
            await self._set_sync_step(
                runtime,
                "profile",
                {"sync_started_at": datetime.now(UTC).isoformat()},
            )
            me = await self._send_and_wait(runtime, {"@type": "getMe"})
            if not me or me.get("@type") != "user":
                raise RuntimeError("profile_sync_incomplete")
            if me and me.get("@type") == "user":
                profile_patch = sync.extract_profile(me)
                await self._bus.update_account(account_id, profile_patch)
                log.info("Profile synced for %s: @%s", account_id, profile_patch.get("tg_username"))
            await asyncio.sleep(self._sync_delay)

            if runtime.ctx.mode is LoginMode.BOT:
                # Bots cannot call loadChats/getContacts. Mirror their own profile;
                # subsequent permitted updates are ingested by the shared pump.
                await self._flush_entities(runtime, [sync.normalize_user(me)])
                await self._set_sync_step(runtime, "complete", {
                    "sync_finished_at": datetime.now(UTC).isoformat(),
                    "entity_counts": await self._bus.count_entities(account_id),
                    "status": LoginStatus.AUTHORIZED.value,
                    "needs": None,
                })
                return

            # ── Step 2: Chats (main list) ───────────────────────────────
            await self._set_sync_step(runtime, "chats")
            # Load chats in pages. TDLib caches them locally; loadChats triggers
            # updateNewChat events for each chat. We request multiple pages with
            # a small sleep between to stay ban-safe.
            for page in range(5):  # Up to 5 × 200 = 1000 chats
                response = await self._send_and_wait(runtime, {
                    "@type": "loadChats",
                    "chat_list": {"@type": "chatListMain"},
                    "limit": 200,
                })
                if response and response.get("@type") == "error" and response.get("code") == 404:
                    break
                if not response or response.get("@type") != "ok":
                    raise RuntimeError("chat_list_sync_incomplete")
                await asyncio.sleep(self._sync_delay * 2)

                # The main receive pump owns the native queue and persists the
                # resulting updateNewChat events. A second receiver here could
                # steal correlated replies from another account.
                await asyncio.sleep(self._sync_delay)

            log.info("Main chat list loaded for %s", account_id)

            # ── Step 3: Archived chats ──────────────────────────────────
            await self._set_sync_step(runtime, "archived")
            for page in range(3):  # Up to 3 × 200 = 600 archived chats
                response = await self._send_and_wait(runtime, {
                    "@type": "loadChats",
                    "chat_list": {"@type": "chatListArchive"},
                    "limit": 200,
                })
                if response and response.get("@type") == "error" and response.get("code") == 404:
                    break
                if not response or response.get("@type") != "ok":
                    raise RuntimeError("chat_list_sync_incomplete")
                await asyncio.sleep(self._sync_delay * 2)

                await asyncio.sleep(self._sync_delay)

            log.info("Archived chat list loaded for %s", account_id)

            # ── Step 4: Contacts ────────────────────────────────────────
            await self._set_sync_step(runtime, "contacts")
            contacts_resp = await self._send_and_wait(runtime, {"@type": "getContacts"}, timeout=30.0)
            if not contacts_resp or contacts_resp.get("@type") != "users":
                raise RuntimeError("contact_sync_incomplete")
            if contacts_resp and contacts_resp.get("@type") == "users":
                user_ids = contacts_resp.get("user_ids") or []
                log.info("Contact list for %s: %d user IDs", account_id, len(user_ids))
                # Fetch full user objects in small batches, paced.
                entity_rows: list[dict] = []
                for i, uid in enumerate(user_ids):
                    user_resp = await self._send_and_wait(
                        runtime, {"@type": "getUser", "user_id": uid}, timeout=10.0
                    )
                    if not user_resp or user_resp.get("@type") != "user":
                        raise RuntimeError("contact_sync_incomplete")
                    if user_resp and user_resp.get("@type") == "user":
                        row = sync.normalize_user(user_resp)
                        # Force kind to "contact" since this came from getContacts.
                        row["kind"] = "contact"
                        entity_rows.append(row)
                    # Pace: brief sleep every few contacts.
                    if (i + 1) % 10 == 0:
                        await asyncio.sleep(self._sync_delay)
                if entity_rows:
                    await self._flush_entities(runtime, entity_rows)
                log.info("Contacts synced for %s: %d entities", account_id, len(entity_rows))
            await asyncio.sleep(self._sync_delay)

            # ── Step 5: Compute counts and mark complete ────────────────
            counts = await self._bus.count_entities(account_id)
            await self._set_sync_step(runtime, "complete", {
                "sync_finished_at": datetime.now(UTC).isoformat(),
                "entity_counts": counts,
                "status": LoginStatus.AUTHORIZED.value,
                "needs": None,
            })
            log.info("Sync complete for %s — counts: %s", account_id, counts)

        except Exception:  # noqa: BLE001
            log.exception("Sync failed for account %s", account_id)
            try:
                await self._set_sync_step(runtime, "error", {"last_error": "sync_failed"})
            except Exception:  # noqa: BLE001
                pass

    async def ingest_container(self, runtime: AccountRuntime, event: dict) -> None:
        """Normalise and persist an ``users``/``chats``/``chat``/``user`` event."""

        etype = event.get("@type")
        rows: list[dict] = []
        if etype == "user":
            rows.append(sync.normalize_user(event))
        elif etype == "chat":
            rows.append(sync.normalize_chat(event))
        elif etype == "updateNewChat":
            chat_obj = event.get("chat")
            if chat_obj:
                rows.append(sync.normalize_chat(chat_obj))
        elif etype == "updateUser":
            user_obj = event.get("user")
            if user_obj:
                rows.append(sync.normalize_user(user_obj))
        if rows:
            for row in rows:
                row["account_id"] = runtime.account_id
            await self._bus.upsert_entities(rows)

    # ---- top-level loop ------------------------------------------------

    async def _rehydrate_authorized(self) -> bool:
        """Reopen already-authorized accounts after a restart/redeploy.

        The TDLib session state persists on the mounted volume, so recreating a
        runtime (which kicks TDLib) restores the session — TDLib reaches
        authorizationStateReady and read-only sync resumes — without the
        operator re-logging in after every deployment.

        Returns ``True`` only when the listing succeeded AND every authorized
        account now has a runtime; ``False`` on a transient listing failure or
        if any individual account failed to open. The caller keeps retrying
        while this is ``False``, and accounts already reopened are skipped on
        the next pass (``account_id in self._runtimes``), so a transiently
        failed account is retried without disturbing the healthy ones.
        """

        try:
            accounts = await self._bus.list_authorized_accounts()
        except Exception:  # noqa: BLE001
            log.exception("Could not list authorized accounts to rehydrate (will retry)")
            return False
        complete = True
        for acc in accounts:
            account_id = acc.get("id")
            if not account_id or account_id in self._runtimes:
                continue
            mode = LoginMode.BOT if acc.get("account_type") == "bot" else LoginMode.PHONE
            try:
                await self._new_runtime(account_id, mode)
                log.info("account %s: rehydrated authorized session", account_id)
            except Exception:  # noqa: BLE001
                log.exception("Failed to rehydrate account %s (will retry)", account_id)
                complete = False
        return complete

    async def run(self) -> None:
        set_log_verbosity(self._settings.tdlib_library_path, level=1)
        # A transient Supabase failure here must not leave the worker running
        # forever with no persisted sessions, so keep retrying (once per poll
        # cycle) until the listing succeeds instead of giving up on first error.
        rehydrated = await self._rehydrate_authorized()
        poll = max(1, int(getattr(self._settings, "command_poll_seconds", 3)))
        last_poll = 0.0
        while True:
            now = time.time()
            if now - last_poll >= poll:
                last_poll = now
                if not rehydrated:
                    rehydrated = await self._rehydrate_authorized()
                try:
                    for command in await self._bus.claim_pending_commands():
                        await self.apply_command(command)
                except Exception:  # noqa: BLE001
                    log.exception("Command poll failed")

            for runtime in list(self._runtimes.values()):
                if runtime.deferred_state and self._cooldowns.get(runtime.account_id, 0) <= time.time():
                    state = runtime.deferred_state
                    try:
                        await self._handle_event({"@client_id": runtime.client.client_id,
                                                  "@type": "updateAuthorizationState", "authorization_state": state})
                        runtime.deferred_state = None
                    except Exception:  # noqa: BLE001
                        log.exception("Deferred authorization update failed; retrying later")
            # Pump the shared TDLib receive queue for a short slice.
            for _ in range(50):
                event = self._receive_any()
                if event is None:
                    break
                try:
                    await self._process_event(event)
                except Exception:  # noqa: BLE001
                    log.exception("Event handling failed")
            await asyncio.sleep(0.1)

    async def _process_event(self, event: dict) -> None:
        """Route one native event: auth/error handling plus entity ingest."""

        cid = event.get("@client_id")
        if cid is not None and cid in self._closing_ids:
            # A client we are closing: reap it once it confirms closed (so the
            # per-account directory is free to reopen), and drop everything else.
            if self._is_closed_event(event):
                account_id = self._closing_ids.pop(cid, None)
                if account_id is not None:
                    self._closing.pop(account_id, None)
                log.info("account %s: previous TDLib client confirmed closed", account_id)
            return
        await self._handle_event(event)
        runtime = self._by_client.get(cid)
        if runtime is not None and event.get("@type") in (
            "user", "chat", "updateNewChat", "updateUser",
        ):
            await self.ingest_container(runtime, event)

    @staticmethod
    def _is_closed_event(event: dict) -> bool:
        etype = event.get("@type")
        state = event.get("authorization_state") if etype == "updateAuthorizationState" else (
            event if etype and etype.startswith("authorizationState") else None
        )
        return state is not None and state.get("@type") == "authorizationStateClosed"

    def _receive_any(self) -> dict | None:
        """Read one event from the shared native queue (any client)."""

        # Keep pumping while any client is still closing, even with no live
        # runtimes, so a lingering close event is reaped and its tombstone freed.
        if not self._runtimes and not self._closing:
            return None
        return receive_any(self._settings.tdlib_library_path, timeout=0.5)

    async def _drain_until_closed(self, client: TdJsonClient, *, timeout: float = 10.0) -> bool:
        """Pump the shared queue until *client* reports ``authorizationStateClosed``.

        Used when replacing a runtime: the new client reuses the same per-account
        TDLib database/files directory, and TDLib holds a database lock until the
        old client finishes closing. Other clients' events are routed normally so
        live accounts keep progressing. Returns ``True`` once closed, ``False`` on
        timeout. Reads the native queue directly (``self._runtimes`` may be empty
        mid-replacement, which would make ``_receive_any`` short-circuit).
        """

        deadline = time.time() + timeout
        target = client.client_id
        while time.time() < deadline:
            # Already reaped by the pump (its Closed was seen outside this drain)?
            if target not in self._closing_ids:
                return True
            event = receive_any(self._settings.tdlib_library_path, timeout=0.5)
            if event is None:
                continue
            if event.get("@client_id") == target:
                if self._is_closed_event(event):
                    account_id = self._closing_ids.pop(target, None)
                    if account_id is not None:
                        self._closing.pop(account_id, None)
                    return True
                continue  # drop other events from the client we are closing
            try:
                await self._process_event(event)
            except Exception:  # noqa: BLE001
                log.exception("Event handling during close-drain failed")
        return False
