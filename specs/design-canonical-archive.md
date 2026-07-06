# Design note: canonical Markdown archive

Status: **approved 2026-07-05** · Supersedes the ad-hoc transcript storage by
generalizing it.

## Context

Today the pipeline is *source → summary note*, and the full content is mostly
discarded (only YouTube/Medium transcripts are kept). That loses information —
especially images/diagrams — and ties us to re-fetching (Supadata credits, scraping,
IP blocks) if we ever want to re-summarize.

We want to **throw anything at the bot — a link, a video, or a PDF — and keep a
faithful, durable copy** we can re-read, re-summarize, and search. The transcript
feature was the first step of this; this note generalizes it into the core model.

## The shift

Make a **canonical Markdown archive** the primary artifact; the summary becomes a
derived view of it.

```
source (link / video / PDF)
  → resolve source + pick adapter
  → dedup by identity (already captured?)
  → adapter: source → canonical Markdown  (full content, images as captions)
  → write archive companion:  sources/<note-stem>.source.md
  → summarize(archive) → Summary
  → write summary note  (links to the archive) + tags
  → reply
```

Every source type is just a **"→ Markdown" adapter**; everything downstream
(summarize, tags, note, dedup, `/ask`) is identical.

### Why it's better
- **Durable / link-rot insurance** — full content lives in the vault, readable in
  Obsidian offline, even after the source dies.
- **Re-summarize anytime, free** — no re-fetch; new prompt/model whenever.
- **Richer `/ask`** — retrieval draws on the full content, not just the TL;DR.
- **Unified pipeline** — links, videos, PDFs collapse into one concept.
- **Images handled** — captions become text the existing summarizer already reads.

## Adapters (source → canonical Markdown)

| Source | Adapter | Notes |
|---|---|---|
| Web link | **Jina Reader** (`r.jina.ai`, `x-with-generated-alt`) | VLM image captions inline. Captions **require `JINA_API_KEY`** (keyless returns 401 for the alt feature) → use Jina only when a key is set; else trafilatura (no captions). |
| Medium (member) | cookie fetch → (Jina or trafilatura) | Reuse the `sid` cookie; then convert. |
| YouTube | **Supadata** (or youtube-transcript-api) | Transcript is the archive body. |
| PDF (Telegram upload) | **markitdown** | PDF → Markdown, local/free. Scanned PDFs need OCR (later). |

Each adapter returns: `title`, `markdown`, `kind` (`article`/`transcript`/`pdf`),
`source_type`, and a stable **identity** for dedup.

## The archive file

`sources/<note-stem>.source.md` — one companion per capture (generalizes the
current `transcripts/` folder). The `.source.md` double-extension keeps
reorg-by-glob (`**/*.source.md`).

```markdown
---
title: "<title> — source"
source: "<url | file:sha256>"
date: 2026-07-05
kind: article            # article | transcript | pdf
adapter: jina            # jina | supadata | markitdown | trafilatura
note: "[[<note-stem>]]"
tags: [source, <source_type>]
---
<full markdown: text, tables, image captions>
```

The **summary note** links down to it via an `archive:` frontmatter link + a
`## Source` section (note: `source:` already holds the URL, so the archive link
uses `archive:`).

## Source identity & dedup

Generalize the current URL-only dedup (`find_by_url`) to `find_by_source(identity)`:
- **Links/video:** normalized URL (as today).
- **PDF/file:** `sha256` of the bytes → `source: file:<hash>` (so re-sending the
  same file is caught; filename kept in frontmatter for readability).

## Images — phased

1. **Captions (default):** Jina's VLM alt-text → images become text in the archive.
   Lightest, survives link rot, feeds the text pipeline directly.
2. **Downloaded (later):** save originals to `attachments/`, rewrite links — a true
   offline copy, at storage + plumbing cost. Opt-in.

## Compatibility & migration

- Existing summary notes are untouched.
- Existing `transcripts/*.transcript.md` → keep, or migrate to `sources/*.source.md`
  with `kind: transcript` (a small script, like `flatten_vault.py`). **Open decision.**
- New captures use the archive from day one.

## Phased plan (build adapter-by-adapter)

- **P1 — Archive core:** introduce the canonical-archive abstraction + storage
  (generalize transcript storage → `.source.md` + `kind`); summarizer reads the
  archive; keep current adapters producing Markdown. Always store the archive.
- **P2 — Jina adapter for links** (image captions), behind a toggle; compare vs
  trafilatura on a real article.
- **P3 — PDF via Telegram:** document handler + markitdown + content-hash dedup.
- **P4 (optional):** image download/attachments; markitdown for more doc types;
  Firecrawl fallback for JS-heavy/blocked sites.

## Decisions (resolved 2026-07-05)

1. **Archive naming:** unify to `.source.md` (+ `kind`). ✅
2. **Always archive** every capture, incl. plain articles. ✅
3. **Images:** captions now (download later). ✅
4. **Link adapter:** Jina primary (captions) with trafilatura fallback. ✅
5. **File dedup:** content `sha256`. ✅

## Out of scope (for now)
OCR for scanned PDFs; downloading/embedding original images; Firecrawl; semantic
`/ask`; a `/resummarize` command (enabled by this, but separate).
