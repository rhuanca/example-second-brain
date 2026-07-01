"""Obsidian vault: PARA routing, note filenames, and Markdown rendering.

This module is the interface to the vault on disk. Task 4 covers the pure pieces
(routing, slug, render); writing and dedup are added in task 5.
"""

from __future__ import annotations

import datetime as _dt
import re

import frontmatter

from second_brain.models import Para, Summary

# PARA category -> folder name inside the vault.
PARA_FOLDERS: dict[Para, str] = {
    Para.PROJECTS: "Projects",
    Para.AREAS: "Areas",
    Para.RESOURCES: "Resources",
    Para.ARCHIVES: "Archives",
}

_SLUG_MAX_LEN = 60
_slug_strip_re = re.compile(r"[^a-z0-9]+")


def folder_for(para: Para) -> str:
    """Return the vault folder name for a PARA category."""
    return PARA_FOLDERS[para]


def slugify(title: str) -> str:
    """Turn a title into a filesystem-safe, lowercase-kebab slug."""
    slug = _slug_strip_re.sub("-", title.lower()).strip("-")
    if len(slug) > _SLUG_MAX_LEN:
        slug = slug[:_SLUG_MAX_LEN].rstrip("-")
    return slug or "untitled"


def note_filename(title: str, date: _dt.date) -> str:
    """Build a date-prefixed Markdown filename, e.g. 2026-06-30-my-article.md."""
    return f"{date.isoformat()}-{slugify(title)}.md"


def render_note(summary: Summary, source_url: str, date: _dt.date) -> str:
    """Render a Summary into Markdown with YAML frontmatter."""
    body_sections = [f"## TL;DR\n\n{summary.tldr.strip()}"]

    if summary.key_points:
        points = "\n".join(f"- {p}" for p in summary.key_points)
        body_sections.append(f"## Key technical points\n\n{points}")

    if summary.prototype_ideas:
        ideas = "\n".join(f"- {i}" for i in summary.prototype_ideas)
        body_sections.append(f"## Prototype ideas\n\n{ideas}")

    post = frontmatter.Post(
        "\n\n".join(body_sections),
        title=summary.title,
        source=source_url,
        date=date.isoformat(),
        para=summary.para.value,
        tags=list(summary.tags),
    )
    return frontmatter.dumps(post)
