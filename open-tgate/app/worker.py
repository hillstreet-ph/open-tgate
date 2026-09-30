import asyncio
import ctypes
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .config import get_settings
from .observability import init_sentry

log = logging.getLogger("open-tgate-worker")
settings = get_settings()

# Unique id for THIS worker process, generated once at startup. It is published
# in every heartbeat so a deploy can prove a *new* process is running (a heartbeat
# whose boot_id differs from the pre-redeploy one) rather than trusting a rolling
# timestamp — the old process keeps beating until it is replaced, so a fresh
# timestamp alone does not distinguish old from new.
WORKER_BOOT_ID = uuid.uuid4().hex

# Shared, best-effort liveness for the account manager so the heartbeat (and the
# operator console) can show whether login commands are actually being processed
# — not just whether the container is up.
MANAGER_STATE: dict[str, object] = {"active": False}


def load_tdlib() -> ctypes.CDLL:
    library = Path(settings.tdlib_library_path)
    if not library.is_file():
        raise RuntimeError(f"TDLib library not found: {library}")
    return ctypes.CDLL(str(library))


async def publish_heartbeat() -> None:
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise RuntimeError("Supabase server configuration is missing")
    endpoint = f"{settings.supabase_url.rstrip('/')}/rest/v1/open_tgate_worker_heartbeats"
    payload = {
        "worker_id": settings.worker_id,
        "service": "tdlib-sync",
        "status": "running",
        "last_seen_at": datetime.now(UTC).isoformat(),
        "metadata": {
            "send_enabled": settings.external_send_enabled,
            # Whether the worker has the credentials to drive Telegram login and
            # whether the account-manager loop is actually running. When
            # telegram_enabled is false the console should tell operators the
            # worker cannot process logins until credentials are configured.
            "telegram_enabled": settings.telegram_enabled,
            "manager_active": bool(MANAGER_STATE.get("active")),
            # Per-process id: a deploy is only verified once a heartbeat with a
            # NEW boot_id (i.e. the redeployed process) appears.
            "boot_id": WORKER_BOOT_ID,
        },
    }
    headers = {
        "apikey": settings.supabase_secret_key,
        "authorization": f"Bearer {settings.supabase_secret_key}",
        "content-type": "application/json",
        "prefer": "resolution=merge-duplicates",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(endpoint, headers=headers, content=json.dumps(payload))
        response.raise_for_status()


async def heartbeat_loop() -> None:
    while True:
        try:
            await publish_heartbeat()
        except Exception:
            log.exception("Heartbeat failed")
        await asyncio.sleep(settings.heartbeat_interval_seconds)


async def account_manager_loop() -> None:
    """Run the multi-account TDLib login + sync manager.

    Imported lazily so a worker without Telegram credentials (CI, local dev)
    never touches the account runtime. Kept resilient: a crash is logged and the
    manager restarts after a short backoff rather than taking the worker down.
    """

    from .telegram.bus import SupabaseBus
    from .telegram.manager import AccountManager

    while True:
        try:
            message_bus = SupabaseBus(settings.supabase_url, settings.supabase_secret_key)
            manager = AccountManager(settings, message_bus)
            log.info("Telegram account manager starting (multi-account login + read-only sync)")
            MANAGER_STATE["active"] = True
            await manager.run()
        except Exception:
            MANAGER_STATE["active"] = False
            log.exception("Account manager crashed; restarting in 5s")
            await asyncio.sleep(5)


async def main() -> None:
    logging.basicConfig(level=settings.log_level)
    init_sentry(settings, component="worker")
    Path(settings.tdlib_database_directory).mkdir(parents=True, exist_ok=True)
    Path(settings.tdlib_files_directory).mkdir(parents=True, exist_ok=True)
    load_tdlib()
    log.info(
        "TDLib loaded; outbound sending=%s; account manager=%s",
        settings.external_send_enabled,
        "enabled" if settings.telegram_enabled else "dormant (credentials not set)",
    )

    tasks = [asyncio.create_task(heartbeat_loop())]
    if settings.telegram_enabled:
        tasks.append(asyncio.create_task(account_manager_loop()))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())

