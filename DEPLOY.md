# Deploy

Runs the bot as a **systemd user service** named `rr-second-brain`.

## Prerequisites

- Linux with systemd, and [uv](https://docs.astral.sh/uv/) installed
- A filled `.env` in the project root (`cp env.example .env` then edit)

## Deploy

```bash
./deploy.sh
```

Idempotent — re-run it any time. It runs `uv sync`, writes
`~/.config/systemd/user/rr-second-brain.service`, enables boot-start (linger),
and starts the service.

## Manage

```bash
systemctl --user status  rr-second-brain      # running?
systemctl --user restart rr-second-brain
systemctl --user stop    rr-second-brain
journalctl  --user -u    rr-second-brain -f   # live logs
```

## Update after code changes

```bash
git pull && uv sync && systemctl --user restart rr-second-brain
```

## Uninstall

```bash
systemctl --user disable --now rr-second-brain
rm ~/.config/systemd/user/rr-second-brain.service
systemctl --user daemon-reload
```
