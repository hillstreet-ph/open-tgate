"""Device metadata must remain stable for a persisted native session."""
import asyncio
import os
import stat
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.telegram import authflow
from app.telegram import device_profile
from app.telegram.authflow import LoginMode
from app.telegram.manager import AccountManager


class Client:
    def __init__(self, _library):
        self.client_id = 1
        self.sent = []

    def send(self, request):
        self.sent.append(request)


def manager(tmp_path, monkeypatch):
    monkeypatch.setattr("app.telegram.manager.TdJsonClient", Client)
    return AccountManager(Settings(tdlib_database_directory=str(tmp_path / "db"),
                                   tdlib_files_directory=str(tmp_path / "files")), AsyncMock())


def profile(runtime):
    p = runtime.ctx.parameters
    return p.device_model, p.system_version, p.application_version


def test_existing_released_session_keeps_its_device_metadata(tmp_path, monkeypatch):
    async def run():
        m = manager(tmp_path, monkeypatch)
        directory = tmp_path / "db" / "existing-account"
        directory.mkdir(parents=True)
        native_state = directory / "td.binlog"
        native_state.write_bytes(b"session-state-sentinel")
        runtime = await m._new_runtime("existing-account", LoginMode.PHONE)
        assert profile(runtime) == ("Desktop", "Windows 10", "5.8.0")
        assert native_state.read_bytes() == b"session-state-sentinel"
    asyncio.run(run())


def test_new_session_retains_selected_profile_after_pool_changes(tmp_path, monkeypatch):
    async def run():
        first = await manager(tmp_path, monkeypatch)._new_runtime("fresh-account", LoginMode.PHONE)
        original = profile(first)
        assert original == authflow.device_profile_for("fresh-account")
        saved = tmp_path / "db" / "fresh-account" / "open-tgate-device-profile.json"
        assert saved.is_file()
        assert saved.stat().st_mode & 0o777 == 0o600
        monkeypatch.setattr(authflow, "_DEVICE_PROFILES", (("Changed model", "Changed system", "99.0"),))
        restored = await manager(tmp_path, monkeypatch)._new_runtime("fresh-account", LoginMode.PHONE)
        assert profile(restored) == original
    asyncio.run(run())


@pytest.mark.parametrize("contents", ["not valid JSON", "[]", '{"version": 2}', '{"version": 1, "device_model": 7}'])
def test_invalid_saved_profile_never_silently_replaces_session_metadata(tmp_path, monkeypatch, contents):
    async def run():
        m = manager(tmp_path, monkeypatch)
        directory = tmp_path / "db" / "existing-account"
        directory.mkdir(parents=True)
        saved = directory / "open-tgate-device-profile.json"
        saved.write_text(contents)
        with pytest.raises(ValueError, match="device profile"):
            await m._new_runtime("existing-account", LoginMode.PHONE)
        assert saved.read_text() == contents
        assert not m._runtimes
    asyncio.run(run())


def test_profile_filename_is_durable_before_native_client_opens(tmp_path, monkeypatch):
    async def run():
        m = manager(tmp_path, monkeypatch)
        events = []
        real_fsync = os.fsync

        def fsync(fd):
            events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
            real_fsync(fd)

        def client(library):
            events.append("client")
            assert "file" in events
            assert "directory" in events[events.index("file") + 1:events.index("client")]
            return Client(library)

        monkeypatch.setattr(device_profile.os, "fsync", fsync)
        monkeypatch.setattr("app.telegram.manager.TdJsonClient", client)
        await m._new_runtime("fresh-account", LoginMode.PHONE)
    asyncio.run(run())


def test_nonregular_profile_cannot_block_the_worker(tmp_path):
    directory = tmp_path / "account"
    directory.mkdir()
    os.mkfifo(directory / "open-tgate-device-profile.json")
    script = """
import sys
from app.telegram.device_profile import persisted_device_profile
try:
    persisted_device_profile('account', sys.argv[1])
except ValueError:
    pass
else:
    raise AssertionError('Nonregular metadata must be rejected')
"""
    try:
        result = subprocess.run([sys.executable, "-c", script, str(directory)],
                                capture_output=True, timeout=2)
    except subprocess.TimeoutExpired:
        pytest.fail("Reading profile metadata must not block the worker")
    assert result.returncode == 0, result.stderr.decode()


def test_symlinked_profile_is_rejected_without_following_it(tmp_path):
    target = tmp_path / "other-file.json"
    target.write_text('{"version":1,"device_model":"Other","system_version":"Other","application_version":"1"}')
    directory = tmp_path / "account"
    directory.mkdir()
    (directory / "open-tgate-device-profile.json").symlink_to(target)
    with pytest.raises(ValueError, match="device profile"):
        device_profile.persisted_device_profile("account", str(directory))
