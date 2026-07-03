"""Shared domain types used across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Summary:
    """The structured technical summary produced for an article."""

    title: str
    tldr: str
    key_points: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    prototype_ideas: list[str] = field(default_factory=list)
