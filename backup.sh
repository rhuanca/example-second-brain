#!/usr/bin/env bash
#
# Back up the Second Brain's irreplaceable data: the vault and the state database.
#
#   ./backup.sh install            create the repository and the daily timer
#   ./backup.sh run                take a snapshot, then prune to the policy
#   ./backup.sh check              verify the repository and the newest snapshot
#   ./backup.sh restore <dir>      restore into a NEW directory (never the vault)
#   ./backup.sh list               what snapshots exist
#   ./backup.sh --dry-run run      print what would happen; change nothing
#   ./backup.sh --no-timer install just the repository, without a systemd timer
#
# Snapshots, not a mirror: the threat is a bug or a slip deleting something, and
# a mirror copies the deletion. restic keeps history, deduplicates (your PDFs are
# stored once, not once per snapshot) and encrypts the repository.
#
# NOT backed up: .env (so a leaked backup carries no credentials), the embedding
# index (derived — rebuilt on the next start), .venv. The code and topics.json
# are in git. See BACKUP.md.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$HOME/second-brain-backups}"
PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-$HOME/.config/second-brain/restic-password}"
STAGING="$BACKUP_DIR/staging"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="rr-second-brain-backup"
KEEP=(--keep-daily 7 --keep-weekly 4 --keep-monthly 6)
STALE_HOURS=48

step() { echo; echo "=== $1 ==="; }
warn() { echo "Warning: $1" >&2; }
err()  { echo "Error: $1" >&2; exit 1; }
run()  { if $DRY_RUN; then echo "would run: $*"; else "$@"; fi; }

usage() {
    sed -n '3,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

# --- arguments ---------------------------------------------------------------
DRY_RUN=false
WITH_TIMER=true
ARGS=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --no-timer) WITH_TIMER=false ;;
        -h|--help) usage ;;
        -*) err "unknown option: $arg (try --help)" ;;
        *) ARGS+=("$arg") ;;
    esac
done
COMMAND="${ARGS[0]:-}"
[[ -n "$COMMAND" ]] || usage 1

command -v restic >/dev/null 2>&1 || err \
    "restic is not installed. On Debian/Ubuntu: sudo apt install restic"
command -v uv >/dev/null 2>&1 || err "uv is not installed (see DEPLOY.md)"

# --- what to back up ---------------------------------------------------------
# Asked of the app's own settings loader, so this script and the service can
# never disagree about where the vault and the database are.
read_paths() {
    (cd "$PROJECT_DIR" && uv run --no-sync python - ) <<'PY'
from second_brain.config import ConfigError
from second_brain.kb.config import KbSettings

try:
    s = KbSettings.from_env()
except ConfigError as exc:
    raise SystemExit(f"config: {exc}")
print(f"VAULT_DIR={s.vault_path}")
print(f"STATE_DB={s.state_db}")
PY
}

VAULT_DIR="" STATE_DB=""
while IFS='=' read -r key value; do
    case "$key" in
        VAULT_DIR) VAULT_DIR="$value" ;;
        STATE_DB)  STATE_DB="$value" ;;
    esac
done < <(read_paths)
[[ -d "$VAULT_DIR" ]] || err "VAULT_PATH is not a directory: $VAULT_DIR"

export RESTIC_REPOSITORY="$BACKUP_DIR/repo"
export RESTIC_PASSWORD_FILE="$PASSWORD_FILE"

# --- commands ----------------------------------------------------------------
do_install() {
    step "Repository at $RESTIC_REPOSITORY"
    if [[ ! -f "$PASSWORD_FILE" ]]; then
        run mkdir -p "$(dirname "$PASSWORD_FILE")"
        if ! $DRY_RUN; then
            umask 077
            python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$PASSWORD_FILE"
            chmod 600 "$PASSWORD_FILE"
        fi
        echo "Wrote a new repository password to $PASSWORD_FILE"
        echo
        echo "   >>> Copy it into your password manager now. Without it these"
        echo "   >>> snapshots cannot be read, by you or by anyone."
        echo
        $DRY_RUN || cat "$PASSWORD_FILE"
    else
        echo "Password file already exists: $PASSWORD_FILE"
    fi

    if [[ -d "$RESTIC_REPOSITORY" ]]; then
        echo "Repository already initialised."
    else
        run restic init
    fi

    # Same disk means one failure takes both copies. Worth saying out loud.
    if [[ "$(stat -c %d "$VAULT_DIR" 2>/dev/null)" == "$(stat -c %d "$BACKUP_DIR" 2>/dev/null)" ]]; then
        warn "the backup and the vault are on the same disk: this protects you from a"
        warn "bad delete, not from losing the machine. Point BACKUP_DIR at another"
        warn "disk, or add an offsite copy later (see BACKUP.md)."
    fi

    if $WITH_TIMER; then
        install_timer
    else
        echo "Skipping the timer (--no-timer): run ./backup.sh run yourself, or from cron."
    fi
    step "Done"
    echo "Take the first snapshot now with: ./backup.sh run"
}

install_timer() {
    step "Daily timer ($UNIT.timer)"
    if $DRY_RUN; then
        echo "would write $UNIT_DIR/$UNIT.{service,timer} and enable the timer"
        return
    fi
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT_DIR/$UNIT.service" <<EOF
[Unit]
Description=Second Brain backup (vault + state database)
# A backup that quietly stopped is the usual way backups fail.
OnFailure=$UNIT-failed.service

[Service]
Type=oneshot
WorkingDirectory=$PROJECT_DIR
Environment=BACKUP_DIR=$BACKUP_DIR
ExecStart=$PROJECT_DIR/backup.sh run
EOF
    cat > "$UNIT_DIR/$UNIT-failed.service" <<EOF
[Unit]
Description=Tell the owner the Second Brain backup failed

[Service]
Type=oneshot
WorkingDirectory=$PROJECT_DIR
ExecStart=$(command -v uv) run --no-sync python scripts/notify_telegram.py "Backup failed on \$(hostname). Check: journalctl --user -u $UNIT -n 30"
EOF
    cat > "$UNIT_DIR/$UNIT.timer" <<EOF
[Unit]
Description=Daily Second Brain backup

[Timer]
OnCalendar=*-*-* 03:30:00
# Run a backup missed while the machine was off, at the next boot.
Persistent=true
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "$UNIT.timer"
    systemctl --user list-timers "$UNIT.timer" --no-pager | sed -n '1,2p'
}

do_run() {
    [[ -d "$RESTIC_REPOSITORY" ]] || err "no repository yet — run: ./backup.sh install"

    step "Copying the state database"
    if [[ -f "$STATE_DB" ]]; then
        run mkdir -p "$STAGING"
        run uv run --no-sync python "$PROJECT_DIR/scripts/snapshot_state_db.py" \
            "$STATE_DB" "$STAGING/$(basename "$STATE_DB")"
    else
        echo "No state database yet at $STATE_DB — skipping."
    fi

    step "Snapshot"
    local targets=("$VAULT_DIR")
    [[ -d "$STAGING" ]] && targets+=("$STAGING")
    run restic backup --verbose=0 --tag second-brain "${targets[@]}"

    step "Pruning to ${KEEP[*]}"
    run restic forget "${KEEP[@]}" --prune

    step "Done"
    $DRY_RUN || restic snapshots --latest 1
}

do_check() {
    [[ -d "$RESTIC_REPOSITORY" ]] || err "no repository yet — run: ./backup.sh install"
    step "Repository integrity"
    run restic check --read-data-subset=5%

    step "Freshness"
    if $DRY_RUN; then
        echo "would check the newest snapshot is under ${STALE_HOURS}h old"
        return
    fi
    local newest age
    newest="$(restic snapshots --latest 1 --json | python3 -c '
import json, sys
snapshots = json.load(sys.stdin)
print(snapshots[-1]["time"] if snapshots else "")')"
    [[ -n "$newest" ]] || err "the repository has no snapshots at all"
    age="$(python3 - "$newest" <<'PY'
import datetime as dt, sys
when = dt.datetime.fromisoformat(sys.argv[1])
print(int((dt.datetime.now(dt.timezone.utc) - when).total_seconds() // 3600))
PY
)"
    echo "Newest snapshot: $newest (${age}h ago)"
    (( age <= STALE_HOURS )) || err "the newest snapshot is ${age}h old — the timer is not running"
    echo "Backups are current."
}

do_restore() {
    local target="${ARGS[1]:-}" snapshot="${ARGS[2]:-latest}"
    [[ -n "$target" ]] || err "where to? usage: ./backup.sh restore <new-directory> [snapshot]"
    # Restoring over the live vault is how a restore becomes the second disaster.
    [[ "$(readlink -f "$target")" != "$(readlink -f "$VAULT_DIR")" ]] \
        || err "refusing to restore over the live vault — restore elsewhere and copy what you need"
    [[ ! -e "$target" || -z "$(ls -A "$target" 2>/dev/null)" ]] \
        || err "$target is not empty — restore into a new directory"

    step "Restoring $snapshot into $target"
    run restic restore "$snapshot" --target "$target"
    step "Done"
    echo "Look at it, then copy back only what you need, e.g.:"
    echo "  cp '$target$VAULT_DIR/<note>.md' '$VAULT_DIR/'"
}

case "$COMMAND" in
    install) do_install ;;
    run)     do_run ;;
    check)   do_check ;;
    restore) do_restore ;;
    list)    restic snapshots ;;
    *)       err "unknown command: $COMMAND (try --help)" ;;
esac
