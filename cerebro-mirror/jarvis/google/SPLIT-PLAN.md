# google.db → per-account projections, one backup per Drive

**Status: DONE 2026-09-21 — as a PROJECTION, not a split.** The full split was built,
measured on the real archive, and **rejected**. This file says what was decided and
what the measurements were, so the next session does not re-derive it.

## The decision

`google.db` **remains the single search and read store.** Jarvis's recall is unchanged
and mail-api is untouched.

The four per-account databases still exist, but as a **projection**: each account's
slice of the index, in its own file, uploaded to that account's own Drive. They are for
ownership and disaster recovery. **They are not searched.**

## Why the full split was rejected

The plan said the read side was "a config change, not a refactor" because all 13
indexers honour `JARVIS_GOOGLE_DB`. That is true of the *indexers*. It is false of the
*reader*: `mail_api.py` resolves one DB path at import, and the brain's `find_mail()`
sends only `q` and `limit` — **no account** — so every mail question is cross-account by
construction.

Two measurements, in order:

**1. Splitting the data changed what was returned.** `_fts_hits` selected its candidate
pool with `ORDER BY bm25(message_fts) LIMIT limit*4`. **bm25's IDF and
average-document-length are per-index**, so lifting an account into its own database
changed the scores, which changed the candidate set, which changed the answer — because
`_rank` breaks ties by input order. Measured: **8 of 14 queries returned different
messages**, 4 of them different messages entirely.

**2. Making selection corpus-independent destroyed relevance.** Ordering the pool by
`internal_date DESC` instead of bm25 made the layouts agree — **26 of 26 queries
identical** across single-term and multi-term sets — but it also removed the only
relevance signal. A one-word query ties every match at `_rank`'s score of 1, so recency
decided:

| query | before | after |
|---|---|---|
| `david` | `Fw: David Meyer — Amber Walker`, pre-intake assessments | a MyChart check-in, an Axios newsletter, two Daily Mail articles |
| `family` | `Join Meyer's family group?` | a Walmart delivery order, an IXL boost, a sprinkler notice |

**0/10 overlap with the previous results on every query tested.** bm25 was doing the
real ranking work. Trading it for partition-independence was not a trade worth making.

**The lesson, stated plainly:** the harm came from splitting the **search index**, not
the data. bm25 scores against a corpus; four corpora are four relevance models. Keep
one index and the problem does not exist.

## What exists now

| path | what |
|---|---|
| `~/jarvis/google/google.db` | the single search/read store — **unchanged** |
| `~/jarvis/google/accounts/<slug>.db` | the four projections, rebuilt by the backup |
| `~/jarvis/google/build_account_dbs.py` | builds and verifies them |
| `~/jarvis/google/regen_schema.py` | regenerates `schema.sql` from the live database |
| `~/jarvis/google/schema.sql` | **regenerated from live** — see below |

Each projection is uploaded by `backup-all-to-drive.sh` to `<slug>:JarvisBackups` as
`<slug>-JarvisAccountDB.gz`. The whole `google.db` still uploads to
`mnmeyer:JarvisBackups` as `mnmeyer-JarvisDB.gz`.

## schema.sql was stale — regenerate it, never hand-edit

The hand-written `schema.sql` had drifted from the live database:

| table | missing |
|---|---|
| `sms`, `mms`, `mms_parts`, `calls` | **`account`** |
| `attachments` | `remote_id`, `text_source`, `inline` |
| `drive_files` | present, but columns in a different order |

Building a database from it would have **silently dropped the phone attribution** —
`domains.py`'s per-domain SMS/MMS/call scoring, gone, with no error. A missing column
fails silently in an `INSERT` that names only the columns it was told about.

`schema.sql` is now generated from `sqlite_master`, which is the one description that
cannot drift because it *is* the database. Regenerate with `python3 regen_schema.py`
after any migration. Verify with a `pragma table_info` comparison; it should report
zero drift.

## Rebuilding the projections

```
python3 ~/jarvis/google/build_account_dbs.py     # builds all four and verifies them
```

It is read-only against `google.db` and writes only into `accounts/`, each database via
a `.building` temp file and `os.replace`, so a failure part-way cannot leave a
half-built file in place.

**Table handling — derived from the live schema, not assumed:**

- **19 tables carry `account`** and are filtered directly.
- **`message_fts` and `document_fts` carry `account`**; they are loaded **by row** so
  SQLite builds the index. **Never copy the shadow tables** (`_data`, `_idx`,
  `_content`, `_docsize`, `_config`) — copying them corrupts the index in a way that
  reads as "no results", not as an error.
- **`sms_fts` and `mms_fts` have NO account column.** `mms_fts` carries `part_id` and
  joins **`mms_parts`** (not `mms`).
- **`message_text`** joins `messages`; **`attachment_text`** joins `attachments`;
  **`chunk_vectors`** joins `chunks` — all via the source database, so the join reads
  `src.<table>`.
- **`accounts`, `exclusions`, `controls`** are copied whole — registry and global config.
- **`_lockprobe`** is scratch and is dropped.
- The joins are written against `src.`, not the destination. Reading the destination
  (mostly empty at that point) yields zero rows that look like a legitimate answer.

## Verification — the row count is not enough

A count that matches while the FTS index is empty looks exactly like success. The
builder checks, per account, that `messages` and `message_fts` match the source **and**
that a known `MATCH` returns rows. Both must pass.

For anything larger, run a real query against the built database and confirm it returns
what the source returns for the same account filter.

## The backup schedule

`jarvis-google-backup.timer` is active and fires **monthly on the 1st**. It had never
run before 2026-09-21 — enabled, scheduled, and never fired. Each run rebuilds the
projections and uploads everything; `backup-all-to-drive.sh` deliberately does not use
`set -e` around the loop, so one failed upload cannot take the others with it.

## If the split is ever revisited

The two blockers are recorded above and neither is theoretical: bm25 is per-corpus, and
removing it costs relevance. A workable version would need a **document-local relevance
score** — term frequency and field weighting computed from the document alone, so no
corpus statistics — replacing bm25's role entirely. That is a ranking redesign and would
need its own quality validation.

**Do not start it without a spot-check like the one that caught this.** The
layout-equivalence test passed 26/26 while returning worse answers, which is exactly the
kind of green result that hides a regression.

## Gotchas recorded elsewhere — do not relearn these

- `drive.file` visibility is **per OAuth client**; folders made under an old client are
  invisible to a new one, and an empty listing is not proof the backup is missing.
- A refresh token belongs to the client that issued it — swapping clients forces consent.
- rclone lives at `~/.local/bin/rclone` (static binary; the box is rpm-ostree) and
  **systemd does not inherit your PATH**. The unit sets it explicitly.
- Testing a refresh: junk the `access_token` on a **copy** (`--config <copy>`), never the
  live config.
