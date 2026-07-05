# Deploy

The project runs as **systemd user services** (one per channel), sharing one
`.env` and one vault:

| Service | Script | Role |
|---|---|---|
| `rr-second-brain-telegram` | `./deploy-telegram.sh` | **Telegram** — capture (required) |
| `rr-second-brain-slack` | `./deploy-slack.sh` | **Slack** — ask (optional) |

## Prerequisites

- Linux with systemd, and [uv](https://docs.astral.sh/uv/) installed
- A filled `.env` in the project root (`cp env.example .env` then edit). Slack
  additionally needs `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` / `SLACK_ALLOWED_USER_ID`.

## Deploy

```bash
./deploy-telegram.sh   # Telegram (capture)
./deploy-slack.sh    # Slack (ask) — only if you use Slack
```

Both are idempotent — re-run any time. Each runs `uv sync`, writes its
`~/.config/systemd/user/<name>.service`, enables boot-start (linger), and starts.

## Manage

```bash
systemctl --user status 'rr-second-brain*'                # both at once
systemctl --user restart rr-second-brain-telegram        # Telegram
systemctl --user restart rr-second-brain-slack           # Slack
journalctl  --user -u    rr-second-brain-slack -f        # live logs (Slack)
```

## Update after code changes

```bash
git pull && uv sync
systemctl --user restart rr-second-brain-telegram rr-second-brain-slack
```

## Uninstall

```bash
systemctl --user disable --now rr-second-brain-telegram rr-second-brain-slack
rm ~/.config/systemd/user/rr-second-brain*.service
systemctl --user daemon-reload
```
