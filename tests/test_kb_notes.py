import tempfile
import unittest
from pathlib import Path

from second_brain.kb.notes import Card, archive_path, load_cards
from second_brain.vault import Vault

NOTE = """\
---
title: Ontology vs Metadata
source: https://youtu.be/abc
date: '2026-08-22'
tags:
- ontology
- rag
topics:
- knowledge-graphs
---

## TL;DR

Metadata describes attributes,
while ontology defines concepts.

## Key technical points

- Ontology reduces hallucinations
- Metadata is a prerequisite

## Prototype ideas

- Build a small graph

## Source

[[sources/x.source|Full source]]
"""


def _write(vault: Path, name: str, text: str) -> Path:
    path = vault / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


class LoadCardsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.vault = Vault(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_parses_every_section(self):
        _write(self.root, "2026-08-22-ontology", NOTE)

        (card,) = load_cards(self.vault)

        self.assertEqual(card.note_id, "2026-08-22-ontology")
        self.assertEqual(card.title, "Ontology vs Metadata")
        self.assertEqual(card.date, "2026-08-22")
        self.assertEqual(card.tags, ["ontology", "rag"])
        self.assertEqual(card.topics, ["knowledge-graphs"])
        self.assertEqual(len(card.key_points), 2)
        self.assertEqual(card.prototype_ideas, ["Build a small graph"])

    def test_tldr_is_collapsed_to_one_line(self):
        _write(self.root, "n", NOTE)
        (card,) = load_cards(self.vault)
        self.assertEqual(
            card.tldr, "Metadata describes attributes, while ontology defines concepts."
        )

    def test_source_section_is_not_mistaken_for_content(self):
        _write(self.root, "n", NOTE)
        (card,) = load_cards(self.vault)
        self.assertNotIn("Full source", card.tldr)

    def test_note_without_prototype_ideas(self):
        text = "---\ntitle: T\nsource: https://x.com/1\n---\n\n## TL;DR\n\nJust this.\n"
        _write(self.root, "n", text)

        (card,) = load_cards(self.vault)

        self.assertEqual(card.tldr, "Just this.")
        self.assertEqual(card.prototype_ideas, [])
        self.assertEqual(card.key_points, [])

    def test_note_with_empty_body(self):
        _write(self.root, "n", "---\ntitle: T\nsource: https://x.com/1\n---\n")
        (card,) = load_cards(self.vault)
        self.assertEqual(card.tldr, "")

    def test_notes_without_a_source_are_skipped(self):
        """Home.md and other hand-written pages are not captured resources."""
        _write(self.root, "n", NOTE)
        _write(self.root, "Home", "---\ntitle: Home\n---\n\nDashboard\n")

        cards = load_cards(self.vault)

        self.assertEqual([c.note_id for c in cards], ["n"])

    def test_cards_are_ordered_by_id(self):
        _write(self.root, "b-note", NOTE)
        _write(self.root, "a-note", NOTE)
        self.assertEqual([c.note_id for c in load_cards(self.vault)], ["a-note", "b-note"])

    def test_malformed_tags_field_is_coerced(self):
        text = "---\ntitle: T\nsource: https://x.com/1\ntags: solo\n---\n\n## TL;DR\n\nx\n"
        _write(self.root, "n", text)
        (card,) = load_cards(self.vault)
        self.assertEqual(card.tags, ["solo"])

    def test_summary_line_carries_id_title_and_tldr(self):
        card = Card(note_id="n1", title="T", tldr="Short summary.")
        self.assertEqual(card.summary_line(), "[n1] T — Short summary.")

    def test_archive_path_points_into_sources(self):
        self.assertEqual(
            archive_path(self.vault, "2026-08-22-x"),
            self.root / "sources" / "2026-08-22-x.source.md",
        )


if __name__ == "__main__":
    unittest.main()
