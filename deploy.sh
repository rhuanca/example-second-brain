#!/usr/bin/env bash
#
# Deploy Second Brain services as systemd user services on Linux.
# Idempotent — re-run any time, e.g. after `git pull`.
#
#   ./deploy.sh telegram          capture bot            rr-second-brain-telegram
#   ./deploy.sh slack             ask bot (optional)     rr-second-brain-slack
#   ./deploy.sh kb                knowledge base         rr-second-brain-kb
#   ./deploy.sh telegram kb       several at once
#   ./deploy.sh all               every service already installed on this machine
#   ./deploy.sh --dry-run kb      check config and print the unit; change nothing
#
# All services share the project's .env and vault. For each service it:
#   1. checks its configuration with the app's own settings loader, so the script
#      and the service can never disagree about .env
#   2. (kb) builds/updates the embedding index — the first run downloads the model
#   3. writes ~/.config/systemd/user/<service>.service, then enables and restarts it
#   4. confirms it is actually running, and shows its logs if not
# Once per run it asks (sudo) whether to cap journald's disk use and whether to
# enable lingering so services start at boot — before any long-running step.
#
# It does not modify .env, pull code, or set up Cloudflare (see DEPLOY.md).

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
JOURNALD_DIR="/etc/systemd/journald.conf.d"
JOURNALD_DROPIN="$JOURNALD_DIR/second-brain.conf"
KNOWN_SERVICES=(telegram slack kb)

step() { echo; echo "=== $1 ==="; }
warn() { echo "Warning: $1" >&2; }
err()  { echo "Error: $1" >&2; exit 1; }

usage() {
    sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

unit_name() { echo "rr-second-brain-$1"; }

module_of() {
    case "$1" in
        telegram) echo "second_brain.main" ;;
        slack)    echo "second_brain.slack_main" ;;
        kb)       echo "second_brain.kb.main" ;;
    esac
}

description_of() {
    case "$1" in
        telegram) echo "Second Brain Telegram service (capture)" ;;
        slack)    echo "Second Brain Slack service (ask)" ;;
        kb)       echo "Second Brain knowledge base (MCP + browse UI)" ;;
    esac
}

# --- arguments ---------------------------------------------------------------
DRY_RUN=false
REQUESTED=()
for arg in "$@"; do
    case "$arg" in
        -n|--dry-run) DRY_RUN=true ;;
        -h|--help)    usage 0 ;;
        all)
            for s in "${KNOWN_SERVICES[@]}"; do
                [[ -f "$UNIT_DIR/$(unit_name "$s").service" ]] && REQUESTED+=("$s")
            done
            [[ ${#REQUESTED[@]} -gt 0 ]] || err "'all' found no installed services; name them, e.g. ./deploy.sh telegram"
            ;;
        telegram|slack|kb) REQUESTED+=("$arg") ;;
        *) echo "Unknown argument: $arg" >&2; usage 2 ;;
    esac
done
[[ ${#REQUESTED[@]} -gt 0 ]] || usage 2
# De-duplicate, keeping order.
SERVICES=()
for s in "${REQUESTED[@]}"; do
    [[ " ${SERVICES[*]} " == *" $s "* ]] || SERVICES+=("$s")
done

# --- preflight ---------------------------------------------------------------
step "Preflight"
command -v uv >/dev/null || err "'uv' not found. Install it: https://docs.astral.sh/uv/getting-started/installation/"
[[ -f "$PROJECT_DIR/.env" ]] || err ".env not found in $PROJECT_DIR. Run: cp env.example .env && \$EDITOR .env"
if ! $DRY_RUN; then
    command -v systemctl >/dev/null || err "systemctl not found — this script requires systemd."
    systemctl --user show-environment >/dev/null 2>&1 \
        || err "can't reach your systemd user manager (log in over SSH as this user, not via su/sudo)."
fi
UV_PATH="$(command -v uv)"
echo "Project:  $PROJECT_DIR"
echo "Services: ${SERVICES[*]}"
if $DRY_RUN; then echo "Mode:     dry run (nothing is installed, started or changed)"; fi

# --- questions first, so nothing prompts after the slow steps -----------------
WANT_JOURNALD=false
WANT_LINGER=false
if ! $DRY_RUN; then
    if compgen -G "$JOURNALD_DIR/second-brain.conf" >/dev/null || compgen -G "$JOURNALD_DIR/rr-second-brain*.conf" >/dev/null; then
        echo "journald size cap: already configured."
    else
        read -rp "Cap journald's disk use at 50M (writes $JOURNALD_DROPIN, needs sudo)? [Y/n] " ans
        [[ "$ans" =~ ^[Nn]$ ]] || WANT_JOURNALD=true
    fi
    if loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
        echo "Start at boot (lingering): already enabled for $USER."
    else
        read -rp "Start services at boot even when you're not logged in (lingering, needs sudo)? [Y/n] " ans
        [[ "$ans" =~ ^[Nn]$ ]] || WANT_LINGER=true
    fi
fi

# --- dependencies ------------------------------------------------------------
if $DRY_RUN; then
    [[ -d "$PROJECT_DIR/.venv" ]] || err "dry run needs an existing environment; run 'uv sync' first."
else
    step "Syncing dependencies (uv sync)"
    (cd "$PROJECT_DIR" && uv sync)
fi

# Validate a service's configuration with the app's own loader and print the
# values the unit needs as KEY=value lines. Exits non-zero with the app's message.
check_config() {
    (cd "$PROJECT_DIR" && uv run --no-sync python - "$1") <<'PY'
import sys

from second_brain.config import ConfigError

service = sys.argv[1]
try:
    if service == "kb":
        from second_brain.kb.config import KbSettings

        s = KbSettings.from_env()
        if not s.auth_tokens:
            raise ConfigError(
                "KB_AUTH_TOKENS is empty (one token per client). Generate one with: "
                "python3 -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        if not s.vault_path.is_dir():
            raise ConfigError(f"VAULT_PATH is not a directory: {s.vault_path}")
        print(f"VAULT_DIR={s.vault_path}")
        print(f"URL=http://{s.host}:{s.port}")
        print(f"CHAT={'on' if s.anthropic_api_key else 'off (no ANTHROPIC_API_KEY)'}")
    else:
        from second_brain.config import Settings

        s = Settings.from_env()
        if service == "slack":
            missing = [
                name
                for name, value in (
                    ("SLACK_BOT_TOKEN", s.slack_bot_token),
                    ("SLACK_APP_TOKEN", s.slack_app_token),
                    ("SLACK_ALLOWED_USER_ID", s.slack_allowed_user_id),
                )
                if not value
            ]
            if missing:
                raise ConfigError(f"Slack needs: {', '.join(missing)}")
        print(f"VAULT_DIR={s.vault_path}")
except ConfigError as exc:
    sys.exit(f"{service}: {exc}")
PY
}

# Whether this machine's user manager can apply the kb sandbox (it needs
# unprivileged user namespaces, which some distros restrict).
sandbox_supported() {
    systemd-run --user --quiet --wait --collect \
        -p NoNewPrivileges=yes -p PrivateTmp=yes -p "ReadOnlyPaths=$1" /bin/true >/dev/null 2>&1
}

write_unit() {  # service, vault dir, sandbox (true/false) -> unit text on stdout
    local service="$1" vault="$2" sandbox="$3"
    cat <<EOF
[Unit]
Description=$(description_of "$service")
After=network-online.target
Wants=network-online.target
# A broken config should fail and stay failed, not restart forever.
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
# Dependencies are synced by deploy.sh; don't let a boot-time start try the network.
ExecStart=$UV_PATH run --no-sync python -m $(module_of "$service")
Restart=on-failure
RestartSec=10s
EOF
    if [[ "$service" == "kb" && "$sandbox" == true ]]; then
        cat <<EOF

# Sandboxing: the knowledge base only reads the vault, so the kernel enforces it.
NoNewPrivileges=true
PrivateTmp=true
ReadOnlyPaths="$vault"
EOF
    fi
    cat <<EOF

[Install]
WantedBy=default.target
EOF
}

# --- per service -------------------------------------------------------------
declare -A SUMMARY=()
for service in "${SERVICES[@]}"; do
    unit="$(unit_name "$service")"
    step "$service: checking configuration"
    config="$(check_config "$service")" || err "fix .env and re-run."
    VAULT_DIR="" URL="" CHAT=""
    while IFS='=' read -r key value; do
        case "$key" in
            VAULT_DIR) VAULT_DIR="$value" ;;
            URL)       URL="$value" ;;
            CHAT)      CHAT="$value" ;;
        esac
    done <<<"$config"
    echo "Vault: $VAULT_DIR"

    sandbox=false
    if [[ "$service" == "kb" ]]; then
        if $DRY_RUN; then
            sandbox=true
        elif sandbox_supported "$VAULT_DIR"; then
            sandbox=true
        else
            warn "this machine's systemd can't sandbox user services (user namespaces restricted); kb will run without it."
        fi

        if $DRY_RUN; then
            echo "Would build the embedding index (scripts/build_index.py --apply)."
        else
            step "kb: building the embedding index (first run downloads the model, ~130MB)"
            (cd "$PROJECT_DIR" && uv run --no-sync python scripts/build_index.py --apply)
        fi
    fi

    unit_text="$(write_unit "$service" "$VAULT_DIR" "$sandbox")"
    if $DRY_RUN; then
        step "$service: unit that would be written to $UNIT_DIR/$unit.service"
        echo "$unit_text"
        SUMMARY[$service]="dry run OK"
    else
        mkdir -p "$UNIT_DIR"
        echo "$unit_text" > "$UNIT_DIR/$unit.service"
        SUMMARY[$service]="pending"
    fi
    if [[ -n "$URL" ]]; then
        SUMMARY[$service]+=" · $URL · chat $CHAT"
    fi
done

if $DRY_RUN; then
    step "Summary"
    for s in "${SERVICES[@]}"; do echo "  $(unit_name "$s"): ${SUMMARY[$s]}"; done
    exit 0
fi

# --- system-wide bits the user agreed to -------------------------------------
if $WANT_JOURNALD; then
    step "Capping journald at 50M"
    sudo install -d "$JOURNALD_DIR"
    printf '[Journal]\nStorage=persistent\nSystemMaxUse=50M\nSystemKeepFree=200M\n' \
        | sudo tee "$JOURNALD_DROPIN" >/dev/null
    sudo systemctl restart systemd-journald
fi
if $WANT_LINGER; then
    step "Enabling lingering for $USER"
    sudo loginctl enable-linger "$USER"
fi

# --- start everything and confirm it stays up --------------------------------
step "Starting"
systemctl --user daemon-reload
failed=0
for service in "${SERVICES[@]}"; do
    unit="$(unit_name "$service")"
    systemctl --user reset-failed "$unit.service" 2>/dev/null || true
    systemctl --user enable "$unit.service" >/dev/null
    systemctl --user restart "$unit.service"
done
# A crash on startup shows up within a few seconds; Type=simple is "active" at once.
sleep 5
for service in "${SERVICES[@]}"; do
    unit="$(unit_name "$service")"
    if systemctl --user is-active --quiet "$unit.service"; then
        SUMMARY[$service]="${SUMMARY[$service]/pending/running}"
    else
        failed=1
        SUMMARY[$service]="${SUMMARY[$service]/pending/FAILED}"
        echo
        warn "$unit is not running. Last log lines:"
        journalctl --user -u "$unit.service" -n 25 --no-pager || true
    fi
done

step "Summary"
for s in "${SERVICES[@]}"; do echo "  $(unit_name "$s"): ${SUMMARY[$s]}"; done
cat <<EOF

Logs:     journalctl --user -u rr-second-brain-<name> -f
Status:   systemctl --user status 'rr-second-brain*'
Update:   git pull && ./deploy.sh all
EOF
exit "$failed"
