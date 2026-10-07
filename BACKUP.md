# Backup

Two things here cannot be rebuilt: the **vault** (notes, captured text, and the
original PDFs) and **`KB_STATE_DB`** (stars, archive flags, read counts, saved
chats). Everything else — the code, `topics.json`, the embedding index — is in
git or derived.

`./backup.sh` keeps **snapshots** of those two, with [restic](https://restic.net).
Snapshots, not a mirror: the thing worth protecting against is a bug or a slip
deleting something, and a mirror copies the deletion faithfully.

## Set it up (once, on the vault server)

```bash
sudo apt install restic          # Debian/Ubuntu
cd ~/dev/example-second-brain
./backup.sh install              # repository + password + daily timer
./backup.sh run                  # the first snapshot
```

`install` writes a repository password to
`~/.config/second-brain/restic-password` and prints it once. **Copy it into your
password manager.** Without it the snapshots cannot be read — by you or by
anyone.

By default the repository lives at `~/second-brain-backups`. Point `BACKUP_DIR`
elsewhere — ideally another disk — and `install` warns when it shares a disk with
the vault.

## Everyday

```bash
./backup.sh run       # the timer does this daily at 03:30
./backup.sh check     # repository intact, and the newest snapshot under 48h old
./backup.sh list      # what snapshots exist
systemctl --user list-timers 'rr-second-brain*'
du -sh ~/second-brain-backups
```

A failed run sends a Telegram message (`OnFailure=`), because the usual way
backups fail is quietly, months before anyone looks.

## Restoring

**Never restore over the live vault.** `backup.sh` refuses to, because a restore
on top of real data is how a bad day becomes a worse one. Restore elsewhere,
look at it, then copy back what you need.

```bash
./backup.sh restore /tmp/restore-test            # the latest snapshot
./backup.sh restore /tmp/restore-test <snapshot> # a specific one, from `list`
```

The restore keeps the original absolute paths inside the target, so a note lands
at `/tmp/restore-test/<vault path>/<note>.md`. One note back:

```bash
cp /tmp/restore-test/home/renan/<vault>/2026-10-07-a-note.md "$VAULT_PATH/"
cp /tmp/restore-test/home/renan/<vault>/sources/2026-10-07-a-note.* "$VAULT_PATH/sources/"
```

The state database is restored to its staging copy
(`<BACKUP_DIR>/staging/kb-state.db`). To put it back, **stop the service first**,
copy it over `KB_STATE_DB`, remove any `-wal`/`-shm` beside it, then start again:

```bash
systemctl --user stop rr-second-brain-kb
cp /tmp/restore-test/.../staging/kb-state.db "$KB_STATE_DB"
rm -f "$KB_STATE_DB"-wal "$KB_STATE_DB"-shm
systemctl --user start rr-second-brain-kb
```

## The drill (quarterly, ten minutes)

A backup nobody has restored is a hypothesis. Once a quarter:

```bash
./backup.sh restore /tmp/drill
find /tmp/drill -name '*.md' | wc -l     # compare with: ls "$VAULT_PATH"/*.md | wc -l
rm -rf /tmp/drill
```

If the counts are close and a PDF opens, the backup is real.

## What this does and does not protect

| | |
|---|---|
| A bad delete, an empty trash, a buggy script | **Covered** — restore from yesterday |
| A corrupted state database | **Covered** |
| The disk failing, the machine stolen, a fire | **Not covered** — the backup is on the same machine |

To fix the last row, point `BACKUP_DIR` at an external disk, or add a second
repository offsite: restic takes an S3/B2 URL in `RESTIC_REPOSITORY`, so it is
the same script with a different destination.

**Not in the backups:** the `.env` file (so a leaked backup carries no
credentials — keep your tokens in your password manager), the embedding index
(rebuilt automatically), and `.venv`.
