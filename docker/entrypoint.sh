#!/bin/sh
# Prepare the state directory on the persistent disk, then drop root.
# A freshly attached disk is root-owned, so the chown has to happen at start-up.
set -eu

STATE_DIR="${STATE_DIR:-/data/state}"
mkdir -p "$STATE_DIR/samples"

if [ "$(id -u)" = "0" ]; then
    chown -R app:app "$STATE_DIR"
    exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
