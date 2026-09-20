# Jarvis's view of the owner's Google accounts

Read-only, local-only, four accounts.

## What this is for

Jarvis as an expert on the owner's calendar, email and life, across four mailboxes that
each cover a different part of it:

| Role | Domain |
|---|---|
| `personal` | the owner's own life, medical and military career |
| `family` | one family member's care — medical, education, extracurricular |
| `property` | the rental property business |
| `brother` | another family member's health |

The addresses themselves are **not listed here**. They live in the `accounts` table in
the index database, which is not in git, and this repository is published — a table of
real addresses beside what each mailbox is for is exactly the kind of thing that should
not be in a public history. `domains.py` reads the mapping from that table; the roles
above are the stable part.

The account **is** the domain. That is what makes "which account was that in?" a lookup
rather than a classifier that can be wrong, and it is why `account` is on every row.

## Two rules that are not negotiable

**LOCAL ONLY.** This material — medical records, a child's education file, a brother's
health — never leaves the house. Embeddings and inference happen on cerebro's own lanes.
No external API, ever, for anything in this index.

**READ-ONLY, FOR NOW AND DELIBERATELY.** `drive.readonly`, `gmail.readonly`,
`calendar.readonly`. The worst case is that Jarvis *knows* something he should not,
rather than that he destroys something unrecoverable. Writes come later, one scope at a
time, with the owner approving anything destructive.

## Setup — the owner does this, once

The Google-side clicks are tied to his identity, so they cannot be automated:

1. **console.cloud.google.com** → create a project
2. **APIs & Services → Library** → enable **Drive API**, **Gmail API**, **Calendar API**
3. **OAuth consent screen** → User type **External** → app name + support email
4. **Audience → Test users** → add all four addresses
5. **Audience → Publish app** — *without this, refresh tokens expire every 7 days*
6. **Credentials → Create credentials → OAuth client ID → Desktop app**
7. Write the result to `~/.config/jarvis/google/client.json`:

```json
{"client_id": "...", "client_secret": "..."}
```

Then, per account:

```bash
python3 auth.py add mnmeyer@gmail.com     # opens a URL; approve in the browser
python3 auth.py check mnmeyer@gmail.com   # proves the token still works
```

The "unverified app" warning during consent is expected and cosmetic for personal use —
*Advanced → Go to … (unsafe)*. You are the app's owner; nothing is shared with anyone.

## Running the index

```bash
python3 gmail_index.py init
python3 gmail_index.py run mnmeyer@gmail.com      # newest first, resumable
python3 gmail_index.py junk mnmeyer@gmail.com     # the cleanup report
```

`run` checkpoints per page, so an interrupted crawl continues rather than restarting.
35,000 messages is roughly an evening; the newest mail is searchable within minutes.

## Design decisions, and why

**Two tiers, not a filter.** A light row (metadata) for every message; full text only
for what is worth reading. Filtering at ingest is irreversible — drop a newsletter and
if it turns out to hold the invoice, it is gone. Ranking at query time costs a
low-ranked result instead of a lost record.

**Nothing is deleted, only demoted.** At ~120 MB for 35k messages, storage is not a
reason to destroy anything. `status` marks active/demoted/excluded; queries default to
active and the rest stays reachable.

**Gmail is the archive; this is a view.** Every row can be re-fetched, so a wrong prune
is recoverable.

**Gmail's labels are the spam filter.** It has been classifying this mail for years,
tuned on this owner's own inbox. We read `CATEGORY_*`, `SPAM`, `TRASH` rather than
rebuilding one. `CATEGORY_UPDATES` gets full text despite looking like noise — that is
where receipts and confirmations live.

**Engagement is the best junk signal, and it is recorded rather than acted on.**
"Never replied in five years" is the owner's own behaviour, not a heuristic — and it
would also delete his bank, his son's school and his doctor, who are precisely the
senders nobody replies to. So it ranks, and the owner decides.

**Embeddings come last, on purpose.** 35k messages is hours of local embedding compute.
FTS5 answers most real questions ("when did the plumber quote me", "what did the school
say") the moment the crawl finishes, and semantic recall fills in behind it.

## Not built yet

- `drive_index.py` — the Drive crawl. Newest first, MIME-filtered, stream-and-discard:
  extract text, keep that, drop the binary. Must skip media/archives, which is most of
  "many GB" and none of the meaning.
- Attachment text extraction — Michael's account carries its content as attachments
  rather than Drive files, so this matters more there than anywhere.
- Calendar sync, and the recurring-deadline tracking (IEP reviews, physicals, renewals).
- A local embedding model, and the chunk/embed job.
- Demote/restore tooling, and the write scopes (`calendar.events`, then `gmail.compose`
  with the send-guard).
