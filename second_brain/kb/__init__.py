"""Knowledge-base tools over the vault: topics, retrieval, and the read-only service.

Separate from the capture pipeline. The collector writes notes; everything here
only reads them (the one exception is `scripts/discover_topics.py`, a user-run
maintenance script that adds a `topics:` field).
"""
