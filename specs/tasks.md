# Tasks: Second Brain Telegram Bot

Ordered, atomic, independently verifiable. Implement top to bottom; do not start
a task until the one above is green and committed.

- [x] 1. **Project scaffold** — done. See Done log.

- [x] 2. **config.py** — done. See Done log.

- [ ] 3. **urls.py** — extract first URL from text; normalize (strip tracking
      params + trailing slash) for a stable dedup key; return None when no URL. —
      verify: unittest over messages with/without URLs, tracking params, trailing
      slashes.

- [ ] 4. **vault.py: routing, slug, render** — PARA folder routing (default
      Resources), filename slug from title+date, render Markdown note with YAML
      frontmatter (title, source, date, para, tags). — verify: unittest: rendered
      note round-trips through `frontmatter.loads`; routing picks correct folder;
      slug is filesystem-safe.

- [ ] 5. **vault.py: write + dedup** — ensure PARA folders exist under VAULT_PATH;
      `is_duplicate(url)` scans existing notes' `source`; `write_note()` writes to
      the right folder and refuses on duplicate. — verify: unittest using a
      tmp vault dir: write a note, dedup detects it; second write is blocked.

- [ ] 6. **summarizer.py** — build the prompt; call Claude (`anthropic`); parse
      strict-JSON into a `Summary` dataclass; tolerant fallback to raw-text note
      when JSON parse fails. — verify: unittest with the Anthropic client mocked:
      valid JSON → populated Summary; malformed JSON → fallback Summary with raw
      text; tags normalized to lowercase-kebab.

- [ ] 7. **fetcher.py** — `fetch(url)` → (title, article_text) via trafilatura;
      raise a clear FetchError on empty/blocked content. — verify: unittest with
      trafilatura's download/extract mocked: good HTML → title+text; empty → FetchError.

- [ ] 8. **bot.py orchestration (pure pipeline fn)** — a `handle_url(text)`
      pipeline function independent of Telegram I/O: extract → dedup → fetch →
      summarize → write → return a reply string; maps each failure to a clear
      message and never writes a partial note. — verify: unittest wiring mocked
      fetcher/summarizer/vault: happy path returns summary + writes note; no-URL,
      duplicate, fetch-fail, summarize-fail each return the right message and
      write nothing.

- [ ] 9. **bot.py Telegram wiring + main** — handlers, single-user allow-list
      (ignore other ids), long-polling startup in `main.py`, create PARA folders
      on boot. — verify: unittest: allow-list filter accepts my id / rejects
      others (handler-level, Telegram update objects faked); manual run documented
      in README (needs real token + key).

- [ ] 10. **README + .env.example polish** — setup, run, and "get a Telegram bot
      token / set VAULT_PATH" instructions; note Phase 2 (ask-your-second-brain)
      as future. — verify: README lists every env var in config.py; `uv run`
      command documented.

## Done log
- 1. Project scaffold — uv project, deps, `second_brain/` + `tests/`, `.gitignore`,
     `env.example` (named without leading dot — `.env*` is hook-blocked), smoke
     test green. Commit `c948d54`.
- 2. config.py — frozen `Settings` dataclass, `from_env()` with validation;
     vault path resolved to absolute, positive user-id guard, api key optional.
     12 tests green. Review: no blockers; addressed relative-path resolve.
     Commit `7f74908`.
