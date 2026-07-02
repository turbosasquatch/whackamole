#!/bin/sh
set -eu

if [ ! -d /config ]; then
  mkdir -p /config
fi

chown -R 10001:10001 /config
chmod 700 /config

exec gosu 10001:10001 "$@"
