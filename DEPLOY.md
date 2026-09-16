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

Menu names move around; these were current in September 2026, with the older
names in brackets. Do **Access first, tunnel second**: the moment the tunnel has
a public hostname, that hostname answers from the internet, and Access is what
keeps it yours.

### 1. Domain and Zero Trust

1. Buy or move a domain to Cloudflare (**Domains → Overview → Add a domain**,
   formerly "Add a site"). Wait for *Active*. The service will live at a
   subdomain such as `kb.example.com`.
2. Open **Zero Trust** (sidebar, under *Protect & connect*). First time, pick a
   team name and the **Free** plan; that gives you
   `<team>.cloudflareaccess.com`. If the Zero Trust menu already lists Networks /
   Access controls / Settings, it is set up — find the team domain under
   **Settings**.

### 2. Access application (before the tunnel)

1. **Access controls → Applications → Add an application → Self-hosted**
   (a policy can name a hostname that does not exist yet).
2. **Destinations → Public hostnames:** subdomain `kb`, your domain, path empty —
   an empty path covers `/mcp` too.
3. **Policies → Create new policy:** name `only-me`, action **Allow**,
   *Include → selector Emails → your address*. Everything else is default-deny.
4. **Login methods:** One-time PIN needs no setup (Cloudflare emails a code).
   Add Google if you prefer it.
5. Save the application (the button is **Create**), then reopen it →
   **Additional settings → AUD tag** and copy the **Application Audience (AUD)
   Tag**. That chip is in the filter row; the "Tags" *section* on the same page is
   something else (App Launcher tags).

### 3. Service token, for agents

1. **Access controls → Service credentials → Service Tokens → Create Service
   Token**, name `claude-code`, longest duration (or non-expiring). Copy the
   **Client ID** (ends `.access`) and **Client Secret** (starts `cfast_`) now —
   the secret is shown once. Later you can only rotate it.
2. **Applications → kb → Policies → Create new policy:** name `agents`,
   **Action: Service Auth**, *Include → selector Service Token → `claude-code`*.
   In the selector list "Service Auth" is a greyed **group heading**, not an
   option; pick `Service Token` under it.
3. **Attach it and press Save.** A policy created from the Policies list is only
   a definition until it is added to the application (*Add existing policy* on
   the application's Policies tab). This is the single most common reason agents
   still get a login page.

### 4. Tunnel

1. **Networks → Tunnels & Mesh → Create a tunnel** → *Cloudflared*, name it
   `second-brain-kb`.
2. Run the install command it shows **on the vault server** (it installs
   `cloudflared` as a system service holding the tunnel token). Running it on the
   wrong machine is easy to do; see Troubleshooting for the clean removal.
3. Add a **public hostname**: `kb.example.com` → service `HTTP` →
   `127.0.0.1:8765`.
4. The tunnel page should show *Healthy* with one connector. If you ever remove
   `cloudflared` from a machine, the tunnel goes *Down* with no connectors —
   **Add a connector** re-issues the install command for the right host.

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
2. `claude mcp list` shows `secondbrain` as **✔ Connected**.
3. In Claude Code, ask something that spans two notes and check it called
   `search_notes` then `get_note`.
4. Restart the router (or `sudo systemctl restart cloudflared`) and confirm the
   hostname works again with nothing edited — no DNS change, no port-forward.
5. Revoke the service token in the dashboard and confirm the agent is refused
   immediately. Then create a new one.

On the server, `ss -tlnp | grep 8765` should show `127.0.0.1:8765` only.

### Troubleshooting

**Read the error's *shape* first — it says which layer refused you:**

| What you see | Who refused | Usual cause |
|---|---|---|
| Cloudflare login page, or `302`/`text/html` on `/mcp` | Cloudflare Access | The `agents` (Service Auth) policy is not attached to the application, or the service-token headers are wrong |
| *"That account does not have access"* on Cloudflare's page | Access, identity | The signed-in email is not the one in the `only-me` policy. Add One-time PIN as a login method, or fix the address; sign out at `https://<team>.cloudflareaccess.com/cdn-cgi/access/logout` |
| *"Forbidden: sign in through Cloudflare Access."* (plain text) | **the app** | Access let you through, but the app could not verify the token: `KB_CF_ACCESS_TEAM_DOMAIN` or `KB_CF_ACCESS_AUD` is wrong |
| `401 Unauthorized` on `/mcp` | the app | The bearer token is not in `KB_AUTH_TOKENS` |
| `421 Invalid Host header` | the app (MCP transport) | `KB_ALLOWED_HOSTS` does not list the public hostname |

**The app logs name the exact JWT failure:**

```bash
journalctl --user -u rr-second-brain-kb -n 30 --no-pager | grep -i "access token rejected"
```

`InvalidAudienceError` → AUD tag. `InvalidIssuerError` → team domain.
`UnicodeError`/`UnicodeEncodeError` → a malformed team domain (a **leading dot**
gives an empty DNS label, which fails at the IDNA encode inside the key fetch)
or an invisible character from a copy-paste. To see the values and the key fetch
exactly as the service does them:

```bash
uv run python - <<'PY'
import traceback, jwt
from second_brain.kb.config import KbSettings
s = KbSettings.from_env()
print("team:", repr(s.cf_access_team_domain), "\naud:", repr(s.cf_access_aud))
domain = s.cf_access_team_domain.strip().removeprefix("https://").rstrip("/")
try:
    print("keys OK:", len(jwt.PyJWKClient(f"https://{domain}/cdn-cgi/access/certs").get_jwk_set().keys))
except Exception:
    traceback.print_exc()
PY
```

**Probe `/mcp` end to end** (200 + `application/json` means both locks passed):

```bash
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' -X POST https://kb.example.com/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H "CF-Access-Client-Id: $ID" -H "CF-Access-Client-Secret: $SECRET" \
  -H "Authorization: Bearer $KB_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

**cloudflared installed on the wrong machine** — remove it completely:

```bash
sudo cloudflared service uninstall
sudo apt-get purge -y cloudflared
sudo rm -f /usr/local/bin/cloudflared
sudo rm -rf /etc/cloudflared ~/.cloudflared
sudo rm -f /etc/apt/sources.list.d/cloudflared.list /usr/share/keyrings/cloudflare-public-v2.gpg
```

The tunnel then shows *Down* with no connectors; **Add a connector** to install it
on the right host. Treat the tunnel token like a password — it is enough to serve
that hostname from anywhere.
