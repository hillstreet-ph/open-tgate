#!/bin/sh
set -eu

mkdir -p "$TDLIB_DATABASE_DIRECTORY" "$TDLIB_FILES_DIRECTORY"
chown -R 65532:65532 /data/tdlib
exec gosu 65532:65532 "$@"
