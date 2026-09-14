"""Geometry and colour assignment for the browse UI's pictures.

Pure functions, no web imports: the templates only draw what is computed here,
so layouts are unit-testable and the SVG stays dumb.

Colour follows the entity, never its rank. Topics take the eight categorical
slots in taxonomy order (the order discovery keeps stable), so a topic is the
same colour on every page and after every filter; a ninth topic onwards shares a
neutral "Other". The slot order is the validated reference palette from the
dataviz method (CSS custom properties `--c1`..`--c8` in `base.html`) -- its order
is what keeps neighbours distinguishable under colour-vision deficiency, so it is
not to be shuffled.
"""

from __future__ import annotations

import calendar
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlencode

import numpy as np

from second_brain.kb.notes import Card
from second_brain.kb.topics import Topic
from second_brain.youtube import video_id

SLOTS = 8
OTHER = 0  # the neutral slot
SOURCE_ORDER = ("youtube", "article", "medium", "pdf")
_MONTH = re.compile(r"^(\d{4})-(\d{2})")

# --- colour -------------------------------------------------------------------


def topic_slots(topics: Sequence[Topic]) -> dict[str, int]:
    """{topic_id: 1..8}, in taxonomy order; later topics map to OTHER."""
    return {t.id: (i + 1 if i < SLOTS else OTHER) for i, t in enumerate(topics)}


def source_slots() -> dict[str, int]:
    return {source: i + 1 for i, source in enumerate(SOURCE_ORDER)}


def youtube_thumbnail(source_url: str) -> str | None:
    vid = video_id(source_url or "")
    return f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else None


# --- topic overview -------------------------------------------------------------


@dataclass
class Rect:
    key: str
    x: float
    y: float
    w: float
    h: float


def squarified_treemap(
    items: Sequence[tuple[str, float]], width: float, height: float
) -> list[Rect]:
    """Lay `items` (key, value) out as rectangles whose areas are proportional to value.

    Squarified layout (Bruls, Huizing, van Wijk): rows are filled while adding a
    tile keeps the row's worst aspect ratio from getting worse, so tiles stay close
    to square and labels fit. Larger values are placed first.
    """
    items = [(k, float(v)) for k, v in items if v > 0]
    if not items or width <= 0 or height <= 0:
        return []
    scale = width * height / sum(v for _, v in items)
    pending = sorted(((k, v * scale) for k, v in items), key=lambda kv: -kv[1])

    rects: list[Rect] = []
    x, y, w, h = 0.0, 0.0, float(width), float(height)
    row: list[tuple[str, float]] = []

    def worst(candidate: list[tuple[str, float]], side: float) -> float:
        total = sum(a for _, a in candidate)
        biggest = max(a for _, a in candidate)
        smallest = min(a for _, a in candidate)
        return max(side * side * biggest / (total * total), total * total / (side * side * smallest))

    def place(done: list[tuple[str, float]], x, y, w, h):
        total = sum(a for _, a in done)
        if w >= h:  # a column down the left edge
            col = total / h
            cursor = y
            for key, area in done:
                rects.append(Rect(key, x, cursor, col, area / col))
                cursor += area / col
            return x + col, y, w - col, h
        strip = total / w  # a row along the top edge
        cursor = x
        for key, area in done:
            rects.append(Rect(key, cursor, y, area / strip, strip))
            cursor += area / strip
        return x, y + strip, w, h - strip

    for item in pending:
        side = min(w, h)
        if not row or worst(row + [item], side) <= worst(row, side):
            row.append(item)
        else:
            x, y, w, h = place(row, x, y, w, h)
            row = [item]
    if row:
        place(row, x, y, w, h)
    return rects


def topic_overlaps(
    cards: Iterable[Card], topics_for: Callable[[Card], list[str]], *, limit: int = 5
) -> list[tuple[str, str, int]]:
    """The topic pairs that most often share a note: [(topic_a, topic_b, count)]."""
    pairs: Counter[tuple[str, str]] = Counter()
    for card in cards:
        ids = sorted(set(topics_for(card)))
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                pairs[(a, b)] += 1
    ranked = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(a, b, n) for (a, b), n in ranked[:limit]]


# --- timeline -----------------------------------------------------------------


@dataclass
class Series:
    key: str
    label: str
    slot: int
    link: dict[str, str] = field(default_factory=dict)  # query params for its notes


@dataclass
class Segment:
    series: Series
    count: int
    path: str
    title: str
    href: str


@dataclass
class Column:
    month: str
    label: str
    total: int
    x: float
    segments: list[Segment] = field(default_factory=list)


@dataclass
class TimelineChart:
    width: int
    height: int
    plot_left: float
    plot_top: float
    plot_bottom: float
    plot_right: float
    bar_width: float
    columns: list[Column]
    series: list[Series]
    ticks: list[tuple[float, int]]  # (y, value)
    table: list[tuple[str, dict[str, int], int]]  # (month, {series key: n}, total)


def month_of(card: Card) -> str | None:
    match = _MONTH.match(card.date or "")
    return f"{match.group(1)}-{match.group(2)}" if match else None


def month_span(first: str, last: str) -> list[str]:
    """Every month from `first` to `last` inclusive, gaps included -- time stays honest."""
    year, month = int(first[:4]), int(first[5:7])
    end = (int(last[:4]), int(last[5:7]))
    months = []
    while (year, month) <= end:
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def month_label(month: str, *, with_year: bool) -> str:
    name = calendar.month_abbr[int(month[5:7])]
    return f"{name} {month[:4]}" if with_year else name


def timeline_chart(
    cards: Sequence[Card],
    series: Sequence[Series],
    series_of: Callable[[Card], str],
    *,
    width: int = 720,
    height: int = 260,
) -> TimelineChart | None:
    """Stacked monthly columns: one segment per series, stacked in `series` order."""
    dated = [(m, card) for card in cards if (m := month_of(card))]
    if not dated:
        return None
    months = month_span(min(m for m, _ in dated), max(m for m, _ in dated))
    counts: dict[str, Counter[str]] = {m: Counter() for m in months}
    for month, card in dated:
        counts[month][series_of(card)] += 1

    left, right, top, bottom = 34.0, 8.0, 10.0, 26.0
    plot_w, plot_h = width - left - right, height - top - bottom
    band = plot_w / len(months)
    bar = min(24.0, band * 0.72)
    peak = max(sum(c.values()) for c in counts.values())
    step, top_value = _nice_scale(peak)
    unit = plot_h / top_value
    label_every = max(1, math.ceil(44 / band))

    columns = []
    for i, month in enumerate(months):
        total = sum(counts[month].values())
        x = left + band * i + (band - bar) / 2
        with_year = i == 0 or month.endswith("-01")
        column = Column(
            month=month,
            label=month_label(month, with_year=with_year) if i % label_every == 0 else "",
            total=total,
            x=x,
        )
        baseline = top + plot_h
        stacked = [(s, counts[month][s.key]) for s in series if counts[month][s.key]]
        for j, (s, n) in enumerate(stacked):
            seg_h = n * unit
            is_top = j == len(stacked) - 1
            # 2px surface gap between stacked segments; the gap comes out of the
            # segment above, so the column's total height still matches the scale.
            drawn = seg_h - (2 if j > 0 else 0)
            y0 = baseline - seg_h
            column.segments.append(
                Segment(
                    series=s,
                    count=n,
                    path=_column_path(x, y0 + (seg_h - drawn), bar, max(drawn, 1.0), rounded=is_top),
                    title=f"{month_label(month, with_year=True)} · {s.label}: {n} note{'s' if n != 1 else ''}",
                    href=_notes_href({**s.link, "month": month}),
                )
            )
            baseline = y0
        columns.append(column)

    ticks = [(top + plot_h - v * unit, v) for v in range(0, top_value + 1, step)]
    table = [
        (month, {s.key: counts[month][s.key] for s in series}, sum(counts[month].values()))
        for month in months
    ]
    return TimelineChart(
        width=width,
        height=height,
        plot_left=left,
        plot_top=top,
        plot_bottom=top + plot_h,
        plot_right=width - right,
        bar_width=bar,
        columns=columns,
        series=[s for s in series if any(counts[m][s.key] for m in months)],
        ticks=ticks,
        table=table,
    )


def _nice_scale(peak: int) -> tuple[int, int]:
    """A clean tick step (1/2/5 × 10^n) giving at most five intervals, and the axis top."""
    peak = max(peak, 1)
    magnitude = 1
    while True:
        for factor in (1, 2, 5):
            step = factor * magnitude
            if math.ceil(peak / step) <= 5:
                return step, step * math.ceil(peak / step)
        magnitude *= 10


def _column_path(x: float, y: float, w: float, h: float, *, rounded: bool) -> str:
    """A column with a 4px rounded data-end on top and a square foot on the baseline."""
    r = min(4.0, w / 2, h) if rounded else 0.0
    if r <= 0:
        return f"M{x:.1f},{y + h:.1f}V{y:.1f}H{x + w:.1f}V{y + h:.1f}Z"
    return (
        f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
        f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{y + h:.1f}Z"
    )


def _notes_href(params: dict[str, str]) -> str:
    query = urlencode({k: v for k, v in params.items() if v})
    return f"/notes?{query}" if query else "/notes"


# --- map ----------------------------------------------------------------------


@dataclass
class Point:
    note_id: str
    x: float
    y: float


def project_2d(vectors: np.ndarray) -> np.ndarray:
    """Principal-component projection to 2D, with a deterministic orientation.

    Notes that embed similarly land close together. PCA rather than UMAP/t-SNE:
    it needs nothing beyond numpy, is deterministic, and at a few hundred notes the
    first two components already separate the broad themes.
    """
    vectors = np.asarray(vectors, dtype=np.float64)
    n = len(vectors)
    if n == 0:
        return np.zeros((0, 2))
    if n == 1:
        return np.zeros((1, 2))
    centred = vectors - vectors.mean(axis=0)
    _, _, components = np.linalg.svd(centred, full_matrices=False)
    basis = components[:2]
    # SVD signs are arbitrary; fix them so the map doesn't flip between runs.
    for i in range(len(basis)):
        if basis[i][np.argmax(np.abs(basis[i]))] < 0:
            basis[i] = -basis[i]
    coords = centred @ basis.T
    if coords.shape[1] == 1:
        coords = np.hstack([coords, np.zeros((n, 1))])
    return coords


def map_layout(
    note_ids: Sequence[str],
    vectors: np.ndarray,
    width: float,
    height: float,
    *,
    pad: float = 24.0,
) -> list[Point]:
    """Place notes in a width×height box, one uniform scale so distances stay honest."""
    coords = project_2d(vectors)
    if len(coords) == 0:
        return []
    lo, hi = coords.min(axis=0), coords.max(axis=0)
    span = np.where(hi - lo > 1e-9, hi - lo, 1.0)
    scale = min((width - 2 * pad) / span[0], (height - 2 * pad) / span[1])
    used = (hi - lo) * scale
    offset = np.array([(width - used[0]) / 2, (height - used[1]) / 2])
    placed = (coords - lo) * scale + offset
    points = [
        Point(note_id, float(x), float(height - y))  # SVG y grows downward
        for note_id, (x, y) in zip(note_ids, placed)
    ]
    return _spread_overlaps(points, width, height)


def _spread_overlaps(points: list[Point], width: float, height: float, *, gap: float = 9.0) -> list[Point]:
    """Nudge dots that land on (almost) the same spot onto a small spiral around it.

    Near-identical notes embed to the same place; without this, one dot hides the
    others and they can't be hovered or clicked. Deterministic: input order decides.
    """
    taken: list[Point] = []
    for point in points:
        x, y, turn = point.x, point.y, 0
        while any(abs(x - q.x) < gap and abs(y - q.y) < gap for q in taken) and turn < 60:
            turn += 1
            angle = turn * 2.4  # golden-angle steps spread evenly
            radius = gap * math.sqrt(turn)
            x = min(max(point.x + radius * math.cos(angle), 0.0), width)
            y = min(max(point.y + radius * math.sin(angle), 0.0), height)
        taken.append(Point(point.note_id, x, y))
    return taken


def label_positions(
    groups: dict[str, list[Point]], *, min_dx: float = 70.0, min_dy: float = 16.0
) -> dict[str, tuple[float, float]]:
    """A label for each group, biggest groups first, skipping ones that would collide.

    Anchored on the group's medoid -- its most central actual note -- rather than
    the centroid, which for a spread-out topic can fall in empty space and point
    at dots that belong to something else.
    """
    placed: dict[str, tuple[float, float]] = {}
    for key, points in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if not points:
            continue
        cx = sum(p.x for p in points) / len(points)
        cy = sum(p.y for p in points) / len(points)
        medoid = min(points, key=lambda p: (p.x - cx) ** 2 + (p.y - cy) ** 2)
        mx, my = medoid.x, medoid.y
        if all(abs(mx - x) > min_dx or abs(my - y) > min_dy for x, y in placed.values()):
            placed[key] = (mx, my)
    return placed
