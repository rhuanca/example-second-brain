# Deploy

The project runs as **systemd user services**, sharing one `.env` and one vault,
all deployed by one script:

| Service | Deploy | Role |
|---|---|---|
| `rr-second-brain-telegram` | `./deploy.sh telegram` | **Telegram** — capture (required) |
| `rr-second-brain-slack` | `./deploy.sh slack` | **Slack** — ask (optional) |
| `rr-second-brain-kb` | `./deploy.sh kb` | **Knowledge base** — MCP for agents, browse UI, chat; read-only (optional) |

## Prerequisites

- Linux with systemd, and [uv](https://docs.astral.sh/uv/) installed
- Logged in as the user the services should run as (over SSH, not `su`/`sudo`)
- A filled `.env` in the project root (`cp env.example .env` then edit). Slack
  additionally needs `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` / `SLACK_ALLOWED_USER_ID`.
  The knowledge base needs `KB_AUTH_TOKENS` (see below).

## Deploy

```bash
./deploy.sh --dry-run telegram kb   # optional: check .env and preview the units, change nothing
./deploy.sh telegram kb             # deploy one or more services
./deploy.sh all                     # re-deploy every service already installed
```

Idempotent — re-run any time. It:

1. Asks up front (sudo) whether to cap journald's disk use at 50M and whether to
   enable lingering so services start at boot — both once per machine.
2. Runs `uv sync`, then checks each service's config with the app's own settings
   loader, so a bad `.env` stops the deploy with the same message the service
   would give.
3. For `kb`, builds the embedding index (the model, ~130MB, downloads once into
   `KB_INDEX_DIR`) and sandboxes the service with the vault read-only — or warns
   and runs unsandboxed if this machine's systemd can't (some distros restrict
   the user namespaces that needs).
4. Writes `~/.config/systemd/user/<service>.service`, restarts, and confirms each
   service stays up, printing its recent logs if it doesn't. A service with a
   broken config stops after 5 failed starts instead of restarting forever.

## Manage

```bash
systemctl --user status 'rr-second-brain*'               # all at once
systemctl --user restart rr-second-brain-kb             # one service
journalctl  --user -u    rr-second-brain-kb -f          # live logs
```

## Update after code changes

```bash
git pull && ./deploy.sh all
```

## Uninstall

```bash
systemctl --user disable --now rr-second-brain-telegram rr-second-brain-slack rr-second-brain-kb
rm ~/.config/systemd/user/rr-second-brain*.service
systemctl --user daemon-reload
```

---

## Knowledge base: tokens and local use

The service listens on `127.0.0.1:8765` only. Two kinds of access:

- **`/mcp` (agents)** needs `Authorization: Bearer <token>` where the token is one
  of the comma-separated `KB_AUTH_TOKENS`. Use one token per client so you can
  revoke a laptop without breaking the others. Generate one with:

  ```bash
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
  ```

  Tokens are only accepted in the header; a token in the URL is refused.
- **Everything else (browse UI)** needs a Cloudflare Access login (below). Until
  Access is configured the UI answers 403, unless you set
  `KB_WEB_ALLOW_UNAUTHENTICATED=true` for local use — then reach it from your
  laptop over SSH instead of exposing it:

  ```bash
  ssh -L 8765:127.0.0.1:8765 you@vault-server   # then open http://127.0.0.1:8765
  ```

Connect Claude Code on the server itself (or through that SSH forward):

```bash
claude mcp add --transport http --scope user secondbrain \
  http://127.0.0.1:8765/mcp \
  -H "Authorization: Bearer <token>"
```

**Rotate a token:** add the new one to `KB_AUTH_TOKENS` (comma-separated),
restart the service, switch the client to it, then remove the old one and restart
again. **Revoke:** remove it and restart.

## Knowledge base: reach it from outside (Cloudflare Tunnel + Access)

The design (`specs/design-knowledge-base.md`) puts the service behind an
**outbound** Cloudflare Tunnel, so no port is opened on your router and a
changing home IP never matters, with **Cloudflare Access** authenticating at the
edge. The only cost is a domain (~$10/yr); Tunnel and Access are free on the
Zero Trust Free plan. Dashboard menu names shift over time — look for the
equivalent if a name below doesn't match.

Know the trade-off: Cloudflare terminates TLS, so it can see the traffic in
plaintext. Tailscale is the alternative if that matters more than browser SSO.

### 1. Domain and Zero Trust

1. Buy or move a domain to Cloudflare (e.g. `example.com`). The service will live
   at a subdomain such as `kb.example.com`.
2. Open **Zero Trust** in the Cloudflare dashboard, pick a team name (this gives
   you `<team>.cloudflareaccess.com`) and choose the **Free** plan.

### 2. Tunnel

1. **Networks → Tunnels → Create a tunnel** → *Cloudflared*, name it
   `second-brain-kb`.
2. Run the install command it shows on the vault server (it installs
   `cloudflared` as a system service holding your tunnel token).
3. Add a **public hostname**: `kb.example.com` → service `HTTP` →
   `127.0.0.1:8765`.

### 3. Access application

1. **Access → Applications → Add an application → Self-hosted**, domain
   `kb.example.com`.
2. Add a policy **Allow** for yourself: *Include → Emails →* your address. Under
   login methods, enable **One-time PIN** or Google.
3. **Access → Service credentials → Service Tokens → Create**. Copy the Client ID
   and Client Secret now (the secret is shown once).
4. Back on the application, add a second policy with action **Service Auth**:
   *Include → Service Token →* the token you created. This is how agents get past
   the edge without a browser sign-in.
5. From the application's overview copy the **Application Audience (AUD) tag**.

### 4. Tell the service

Add to `.env` on the vault server, then `systemctl --user restart rr-second-brain-kb`:

```bash
KB_ALLOWED_HOSTS=kb.example.com
KB_CF_ACCESS_TEAM_DOMAIN=<team>.cloudflareaccess.com
KB_CF_ACCESS_AUD=<the AUD tag>
KB_WEB_ALLOW_UNAUTHENTICATED=false
```

- `KB_ALLOWED_HOSTS` — without it, MCP requests arriving through the tunnel are
  refused with `421 Invalid Host header` (DNS-rebinding protection).
- `KB_CF_ACCESS_*` — the browse UI then requires the signed assertion Access adds
  after sign-in, so a request that somehow bypasses the edge is refused.

### 5. Connect agents from anywhere

```bash
claude mcp add --transport http --scope user secondbrain \
  https://kb.example.com/mcp \
  -H "CF-Access-Client-Id: <client id>" \
  -H "CF-Access-Client-Secret: <client secret>" \
  -H "Authorization: Bearer <token>"
```

`--scope user` makes it available in every project. Codex takes the same URL and
headers in its own MCP config.

### 6. Verify (do these once)

1. From **off the home network** (phone on cellular): `https://kb.example.com`
   asks you to sign in, and an address not in the policy is refused.
2. `curl -i https://kb.example.com/mcp` with no service-token headers is refused
   at the edge; with them but without the bearer token, the service answers 401.
3. In Claude Code, ask something that spans two notes and check it called
   `search_notes` then `get_note`.
4. Restart the router (or `sudo systemctl restart cloudflared`) and confirm the
   hostname works again with nothing edited — no DNS change, no port-forward.
5. Revoke the service token in the dashboard and confirm the agent is refused
   immediately. Then create a new one.

On the server, `ss -tlnp | grep 8765` should show `127.0.0.1:8765` only.
