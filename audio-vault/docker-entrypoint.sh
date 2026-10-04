#!/bin/sh
# Make the mounted storage writable for the unprivileged "node" user, then drop root.
set -e
mkdir -p /data/files /data/tmp
chown node:node /data /data/files /data/tmp
exec setpriv --reuid=node --regid=node --init-groups "$@"
