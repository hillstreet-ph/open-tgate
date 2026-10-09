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
import uuid
from datetime import UTC, datetime
from dataclasses import dataclass, field

from ..config import Settings
from . import bus, sync
from .authflow import (
    LoginContext,
    LoginMode,
    LoginStatus,
    TdlibParameters,
    operator_error_message,
    parse_flood_wait_seconds,
    plan,
)
from .tdjson import TdJsonClient, receive_any, set_log_verbosity
from .tombstones import InboxJournal, TombstoneJournal

log = logging.getLogger("open-tgate.manager")


class IdentityMismatch(RuntimeError):
    """The slot belongs to another Telegram identity and must never be rebound."""


@dataclass
class AccountRuntime:
    """In-memory state for one connecting/connected Telegram account."""

    account_id: str
    client: TdJsonClient
    ctx: LoginContext
    session_generation: str = field(default_factory=lambda: uuid.uuid4().hex)
    telegram_identity: str | None = None
    identity_verified: bool = False
    identity_rejected: bool = False
    status: LoginStatus = LoginStatus.INITIALIZING
    synced: bool = False
    sync_step: str | None = None
    sync_task: asyncio.Task | None = field(default=None, repr=False)
    history_task: asyncio.Task | None = field(default=None, repr=False)
    history_restarted: bool = False
    last_history_pass: float = 0.0
    last_activity_write: float = 0.0
    message_refresh_task: asyncio.Task | None = field(default=None, repr=False)
    # Ordered, coalesced IDs only; complete payloads are fetched by one worker.
    message_refresh_pending: dict[tuple[str, int], dict[str, int]] = field(default_factory=dict, repr=False)
    message_refresh_active: tuple[str, int, dict[str, int]] | None = field(default=None, repr=False)
    chat_membership: dict[str, set[str]] = field(default_factory=dict, repr=False)
    unknown_last_message: set[str] = field(default_factory=set, repr=False)
    contact_snapshot_active: bool = False
    contact_updates: dict[str, dict] = field(default_factory=dict, repr=False)
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
        self._sync_delay = max(
            0.2, float(getattr(settings, "sync_pacing_seconds", 0.4))
        )
        self._sync_retry_delays = (5.0, 15.0)
        self._entity_batch_size = 50
        self._pending_requests: dict[str, asyncio.Future[dict]] = {}
        self._request_owners: dict[str, AccountRuntime] = {}
        self._cooldowns: dict[str, float] = {}
        self._tombstones = TombstoneJournal(
            f"{settings.tdlib_database_directory}/open_tgate_delete_journal.sqlite3"
        )
        # Deletion persistence is independent from a logged-in runtime so logout
        # and account replacement cannot discard already-consumed deletions.
        self._tombstone_task: asyncio.Task | None = None
        self._inbox = InboxJournal(self._tombstones.path)
        self._inbox_task: asyncio.Task | None = None
        self._inbox_progress = asyncio.Event()
        self._inbox_enqueue_lock = asyncio.Lock()

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
                    "aborting re-login so it is retried after the old client closes",
                    account_id,
                )
                raise RuntimeError(
                    "previous login session is still closing; please retry in a moment"
                )
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
        runtime = AccountRuntime(
            account_id=account_id,
            client=client,
            ctx=LoginContext(mode=mode, parameters=params),
        )
        await self._inbox.open_session(account_id, runtime.session_generation)
        self._runtimes[account_id] = runtime
        self._by_client[client.client_id] = runtime
        # A TDLib client created via td_create_client_id() stays idle and emits
        # nothing until it receives its first request. Kick it so TDLib starts
        # the auth handshake (emits updateAuthorizationState) and the login flow
        # actually begins; without this the account never leaves 'pending'.
        self._kick(runtime)
        log.info(
            "account %s: runtime created (mode=%s), kicked auth flow",
            account_id,
            mode.value,
        )
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
                await self._bus.mark_command(
                    command["id"],
                    "error",
                    "telegram_cooldown_active; wait before retrying",
                )
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
                    await self._bus.mark_command(
                        command["id"], "error", "no active login; start the login again"
                    )
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
                    await self._bus.mark_command(
                        command["id"], "error", "no active login; start the login again"
                    )
                    return
                runtime.ctx.password = payload.get("password")
                runtime.ctx.sent.discard("password")
                self._kick(runtime)  # re-evaluate authorizationStateWaitPassword
            elif action == bus.ACTION_RESEND_CODE:
                runtime = self._runtimes.get(account_id)
                if runtime is None:
                    await self._bus.mark_command(
                        command["id"], "error", "no active login; start the login again"
                    )
                    return
                # Telegram only re-sends when TDLib is actually waiting for the
                # code; otherwise this is a harmless no-op the console shows as
                # "code already sent".
                if runtime.ctx.mode is LoginMode.BOT:
                    await self._bus.mark_command(
                        command["id"], "error", "resend_code is only for phone logins"
                    )
                    return
                runtime.client.send({"@type": "resendAuthenticationCode"})
                await self._bus.update_account(
                    account_id, {"needs": "code", "last_error": None}
                )
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
                    {
                        "status": "logged_out",
                        "needs": None,
                        "qr_link": None,
                        "last_error": None,
                    },
                )
            await self._bus.mark_command(command["id"], "done")
        except Exception as exc:  # noqa: BLE001 - report and keep the loop alive
            log.exception("Command %s failed", command.get("id"))
            await self._bus.mark_command(command["id"], "error", str(exc)[:200])

    async def _cancel_sync(self, runtime: AccountRuntime) -> None:
        tasks = (runtime.sync_task, runtime.history_task, runtime.message_refresh_task)
        runtime.sync_task = None
        runtime.history_task = None
        runtime.message_refresh_task = None
        runtime.message_refresh_pending.clear()
        for task in tasks:
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
            await asyncio.sleep(
                min(1.0, self._cooldowns[runtime.account_id] - time.time())
            )
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
            runtime.paused_until = max(
                self._cooldowns.get(runtime.account_id, 0), time.time() + wait
            )
            self._cooldowns[runtime.account_id] = runtime.paused_until
        extra_id = event.get("@extra")
        if extra_id:
            future = self._pending_requests.pop(str(extra_id), None)
            self._request_owners.pop(str(extra_id), None)
            if future is not None and not future.done():
                future.set_result(event)
            # Timed-out read replies belong to sync, never the login flow.
            # A late getChatHistory/loadChats 404 must not demote authorization.
            return
        if etype == "updateConnectionState":
            cs = (event.get("state") or {}).get("@type")
            log.info("account %s: connection state %s", runtime.account_id, cs)
            await self._queue_inbox({"activity": [{
                "account_id": runtime.account_id, "connection_state": cs,
                "last_activity_at": datetime.now(UTC).isoformat(),
            }]}, runtime)
            return
        if etype == "error":
            if runtime.status is LoginStatus.AUTHORIZED:
                # Request errors cannot revoke a ready native session. Auth state
                # transitions remain authoritative; FLOOD deadlines were retained above.
                log.warning("Ignoring uncorrelated TDLib error for ready account %s (code %s)",
                            runtime.account_id, event.get("code"))
                return
            wait = parse_flood_wait_seconds(event)
            if wait:
                runtime.paused_until = time.time() + wait
                log.warning("Account %s FLOOD_WAIT %ss", runtime.account_id, wait)
                await self._bus.update_account(
                    runtime.account_id,
                    {
                        "status": "error",
                        "needs": None,
                        "last_error": f"Telegram cooldown: retry after {wait} seconds",
                    },
                )
            else:
                raw = str(event.get("message"))[:200]
                friendly = operator_error_message(raw)[:300]
                log.warning("Account %s TDLib error: %s", runtime.account_id, raw)
                await self._bus.update_account(
                    runtime.account_id,
                    {"status": "error", "needs": None, "last_error": friendly},
                )
            return

        state = (
            event.get("authorization_state")
            if etype == "updateAuthorizationState"
            else (event if etype and etype.startswith("authorizationState") else None)
        )
        if state is None:
            return

        if state.get("@type") in (
            "authorizationStateLoggingOut",
            "authorizationStateClosing",
            "authorizationStateClosed",
        ):
            await self._cancel_sync(runtime)
        elif self._cooldowns.get(runtime.account_id, 0) > time.time():
            runtime.deferred_state = state
            return
        if (state.get("@type") == "authorizationStateWaitTdlibParameters"
                and (runtime.status is LoginStatus.AUTHORIZED
                     or ("parameters" in runtime.ctx.sent and runtime.status is not LoginStatus.INITIALIZING))):
            # A queued kick/query response may trail code/password/QR or ready.
            # Initialization cannot erase a later native authentication state.
            return
        decision = plan(state, runtime.ctx, api_hash=self._settings.telegram_api_hash)
        log.info(
            "account %s: auth state %s -> status=%s needs=%s%s",
            runtime.account_id,
            state.get("@type"),
            decision.status.value,
            decision.needs,
            (" error=" + decision.error) if decision.error else "",
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
            await self._bus.update_account(
                runtime.account_id, bus.account_patch_from_decision(decision)
            )
            return

        if decision.status is LoginStatus.AUTHORIZED and runtime.identity_rejected:
            # Native readiness cannot undo the terminal mirror identity mismatch.
            # Closing/logout transitions above still perform their normal cleanup.
            await self._set_sync_step(runtime, "error", {
                "status": "error", "last_error": "This slot belongs to another Telegram account. Add a new account slot for this identity.",
            })
            return
        await self._bus.update_account(
            runtime.account_id, self._account_patch(runtime, decision)
        )

        if decision.status is LoginStatus.AUTHORIZED and runtime.identity_verified:
            await self._reload_message_refreshes(runtime)

        if (
            decision.status is LoginStatus.AUTHORIZED
            and not runtime.synced
            and runtime.sync_step != "error"
        ):
            if runtime.sync_task is None or runtime.sync_task.done():
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

    async def _set_sync_step(
        self, runtime: AccountRuntime, step: str, extra: dict | None = None
    ) -> None:
        """Update the sync_step column and any extra fields on the account row."""
        runtime.sync_step = step
        patch: dict = {"sync_step": step}
        if extra:
            patch.update(extra)
        await self._bus.update_account(runtime.account_id, patch)

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
                log.exception(
                    "Entity upsert failed for %s (batch %d)", runtime.account_id, i
                )
                raise
            await asyncio.sleep(self._sync_delay * 0.5)

    async def _send_and_wait(
        self, runtime: AccountRuntime, request: dict, *, timeout: float = 30.0,
        retry_flood: bool = True,
    ) -> dict | None:
        # Retry read-only sync requests after Telegram's deadline; never replay auth secrets.
        for _ in range(3 if retry_flood else 1):
            response = await self._send_once(runtime, dict(request), timeout=timeout)
            wait = parse_flood_wait_seconds(response or {})
            if wait is None:
                return response
            # The receive pump normally sets this before resolving @extra, but
            # enforcing it here also protects alternate response adapters and
            # guarantees every optional retry passes through the deadline gate.
            runtime.paused_until = max(
                self._cooldowns.get(runtime.account_id, 0), time.time() + wait,
            )
            self._cooldowns[runtime.account_id] = runtime.paused_until
        return response

    async def _send_once(
        self, runtime: AccountRuntime, request: dict, *, timeout: float = 30.0
    ) -> dict | None:
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
            log.warning(
                "Sync request timed out for %s: %s",
                runtime.account_id,
                request.get("@type"),
            )
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

    async def _sleep_for_sync_retry(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def _sync_account(self, runtime: AccountRuntime) -> None:
        """Run sync with a small, cooldown-aware retry budget.

        A transient Supabase or TDLib read error must not permanently leave a
        logged-in account with ``synced=True`` and no entities. Retries are
        limited and paced; any Telegram FLOOD_WAIT deadline takes precedence
        over the local backoff. A failed final attempt stays visible and can be
        restarted by reconnecting the account.
        """

        for attempt in range(len(self._sync_retry_delays) + 1):
            if self._runtimes.get(runtime.account_id) is not runtime:
                return
            try:
                await self._sync_account_once(runtime)
                runtime.synced = True
                return
            except asyncio.CancelledError:
                raise
            except IdentityMismatch:
                runtime.identity_rejected = True
                await self._set_sync_step(runtime, "error", {
                    "status": "error", "last_error": "This slot belongs to another Telegram account. Add a new account slot for this identity.",
                })
                return
            except Exception:  # noqa: BLE001 - keep the worker alive and report the sync failure
                log.exception(
                    "Sync attempt %d failed for account %s",
                    attempt + 1,
                    runtime.account_id,
                )
                if self._runtimes.get(runtime.account_id) is not runtime:
                    return
                if attempt == len(self._sync_retry_delays):
                    runtime.synced = False
                    try:
                        await self._set_sync_step(
                            runtime,
                            "error",
                            {
                                "last_error": "Telegram data sync failed after 3 attempts. Reconnect to retry.",
                            },
                        )
                    except Exception:  # noqa: BLE001 - a database outage must not kill the receive loop
                        log.exception(
                            "Could not persist final sync failure for %s",
                            runtime.account_id,
                        )
                    return

                backoff = self._sync_retry_delays[attempt]
                cooldown = max(
                    0.0, self._cooldowns.get(runtime.account_id, 0.0) - time.time()
                )
                wait_seconds = max(backoff, cooldown)
                step = runtime.sync_step or "profile"
                try:
                    await self._set_sync_step(
                        runtime,
                        step,
                        {
                            "last_error": f"Sync paused after a temporary error; retrying automatically ({attempt + 1}/3).",
                        },
                    )
                except Exception:  # noqa: BLE001
                    log.exception(
                        "Could not persist sync retry status for %s", runtime.account_id
                    )
                await self._sleep_for_sync_retry(wait_seconds)

    async def _verify_identity(self, runtime: AccountRuntime, identity: str) -> None:
        if self._runtimes.get(runtime.account_id) is not runtime:
            raise asyncio.CancelledError
        if runtime.identity_verified:
            if runtime.telegram_identity != identity:
                raise IdentityMismatch
            return
        runtime.telegram_identity = identity
        if runtime.identity_rejected or not await self._inbox.note_identity(
            runtime.account_id, runtime.session_generation, identity,
        ):
            runtime.identity_rejected = True
            await self._inbox.reject_session(runtime.session_generation)
            raise IdentityMismatch
        if not await self._bus.bind_identity(runtime.account_id, int(identity)):
            runtime.identity_rejected = True
            await self._inbox.reject_session(runtime.session_generation)
            raise IdentityMismatch
        if self._runtimes.get(runtime.account_id) is not runtime:
            raise asyncio.CancelledError
        if not await self._inbox.approve_session(runtime.account_id, runtime.session_generation, identity):
            raise asyncio.CancelledError
        runtime.identity_verified = True
        self._start_inbox_drain()
        self._start_tombstone_drain()
        await self._reload_message_refreshes(runtime)

    async def _resume_identity_binding(self, runtime: AccountRuntime) -> None:
        if runtime.identity_verified or runtime.identity_rejected or not runtime.telegram_identity:
            return
        try:
            await self._verify_identity(runtime, runtime.telegram_identity)
            if not runtime.synced and (runtime.sync_task is None or runtime.sync_task.done()):
                runtime.sync_step = None
                runtime.sync_task = asyncio.create_task(self._sync_account(runtime))
        except IdentityMismatch:
            await self._set_sync_step(runtime, "error", {
                "status": "error", "last_error": "This slot belongs to another Telegram account. Add a new account slot for this identity.",
            })
        except Exception:  # noqa: BLE001 - poll retries only verified, non-secret identity metadata
            log.exception("Identity binding remains pending for %s", runtime.account_id)

    async def _sync_account_once(self, runtime: AccountRuntime) -> None:
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

        # ── Step 1: Profile ─────────────────────────────────────────
        await self._set_sync_step(
            runtime,
            "profile",
            {"sync_started_at": datetime.now(UTC).isoformat()},
        )
        me = await self._send_and_wait(runtime, {"@type": "getMe"})
        if not me or me.get("@type") != "user":
            raise RuntimeError("profile_sync_incomplete")
        await self._verify_identity(runtime, str(me["id"]))
        profile_patch = sync.extract_profile(me)
        await self._bus.update_account(account_id, profile_patch)
        log.info(
            "Profile synced for %s: @%s", account_id, profile_patch.get("tg_username")
        )
        await asyncio.sleep(self._sync_delay)

        if runtime.ctx.mode is LoginMode.BOT:
            # Bots cannot call loadChats/getContacts. Mirror their own profile;
            # subsequent permitted updates are ingested by the shared pump.
            await self._flush_entities(runtime, [sync.normalize_user(me)])
            await self._set_sync_step(
                runtime,
                "complete",
                {
                    "sync_finished_at": datetime.now(UTC).isoformat(),
                    "entity_counts": await self._bus.count_entities(account_id),
                    "status": "bot_authorized",
                    "needs": None,
                    "last_error": None,
                },
            )
            return

        # ── Step 2: Chats (main list) ───────────────────────────────
        await self._set_sync_step(runtime, "chats")
        # TDLib can load fewer chats than limit on any request. Continue its
        # persistent list cursor until the documented 404 exhaustion response;
        # a fixed request count cannot prove inventory is complete.
        while True:
            response = await self._send_and_wait(
                runtime,
                {
                    "@type": "loadChats",
                    "chat_list": {"@type": "chatListMain"},
                    "limit": 200,
                },
            )
            if (
                response
                and response.get("@type") == "error"
                and response.get("code") == 404
            ):
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
        while True:
            response = await self._send_and_wait(
                runtime,
                {
                    "@type": "loadChats",
                    "chat_list": {"@type": "chatListArchive"},
                    "limit": 200,
                },
            )
            if (
                response
                and response.get("@type") == "error"
                and response.get("code") == 404
            ):
                break
            if not response or response.get("@type") != "ok":
                raise RuntimeError("chat_list_sync_incomplete")
            await asyncio.sleep(self._sync_delay * 2)

            await asyncio.sleep(self._sync_delay)

        log.info("Archived chat list loaded for %s", account_id)

        await self._sync_contacts(runtime)

        # Inventory is remotely persisted before reporting counts/readiness.
        # This bounded checkpoint excludes later live events and never blocks the pump.
        await self._wait_inbox_persisted()

        # ── Step 5: Compute counts and mark complete ────────────────
        counts = await self._bus.count_entities(account_id)
        await self._set_sync_step(
            runtime,
            "complete",
            {
                "sync_finished_at": datetime.now(UTC).isoformat(),
                "entity_counts": counts,
                "status": LoginStatus.AUTHORIZED.value,
                "needs": None,
                "last_error": None,
            },
        )
        log.info("Sync complete for %s — counts: %s", account_id, counts)
        # Publish inventory readiness before any potentially slow history read.
        # The task is independent, cancellable, and guarded against duplicates.
        if runtime.history_task is None or runtime.history_task.done():
            runtime.history_task = asyncio.create_task(self._continue_history(runtime))

    async def _sync_contacts(self, runtime: AccountRuntime) -> None:
        account_id = runtime.account_id
        await self._set_sync_step(runtime, "contacts")
        runtime.contact_snapshot_active = True
        try:
            contacts_resp = await self._send_and_wait(
                runtime, {"@type": "getContacts"}, timeout=30.0
            )
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
                        # Membership may change after the ID snapshot; never
                        # reintroduce someone removed before getUser returned.
                        if user_resp.get("is_contact") or user_resp.get("is_mutual_contact"):
                            row["kind"] = "contact"
                            entity_rows.append(row)
                    # Pace: brief sleep every few contacts.
                    if (i + 1) % 10 == 0:
                        await asyncio.sleep(self._sync_delay)
                await self._queue_contact_snapshot(runtime, entity_rows)
                log.info(
                    "Contact snapshot queued for %s: %d fetched entities", account_id, len(entity_rows)
                )
            await asyncio.sleep(self._sync_delay)

        finally:
            # Reapply latest observed user states after snapshot persistence.
            # New events keep coalescing here; no lock blocks the receive pump.
            await self._replay_contact_updates(runtime)

    async def _persist_user(self, runtime: AccountRuntime, user: dict) -> None:
        row = {"account_id": runtime.account_id, **sync.normalize_user(user)}
        payload = {"entities": sync.user_projections(row)}
        if not user.get("is_contact") and not user.get("is_mutual_contact"):
            payload["removed_contacts"] = [(runtime.account_id, str(user["id"]))]
        await self._queue_inbox(payload, runtime)

    async def _queue_contact_snapshot(self, runtime: AccountRuntime, rows: list[dict]) -> None:
        """Capture latest membership and order authoritative prune with live deltas."""
        async with self._inbox_enqueue_lock:
            merged = {row["tg_id"]: row for row in rows}
            captured_updates = dict(runtime.contact_updates)
            for uid, user in captured_updates.items():
                merged[uid] = sync.normalize_user(user)
            entities = [{"account_id": runtime.account_id, **projection}
                        for row in merged.values() for projection in sync.user_projections(row)]
            keep_ids = sorted({row["tg_id"] for row in entities if row["kind"] == "contact"})
            await self._inbox.enqueue_payload(self._fenced_payload({
                "entities": entities,
                "contact_snapshots": [{"account_id": runtime.account_id, "keep_ids": keep_ids}],
            }, runtime))
            # Newer updates during the SQLite commit remain buffered and queue
            # after this snapshot; captured states are already represented here.
            for uid, user in captured_updates.items():
                if runtime.contact_updates.get(uid) is user:
                    runtime.contact_updates.pop(uid)
        self._start_inbox_drain()

    async def _replay_contact_updates(self, runtime: AccountRuntime) -> None:
        while runtime.contact_updates:
            uid = next(iter(runtime.contact_updates))
            user = runtime.contact_updates[uid]
            await self._persist_user(runtime, user)
            # A newer update for this ID arriving during persistence wins.
            if runtime.contact_updates.get(uid) is user:
                runtime.contact_updates.pop(uid)
        runtime.contact_snapshot_active = False

    async def ingest_container(self, runtime: AccountRuntime, event: dict) -> None:
        """Normalise and persist an ``users``/``chats``/``chat``/``user`` event."""

        etype = event.get("@type")
        if etype in ("user", "updateUser"):
            user = event if etype == "user" else event.get("user")
            if user:
                if runtime.contact_snapshot_active:
                    runtime.contact_updates[str(user["id"])] = user
                # Even buffered membership changes commit before native-event
                # acknowledgement, so a crash during getUser cannot lose them.
                await self._persist_user(runtime, user)
            return
        rows: list[dict] = []
        if etype == "chat":
            rows.append(sync.normalize_chat(event))
        elif etype == "updateNewChat" and event.get("chat"):
            rows.append(sync.normalize_chat(event["chat"]))
        if rows:
            for row in rows:
                row["account_id"] = runtime.account_id
        chat = event if etype == "chat" else event.get("chat")
        if etype in ("chat", "updateNewChat") and chat:
            if "positions" in chat:
                runtime.chat_membership[str(chat["id"])] = sync.chat_membership(chat["positions"])
            chat_row = {
                "account_id": runtime.account_id, **sync.normalize_inbox_chat(chat),
            }
            if runtime.ctx.mode is LoginMode.BOT:
                # Bots have no user main/archive lists; received chats remain
                # visible even when TDLib supplies an empty positions vector.
                chat_row["is_visible"] = True
                if not chat.get("last_message"):
                    # TDLib bot chat snapshots do not provide their last message.
                    # Keep the independently mirrored latest bot ID/preview.
                    for field in ("last_message_id", "last_message", "last_message_at"):
                        chat_row.pop(field, None)
            payload = {"entities": rows, "chats": [chat_row], "history_messages": True}
            unknown_last = None
            if runtime.ctx.mode is not LoginMode.BOT and "last_message" in chat:
                chat_id = str(chat["id"])
                unknown_last = not bool(chat.get("last_message"))
                request_recent = (
                    (unknown_last and runtime.synced and chat_id not in runtime.unknown_last_message)
                    or (not unknown_last and chat_id in runtime.unknown_last_message)
                )
                if request_recent:
                    payload["recent_requests"] = [{"account_id": runtime.account_id, "chat_id": chat_id}]
            if chat.get("last_message"):
                payload["messages"] = [{
                    "account_id": runtime.account_id, **sync.normalize_message(chat["last_message"]),
                }]
            await self._queue_inbox(payload, runtime)
            if unknown_last is True:
                runtime.unknown_last_message.add(str(chat["id"]))
            elif unknown_last is False:
                runtime.unknown_last_message.discard(str(chat["id"]))

    async def _ingest_inbox_update(self, runtime: AccountRuntime, event: dict) -> None:
        """Mirror permitted TDLib events; never mark read or send to Telegram."""
        etype = event.get("@type")
        account_id = runtime.account_id
        chat_id = str(event.get("chat_id", ""))
        patch = {}
        history_messages = []
        request_recent = False
        unknown_last = None
        if etype == "updateNewMessage":
            message = event.get("message") or {}
            row = {
                "account_id": account_id, **sync.normalize_message(message),
            }
            # Body and discoverable preview commit locally together, before any
            # remote write. User visibility follows known main/archive membership.
            summary = {
                "account_id": account_id, "chat_id": row["chat_id"],
                "last_message_id": row["message_id"],
                "last_message": row["text"], "last_message_at": row["sent_at"],
            }
            if runtime.ctx.mode is LoginMode.BOT:
                summary["is_visible"] = True
            elif row["chat_id"] in runtime.chat_membership:
                summary.update(sync.membership_patch(runtime.chat_membership[row["chat_id"]]))
            await self._queue_inbox({"messages": [row], "chats": [summary]}, runtime)
        elif etype in ("updateMessageContent", "updateMessageEdited"):
            # Content/date arrive separately. Fetch both atomically outside the
            # receive pump so stale history can never bind old text to a new date.
            await self._schedule_message_refresh(runtime, chat_id, event["message_id"])
        elif etype == "updateDeleteMessages" and (
            event.get("is_permanent") or event.get("from_cache") is False
        ):
            # TDLib distinguishes inaccessible messages from cache-only eviction.
            # Both permanent and non-cache removals must disappear from the mirror.
            await self._tombstones.enqueue(account_id, chat_id, event.get("message_ids") or [], runtime.session_generation)
            self._start_tombstone_drain()
        elif etype == "updateChatLastMessage":
            last = event.get("last_message") or {}
            if runtime.ctx.mode is not LoginMode.BOT:
                if not last:
                    request_recent = chat_id not in runtime.unknown_last_message
                    unknown_last = True
                elif chat_id in runtime.unknown_last_message:
                    # Messages may have arrived without new-message events
                    # throughout the unknown interval, including after its
                    # first scan completed. Reconcile when it becomes known.
                    request_recent = True
                    unknown_last = False
            patch = {"last_message_id": int(last.get("id") or 0),
                     "last_message": sync.normalize_content(last.get("content") or {})["text"],
                     "last_message_at": sync.epoch_timestamp(last.get("date"))}
            if "positions" in event:
                membership = sync.chat_membership(event["positions"])
                runtime.chat_membership[chat_id] = membership
                patch.update(sync.membership_patch(membership))
            if last:
                last_row = {"account_id": account_id, **sync.normalize_message(last)}
                history_messages = [last_row]
        elif etype == "updateChatReadInbox":
            patch = {"unread_count": event.get("unread_count", 0),
                     "last_read_inbox_message_id": event.get("last_read_inbox_message_id", 0)}
        elif etype == "updateChatReadOutbox":
            patch = {"last_read_outbox_message_id": event.get("last_read_outbox_message_id", 0)}
        elif etype == "updateChatIsMarkedAsUnread":
            patch = {"is_marked_unread": bool(event.get("is_marked_as_unread"))}
        elif etype == "updateChatTitle":
            patch = {"title": event.get("title") or ""}
        elif etype == "updateChatPosition":
            position = event.get("position") or {}
            list_type = (position.get("list") or {}).get("@type")
            if list_type in ("chatListMain", "chatListArchive"):
                membership = runtime.chat_membership.get(chat_id)
                if membership is None and runtime.ctx.mode is not LoginMode.BOT:
                    # Resolve unknown list membership during FIFO replay, rather
                    # than losing a consumed event if this database read fails.
                    await self._queue_inbox({"chat_positions": [{
                        "account_id": account_id, "chat_id": chat_id,
                        "list": list_type, "enabled": bool(position.get("order")),
                    }]}, runtime)
                elif membership is not None:
                    if position.get("order"):
                        membership.add(list_type)
                    else:
                        membership.discard(list_type)
                    patch = sync.membership_patch(membership)
                else:
                    field_name = "is_in_main" if list_type == "chatListMain" else "is_in_archive"
                    patch = {field_name: bool(position.get("order"))}
                    if list_type == "chatListArchive":
                        patch["is_archived"] = bool(position.get("order"))
        if patch:
            chat_row = {"account_id": account_id, "chat_id": chat_id, **patch}
            if runtime.ctx.mode is LoginMode.BOT:
                chat_row["is_visible"] = True
            payload = {"chats": [chat_row]}
            if history_messages:
                payload.update(messages=history_messages, history_messages=True)
            if request_recent:
                payload["recent_requests"] = [{"account_id": account_id, "chat_id": chat_id}]
            await self._queue_inbox(payload, runtime)
            if unknown_last is True:
                runtime.unknown_last_message.add(chat_id)
            elif unknown_last is False:
                runtime.unknown_last_message.discard(chat_id)
        if time.time() - runtime.last_activity_write >= 30:
            await self._queue_inbox({"activity": [{
                "account_id": account_id, "last_activity_at": datetime.now(UTC).isoformat(),
            }]}, runtime)
            runtime.last_activity_write = time.time()

    def _fenced_payload(self, payload: dict, runtime: AccountRuntime | None = None) -> dict:
        if runtime is None:
            account_id = next((row["account_id"] for rows in payload.values() if isinstance(rows, list)
                               for row in rows if isinstance(row, dict) and "account_id" in row), None)
            if account_id is None:
                account_id = (payload.get("removed_contacts") or [(None,)])[0][0]
            runtime = self._runtimes.get(account_id)
        if runtime is None:
            raise RuntimeError("Inbox payload has no originating Telegram runtime")
        return {**payload, "_account_id": runtime.account_id, "_session_generation": runtime.session_generation}

    async def _queue_inbox(self, payload: dict, runtime: AccountRuntime | None = None) -> None:
        payload = self._fenced_payload(payload, runtime)
        async with self._inbox_enqueue_lock:
            await self._inbox.enqueue_payload(payload)
        self._start_inbox_drain()

    async def _wait_inbox_persisted(self) -> None:
        """Wait for a finite queued checkpoint; cancellation never discards its rows."""
        checkpoint = await self._inbox.checkpoint()
        while True:
            progress = self._inbox_progress
            if not await self._inbox.pending_through(checkpoint):
                return
            self._start_inbox_drain()
            # A large inventory or a recovered outage can take minutes. The
            # drainer bounds/paces its own requests and retries; this finite
            # checkpoint wait remains immediately cancellable without requeueing.
            await progress.wait()

    def _start_inbox_drain(self) -> None:
        if self._inbox_task is None or self._inbox_task.done():
            self._inbox_task = asyncio.create_task(self._drain_inbox())

    async def _drain_inbox(self) -> None:
        """Replay FIFO payloads; partial remote writes retry idempotently."""
        failures = 0
        while True:
            try:
                pending = await self._inbox.pending_payloads()
                if not pending:
                    return
                for sequence, payload in pending:
                    for activity in payload.get("activity") or []:
                        await self._bus.update_account(activity["account_id"], {
                            field: value for field, value in activity.items() if field != "account_id"
                        })
                    entities = payload.get("entities") or []
                    for index in range(0, len(entities), self._entity_batch_size):
                        await self._bus.upsert_entities(entities[index:index + self._entity_batch_size])
                        await asyncio.sleep(self._sync_delay * 0.5)
                    for snapshot in payload.get("contact_snapshots") or []:
                        await self._bus.prune_contacts(snapshot["account_id"], set(snapshot["keep_ids"]))
                    for account_id, user_id in payload.get("removed_contacts") or []:
                        await self._bus.delete_contact(account_id, user_id)
                    for position in payload.get("chat_positions") or []:
                        account_id, chat_id = position["account_id"], position["chat_id"]
                        stored = await self._bus.get_chat_membership(account_id, chat_id)
                        field = "is_in_main" if position["list"] == "chatListMain" else "is_in_archive"
                        patch = {field: position["enabled"]}
                        if stored:
                            membership = {name for name, enabled in (
                                ("chatListMain", stored.get("is_in_main")),
                                ("chatListArchive", stored.get("is_in_archive")),
                            ) if enabled}
                            if position["enabled"]:
                                membership.add(position["list"])
                            else:
                                membership.discard(position["list"])
                            patch = sync.membership_patch(membership)
                        elif position["list"] == "chatListArchive":
                            patch["is_archived"] = position["enabled"]
                        await self._bus.upsert_chats([{
                            "account_id": account_id, "chat_id": chat_id, **patch,
                        }])
                    if payload.get("chats"):
                        await self._bus.upsert_chats(payload["chats"])
                    for request in payload.get("recent_requests") or []:
                        await self._bus.request_recent_history(request["account_id"], request["chat_id"])
                    if payload.get("messages"):
                        await self._bus.upsert_messages(
                            payload["messages"], history=bool(payload.get("history_messages")),
                        )
                    for preview in payload.get("latest_previews") or []:
                        await self._bus.refresh_last_preview(
                            preview["account_id"], preview["chat_id"], preview["message_id"], preview["text"],
                        )
                    await self._inbox.acknowledge_payload(sequence)
                    self._inbox_progress.set()
                    self._inbox_progress = asyncio.Event()
                    failures = 0
                    await asyncio.sleep(self._sync_delay)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - normalized inbox data remains durable through outages
                failures += 1
                log.exception("Inbox persistence paused")
                await asyncio.sleep(min(30, failures * 2))

    def _start_tombstone_drain(self) -> None:
        if self._tombstone_task is None or self._tombstone_task.done():
            self._tombstone_task = asyncio.create_task(self._drain_tombstones())

    async def _drain_tombstones(self) -> None:
        """Persist complete durable batches; transient failures retain all IDs."""
        failures = 0
        while True:
            try:
                pending = await self._tombstones.pending()
                if not pending:
                    return
                rows = [{"account_id": account_id, "chat_id": chat_id,
                         "message_id": message_id, "deleted": True}
                        for account_id, chat_id, message_id, _session_generation in pending]
                await self._bus.upsert_messages(rows)
                await self._tombstones.acknowledge(pending)
                failures = 0
                await asyncio.sleep(self._sync_delay)
            except asyncio.CancelledError:
                # Pending SQLite rows survive shutdown and session lifecycle.
                raise
            except Exception:  # noqa: BLE001 - journal or PostgREST outages are retried
                failures += 1
                log.exception("Deletion journal persistence paused")
                await asyncio.sleep(min(30, failures * 2))

    async def _schedule_message_refresh(self, runtime: AccountRuntime, chat_id: str, message_id: int) -> None:
        generation = await self._inbox.enqueue_refresh(runtime.account_id, chat_id, message_id, runtime.session_generation)
        key = (chat_id, message_id)
        revisions = runtime.message_refresh_pending.setdefault(key, {})
        revisions[runtime.session_generation] = max(revisions.get(runtime.session_generation, 0), generation)
        self._start_message_refresh(runtime)

    def _start_message_refresh(self, runtime: AccountRuntime) -> None:
        if (runtime.identity_verified and runtime.message_refresh_pending
                and (runtime.message_refresh_task is None or runtime.message_refresh_task.done())):
            runtime.message_refresh_task = asyncio.create_task(self._refresh_messages(runtime))

    async def _reload_message_refreshes(self, runtime: AccountRuntime) -> None:
        if not runtime.identity_verified:
            return
        try:
            for chat_id, message_id, generation, session in await self._inbox.pending_refreshes(
                runtime.account_id, include_sessions=True,
            ):
                active = runtime.message_refresh_active
                if active and active[:2] == (chat_id, message_id) and active[2].get(session, 0) >= generation:
                    continue
                revisions = runtime.message_refresh_pending.setdefault((chat_id, message_id), {})
                revisions[session] = max(revisions.get(session, 0), generation)
            self._start_message_refresh(runtime)
        except Exception:  # noqa: BLE001 - local state remains pending for the next poll
            log.exception("Could not reload pending message revisions for %s", runtime.account_id)

    async def _refresh_messages(self, runtime: AccountRuntime) -> None:
        """Persist complete revisions before acknowledging their originating sessions."""
        while runtime.message_refresh_pending:
            key = next(iter(runtime.message_refresh_pending))
            generations = runtime.message_refresh_pending.pop(key)
            chat_id, message_id = key
            runtime.message_refresh_active = (chat_id, message_id, generations)
            try:
                response = await self._send_and_wait(runtime, {
                    "@type": "getMessage", "chat_id": int(chat_id), "message_id": message_id,
                })
                if response and response.get("@type") == "message":
                    row = {"account_id": runtime.account_id, **sync.normalize_message(response)}
                    payload = {"messages": [row]}
                    if runtime.ctx.mode is LoginMode.BOT:
                        payload["latest_previews"] = [row]
                    await self._queue_inbox(payload, runtime)
                elif (response and response.get("code") in (400, 401, 403, 404)
                      and parse_flood_wait_seconds(response) is None):
                    log.info("Message refresh unavailable for %s (code %s)", runtime.account_id, response["code"])
                else:
                    raise RuntimeError("message_refresh_incomplete")
                for session, generation in generations.items():
                    await self._inbox.acknowledge_refresh(runtime.account_id, chat_id, message_id, generation, session)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - retain every originating generation for paced retry
                log.exception("Message refresh pending for %s", runtime.account_id)
                pending = runtime.message_refresh_pending.setdefault(key, {})
                for session, generation in generations.items():
                    pending[session] = max(pending.get(session, 0), generation)
                await asyncio.sleep(2)
            finally:
                runtime.message_refresh_active = None
            await asyncio.sleep(self._sync_delay)

    async def _backfill_history(self, runtime: AccountRuntime) -> None:
        """Read at most 20 chats × 3 pages × 100 messages per paced pass.

        Commit a cursor only after its message page persisted. TDLib can return
        short pages before exhaustion, so only an empty page marks completion.
        """
        if runtime.ctx.mode is LoginMode.BOT or not runtime.identity_verified:
            return
        for chat in await self._bus.list_history_chats(runtime.account_id, 20):
            if self._cooldowns.get(runtime.account_id, 0) > time.time():
                return
            try:
                await self._backfill_chat_history(runtime, chat)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one inaccessible chat must not starve the account
                log.exception("History pending for account %s chat %s", runtime.account_id, chat["chat_id"])
                cooldown = self._cooldowns.get(runtime.account_id, 0) > time.time()
                try:
                    await self._bus.patch_chat(runtime.account_id, str(chat["chat_id"]), {
                        "history_synced_at": datetime.now(UTC).isoformat(),
                        "history_complete": False,
                        "history_note": ("Telegram cooldown; history will resume after its deadline."
                                         if cooldown else "History read or storage is temporarily unavailable; retrying on a later pass."),
                    })
                except Exception:  # noqa: BLE001 - an outage must not fail metadata sync
                    log.exception("Could not record pending history for %s", runtime.account_id)
                if cooldown:
                    return
                await asyncio.sleep(self._sync_delay)

    async def _backfill_chat_history(self, runtime: AccountRuntime, chat: dict) -> None:
        chat_id = str(chat["chat_id"])
        cursor = int(chat.get("history_cursor") or 0)
        for _ in range(3):
            if cursor == 1 << 20:
                # The first regular server message has no earlier server ID.
                await self._bus.patch_chat(runtime.account_id, chat_id, {
                    "history_synced_at": datetime.now(UTC).isoformat(),
                    "history_complete": True,
                    "history_note": None,
                })
                break
            anchor = sync.previous_history_anchor(cursor) if cursor else 0
            if anchor is None:
                # Local IDs and the first server ID have no safe preceding
                # server anchor. Keep an honest incomplete checkpoint.
                await self._bus.patch_chat(runtime.account_id, chat_id, {
                    "history_synced_at": datetime.now(UTC).isoformat(),
                    "history_complete": False,
                    "history_note": "History checkpoint is retained; this Telegram message ID has no safe earlier server cursor.",
                })
                break
            response = await self._send_and_wait(runtime, {
                "@type": "getChatHistory", "chat_id": int(chat_id),
                "from_message_id": anchor, "offset": 0,
                "limit": 100, "only_local": False,
            }, retry_flood=False)
            flood_wait = parse_flood_wait_seconds(response or {})
            if flood_wait:
                runtime.paused_until = max(self._cooldowns.get(runtime.account_id, 0), time.time() + flood_wait)
                self._cooldowns[runtime.account_id] = runtime.paused_until
            if not response or response.get("@type") != "messages":
                raise RuntimeError("message_history_sync_incomplete")
            messages = response.get("messages") or []
            rows = [sync.normalize_message(m) for m in messages
                    if not cursor or m["id"] < cursor]
            for row in rows:
                row["account_id"] = runtime.account_id
            await self._bus.upsert_messages(rows, history=True)
            next_cursor = min((row["message_id"] for row in rows), default=cursor)
            patch = {"history_cursor": next_cursor,
                     "history_synced_at": datetime.now(UTC).isoformat(),
                     "history_note": None,
                     "history_complete": not messages}
            await self._bus.patch_chat(runtime.account_id, chat_id, patch)
            await asyncio.sleep(self._sync_delay)
            # A cached boundary-only response isn't proof of exhaustion.
            if not rows:
                break
            cursor = next_cursor

    async def _backfill_recent_history(self, runtime: AccountRuntime) -> None:
        """Catch up 10 chats × 1 page independently of older history progress.

        A separate durable newest-page head and previous completed boundary let
        offline gaps span multiple passes. Only completed catch-up advances the
        latest watermark; realtime/last-message updates never change it.
        """
        if runtime.ctx.mode is LoginMode.BOT or not runtime.identity_verified:
            return
        for chat in await self._bus.list_recent_history_chats(runtime.account_id, 10):
            if self._cooldowns.get(runtime.account_id, 0) > time.time():
                return
            chat_id = str(chat["chat_id"])
            try:
                await self._backfill_recent_chat(runtime, chat)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - recent errors never discard the old cursor
                log.exception("Recent history pending for %s", runtime.account_id)
                cooldown = self._cooldowns.get(runtime.account_id, 0) > time.time()
                try:
                    await self._bus.patch_chat(runtime.account_id, chat_id, {
                        "recent_synced_at": datetime.now(UTC).isoformat(),
                        "recent_complete": False,
                        "recent_note": ("Telegram cooldown; catch-up will resume after its deadline."
                                        if cooldown else "Recent history is temporarily unavailable; retrying on a later pass."),
                    })
                except Exception:  # noqa: BLE001
                    log.exception("Could not record pending catch-up for %s", runtime.account_id)
                if cooldown:
                    return
                await asyncio.sleep(self._sync_delay)

    async def _backfill_recent_chat(self, runtime: AccountRuntime, chat: dict) -> None:
        chat_id = str(chat["chat_id"])
        cursor = int(chat.get("recent_cursor") or 0)
        boundary = int(chat.get("recent_boundary") or 0)
        head = int(chat.get("recent_head") or 0)
        complete = bool(cursor and (cursor == 1 << 20 or (boundary and cursor <= boundary)))
        patch = {"recent_synced_at": datetime.now(UTC).isoformat(), "recent_note": None}
        if not complete:
            anchor = sync.previous_history_anchor(cursor) if cursor else 0
            if anchor is None:
                await self._bus.patch_chat(runtime.account_id, chat_id, {
                    **patch, "recent_complete": False,
                    "recent_note": "Catch-up retains its checkpoint; this message has no safe earlier server cursor.",
                })
                return
            response = await self._send_and_wait(runtime, {
                "@type": "getChatHistory", "chat_id": int(chat_id),
                "from_message_id": anchor, "offset": 0, "limit": 100, "only_local": False,
            }, retry_flood=False)
            if not response or response.get("@type") != "messages":
                wait = parse_flood_wait_seconds(response or {})
                if wait:
                    runtime.paused_until = max(self._cooldowns.get(runtime.account_id, 0), time.time() + wait)
                    self._cooldowns[runtime.account_id] = runtime.paused_until
                raise RuntimeError("recent_history_sync_incomplete")
            messages = response.get("messages") or []
            rows = [sync.normalize_message(message) for message in messages
                    if not cursor or message["id"] < cursor]
            for row in rows:
                row["account_id"] = runtime.account_id
            await self._bus.upsert_messages(rows, history=True)
            head = head or max((row["message_id"] for row in rows), default=0)
            cursor = min((row["message_id"] for row in rows), default=cursor)
            complete = not messages or bool(cursor and (
                cursor == 1 << 20 or (boundary and cursor <= boundary)
            ))
            patch.update({"recent_cursor": cursor, "recent_head": head})
        if complete:
            # Never publish completion before this transaction: it also stages
            # a new top-gap scan requested by a restart during pending catch-up.
            await self._bus.complete_recent_history(runtime.account_id, chat_id, head)
        else:
            await self._bus.patch_chat(runtime.account_id, chat_id, {**patch, "recent_complete": False})
        await asyncio.sleep(self._sync_delay)

    async def _continue_history(self, runtime: AccountRuntime) -> None:
        try:
            if runtime.ctx.mode is not LoginMode.BOT and not runtime.history_restarted:
                await self._bus.restart_history(runtime.account_id)
                runtime.history_restarted = True
            try:
                await self._backfill_recent_history(runtime)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - an unavailable recent lane must not starve older history
                log.exception("Recent history listing failed for %s", runtime.account_id)
            await self._backfill_history(runtime)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - retry next paced pass from durable cursors
            log.exception("History continuation failed for %s", runtime.account_id)
        finally:
            runtime.last_history_pass = time.time()

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
            log.exception(
                "Could not list authorized accounts to rehydrate (will retry)"
            )
            return False
        complete = True
        for acc in accounts:
            account_id = acc.get("id")
            if not account_id or account_id in self._runtimes:
                continue
            mode = (
                LoginMode.BOT if acc.get("account_type") == "bot" else LoginMode.PHONE
            )
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
        self._start_tombstone_drain()
        self._start_inbox_drain()
        poll = max(1, int(getattr(self._settings, "command_poll_seconds", 3)))
        last_poll = 0.0
        while True:
            now = time.time()
            if now - last_poll >= poll:
                last_poll = now
                # Also closes an enqueue/empty-drain race without hot polling.
                self._start_tombstone_drain()
                self._start_inbox_drain()
                if not rehydrated:
                    rehydrated = await self._rehydrate_authorized()
                for runtime in list(self._runtimes.values()):
                    if runtime.status is LoginStatus.AUTHORIZED:
                        await self._resume_identity_binding(runtime)
                        await self._reload_message_refreshes(runtime)
                try:
                    for command in await self._bus.claim_pending_commands():
                        await self.apply_command(command)
                except Exception:  # noqa: BLE001
                    log.exception("Command poll failed")

            for runtime in list(self._runtimes.values()):
                if (runtime.synced and runtime.status is LoginStatus.AUTHORIZED
                        and runtime.ctx.mode is not LoginMode.BOT
                        and (runtime.history_task is None or runtime.history_task.done())
                        and now - runtime.last_history_pass >= 60
                        and self._cooldowns.get(runtime.account_id, 0) <= now):
                    runtime.history_task = asyncio.create_task(self._continue_history(runtime))
                if (
                    runtime.deferred_state
                    and self._cooldowns.get(runtime.account_id, 0) <= time.time()
                ):
                    state = runtime.deferred_state
                    try:
                        await self._handle_event(
                            {
                                "@client_id": runtime.client.client_id,
                                "@type": "updateAuthorizationState",
                                "authorization_state": state,
                            }
                        )
                        runtime.deferred_state = None
                    except Exception:  # noqa: BLE001
                        log.exception(
                            "Deferred authorization update failed; retrying later"
                        )
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
                log.info(
                    "account %s: previous TDLib client confirmed closed", account_id
                )
            return
        await self._handle_event(event)
        runtime = self._by_client.get(cid)
        if runtime is not None and event.get("@type") in (
            "user",
            "chat",
            "updateNewChat",
            "updateUser",
        ):
            await self.ingest_container(runtime, event)
        if runtime is not None and event.get("@type") in (
            "updateNewMessage", "updateMessageContent", "updateMessageEdited",
            "updateDeleteMessages", "updateChatLastMessage", "updateChatReadInbox",
            "updateChatReadOutbox", "updateChatIsMarkedAsUnread", "updateChatTitle",
            "updateChatPosition",
        ):
            await self._ingest_inbox_update(runtime, event)

    @staticmethod
    def _is_closed_event(event: dict) -> bool:
        etype = event.get("@type")
        state = (
            event.get("authorization_state")
            if etype == "updateAuthorizationState"
            else (event if etype and etype.startswith("authorizationState") else None)
        )
        return state is not None and state.get("@type") == "authorizationStateClosed"

    def _receive_any(self) -> dict | None:
        """Read one event from the shared native queue (any client)."""

        # Keep pumping while any client is still closing, even with no live
        # runtimes, so a lingering close event is reaped and its tombstone freed.
        if not self._runtimes and not self._closing:
            return None
        return receive_any(self._settings.tdlib_library_path, timeout=0.5)

    async def _drain_until_closed(
        self, client: TdJsonClient, *, timeout: float = 10.0
    ) -> bool:
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
