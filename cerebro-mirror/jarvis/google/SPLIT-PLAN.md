# google.db → four per-account databases, then four backup units

**Status: NOT STARTED. Written 2026-09-21 at the end of a session that finished the rclone half.**
Owner approved the direction; this file exists so the next session can execute without re-deriving
the shape. Read `docs/`-adjacent context first: memory `drive-backup-plan`.

## Why

One `google.db` aggregates four life-domains — the owner's own medical/military, Michael's care,
the rental property, Dave's health. Per-account files put each domain's index back with the Google
account that owns it, and let each back up to *its own* Drive via its own rclone remote.

## State when written

| | |
|---|---|
| `google.db` | **3.06 GB**, 51 tables — the plan was costed at 498 MB, so re-check before sizing Drive |
| accounts | mnmeyer 100,095 · meyerfamily813 63,282 · meyerfamilyhomes 6,355 · meyerbrothers78 1,519 — **all four indexed** |
| rclone remotes | **DONE** — `mnmeyer:`, `meyerfamily813:`, `meyerfamilyhomes:`, `meyerbrothers78:`, all verified by folder contents |
| `JARVIS_GOOGLE_DB` | honoured by all 13 indexers ✓ — the read side really is a config change |

## The correction that matters

The plan said splitting was "nearly free — a config change, not a refactor". That is true of the
**read** side and **false** of the **data** side. The data split is a migration:

- **19 tables have an `account` column** — filterable directly.
  messages, attachments, drive_files, document_text, chunks, sync_state, actions, calls, sms,
  calendar_events, event_attendees, mms, mms_parts, contact_domain, mms_text, replied_addresses,
  message_fts, document_fts, records_facts
- **22 are FTS shadow tables** — `*_fts_data/_idx/_content/_docsize/_config` plus `sms_fts`,
  `mms_fts`. **Rebuild them per database; never copy them.** Copying shadow tables corrupts the
  index in ways that read as "no results" rather than as an error.
- **9 have NO `account` column — these need a decision each, and the decision must be written down:**

  | table | proposed handling |
  |---|---|
  | `message_text` | filter via join to `messages.account` — holds the bodies |
  | `attachment_text` | filter via join to `attachments.account` |
  | `accounts` | keep ALL rows in every DB — it is the registry; harmless and keeps `domains.py` working |
  | `chunk_vectors` | filter via its owning document/message — **verify the link column first** |
  | `contact_score` | per-account; derive from the rows present in that DB, or recompute |
  | `domain_signals` | global lookup — keep in all, or recompute per DB |
  | `exclusions`, `controls` | config — keep in all four |
  | `_lockprobe` | scratch table — drop from the copies |

  **Confirm each of these before running.** The failure mode of getting one wrong is silent:
  missing search results in one mailbox, discovered much later.

## Procedure

1. **Snapshot first.** `cp google.db google.db.pre-split` (657 GB free — this is cheap insurance).
   The split must always be reversible.
2. Create each DB from `schema.sql` so indexes and FTS objects exist before data lands.
3. **Do ONE account end to end — `meyerbrothers78` (smallest, 1,519 rows) — and verify it**
   before touching the other three. Fast to iterate, and a mistake costs seconds.
4. Copy account-keyed tables with `INSERT INTO dst SELECT * FROM src WHERE account = ?`.
   For the 9 no-account tables, apply the decisions above (see the table).
5. Rebuild FTS (`INSERT INTO <fts> SELECT ...` or `optimize`) rather than copying shadow tables.
6. Write `JARVIS_GOOGLE_DB=<account>.db` where each service/script runs.

## Verification — do not skip, and do not trust the row counts alone

- Per table, compare `SELECT COUNT(*)` in the source (filtered) against the destination.
- **Prove the FTS works**: run a known query against each new DB and confirm it returns hits that
  the old `google.db` also returns for that account. A row count matching while FTS is empty is
  the exact silent failure this plan warns about.
- `gmail_index.py accounts` should list the same four with the same labels.
- Only then point any service at the new files.

## Then the backup units

`jarvis-google-backup.service` currently runs `backup-all-to-drive.sh` with a single
`JARVIS_DRIVE_REMOTE=mnmeyer:JarvisBackups`. Replace with one unit per account:

- one `jarvis-google-backup@<account>.service` (template), each with its own
  `JARVIS_DRIVE_REMOTE=<account>:JarvisBackups` and its own `JARVIS_GOOGLE_DB`
- keep the existing timer cadence
- **verify each unit with a real write + read-back**, not a listing — the plan's memory already
  records that a listing once read as "backup missing" for a `drive.file` visibility reason

## Gotchas recorded elsewhere — do not relearn these

- **`rclone config create <name> drive ... token='...'` IGNORES the token** and starts its own
  interactive OAuth flow, which hangs forever on headless cerebro. Append the `[section]` to
  `rclone.conf` directly instead. Cost an hour on 2026-09-21.
- `drive.file` visibility is **per-client**: files made under a different client_id are invisible.
  Do not read an empty listing as a missing backup.
- Splitting services must not run while an indexer is writing — take the same lock discipline the
  indexers use.
