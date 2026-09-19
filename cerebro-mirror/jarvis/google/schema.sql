-- Jarvis's view of the owner's Google accounts.
--
-- Design decisions this schema encodes, all of them argued out before it was written:
--
-- 1. THE ACCOUNT IS THE DOMAIN. The owner's four accounts are not interchangeable —
--    each is a separate part of his life (personal/medical/military, his son's care,
--    the rental business, his brother's health). So `account` is on every row and every
--    query can be scoped to one. "Which account was that in?" is a lookup, not a guess.
--
-- 2. TWO TIERS, NOT A FILTER. Filtering at ingest is irreversible; ranking at query time
--    is not. So we keep a LIGHT ROW for every message and the FULL TEXT for the ones
--    worth reading. Nothing is decided-and-lost at the door.
--
-- 3. NOTHING IS EVER DELETED, ONLY DEMOTED. At this scale (35k messages ≈ 120 MB of
--    text) storage is not a reason to destroy anything. `status` marks a row
--    active/demoted/excluded; queries default to active, and the rest stays reachable
--    if explicitly asked for. Gmail remains the archive either way.
--
-- 4. GMAIL IS THE SOURCE OF TRUTH, THIS IS A VIEW. Every row can be re-fetched, so a
--    wrong prune is recoverable rather than permanent.
--
-- 5. `sync_state` exists so a long crawl is RESUMABLE. These crawls run for hours and
--    will be interrupted; the first pass cannot be the only pass.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- accounts
CREATE TABLE IF NOT EXISTS accounts (
    email       TEXT PRIMARY KEY,
    label       TEXT NOT NULL,          -- personal | family | property | brother
    purpose     TEXT,                   -- free text: what this mailbox is FOR
    shared_with TEXT,                   -- comma list: who else can read this mailbox
    added_at    REAL
);

-- ---------------------------------------------------------------- email: light tier
-- One row per message. Metadata only — cheap enough to hold the entire mailbox.
CREATE TABLE IF NOT EXISTS messages (
    id             TEXT PRIMARY KEY,    -- Gmail message id
    account        TEXT NOT NULL REFERENCES accounts(email),
    thread_id      TEXT,
    internal_date  INTEGER,             -- epoch millis, Gmail's own ordering
    from_addr      TEXT,
    to_addrs       TEXT,                -- JSON array
    subject        TEXT,
    labels         TEXT,                -- JSON array, Gmail's labels (carries its spam work)
    snippet        TEXT,
    has_attachment INTEGER DEFAULT 0,
    size_estimate  INTEGER,
    -- Engagement is the best junk signal available and it is the owner's OWN behaviour,
    -- not a heuristic: a sender he has never opened or replied to in five years is junk
    -- in a way that is tuned to him.
    replied        INTEGER DEFAULT 0,
    unread         INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'active',   -- active | demoted | excluded
    demoted_why    TEXT,                    -- so a demotion can be explained and undone
    indexed_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_messages_account_date ON messages(account, internal_date DESC);
CREATE INDEX IF NOT EXISTS idx_messages_from ON messages(account, from_addr);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(account, status);

-- ---------------------------------------------------------------- email: full tier
CREATE TABLE IF NOT EXISTS message_text (
    message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    body       TEXT NOT NULL
);

-- Standalone (not external-content) on purpose: it duplicates the text, which costs a
-- couple of hundred MB at this scale, in exchange for an index that is obvious to write
-- and cannot drift out of sync with its content table.
CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
    message_id UNINDEXED,
    account    UNINDEXED,
    subject,
    sender,
    body,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS attachments (
    id            TEXT PRIMARY KEY,     -- sha256 of the part, stable across re-fetch
    message_id    TEXT REFERENCES messages(id) ON DELETE CASCADE,
    account       TEXT NOT NULL,
    filename      TEXT,
    mime_type     TEXT,
    size          INTEGER,
    -- Michael's school and medical documents arrive as attachments rather than Drive
    -- files, so this table is where his account's actual content lives.
    extracted     INTEGER DEFAULT 0,
    indexed_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_attachments_message ON attachments(message_id);

-- Attachment bodies reuse the message FTS (they are the same kind of content, searched
-- the same way) with a synthetic id so a hit can be traced back to its source.
CREATE TABLE IF NOT EXISTS attachment_text (
    attachment_id TEXT PRIMARY KEY REFERENCES attachments(id) ON DELETE CASCADE,
    body          TEXT NOT NULL
);

-- ---------------------------------------------------------------- drive
CREATE TABLE IF NOT EXISTS drive_files (
    id            TEXT PRIMARY KEY,
    account       TEXT NOT NULL REFERENCES accounts(email),
    name          TEXT,
    mime_type     TEXT,
    modified_time TEXT,
    created_time  TEXT,
    size          INTEGER,
    owners        TEXT,
    trashed       INTEGER DEFAULT 0,
    -- Most of "many GB" is media and archives with no text in them at all. Skipping
    -- those by MIME type is what turns a multi-GB Drive into a readable corpus.
    indexable     INTEGER DEFAULT 1,
    status        TEXT DEFAULT 'active',
    indexed_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_drive_account ON drive_files(account, modified_time DESC);

CREATE TABLE IF NOT EXISTS document_text (
    file_id    TEXT PRIMARY KEY REFERENCES drive_files(id) ON DELETE CASCADE,
    account    TEXT NOT NULL,
    body       TEXT NOT NULL,
    source     TEXT             -- export:text | export:pdf | download:pdf | download:docx
);

CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(
    file_id    UNINDEXED,
    account    UNINDEXED,
    name,
    body,
    tokenize = 'porter unicode61'
);

-- ---------------------------------------------------------------- embeddings (later)
-- Deliberately separate from the text tables. Embedding 35k messages is HOURS of local
-- compute, so the index has to be fully useful BEFORE any of this is populated — FTS5
-- answers most real questions on its own, and this fills in behind it.
CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL,
    source      TEXT NOT NULL,          -- message | attachment | document
    source_id   TEXT NOT NULL,
    ordinal     INTEGER,
    body        TEXT NOT NULL,
    embedded_at REAL
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source, source_id);

CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model    TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vector   BLOB NOT NULL             -- float32 little-endian
);

-- ---------------------------------------------------------------- crawl state
CREATE TABLE IF NOT EXISTS sync_state (
    account        TEXT NOT NULL,
    stream         TEXT NOT NULL,       -- gmail | drive | calendar
    cursor         TEXT,                -- historyId | pageToken | syncToken
    newest_seen    INTEGER,
    oldest_seen    INTEGER,
    last_run       REAL,
    last_ok        REAL,
    note           TEXT,
    PRIMARY KEY (account, stream)
);

-- ---------------------------------------------------------------- phone
-- Read over adb from the phone's content providers — no Google API, no quota. This is
-- the most intimate data in the whole database: a call log is who you spoke to and for
-- how long, and SMS is the channel people use when it actually matters.

CREATE TABLE IF NOT EXISTS calls (
    id          TEXT PRIMARY KEY,      -- provider _id, namespaced by device
    device      TEXT,
    number      TEXT,
    -- 1 incoming, 2 outgoing, 3 missed, 4 voicemail, 5 rejected, 6 blocked
    call_type   INTEGER,
    ts          INTEGER,               -- epoch millis
    duration_s  INTEGER,
    cached_name TEXT,                  -- what the phone already knew
    status      TEXT DEFAULT 'active',
    indexed_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_calls_number ON calls(number);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls(ts DESC);

CREATE TABLE IF NOT EXISTS sms (
    id         TEXT PRIMARY KEY,
    device     TEXT,
    thread_id  TEXT,
    address    TEXT,
    ts         INTEGER,
    direction  TEXT,                   -- in | out
    -- A 5-6 digit sender is a machine: banks, 2FA, delivery. Worth keeping as a row,
    -- never worth reading — and 2FA codes in particular should never be searchable.
    automated  INTEGER DEFAULT 0,
    body       TEXT,
    status     TEXT DEFAULT 'active',
    indexed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_sms_address ON sms(address);
CREATE INDEX IF NOT EXISTS idx_sms_ts ON sms(ts DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS sms_fts USING fts5(
    sms_id UNINDEXED, address, body, tokenize = 'porter unicode61'
);

-- ---------------------------------------------------------------- MMS
-- Pictures in texts. A separate provider from SMS (`content://mms` with its payload in
-- `content://mms/part`), and the reason it matters is specific: contractors send quotes,
-- receipts and progress photos by text, and Zelle confirmations arrive the same way.
--
-- The image bytes are NOT stored here — only the part id needed to fetch them. Same
-- design as email attachments: record what exists, read it later, and read selectively.
CREATE TABLE IF NOT EXISTS mms (
    id         TEXT PRIMARY KEY,      -- "<device>:<mms id>"
    device     TEXT,
    thread_id  TEXT,
    address    TEXT,                  -- resolved from the shared thread table
    ts         INTEGER,               -- seconds upstream; stored as millis for parity
    direction  TEXT,                  -- in | out
    subject    TEXT,
    automated  INTEGER DEFAULT 0,
    indexed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_mms_thread ON mms(thread_id);
CREATE INDEX IF NOT EXISTS idx_mms_ts ON mms(ts DESC);

CREATE TABLE IF NOT EXISTS mms_parts (
    id           TEXT PRIMARY KEY,    -- "<device>:<part id>" — this is the fetch handle
    mms_id       TEXT REFERENCES mms(id) ON DELETE CASCADE,
    device       TEXT,
    content_type TEXT,
    name         TEXT,
    is_image     INTEGER DEFAULT 0,
    bytes        INTEGER,
    status       TEXT DEFAULT 'active',
    extracted    INTEGER DEFAULT 0,
    text_source  TEXT,
    indexed_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_mms_parts_mms ON mms_parts(mms_id);
CREATE INDEX IF NOT EXISTS idx_mms_parts_image ON mms_parts(is_image, extracted);

-- What was IN the picture. Kept apart from the part row for the same reason email text
-- is kept apart from message metadata: the row describes a thing, this holds its words,
-- and they are read, searched and purged on different schedules.
CREATE TABLE IF NOT EXISTS mms_text (
    part_id TEXT PRIMARY KEY REFERENCES mms_parts(id) ON DELETE CASCADE,
    account TEXT,
    body    TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS mms_fts USING fts5(
    part_id UNINDEXED, address, body, tokenize = 'porter unicode61'
);

-- ---------------------------------------------------------------- calendar
CREATE TABLE IF NOT EXISTS calendar_events (
    id             TEXT PRIMARY KEY,   -- "<account>:<event id>"
    account        TEXT NOT NULL REFERENCES accounts(email),
    calendar_id    TEXT,
    summary        TEXT,
    description    TEXT,
    location       TEXT,
    start_ts       INTEGER,
    end_ts         INTEGER,
    all_day        INTEGER DEFAULT 0,
    organizer      TEXT,
    ev_status      TEXT,               -- confirmed | tentative | cancelled
    recurring      INTEGER DEFAULT 0,
    attendee_count INTEGER DEFAULT 0,
    indexed_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_events_start ON calendar_events(start_ts DESC);

-- The join that makes "do I actually know this person?" answerable.
CREATE TABLE IF NOT EXISTS event_attendees (
    event_id        TEXT NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    account         TEXT NOT NULL,
    email           TEXT,
    display_name    TEXT,
    response_status TEXT,              -- accepted | declined | tentative | needsAction
    is_organizer    INTEGER DEFAULT 0,
    is_self         INTEGER DEFAULT 0,
    PRIMARY KEY (event_id, email)
);
CREATE INDEX IF NOT EXISTS idx_attendee_email ON event_attendees(email);

-- ---------------------------------------------------------------- contact domains
-- Which part of the owner's life a contact belongs to.
--
-- Phone data arrives from the handset, not from a Google account, but a contractor is
-- still a property matter and Michael's school is still family. This table is what lets
-- a question about the rental business search texts and calls without wading through
-- medical records — the domain label is the join key, and it works whether or not the
-- matching Google account has been indexed yet.
--
-- `assigned_by` matters: a rule can be re-evaluated when the rules change, an owner's
-- word cannot. The owner's rows are never overwritten by a later scoring pass.
CREATE TABLE IF NOT EXISTS contact_domain (
    address     TEXT PRIMARY KEY,      -- phone number or email, normalised
    account     TEXT NOT NULL,         -- one of the four account emails
    reason      TEXT,
    assigned_by TEXT,                  -- rule | owner
    score       REAL,
    assigned_at REAL
);

-- ---------------------------------------------------------------- contact score
-- How much this person matters, computed from the owner's OWN behaviour rather than
-- anyone's idea of importance. Deliberately explainable: `signals` carries the inputs
-- so a surprising score can be interrogated instead of merely distrusted.
--
-- The traps this has to survive: your spouse (never on a calendar), your dentist (twice
-- a year, on a calendar, important), a 40-person all-hands (attendee count), and volume
-- that is really a newsletter.
CREATE TABLE IF NOT EXISTS contact_score (
    email          TEXT PRIMARY KEY,
    display_name   TEXT,
    score          REAL,
    replied_to     INTEGER DEFAULT 0,  -- you wrote to them
    met_1on1       INTEGER DEFAULT 0,
    meetings       INTEGER DEFAULT 0,
    meetings_1on1  INTEGER DEFAULT 0,  -- weight these far above large ones
    calls          INTEGER DEFAULT 0,
    call_minutes   INTEGER DEFAULT 0,
    texts          INTEGER DEFAULT 0,
    last_contact   INTEGER,
    signals        TEXT,               -- JSON: the inputs behind the number
    computed_at    REAL
);

-- ---------------------------------------------------------------- owner controls
--
-- Two switches the owner has to have, because a system that only ingests is one you
-- cannot trust with anything you might later regret.

-- Exclusions are a PERSISTENT rule, not a one-time deletion. The crawlers are resumable
-- and re-run; anything removed but not recorded here comes back on the next sync, which
-- would make "erase this" a lie that quietly undoes itself overnight.
CREATE TABLE IF NOT EXISTS exclusions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    stream   TEXT,             -- gmail | sms | calls | calendar | drive | all
    kind     TEXT,             -- address | number | thread | contains | between | id
    value    TEXT,             -- the address, the substring, "2026-01-01..2026-03-01"
    reason   TEXT,             -- why. Future-you will not remember.
    created  REAL
);
CREATE INDEX IF NOT EXISTS idx_exclusions_stream ON exclusions(stream, kind);

-- Pause. A crawler checks this before writing anything, so indexing can stop without
-- losing the cursor — resuming continues where it left off rather than starting over.
CREATE TABLE IF NOT EXISTS controls (
    key     TEXT PRIMARY KEY,  -- 'pause:gmail', 'pause:sms', 'pause:all'
    value   TEXT,
    set_at  REAL,
    note    TEXT
);

-- A journal of every action that touched the owner's data, so "why did you archive
-- that?" always has an answer. Reads are not logged (too many); writes always are.
-- NOTE: an erasure is logged by its RULE, never by its content — a log that recorded
-- what you deleted would defeat the deletion.
CREATE TABLE IF NOT EXISTS actions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL,
    account TEXT,
    kind    TEXT,                       -- index | demote | restore | archive | draft
    detail  TEXT,
    ok      INTEGER
);

-- The reply graph: every address the owner has ever SENT to. Built by one slow pass over
-- the Sent folder (~50 min at 20k messages) and then CACHED, because it is a property of
-- the account rather than of the crawl, and a crawl that dies at hour three should not
-- pay for it twice.
CREATE TABLE IF NOT EXISTS replied_addresses (
    account    TEXT NOT NULL,
    address    TEXT NOT NULL,
    scanned_at REAL,
    PRIMARY KEY (account, address)
);
