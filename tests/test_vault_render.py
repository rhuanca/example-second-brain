import datetime
import unittest

import frontmatter

from second_brain.models import Para, Summary
from second_brain.vault import folder_for, note_filename, render_note, slugify

DATE = datetime.date(2026, 6, 30)


def _summary(**overrides):
    data = dict(
        title="Building Agentic Systems",
        tldr="A practical guide to agent loops.",
        key_points=["Tool use matters", "Keep context tight"],
        tags=["agentic-dev", "llm"],
        prototype_ideas=["A tiny planner loop"],
        para=Para.RESOURCES,
    )
    data.update(overrides)
    return Summary(**data)


class RoutingTest(unittest.TestCase):
    def test_folder_for_each_category(self):
        self.assertEqual(folder_for(Para.PROJECTS), "Projects")
        self.assertEqual(folder_for(Para.AREAS), "Areas")
        self.assertEqual(folder_for(Para.RESOURCES), "Resources")
        self.assertEqual(folder_for(Para.ARCHIVES), "Archives")

    def test_default_para_is_resources(self):
        self.assertEqual(_summary(para=Para.from_str("bogus")).para, Para.RESOURCES)


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
        self.assertEqual(post["para"], "resources")
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


if __name__ == "__main__":
    unittest.main()
