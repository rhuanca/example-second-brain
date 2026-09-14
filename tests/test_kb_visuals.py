import unittest

import numpy as np

from second_brain.kb.notes import Card
from second_brain.kb.topics import Topic
from second_brain.kb.visuals import (
    OTHER,
    Series,
    label_positions,
    map_layout,
    month_span,
    project_2d,
    squarified_treemap,
    timeline_chart,
    topic_overlaps,
    topic_slots,
    youtube_thumbnail,
)


def _card(note_id, date="2026-08-01", tags=(), topics=()):
    return Card(note_id=note_id, title=note_id, date=date, tags=list(tags), topics=list(topics))


class PaletteTest(unittest.TestCase):
    def test_slots_follow_taxonomy_order_and_fold_after_eight(self):
        topics = [Topic(f"t{i}", f"T{i}") for i in range(10)]
        slots = topic_slots(topics)
        self.assertEqual([slots[f"t{i}"] for i in range(8)], list(range(1, 9)))
        self.assertEqual(slots["t8"], OTHER)
        self.assertEqual(slots["t9"], OTHER)

    def test_colour_follows_the_topic_not_its_position_in_a_filtered_list(self):
        topics = [Topic("a", "A"), Topic("b", "B"), Topic("c", "C")]
        self.assertEqual(topic_slots(topics)["c"], 3)


class ThumbnailTest(unittest.TestCase):
    def test_youtube_urls_get_a_thumbnail(self):
        self.assertEqual(
            youtube_thumbnail("https://youtu.be/dQw4w9WgXcQ"),
            "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
        )
        self.assertEqual(
            youtube_thumbnail("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3"),
            "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
        )

    def test_everything_else_does_not(self):
        for url in ["https://example.com/post", "", "javascript:alert(1)", "https://youtu.be/bad"]:
            with self.subTest(url=url):
                self.assertIsNone(youtube_thumbnail(url))


class TreemapTest(unittest.TestCase):
    def test_areas_are_proportional_and_fill_the_box(self):
        items = [("a", 6), ("b", 6), ("c", 4), ("d", 3), ("e", 2), ("f", 2), ("g", 1)]
        rects = squarified_treemap(items, 600, 400)

        self.assertEqual(sorted(r.key for r in rects), sorted(k for k, _ in items))
        total_area = sum(r.w * r.h for r in rects)
        self.assertAlmostEqual(total_area, 600 * 400, delta=1)
        per_unit = 600 * 400 / sum(v for _, v in items)
        for rect in rects:
            value = dict(items)[rect.key]
            self.assertAlmostEqual(rect.w * rect.h, value * per_unit, delta=1)
            self.assertGreaterEqual(rect.x, -1e-6)
            self.assertGreaterEqual(rect.y, -1e-6)
            self.assertLessEqual(rect.x + rect.w, 600 + 1e-6)
            self.assertLessEqual(rect.y + rect.h, 400 + 1e-6)

    def test_tiles_stay_reasonably_square(self):
        rects = squarified_treemap([(str(i), 1) for i in range(12)], 600, 400)
        worst = max(max(r.w / r.h, r.h / r.w) for r in rects)
        self.assertLess(worst, 3)

    def test_degenerate_inputs(self):
        self.assertEqual(squarified_treemap([], 100, 100), [])
        self.assertEqual(squarified_treemap([("a", 0)], 100, 100), [])
        (only,) = squarified_treemap([("a", 5)], 100, 50)
        self.assertEqual((only.w, only.h), (100, 50))


class OverlapTest(unittest.TestCase):
    def test_counts_shared_notes_per_pair(self):
        cards = [
            _card("1", topics=("a", "b")),
            _card("2", topics=("b", "a")),
            _card("3", topics=("a", "c", "b")),
            _card("4", topics=("c",)),
        ]
        overlaps = topic_overlaps(cards, lambda c: c.topics)
        self.assertEqual(overlaps[0], ("a", "b", 3))
        self.assertIn(("a", "c", 1), overlaps)


class TimelineTest(unittest.TestCase):
    def setUp(self):
        self.series = [Series("yt", "YouTube", 1, {"source": "youtube"}), Series("ar", "Articles", 2)]

    def test_months_include_gaps(self):
        self.assertEqual(month_span("2025-11", "2026-02"), ["2025-11", "2025-12", "2026-01", "2026-02"])

    def test_stacks_counts_per_month(self):
        cards = [
            _card("1", "2026-06-02", tags=("youtube",)),
            _card("2", "2026-06-20"),
            _card("3", "2026-08-01", tags=("youtube",)),
            _card("undated", ""),
        ]
        chart = timeline_chart(
            cards, self.series, lambda c: "yt" if "youtube" in c.tags else "ar"
        )

        self.assertEqual([c.month for c in chart.columns], ["2026-06", "2026-07", "2026-08"])
        self.assertEqual([c.total for c in chart.columns], [2, 0, 1])
        june = chart.columns[0]
        self.assertEqual([(s.series.key, s.count) for s in june.segments], [("yt", 1), ("ar", 1)])
        self.assertEqual(june.segments[0].href, "/notes?source=youtube&month=2026-06")
        self.assertEqual(june.segments[1].href, "/notes?month=2026-06")
        self.assertEqual(june.segments[0].title, "Jun 2026 · YouTube: 1 note")
        self.assertEqual(chart.table[0], ("2026-06", {"yt": 1, "ar": 1}, 2))
        self.assertLessEqual(chart.bar_width, 24)

    def test_ticks_are_clean_and_cover_the_peak(self):
        cards = [_card(str(i), "2026-01-01") for i in range(37)]
        chart = timeline_chart(cards, [Series("all", "All", 1)], lambda c: "all")
        values = [v for _, v in chart.ticks]
        self.assertEqual(values[0], 0)
        self.assertGreaterEqual(values[-1], 37)
        self.assertLessEqual(len(values), 6)
        step = values[1] - values[0]
        self.assertIn(step, {1, 2, 5, 10, 20, 50})

    def test_no_dated_notes(self):
        self.assertIsNone(timeline_chart([_card("x", "")], self.series, lambda c: "yt"))


class MapTest(unittest.TestCase):
    def test_similar_vectors_land_closer_than_different_ones(self):
        rng = np.random.default_rng(0)
        a = rng.normal(0, 0.05, (5, 16)) + np.eye(16)[0]
        b = rng.normal(0, 0.05, (5, 16)) + np.eye(16)[1]
        points = map_layout([f"a{i}" for i in range(5)] + [f"b{i}" for i in range(5)],
                            np.vstack([a, b]), 800, 500)
        xy = {p.note_id: np.array([p.x, p.y]) for p in points}
        within = np.linalg.norm(xy["a0"] - xy["a1"])
        across = np.linalg.norm(xy["a0"] - xy["b0"])
        self.assertLess(within, across)
        for p in points:
            self.assertTrue(0 <= p.x <= 800 and 0 <= p.y <= 500)

    def test_projection_is_deterministic(self):
        vectors = np.random.default_rng(1).normal(size=(20, 8))
        np.testing.assert_allclose(project_2d(vectors), project_2d(vectors.copy()))

    def test_tiny_inputs(self):
        self.assertEqual(map_layout([], np.zeros((0, 4)), 100, 100), [])
        (only,) = map_layout(["x"], np.ones((1, 4)), 100, 100)
        self.assertEqual((only.x, only.y), (50.0, 50.0))
        two = map_layout(["x", "y"], np.eye(2), 100, 100)
        self.assertEqual(len(two), 2)

    def test_identical_notes_do_not_hide_each_other(self):
        vectors = np.vstack([np.ones((4, 8)), np.zeros((1, 8))])
        points = map_layout(["a", "b", "c", "d", "e"], vectors, 800, 500)
        for i, p in enumerate(points):
            for q in points[i + 1 :]:
                self.assertTrue(abs(p.x - q.x) >= 9 or abs(p.y - q.y) >= 9, (p, q))

    def test_label_sits_on_a_real_note(self):
        from second_brain.kb.visuals import Point

        ring = [Point("1", 0, 0), Point("2", 200, 0), Point("3", 100, 10)]
        (position,) = label_positions({"t": ring}).values()
        self.assertEqual(position, (100, 10))

    def test_labels_skip_collisions(self):
        from second_brain.kb.visuals import Point

        groups = {
            "big": [Point("1", 100, 100), Point("2", 102, 100), Point("3", 98, 100)],
            "clash": [Point("4", 110, 104)],
            "far": [Point("5", 400, 300)],
        }
        labels = label_positions(groups)
        self.assertEqual(set(labels), {"big", "far"})


if __name__ == "__main__":
    unittest.main()
