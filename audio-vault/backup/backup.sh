#!/bin/sh
# Daily backup of Audio OnAir Turbo: the database (pg_dump) and all audio files.
# Runs as its own container. Because the server may be a normal PC that is off at
# night, it does not wait for a fixed time: it checks every 30 minutes and makes a
# backup whenever the last good one is more than BACKUP_EVERY_HOURS old.
#   backup.sh        run forever (container default)
#   backup.sh now    make one backup right away
set -eu

TARGET=/backup
KEEP_DAYS=${BACKUP_KEEP_DAYS:-30}
EVERY=$(( ${BACKUP_EVERY_HOURS:-24} * 3600 ))

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$TARGET/backup.log"; }

run() {
  stamp=$(date +%Y-%m-%d_%H%M)
  mkdir -p "$TARGET/database" "$TARGET/audio"
  # 1. Database: a compact, restorable dump. Written to a temp name first so a
  #    half-written file never looks like a good backup.
  pg_dump -h db -U onair -d onair -Fc -f "$TARGET/database/onair-$stamp.dump.part"
  mv "$TARGET/database/onair-$stamp.dump.part" "$TARGET/database/onair-$stamp.dump"
  # 2. Audio: copy new files only (file names are unique and never change).
  copied=0
  for f in /data/files/*; do
    [ -e "$f" ] || continue
    name=$(basename "$f")
    if [ ! -e "$TARGET/audio/$name" ]; then
      cp "$f" "$TARGET/audio/$name.part" && mv "$TARGET/audio/$name.part" "$TARGET/audio/$name"
      copied=$((copied + 1))
    fi
  done
  # 3. Keep KEEP_DAYS days of database dumps (audio is never deleted from the backup).
  find "$TARGET/database" -name 'onair-*.dump' -mtime +"$KEEP_DAYS" -delete
  date +%s > "$TARGET/.last-success"
  log "Backup klaar: database onair-$stamp.dump, $copied nieuwe audiobestanden"
}

mkdir -p "$TARGET"
if [ "${1:-}" = "now" ]; then run; exit 0; fi

log "Backup-service gestart (elke ${BACKUP_EVERY_HOURS:-24} uur, database $KEEP_DAYS dagen bewaren)"
while true; do
  last=$(cat "$TARGET/.last-success" 2>/dev/null || echo 0)
  if [ $(( $(date +%s) - last )) -ge "$EVERY" ]; then
    run || log "BACKUP MISLUKT — controleer of de backupmap bereikbaar is"
  fi
  sleep 1800
done
