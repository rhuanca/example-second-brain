#!/usr/bin/env python3
"""Keep the vault's topics current, on a timer.

Capture never stops; discovery used to be something you remembered to run. Every
note saved in between has no topic, which is most of the grey on the map. This is
the job that closes that gap:

    uv run python scripts/refresh_topics.py                # run it
    uv run python scripts/refresh_topics.py --dry-run      # show, change nothing
    uv run python scripts/refresh_topics.py --install-timer  # every 3 days

Unlike the other scripts this one applies by default — it exists to be run
unattended. Three things keep that safe:

  - it skips entirely unless enough notes are unfiled, so a quiet week costs
    nothing and the model is not asked to re-think a library that has not moved;
  - it refuses a taxonomy that fails `health()`, because a bad call must not land
    while nobody is looking;
  - it snapshots the taxonomy it is replacing, and `/topics undo` in Telegram
    puts that snapshot back.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from second_brain.config import ConfigError  # noqa: E402
from second_brain.kb.config import KbSettings  # noqa: E402
from second_brain.kb.curation import diff, needs_refresh, stable_order  # noqa: E402
from second_brain.kb.notes import load_cards  # noqa: E402
from second_brain.kb import topics as topics_module  # noqa: E402
from second_brain.kb.topics import (  # noqa: E402
    TopicError,
    discover,
    health,
    load_taxonomy,
    save_taxonomy,
    suggest_target,
)
from second_brain.notify import notify  # noqa: E402
from second_brain.vault import Vault  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discover_topics import write_topics  # noqa: E402

HISTORY_DIR = Path(
    os.environ.get("KB_TOPICS_HISTORY", "~/.local/share/second-brain/topics-history")
).expanduser()
KEEP_SNAPSHOTS = 10
UNIT = "rr-second-brain-topics"


def snapshot(taxonomy, history: Path | None = None, keep: int = KEEP_SNAPSHOTS) -> Path | None:
    """Store the taxonomy being replaced; this is what `/topics undo` restores."""
    if taxonomy is None or not taxonomy.topics:
        return None
    history = Path(history or HISTORY_DIR)
    history.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    path = save_taxonomy(taxonomy, history / f"{stamp}.json")
    for old in sorted(history.glob("*.json"))[:-keep]:
        old.unlink(missing_ok=True)
    return path


def refresh(
    settings: KbSettings,
    *,
    dry_run: bool = False,
    discover_fn=discover,
    topics_path: Path | None = None,
    history: Path | None = None,
) -> int:
    """One pass. Returns a process exit code.

    The paths are arguments rather than module defaults: a default argument binds
    at import, which is how a test once wrote a taxonomy into the source tree.
    """
    topics_path = Path(topics_path or topics_module.TOPICS_FILE)
    vault = Vault(settings.vault_path)
    cards = load_cards(vault)
    current = load_taxonomy(topics_path)

    wanted, why = needs_refresh(cards, current)
    print(f"{len(cards)} notes · {why}")
    if not wanted:
        return 0

    try:
        fresh = discover_fn(
            cards,
            existing=current,
            target=suggest_target(len(cards)),
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
        )
    except TopicError as exc:
        print(f"discovery failed: {exc}", file=sys.stderr)
        notify(f"⚠️ Topic refresh failed: {exc}")
        return 1

    fresh = stable_order(current, fresh)
    complaints = health(fresh, len(cards))
    if complaints:
        # Auto-apply is exactly where a bad taxonomy must not land silently.
        detail = "; ".join(complaints)
        print(f"refusing to apply: {detail}", file=sys.stderr)
        notify(f"⚠️ Topic refresh refused a bad taxonomy: {detail}")
        return 1

    changes = diff(current, fresh)
    print(f"{len(fresh.topics)} topics · {changes.summary()}")
    if not changes:
        return 0
    if dry_run:
        print("dry run — nothing written")
        return 0

    kept = snapshot(current, history)
    save_taxonomy(fresh, topics_path)
    written = write_topics(settings.vault_path, fresh.assignments())
    print(f"wrote topics.json and {len(written)} note(s)" + (f" · snapshot {kept.name}" if kept else ""))
    notify(f"🗂 Topics refreshed: {changes.summary()}\nSend /topics undo to put it back.")
    return 0


def install_timer(project_dir: Path) -> None:
    """A user timer every three days, in the shape backup.sh uses."""
    unit_dir = Path("~/.config/systemd/user").expanduser()
    unit_dir.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv") or "uv"
    (unit_dir / f"{UNIT}.service").write_text(
        f"""[Unit]
Description=Second Brain topic refresh
OnFailure={UNIT}-failed.service

[Service]
Type=oneshot
WorkingDirectory={project_dir}
ExecStart={uv} run --no-sync python scripts/refresh_topics.py
""",
        encoding="utf-8",
    )
    (unit_dir / f"{UNIT}-failed.service").write_text(
        f"""[Unit]
Description=Tell the owner the topic refresh failed

[Service]
Type=oneshot
WorkingDirectory={project_dir}
ExecStart={uv} run --no-sync python scripts/notify_telegram.py "Topic refresh failed. Check: journalctl --user -u {UNIT} -n 30"
""",
        encoding="utf-8",
    )
    (unit_dir / f"{UNIT}.timer").write_text(
        f"""[Unit]
Description=Refresh the Second Brain's topics every three days

[Timer]
OnBootSec=30m
OnUnitActiveSec=3d
Persistent=true
RandomizedDelaySec=30m

[Install]
WantedBy=timers.target
""",
        encoding="utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"], check=True)
    print(f"Installed {UNIT}.timer — every 3 days, skipping runs with little to file.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="show the change, write nothing")
    parser.add_argument("--install-timer", action="store_true", help="install the 3-day timer")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent.parent
    if args.install_timer:
        install_timer(project_dir)
        return

    try:
        settings = KbSettings.from_env()
    except ConfigError as exc:
        raise SystemExit(f"config: {exc}")
    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY is needed to discover topics")

    raise SystemExit(refresh(settings, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
