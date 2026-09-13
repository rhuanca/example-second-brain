# Knowledge base service: browse + MCP over the second brain

Status: **implemented 2026-09-13** (design agreed 2026-09-08) · Build order: topics → MCP → web UI ·
See **Implementation notes** at the end for what was decided at build time.


## Context

The vault has 73 notes and grows ~35/month (Jul 37, Aug 32) — this is a daily habit,
not a fixed corpus. Three problems: the list is too long to browse, there is no way to
see its shape, and the 213 `## Prototype ideas` already captured are never re-read.

**The blocker is a missing topic layer, not a missing UI.** Measured: 224 distinct
tags over 73 notes, **167 (75%) used exactly once**; the two most common "tags" are
source types (`youtube` 41, `article` 26); one concept is split across ~26 spellings
(`agentic-systems` 13, `agentic-ai` 8, `llm-agents` 4, …). Cause: `summarizer.py:22-37`
asks for "2-5 topic tags" with no vocabulary and no awareness of existing tags.
`Home.md` already works around this by hand-ORing 10-16 tag variants per bucket — that
manual work is what this replaces. Any UI built on raw tags renders 224 nodes, three
quarters of them singletons: worse than the flat list today.

Also measured: no note→note links exist (all 132 wikilinks point note→archive), so any
structure must be derived. TL;DR + key points are uniform and clean (~67 words, 5-6
bullets every note) — usable as card content as-is.

**Outcome:** one read-only service on the vault server exposing an auto-discovered
topic layer through two front doors — MCP for Claude Code/Codex, HTML for browsing.

## Decisions locked

- **Topics are auto-discovered**, not hand-curated. Nothing seeded from `Home.md`.
- **Hybrid retrieval from the start**: topic filter, then semantic rank within topic.
- **Build order: topics → MCP → web UI.**
- **Read-only.** No write/capture/delete tools. Capture stays on Telegram.
- **Pull, not push.** Collector writes markdown; this service reads the same directory.
  The vault stays the single source of truth; capture can never be broken by this
  service being down.
- **Same repo**, new `second_brain/kb/` subpackage. Collector code untouched.
- **Reachable from outside the home network, permanently, with no IP to maintain.**
  Cloudflare Tunnel (outbound only, so a changing home IP never matters), on a domain
  the user buys (~$10/yr; the tunnel and Access themselves are free). Cloudflare Access
  authenticates at the edge — IdP sign-in for the browser, service token for MCP —
  plus an independent app-level token check. App binds to `127.0.0.1`.
  See **Security & external access**.
- **No database.** At 1,300 notes the embedding index is ~4 MB — a numpy file loaded
  in-process, not Postgres/pgvector/Chroma/Neo4j.

Why hybrid rather than sending the whole card menu: at ~35 notes/month the menu is 7k
tokens today but ~49k in a year, and agent sessions are sporadic so most searches hit
a **cold** cache — ~$0.25/search within a year, ~$0.67 at three. Semantic retrieval is
~$0.005 flat regardless of corpus size.

## Architecture

```
Telegram ─▶ second_brain (collector) ─▶ vault/*.md ◀── single source of truth
                                            │  reads off disk
                                            ▼
                              second_brain/kb  (one process)
                    topics · embedding index · hybrid retrieval
                                            │
                   ┌────────────────────────┴────────────────────┐
                   ▼                                             ▼
            /mcp  (bearer token)                          /  (browse UI)
       Claude Code / Codex, anywhere              topics → cards → note → archive
```

One FastAPI process serves both, so there is one port, one unit, one tunnel.

## Phase 1 — Topic layer

**`second_brain/kb/topics.py`** — discovery and assignment.

- Discovery reads all note **cards** (title + TL;DR ≈ 100 tokens each; 73 notes ≈ 7k
  tokens) in one Claude call and returns topics with a stable `id`, display `name`,
  one-line description, and members. Two levels: main topics with sub-clusters.
- **Refinement, not rediscovery.** Pass the existing topic set in and ask for
  merge/split/add/retire. Keeps ids stable so links and MOCs don't rot. Renames are
  display-only; ids never change.
- New notes are assigned to existing topics; anything that fits nothing lands in an
  explicit **unassigned** pile. When that pile crosses a threshold, that is the signal
  to re-run discovery — the vault says when the taxonomy is stale rather than a cron
  guessing.
- Quality gates worth asserting in tests: every note assigned or explicitly
  unassigned; no topic holding one note or more than ~40% of the vault.

**`scripts/discover_topics.py`** — follows the existing `scripts/` pattern exactly
(`sys.path` insert, optional vault-path positional, **dry-run by default**, `--apply`);
see `scripts/dedupe_vault.py` as the template. `--apply` writes a `topics:` list into
each note's frontmatter, leaving free-form `tags:` untouched — non-destructive and
reversible. Topic definitions live outside the vault (see storage below).

Use the **Batch API** for the one-off backfill across all notes: not latency-sensitive,
50% cost.

## Phase 2 — MCP server

**`second_brain/kb/mcp_server.py`** — tools, cheapest → most expensive:

| Tool | Returns | ~Tokens |
|---|---|---|
| `list_topics()` | topic index | small, constant |
| `search_notes(query, topic?)` | note **cards** only | ~100 × k |
| `get_note(id)` | one full note | ~500 |
| `get_source(id)` | one archive | ~4,700 |
| `ask(question)` | prose answer, reuses `ask.py` | varies |

**`search_notes` must return cards, never bodies.** Ten cards ≈ 1k tokens vs ten
archives ≈ 47k — the single ~50x decision in the whole design, and it also stops one
search from swallowing the agent's context.

Note on `ask`: when the consumer is itself an agent, `search` + `get` beats a pre-baked
answer, since it can reason over raw material alongside its own task. `ask` is there
for the web UI and cheap one-shot lookups, not as the primary agent path.

**Transport** streamable HTTP (both Claude Code and Codex speak MCP). **Auth** is
covered in Security & external access: Cloudflare Access service token at the edge,
plus an independent bearer check before any vault read.

## Phase 3 — Web browse UI

Server-rendered FastAPI + Jinja, no SPA: topic tiles → note cards → note detail
(TL;DR, key points, prototype ideas, source link, archive). Facets: topic × source type
× date.

Deliberately **not** a force-directed graph as primary navigation — with ~15 topics and
73 notes it is a star layout: busy, low information, and poor on a phone. If a graph is
added later it should be a secondary view of topic **co-occurrence**.

## Security & external access

The asset is an entire personal reading history behind one endpoint, on a box inside a
home network. Worst realistic outcome is **disclosure**, not corruption (the service is
read-only) — but the home network behind it is the thing to protect.

### 1. Exposure: outbound tunnel, never port forwarding

**The hard requirement: it must keep working from outside the house without ever
touching an IP again.** This is what selects a tunnel over every other option.

Run `cloudflared` on the vault server. It dials **out** to Cloudflare and holds the
connection open, so:

- **No inbound IP is ever published, and DNS is a one-time CNAME to the tunnel's UUID
  — not to the home address.** ISP rotates the IP, router reboots or gets replaced,
  the house moves: the tunnel reconnects and the hostname keeps working. No dynamic
  DNS client, no records to update, no port-forward rules to re-apply. Ever.
- No inbound ports opened; no router/NAT/firewall changes.
- TLS terminated at the edge, certificates managed.
- The origin IP is never exposed.
- One place to revoke everything.

**Cost and plan:** select **Zero Trust Free** ($0/seat, 50-seat limit, described by
Cloudflare as "best for home labs") — it includes Access application policies, identity
provider integration, and service tokens, which is everything this design uses. Its one
relevant limit is 24h log retention vs 30 days on Standard ($7/seat); irrelevant for a
single user. Tunnel itself is free on any plan (free outright since July 2026) — Zero
Trust is selected for *Access*, the auth layer.

The only actual charge is **domain registration, ~$10/yr, bought separately from the
registrar** — not part of the plan choice. A `trycloudflare.com` quick tunnel is *not*
an option: random hostname, dies with the process.

**Bind the app to `127.0.0.1` only.** The tunnel connects locally, so the service is
unreachable from the LAN as well as the internet — the tunnel becomes the only path in.

### 2. Authentication: two consumers, two mechanisms, both at the edge

This is the crux — a human browser and a headless agent cannot authenticate the same
way.

- **Web UI (human): Cloudflare Access with an identity provider.** Google sign-in, or
  one-time PIN to a single allow-listed email. MFA comes from the IdP. No login page to
  build, no password to store, no session code to get wrong — and unauthenticated
  requests **never reach the home box**.
- **MCP (agent): Cloudflare Access service token** (client id + secret headers), which
  Claude Code and Codex can send as static headers. Machine access without interactive
  SSO, still terminated at the edge.

### 2b. What this looks like in daily use

**Opening the web app:** browse to `https://kb.<domain>` on laptop or phone. Access
intercepts at the edge, Google sign-in or an emailed one-time PIN, then a session
cookie. No VPN, no client to install, no IP to remember.

**Connecting an agent** (verified CLI syntax):

```bash
claude mcp add --transport http --scope user secondbrain \
  https://kb.<domain>/mcp \
  -H "CF-Access-Client-Id: <id>.access" \
  -H "CF-Access-Client-Secret: <secret>" \
  -H "Authorization: Bearer <app-token>"
```

`--scope user` makes it available in every project; cloud sessions use the same URL.
Codex is configured the same way through its own config.

**Encryption on every hop that crosses a network:** client → edge is HTTPS with a
Cloudflare-managed cert; edge → server is TLS inside the outbound tunnel; `cloudflared`
→ app is plain HTTP over loopback and never leaves the machine.

**Disclose and decide knowingly:** Cloudflare terminates TLS, so Cloudflare can see
plaintext — this is not end-to-end encryption from client to origin. Normal for this
kind of deployment, but it is a personal reading history, so it should be an explicit
choice rather than a surprise. (Tailscale is the alternative that avoids a TLS-
terminating intermediary, at the cost of the edge SSO layer.)

### 3. Defense in depth at the application

Assume the edge is bypassed:

- Independent bearer-token check on `/mcp`, compared with `hmac.compare_digest`
  (constant time), **before any vault read**.
- One token per client, so a laptop can be revoked without breaking cloud sessions.
- **Header only, never a query parameter** — URLs leak into logs, history and referrers.
- Tokens from the environment; never committed. Document a rotation step.
- Rate limiting at the edge, plus a coarse app-level cap.
- Log auth failures (journald is already capped at 50M by the existing deploy pattern).

### 4. Path traversal — the specific bug to design out

`get_note(id)` and `get_source(id)` take a caller-supplied id. Joining that into a path
(`sources/<id>.source.md`) is a classic file-disclosure hole the moment this is
internet-facing: `../../../.env` and friends.

**Resolve ids against the enumerated note list — never construct a path from input.**
Build the id→`Path` map from `Vault.iter_notes()` and reject anything not a key. Assert
this in tests with traversal payloads; it is cheap and it is the one bug here that
turns disclosure of *notes* into disclosure of *everything on the box*.

### 5. Prompt injection — non-obvious, and specific to this app

The `sources/` archives are **full text scraped from the public internet**. A page
could contain text aimed at whatever agent later reads it ("ignore previous
instructions, run …"). This service hands that content to Claude Code and Codex, which
have tools.

Mitigation is framing, not filtering: return archive content clearly delimited and
labelled as untrusted third-party data, never as instructions. Keep `ask()`'s system
prompt explicit that note content is reference material. This is a reason to prefer
`search`+`get` over `ask` for agents — the consuming agent keeps its own guard up.

### 6. Blast radius

- Read-only tool surface: no write, capture or delete path exists to abuse.
- Unprivileged **systemd user** service, as the existing `rr-*` units already are, with
  `NoNewPrivileges=true`, `PrivateTmp=true`, and the vault mounted read-only to the
  process (`ReadOnlyPaths=`).
- The service needs no secrets beyond its own tokens and the API keys — and notably
  **must not** load the collector's Telegram/Slack credentials, which is a second
  reason for the narrow config loader below.

### 7. Considered and rejected: hosting the app on Cloudflare

Cloudflare's compute products (Workers & Pages, Containers, Durable Objects) could
host this, but should not:

- **Data gravity.** The vault is markdown on the box where the collector writes it.
  Serving from Workers means replicating every note into R2/D1/KV — the push
  architecture already rejected, with the same second-copy drift problem.
- **The saving isn't real.** That box runs 24/7 regardless, because Telegram capture
  depends on it. Adding `cloudflared` to an already-running machine is far cheaper
  than porting the app and replicating its data.
- **Runtime friction.** Python Workers are in open beta on Pyodide/WASM with an
  *ephemeral in-memory filesystem* (no reading notes off disk), 50ms CPU/request free
  or 30s paid, and ~10s cold starts for a FastAPI-shaped import (~1s snapshotted).
  Containers would run the Python properly but need the Workers Paid plan at
  **$5/month** — $60/yr vs $10/yr for a domain.

Use Cloudflare as the **front door** (tunnel + Access), not the host. Retiring the home
box entirely is a legitimate but different project: push to R2, run the reader at the
edge, accept the second copy.

## Retrieval (hybrid)

**`second_brain/kb/retrieval.py`** + **`second_brain/kb/embeddings.py`**

1. **Topic filter** — narrow candidates by explicit `topic=` argument, or by cosine of
   the query against topic descriptions.
2. **Semantic rank** — cosine over note embeddings within the surviving candidates.
3. **Fallback** — rank across everything when no topic clears a threshold.

Plugs into the existing injectable seam: `ask()` already accepts `searcher=`
(`ask.py:56-60`), and `search(notes, question, *, limit)` (`ask.py:101-112`) is the
signature to match, so this is a drop-in with no changes to the Telegram/Slack paths.

**Index storage: outside the vault** (e.g. `~/.local/share/second-brain-kb/`). Only
`topics:` frontmatter and any generated MOCs go into the vault — derived binaries must
not pollute Obsidian or sync.

**Incremental embedding**, keyed by content hash, so a daily capture embeds one note
rather than re-embedding the corpus.

## Reuse (do not rewrite)

- `ask.load_notes()` / `ask.Note` (`ask.py:41-47, 77-98`) — note loading, already
  handles the malformed-`tags` edge case.
- `Vault.iter_notes()` (`vault.py:118-120`) and `SOURCES_DIR` (`vault.py:25`) for
  locating archives at `sources/<stem>.source.md`.
- Note schema from `render_note` (`vault.py:41-70`) — frontmatter keys and the
  `## TL;DR` / `## Key technical points` / `## Prototype ideas` sections.
- The `rr-*` systemd deploy pattern: add `deploy-kb.sh` modeled on `deploy-slack.sh`,
  plus a row in `DEPLOY.md`'s service table.

**`second_brain/kb/config.py` — a narrow loader.** Do not call `Settings.from_env()`:
it `_require`s `TELEGRAM_BOT_TOKEN`/`TELEGRAM_ALLOWED_USER_ID` (`config.py:52-64`),
which a browsing service has no business needing. Read `VAULT_PATH`,
`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, plus new `KB_AUTH_TOKEN` and the embedding key.

## Testing

Stdlib `unittest`, one file per module (`tests/test_kb_topics.py`, `…_retrieval.py`,
`…_mcp.py`, `…_web.py`), mirroring the existing convention. Network and LLM always
mocked via keyword injection, as `ask()` and `handle_url()` already do.

Cover at minimum: topic assignment is stable across a re-run with unchanged input;
ids survive a rename; `search_notes` returns cards and **never** note bodies (a token
regression test — this is the 50x property); incremental embedding skips unchanged
notes.

Security tests are not optional here, since this is internet-facing:

- missing, empty, wrong and near-miss tokens are all rejected **before** any vault read;
- `get_note`/`get_source` reject traversal ids (`../`, absolute paths, URL-encoded
  variants, symlinks) and anything not in the enumerated note map;
- no endpoint accepts a token via query string.

## Verification

1. `uv run python -m unittest discover -s tests` — all green.
2. `uv run python scripts/discover_topics.py "<vault copy>"` — dry run; eyeball the
   topics, confirm coverage and no degenerate buckets. Then `--apply` on the copy and
   diff the frontmatter.
3. Start the service locally; `curl /mcp` with a good token, no token, and a traversal
   id — confirm 200 / 401 / rejected.
4. Confirm the listener is localhost-only: `ss -tlnp` shows `127.0.0.1`, and the
   service is unreachable from another machine on the LAN.
5. Connect Claude Code to it and ask a question that spans two notes; confirm from the
   transcript that it called `search_notes` then `get_note` — not a single giant blob.
6. Open the browse UI on a phone-width viewport; walk topic → card → note → archive.
7. Deploy via `deploy-kb.sh`, bring up the tunnel, then from **off the home network**
   (phone on cellular, not wifi): the browse URL prompts for Access sign-in and refuses
   an unknown email; `/mcp` without the service token is refused at the edge; Claude
   Code in a cloud session completes step 5 end to end.
8. **Prove the no-IP-maintenance property**: restart the router (or the `cloudflared`
   service) and confirm the hostname works again with nothing edited — no DNS change,
   no port-forward, no DDNS. This is the requirement that chose the architecture, so
   verify it rather than assume it.
9. Revoke the service token and confirm the cloud session immediately fails — proving
   the kill switch works before relying on it.

## Open items to settle at build time

- **Embedding provider.** Anthropic's Messages API does not serve embeddings — verify
  the current recommended option rather than assuming. If the vault server is a small
  box (the journald "spare the SD card" note in `deploy-telegram.sh` suggests a Pi), an
  API-based embedding avoids running torch on it; a real server could embed locally and
  keep the data on-box.
- Whether to *also* put the service on a tailnet for the user's own machines, so
  day-to-day access skips the public path entirely. Cheap to add, narrows exposure.
- Whether `summarizer.py` should tag new captures from the discovered topic set (less
  drift, small change to the collector) or leave assignment entirely to the periodic
  pass. Deferred: it touches the collector, which is otherwise untouched.
- Whether generated topic MOCs in the vault are still wanted once the web UI exists —
  they are the only surface that works in Obsidian on mobile.

## Implementation notes (2026-09-13)

Built in four commits on `feat/kb-service`: retrieval → MCP → web UI → deploy/docs.
Decisions taken at build time, and where the build differs from the text above:

- **Embedding provider: local fastembed** (`BAAI/bge-small-en-v1.5`, ONNX on CPU,
  384 dims) on an x86 server. No API key, no note text leaves the box; the model
  (~65 MB on disk) is downloaded into `KB_INDEX_DIR/models` by `build_index.py`.
- **What is embedded:** the card — title, TL;DR and key points — not the archive.
- **Hybrid ranking:** an explicit `topic=` filters. Otherwise the query is compared
  with each topic's name + description; notes in the (up to two) matching topics
  rank first and the rest of the vault fills the remaining slots, so a topic match
  can reorder results but never hide a relevant note. Thresholds were calibrated
  on a sample vault (unrelated notes 0.53–0.61, the right note 0.73–0.84) and
  should be re-checked on the real one.
- **Freshness:** the service re-lists the vault at most every 30 s (names + mtimes)
  and only re-parses/embeds when that changed; the taxonomy file is re-read then too.
- **MCP:** SDK v2 (`mcp.server.MCPServer`), stateless streamable HTTP with JSON
  responses. The SDK's Host check defaults to localhost only, so the public
  hostname must be listed in `KB_ALLOWED_HOSTS` or tunnel traffic gets 421. Text
  tools return content only (no duplicate `structuredContent`).
- **Browser auth at the app:** the `Cf-Access-Jwt-Assertion` is verified (RS256,
  audience, issuer, expiry, certs from `<team>/cdn-cgi/access/certs`). With Access
  unconfigured the UI is closed unless `KB_WEB_ALLOW_UNAUTHENTICATED=true`, and that
  flag cannot override a configured Access check.
- **Web UI:** one `/notes?topic=&source=&month=` listing covers the three facets
  instead of separate topic pages. No JavaScript; a strict CSP forbids scripts.
- **Not done here:** Telegram/Slack `/ask` still use lexical search (the `searcher=`
  swap is a follow-up — it would make those processes load the ONNX model); the
  Batch API backfill was unnecessary at ~12k tokens; topic MOCs in the vault were
  not generated; the tunnel/Access setup is documented in DEPLOY.md, not automated.
