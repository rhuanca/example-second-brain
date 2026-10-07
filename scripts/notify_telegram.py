#!/usr/bin/env python3
"""Send one message to the owner's Telegram chat, from a script or a unit file.

Used by the backup unit's `OnFailure=`: a backup that quietly stopped months ago
is the usual way backups fail, and the journal is only read by someone who
already suspects something.

    uv run python scripts/notify_telegram.py "backup failed on renan-nuc"

Never exits non-zero — the caller is usually already failing, and a notifier that
fails too makes the report harder to find.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from second_brain.notify import notify  # noqa: E402


def main() -> None:
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        print("nothing to send", file=sys.stderr)
        return
    print("sent" if notify(text) else "not sent (see the log line above)")


if __name__ == "__main__":
    main()
