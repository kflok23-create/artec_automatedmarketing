#!/bin/sh
# hermes-brain boot: fail hard without the volume, install the seam, start the gateway.
set -eu

: "${HERMES_HOME:?HERMES_HOME must be set (the /data/hermes volume)}"

if [ ! -d "$HERMES_HOME" ]; then
    echo "FATAL: HERMES_HOME=$HERMES_HOME does not exist — is the volume mounted?" >&2
    exit 1
fi
if ! touch "$HERMES_HOME/.write-probe" 2>/dev/null; then
    echo "FATAL: HERMES_HOME=$HERMES_HOME is not writable — read-only volume?" >&2
    exit 1
fi
rm -f "$HERMES_HOME/.write-probe"

mkdir -p "$HERMES_HOME/plugins"
cp /bootstrap/plugins/artec_hermes.py "$HERMES_HOME/plugins/artec_hermes.py"

# Profile 'artec' isolates config, memory, sessions and skills. Idempotent.
hermes profile create artec 2>/dev/null || true
mkdir -p "$HERMES_HOME/profiles/artec"
cp /bootstrap/config.yaml "$HERMES_HOME/profiles/artec/config.yaml"

exec hermes gateway --profile artec
