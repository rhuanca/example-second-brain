import datetime
import unittest

import frontmatter

from second_brain.models import Summary
from second_brain.vault import (
    note_filename,
    render_note,
    render_transcript,
    slugify,
)

DATE = datetime.date(2026, 6, 30)


def _summary(**overrides):
    data = dict(
        title="Building Agentic Systems",
        tldr="A practical guide to agent loops.",
        key_points=["Tool use matters", "Keep context tight"],
        tags=["agentic-dev", "llm"],
        prototype_ideas=["A tiny planner loop"],
    )
    data.update(overrides)
    return Summary(**data)


class SlugTest(unittest.TestCase):
    def test_basic_slug(self):
        self.assertEqual(slugify("Building Agentic Systems"), "building-agentic-systems")

    def test_strips_punctuation_and_collapses(self):
        self.assertEqual(slugify("Hello,   World!! -- Again"), "hello-world-again")

    def test_empty_title_falls_back(self):
        self.assertEqual(slugify("!!!"), "untitled")

    def test_long_title_truncated(self):
        self.assertLessEqual(len(slugify("word " * 40)), 60)

    def test_filename_has_date_prefix(self):
        self.assertEqual(
            note_filename("My Article", DATE), "2026-06-30-my-article.md"
        )


class RenderTest(unittest.TestCase):
    def test_note_roundtrips_through_frontmatter(self):
        rendered = render_note(_summary(), "https://example.com/post", DATE)
        post = frontmatter.loads(rendered)
        self.assertEqual(post["title"], "Building Agentic Systems")
        self.assertEqual(post["source"], "https://example.com/post")
        self.assertEqual(post["date"], "2026-06-30")
        self.assertNotIn("para", post.keys())
        self.assertEqual(post["tags"], ["agentic-dev", "llm"])

    def test_body_contains_sections(self):
        rendered = render_note(_summary(), "https://example.com/post", DATE)
        self.assertIn("## TL;DR", rendered)
        self.assertIn("## Key technical points", rendered)
        self.assertIn("## Prototype ideas", rendered)
        self.assertIn("Tool use matters", rendered)

    def test_optional_sections_omitted_when_empty(self):
        rendered = render_note(
            _summary(key_points=[], prototype_ideas=[]),
            "https://example.com/post",
            DATE,
        )
        self.assertIn("## TL;DR", rendered)
        self.assertNotIn("## Key technical points", rendered)
        self.assertNotIn("## Prototype ideas", rendered)

    def test_no_transcript_link_by_default(self):
        rendered = render_note(_summary(), "https://example.com/post", DATE)
        self.assertNotIn("## Transcript", rendered)
        self.assertNotIn("transcript", frontmatter.loads(rendered).metadata)

    def test_transcript_link_added_when_provided(self):
        link = "transcripts/2026-06-30-building-agentic-systems.transcript"
        rendered = render_note(
            _summary(), "https://example.com/post", DATE, transcript_link=link
        )
        self.assertIn("## Transcript", rendered)
        self.assertIn(f"[[{link}|Full transcript]]", rendered)
        self.assertEqual(frontmatter.loads(rendered)["transcript"], f"[[{link}]]")


class RenderTranscriptTest(unittest.TestCase):
    def test_transcript_file_shape(self):
        rendered = render_transcript(
            "Agent Memory",
            "https://youtu.be/abc",
            DATE,
            "  full transcript text  ",
            note_stem="2026-06-30-agent-memory",
            source_type="youtube",
        )
        post = frontmatter.loads(rendered)
        self.assertEqual(post["title"], "Agent Memory — transcript")
        self.assertEqual(post["source"], "https://youtu.be/abc")
        self.assertEqual(post["tags"], ["transcript", "youtube"])
        self.assertEqual(post["note"], "[[2026-06-30-agent-memory]]")
        self.assertEqual(post.content, "full transcript text")


if __name__ == "__main__":
    unittest.main()
