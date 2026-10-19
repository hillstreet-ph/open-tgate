"""Keep non-secret client metadata stable alongside the native session."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .authflow import device_profile_for


# Release 1.9.0 used these values for every account. Existing databases predate
# profile persistence and must not silently switch to the newer selection pool.
LEGACY_PROFILE = ("Desktop", "Windows 10", "5.8.0")
PROFILE_NAME = "open-tgate-device-profile.json"
PROFILE_FIELDS = ("device_model", "system_version", "application_version")


def _sync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_profile(path: Path) -> tuple[str, str, str]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                raise ValueError("invalid device profile file")
            content = stream.read(4097)
            if len(content) > 4096:
                raise ValueError("device profile is too large")
            data = json.loads(content)
            # A concurrently created record must also be durable before native
            # session files can be written, not just before its creator returns.
            os.fsync(stream.fileno())
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("unsupported device profile version")
        values = tuple(data.get(key) for key in PROFILE_FIELDS)
        if not all(isinstance(value, str) and value.strip() and len(value) <= 128 for value in values):
            raise ValueError("invalid device profile fields")
        _sync_directory(path.parent)
        return values
    except (OSError, ValueError) as exc:
        raise ValueError("Saved device profile is invalid; preserve the session directory and repair its metadata") from exc


def persisted_device_profile(account_id: str, database_directory: str) -> tuple[str, str, str]:
    """Select once; preserve existing native files and never overwrite metadata."""
    directory = Path(database_directory)
    path = directory / PROFILE_NAME
    if path.exists() or path.is_symlink():
        return _read_profile(path)
    existing_state = directory.exists() and any(directory.iterdir())
    profile = LEGACY_PROFILE if existing_state else device_profile_for(account_id)
    missing = []
    parent = directory
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    directory.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        _sync_directory(created.parent)
    data = {"version": 1, **dict(zip(PROFILE_FIELDS, profile))}
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process selected first. Never replace its choice; incomplete
        # metadata fails closed so a retry cannot silently choose a new device.
        return _read_profile(path)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(directory)
    return profile
