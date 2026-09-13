#!/usr/bin/env bash
#
# Deploy the Second Brain *knowledge base* (MCP + browse UI) as a systemd user
# service on Linux. Idempotent — safe to re-run after pulling code changes.
#
# A third service in the same project, sharing the one .env and vault:
#   rr-second-brain-telegram  -> Telegram (capture)       [deploy-telegram.sh]
#   rr-second-brain-slack     -> Slack (ask)              [deploy-slack.sh]
#   rr-second-brain-kb        -> MCP + browse (read-only) [this script]
#
# What it does:
#   1. Verifies prerequisites (systemd, uv, .env with VAULT_PATH and KB_AUTH_TOKENS)
#   2. Runs `uv sync`
#   3. Builds/updates the embedding index (downloads the model on first run)
#   4. Optionally writes a journald drop-in to cap log size at 50M (sudo)
#   5. Writes ~/.config/systemd/user/rr-second-brain-kb.service, sandboxed:
#      no new privileges, private /tmp, and the vault mounted read-only
#   6. Enables user lingering so the service starts at boot (sudo)
#   7. Enables and (re)starts the service, then prints status
#
# What it does NOT do:
#   - Modify your .env. It only reads VAULT_PATH (for the read-only mount) and
#     checks that KB_AUTH_TOKENS is set; it never prints either.
#   - Set up Cloudflare Tunnel/Access — see "Reach it from outside" in DEPLOY.md.
#     The service listens on 127.0.0.1 only.
#   - Pull code from git (run `git pull` separately)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="rr-second-brain-kb"
UNIT_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
JOURNALD_DROPIN="/etc/systemd/journald.conf.d/${SERVICE_NAME}.conf"
ENV_FILE="$PROJECT_DIR/.env"

step() { echo; echo "=== $1 ==="; }
err()  { echo "Error: $1" >&2; exit 1; }

# Last assignment of KEY in .env, with surrounding quotes stripped; empty if unset.
# (`|| true`: under pipefail a key with no match would otherwise end the script.)
env_value() {
    { grep -E "^[[:space:]]*$1=" "$ENV_FILE" || true; } | tail -n 1 | cut -d= -f2- \
        | sed -E 's/^[[:space:]]+|[[:space:]]+$//g; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/'
}

# ---------- 1. preflight ----------
step "Preflight"

command -v systemctl >/dev/null || err "systemctl not found — this script requires systemd."
command -v uv        >/dev/null || err "'uv' not found. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
[[ -f "$ENV_FILE" ]]            || err ".env not found at $ENV_FILE. Run: cp env.example .env && \$EDITOR .env"
[[ -n "$(env_value KB_AUTH_TOKENS)" ]] \
    || err "KB_AUTH_TOKENS is empty in .env. Generate one with: python3 -c 'import secrets; print(secrets.token_urlsafe(32))'"

RAW_VAULT="$(env_value VAULT_PATH)"
[[ -n "$RAW_VAULT" ]] || err "VAULT_PATH is empty in .env."
RAW_VAULT="${RAW_VAULT/#\~/$HOME}"
VAULT_DIR="$(cd "$PROJECT_DIR" && realpath -m "$RAW_VAULT")"
[[ -d "$VAULT_DIR" ]] || err "VAULT_PATH is not a directory: $VAULT_DIR"

UV_PATH="$(command -v uv)"
echo "Project:   $PROJECT_DIR"
echo "uv:        $UV_PATH"
echo "User:      $USER"
echo "Vault:     $VAULT_DIR (read-only to the service)"
echo "Unit file: $UNIT_FILE"

# ---------- 2. dependencies ----------
step "Syncing dependencies (uv sync)"
(cd "$PROJECT_DIR" && uv sync)

# ---------- 3. embedding index ----------
step "Building the embedding index (first run downloads the model, ~130MB)"
(cd "$PROJECT_DIR" && uv run python scripts/build_index.py --apply)

# ---------- 4. journald drop-in (optional) ----------
step "Journald config (caps logs at 50M to spare the SD card)"
if [[ -f "$JOURNALD_DROPIN" ]]; then
    echo "Already present at $JOURNALD_DROPIN — skipping."
else
    read -rp "Write $JOURNALD_DROPIN (requires sudo)? [Y/n] " ans
    if [[ ! "$ans" =~ ^[Nn]$ ]]; then
        sudo install -d /etc/systemd/journald.conf.d
        sudo tee "$JOURNALD_DROPIN" > /dev/null <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=50M
SystemKeepFree=200M
EOF
        sudo systemctl restart systemd-journald
        echo "Journald configured."
    else
        echo "Skipped."
    fi
fi

# ---------- 5. systemd unit ----------
step "Writing $UNIT_FILE"
mkdir -p "$(dirname "$UNIT_FILE")"
cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Second Brain knowledge base (MCP + browse UI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$UV_PATH run python -m second_brain.kb.main
Restart=on-failure
RestartSec=10s

# Sandboxing. The service only reads the vault, so the kernel enforces that.
# In a user unit these need unprivileged user namespaces; if the service fails
# to start with a namespace/capabilities error, comment these three lines out.
NoNewPrivileges=true
PrivateTmp=true
ReadOnlyPaths=$VAULT_DIR

[Install]
WantedBy=default.target
EOF
echo "Wrote unit file."

# ---------- 6. linger ----------
step "User lingering (lets the service start at boot without you logged in)"
if loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
    echo "Already enabled for $USER."
else
    read -rp "Enable lingering for $USER (requires sudo)? [Y/n] " ans
    if [[ ! "$ans" =~ ^[Nn]$ ]]; then
        sudo loginctl enable-linger "$USER"
        echo "Lingering enabled."
    else
        echo "Skipped — service will only run while you're logged in."
        echo "  Run later: sudo loginctl enable-linger $USER"
    fi
fi

# ---------- 7. reload + (re)start ----------
step "Reloading systemd and starting the service"
systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}.service" >/dev/null
systemctl --user restart "${SERVICE_NAME}.service"

# Give it a moment to crash if it's going to
sleep 2

step "Status"
systemctl --user --no-pager status "${SERVICE_NAME}.service" || true

PORT="$(env_value KB_PORT)"
cat <<EOF

Done. Listening on http://127.0.0.1:${PORT:-8765} (loopback only).

Tail live logs:
  journalctl --user -u $SERVICE_NAME -f

Restart after code changes:
  cd $PROJECT_DIR && git pull && uv sync && systemctl --user restart $SERVICE_NAME

Reach it from outside (Cloudflare Tunnel + Access): see DEPLOY.md.

EOF
