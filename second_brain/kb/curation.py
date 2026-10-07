"""The decisions a scheduled topic refresh makes, with no API call in sight.

Discovery itself lives in `topics.py`; this is what a job needs around it —
whether a run is worth making, how to keep the result from disturbing what is
already on screen, and what to say about what changed. All of it is pure, so a
job that rewrites every note's frontmatter can be reasoned about in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from second_brain.kb.notes import Card
from second_brain.kb.topics import Taxonomy, Topic

# Below this many unfiled notes a refresh is not worth a call: the taxonomy will
# not move, and the job runs often enough to catch them next time.
MIN_UNFILED = 5


def needs_refresh(
    cards: list[Card], taxonomy: Taxonomy | None, *, minimum: int = MIN_UNFILED
) -> tuple[bool, str]:
    """Whether to spend a discovery call, and the reason either way."""
    if not cards:
        return False, "no notes in the vault"
    if taxonomy is None or not taxonomy.topics:
        return True, f"no taxonomy yet · {len(cards)} notes to file"

    filed = set(taxonomy.assignments())
    unfiled = [card for card in cards if card.note_id not in filed]
    if len(unfiled) >= minimum:
        return True, f"{len(unfiled)} notes unfiled (threshold {minimum})"
    return False, f"{len(unfiled)} notes unfiled, under the threshold of {minimum}"


def stable_order(previous: Taxonomy | None, fresh: Taxonomy) -> Taxonomy:
    """`fresh`, with surviving topics back in the order `previous` had them.

    Map colours are handed out by position (`visuals.topic_slots`), so a topic the
    model happens to list first would take slot 1 from whatever held it and move
    every colour on the map. Discovery is free to merge, split, add and retire;
    it is not free to repaint a map nobody asked it to touch. New topics go to the
    end, where they take the next free colour or fall into the neutral slot.
    """
    if previous is None or not previous.topics:
        return fresh

    order = {topic.id: i for i, topic in enumerate(previous.topics)}
    ordered = sorted(
        fresh.topics,
        # Surviving topics first, in their old order; new ones after, in the
        # order discovery produced them.
        key=lambda t: (order.get(t.id, len(order)), 0 if t.id in order else 1),
    )
    return Taxonomy(topics=ordered, unassigned=list(fresh.unassigned))


@dataclass
class TaxonomyDiff:
    added: list[str] = field(default_factory=list)  # names
    retired: list[str] = field(default_factory=list)
    renamed: list[tuple[str, str]] = field(default_factory=list)  # (before, after)
    filed: int = 0  # notes that had no topic and now have one
    moved: int = 0  # notes whose topics changed

    def __bool__(self) -> bool:
        return bool(self.added or self.retired or self.renamed or self.filed or self.moved)

    def summary(self) -> str:
        """One line, for the log and the Telegram message."""
        parts = []
        if self.added:
            parts.append(f"{len(self.added)} new: {', '.join(self.added)}")
        if self.retired:
            parts.append(f"{len(self.retired)} retired: {', '.join(self.retired)}")
        if self.renamed:
            parts.append(
                f"{len(self.renamed)} renamed: "
                + ", ".join(f"{before} → {after}" for before, after in self.renamed)
            )
        if self.filed:
            parts.append(f"{self.filed} notes filed")
        if self.moved:
            parts.append(f"{self.moved} notes moved")
        return " · ".join(parts) if parts else "nothing changed"


def diff(previous: Taxonomy | None, fresh: Taxonomy) -> TaxonomyDiff:
    """What a refresh would change, in the terms a person cares about."""
    before: dict[str, Topic] = {t.id: t for t in (previous.topics if previous else [])}
    after = {t.id: t for t in fresh.topics}

    changes = TaxonomyDiff(
        added=[after[i].name for i in after if i not in before],
        retired=[before[i].name for i in before if i not in after],
        renamed=[
            (before[i].name, after[i].name)
            for i in after
            if i in before and before[i].name != after[i].name
        ],
    )

    was = previous.assignments() if previous else {}
    now = fresh.assignments()
    for note_id, topics in now.items():
        if note_id not in was:
            changes.filed += 1
        elif sorted(was[note_id]) != sorted(topics):
            changes.moved += 1
    return changes
