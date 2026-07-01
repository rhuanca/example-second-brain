"""Shared domain types used across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Para(str, Enum):
    """PARA categories. Values are lowercase; folder names are derived in vault."""

    PROJECTS = "projects"
    AREAS = "areas"
    RESOURCES = "resources"
    ARCHIVES = "archives"

    @classmethod
    def from_str(cls, value: str | None) -> "Para":
        """Parse a category name, defaulting to RESOURCES for anything unknown."""
        if not value:
            return cls.RESOURCES
        try:
            return cls(value.strip().lower())
        except ValueError:
            return cls.RESOURCES


@dataclass
class Summary:
    """The structured technical summary produced for an article."""

    title: str
    tldr: str
    key_points: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    prototype_ideas: list[str] = field(default_factory=list)
    para: Para = Para.RESOURCES
