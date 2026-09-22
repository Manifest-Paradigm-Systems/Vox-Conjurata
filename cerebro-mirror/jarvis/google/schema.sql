-- schema.sql — GENERATED FROM THE LIVE DATABASE, do not hand-edit.
--
-- Regenerated on the live google.db rather than maintained by hand, because the
-- hand-written copy had drifted: `account` was missing from sms, mms, mms_parts and
-- calls, and three columns were missing from attachments. A database built from it
-- would have been missing the phone attribution entirely — and a missing COLUMN fails
-- silently in an INSERT that names only the columns it knows about.
--
-- Regenerate with:  python3 regen_schema.py
--
-- Tables: 30   Indexes: 19

CREATE TABLE accounts (
    email       TEXT PRIMARY KEY,
    label       TEXT NOT NULL,          -- personal | family | property | brother
    purpose     TEXT,                   -- free text: what this mailbox is FOR
    shared_with TEXT,                   -- comma list: who else can read this mailbox
    added_at    REAL
);
CREATE TABLE messages (
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
CREATE TABLE message_text (
    message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    body       TEXT NOT NULL
);
CREATE TABLE attachments (
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
, remote_id TEXT, text_source TEXT, inline INTEGER DEFAULT 0);
CREATE TABLE attachment_text (
    attachment_id TEXT PRIMARY KEY REFERENCES attachments(id) ON DELETE CASCADE,
    body          TEXT NOT NULL
);
CREATE TABLE drive_files (
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
, source TEXT DEFAULT 'drive', text_state TEXT, collection TEXT, parents TEXT);
CREATE TABLE document_text (
    file_id    TEXT PRIMARY KEY REFERENCES drive_files(id) ON DELETE CASCADE,
    account    TEXT NOT NULL,
    body       TEXT NOT NULL,
    source     TEXT             -- export:text | export:pdf | download:pdf | download:docx
);
CREATE TABLE chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL,
    source      TEXT NOT NULL,          -- message | attachment | document
    source_id   TEXT NOT NULL,
    ordinal     INTEGER,
    body        TEXT NOT NULL,
    embedded_at REAL
);
CREATE TABLE chunk_vectors (
    chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model    TEXT NOT NULL,
    dim      INTEGER NOT NULL,
    vector   BLOB NOT NULL             -- float32 little-endian
);
CREATE TABLE sync_state (
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
CREATE TABLE actions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL,
    account TEXT,
    kind    TEXT,                       -- index | demote | restore | archive | draft
    detail  TEXT,
    ok      INTEGER
);
CREATE TABLE exclusions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    stream   TEXT,             -- gmail | sms | calls | calendar | drive | all
    kind     TEXT,             -- address | number | thread | contains | between | id
    value    TEXT,             -- the address, the substring, "2026-01-01..2026-03-01"
    reason   TEXT,             -- why. Future-you will not remember.
    created  REAL
);
CREATE TABLE controls (
    key     TEXT PRIMARY KEY,  -- 'pause:gmail', 'pause:sms', 'pause:all'
    value   TEXT,
    set_at  REAL,
    note    TEXT
);
CREATE TABLE calls (
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
, account TEXT);
CREATE TABLE sms (
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
, account TEXT);
CREATE TABLE calendar_events (
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
CREATE TABLE event_attendees (
    event_id        TEXT NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
    account         TEXT NOT NULL,
    email           TEXT,
    display_name    TEXT,
    response_status TEXT,              -- accepted | declined | tentative | needsAction
    is_organizer    INTEGER DEFAULT 0,
    is_self         INTEGER DEFAULT 0,
    PRIMARY KEY (event_id, email)
);
CREATE TABLE contact_score (
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
CREATE TABLE mms (
    id         TEXT PRIMARY KEY,      -- "<device>:<mms id>"
    device     TEXT,
    thread_id  TEXT,
    address    TEXT,                  -- resolved from the shared thread table
    ts         INTEGER,               -- seconds upstream; stored as millis for parity
    direction  TEXT,                  -- in | out
    subject    TEXT,
    automated  INTEGER DEFAULT 0,
    indexed_at REAL
, account TEXT);
CREATE TABLE mms_parts (
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
, account TEXT);
CREATE TABLE contact_domain (
    address     TEXT PRIMARY KEY,      -- phone number or email, normalised
    account     TEXT NOT NULL,         -- one of the four account emails
    reason      TEXT,
    assigned_by TEXT,                  -- rule | owner
    score       REAL,
    assigned_at REAL
);
CREATE TABLE mms_text (
    part_id TEXT PRIMARY KEY REFERENCES mms_parts(id) ON DELETE CASCADE,
    account TEXT,
    body    TEXT NOT NULL
);
CREATE TABLE replied_addresses (
    account    TEXT NOT NULL,
    address    TEXT NOT NULL,
    scanned_at REAL,
    PRIMARY KEY (account, address)
);
CREATE TABLE _lockprobe (x INTEGER);
CREATE INDEX idx_messages_account_date ON messages(account, internal_date DESC);
CREATE INDEX idx_messages_from ON messages(account, from_addr);
CREATE INDEX idx_messages_status ON messages(account, status);
CREATE INDEX idx_attachments_message ON attachments(message_id);
CREATE INDEX idx_drive_account ON drive_files(account, modified_time DESC);
CREATE INDEX idx_chunks_source ON chunks(source, source_id);
CREATE INDEX idx_exclusions_stream ON exclusions(stream, kind);
CREATE INDEX idx_calls_number ON calls(number);
CREATE INDEX idx_calls_ts ON calls(ts DESC);
CREATE INDEX idx_sms_address ON sms(address);
CREATE INDEX idx_sms_ts ON sms(ts DESC);
CREATE INDEX idx_events_start ON calendar_events(start_ts DESC);
CREATE INDEX idx_attendee_email ON event_attendees(email);
CREATE INDEX idx_mms_thread ON mms(thread_id);
CREATE INDEX idx_mms_ts ON mms(ts DESC);
CREATE INDEX idx_mms_parts_mms ON mms_parts(mms_id);
CREATE INDEX idx_mms_parts_image ON mms_parts(is_image, extracted);
CREATE VIRTUAL TABLE message_fts USING fts5(
    message_id UNINDEXED,
    account    UNINDEXED,
    subject,
    sender,
    body,
    tokenize = 'porter unicode61'
);
CREATE VIRTUAL TABLE document_fts USING fts5(
    file_id    UNINDEXED,
    account    UNINDEXED,
    name,
    body,
    tokenize = 'porter unicode61'
);
CREATE VIRTUAL TABLE sms_fts USING fts5(
    sms_id UNINDEXED, address, body, tokenize = 'porter unicode61'
);
CREATE VIRTUAL TABLE mms_fts USING fts5(
    part_id UNINDEXED, address, body, tokenize = 'porter unicode61'
);
CREATE INDEX idx_drive_collection ON drive_files(collection);
CREATE TABLE domain_signals (label TEXT NOT NULL, keyword TEXT NOT NULL, PRIMARY KEY (label, keyword));
CREATE TABLE records_facts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account        TEXT NOT NULL,
    domain         TEXT,
    key_name       TEXT NOT NULL,
    value          TEXT NOT NULL,
    effective_date TEXT,        -- the source document's own date, never the write time
    source_file_id TEXT,        -- -> drive_files.id (a lifepacket: document)
    source_name    TEXT,        -- its filename, so an answer can name it
    sourced        INTEGER,     -- 1 = the value appears in that document's text
    evidence_date  TEXT,        -- the LATEST document in the corpus that supports it
    evidence_count INTEGER,     -- how many documents support it (the stable-fact judge)
    is_current     INTEGER DEFAULT 0,
    rebuilt_at     REAL
);
CREATE INDEX idx_facts_key ON records_facts(domain, key_name, is_current);
