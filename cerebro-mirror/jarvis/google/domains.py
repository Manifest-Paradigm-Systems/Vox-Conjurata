"""Which part of the owner's life does this contact belong to?

The four Google accounts are four domains — personal, Michael's care, the rental
business, Dave's health. Email and calendar arrive already labelled by which account they
came from. **Phone data does not**: a call and a text come from the handset, with no idea
that the number belongs to a contractor rather than to a brother.

So this assigns one. It matters for a concrete reason: without it, "what did the roofer
quote me" searches medical records, and the accounts rule we agreed — open retrieval,
labelled sources — has nothing to label with.

TWO WAYS A CONTACT GETS A DOMAIN

  * **A rule** — keyword evidence in the threads, scored per domain. Re-evaluated
    whenever the rules change, because a rule is a guess.
  * **The owner** — `domains.py set <number> property`. Never overwritten by a later
    scoring pass. A rule can be wrong and be corrected by another rule; a person's word
    should not be quietly undone by a heuristic.

Contacts the rules cannot place are left UNASSIGNED rather than defaulted to personal.
An empty label is honest; a wrong one is a lie that reads like data.

Usage:
    python3 domains.py assign            # score every contact from its threads
    python3 domains.py report            # what is assigned, and to what
    python3 domains.py set +17276577267 property --reason "roofing contractor"
    python3 domains.py apply             # push assignments onto the phone rows
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import time

DB_PATH = os.path.expanduser(os.environ.get("JARVIS_GOOGLE_DB",
                                            "~/jarvis/google/google.db"))

# Where phone rows land when no contact claims them. Owner decision 2026-09-18:
# the owner's own device traffic defaults to the personal account. Kept here as
# the single definition rather than a literal inside apply_to_rows.
DEFAULT_ACCOUNT = os.environ.get("JARVIS_GOOGLE_DEFAULT_ACCOUNT", "mnmeyer@gmail.com")

# Label -> account email. The same four as everywhere else.
DOMAINS = {
    "personal": "mnmeyer@gmail.com",
    "family": "meyerfamily813@gmail.com",
    "property": "meyerfamilyhomes@gmail.com",
    "brother": "meyerbrothers78@gmail.com",
}

# What a thread has to talk about to belong to a domain. Deliberately concrete: these are
# words people actually use, not categories. Weights are not used because a thread that
# talks about invoices eight times is not eight times as likely to be the property account
# as one that mentions it once — it is either a business thread or it is not.
SIGNALS = {
    "property": [
        "invoice", "quote", "estimate", "paid", "pay ", "zelle", "receipt", "deposit",
        "balance", "materials", "labor", "supply", "repair", "install", "roof", "plumb",
        "electr", "hvac", "contract", "tenant", "rent", "lease", "property", "drywall",
        "foundation", "septic", "well pump", "insulation", "siding", "permit", "inspect",
        "closing", "escrow", "mortgage", "landlord", "home warranty",
    ],
    "family": [
        # Both names, because both are used — "Mikie" is what he is called in person and
        # therefore what the texts say. A search for the formal name would miss the
        # overwhelming majority of the threads about him.
        "michael", "mikie", "mikey", "mike ",
        "school", "iep", "teacher", "therapy", "pediatric", "homework",
        "field trip", "soccer", "practice", "tutor", "counselor", "grades", "permission",
        "slip", "absence", "pickup", "bus", "daycare", "camp",
        # Appointments are the single most common thing about him worth finding later:
        # "when is Mikie's appointment", "what time is the dentist".
        "appointment", "appt", "dentist", "orthodont", "checkup", "check-up",
        "immunization", "vaccine", "referral", "prescription", "pharmacy",
    ],
    "brother": [
        # His name, spelled the ways people actually type it.
        "dave", "david", "brother",
        "hospital", "clinic", "oncolog", "dialysis", "medication", "medicaid",
        "medicare", "caregiver", "hospice", "surgery", "specialist", "prescription",
        "pharmacy", "rehab", "physical therapy", "infusion", "chemo", "radiation",
        "mri", "ct scan", "blood work", "lab work", "disability", "insurance",
        # Appointments again. They are the substance of a health thread whoever it is
        # about — what separates them is whose name is in the same conversation, which
        # is why scoring happens per contact across all of their threads rather than per
        # message. A thread that only ever says "appointment" is ambiguous and stays
        # unassigned; one that says "Dave" and "appointment" is not.
        "appointment", "appt", "doctor", "dr ", "nurse",
    ],
}

# Two hits before a domain is claimed. One keyword is a coincidence — "paid" appears in
# ordinary conversation; a thread about a job mentions several of these.
MIN_HITS = int(os.getenv("JARVIS_DOMAIN_MIN_HITS", "2"))

# The owner's own number and any shortcode never needs a domain.
_SHORTCODE = re.compile(r"^\d{4,6}$")


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    # The mail crawler is writing to this database at the same time. SQLite serialises
    # writers, so this waits its turn rather than failing — a lock error here reads like a
    # bug in the assignment when it is really just a busy neighbour.
    conn.execute("PRAGMA busy_timeout = 120000")
    return conn


def normalise(number: str) -> str:
    """Compare numbers as their last 10 digits.

    The same contact arrives as `+18134446667`, `18134446667` and `8134446667` depending
    on which provider wrote the row. Treating those as three people would split one
    contractor across three domains and three call histories.
    """
    digits = re.sub(r"\D", "", number or "")
    return digits[-10:] if len(digits) >= 10 else digits


def thread_text(conn, address: str) -> str:
    """Everything said in this contact's threads, for scoring."""
    norm = normalise(address)
    parts = []
    for r in conn.execute("SELECT body FROM sms WHERE body != ''"):
        pass                                        # scored per-thread below instead
    # Score by THREAD rather than by contact: a contractor's thread is the unit that
    # talks about a job, and it avoids one stray keyword in an unrelated thread deciding
    # a person's whole domain.
    threads = {r["thread_id"] for r in conn.execute(
        "SELECT DISTINCT thread_id FROM sms WHERE address=? OR address LIKE ?",
        (address, f"%{norm}%"))}
    threads |= {r["thread_id"] for r in conn.execute(
        "SELECT DISTINCT thread_id FROM mms WHERE address=? OR address LIKE ?",
        (address, f"%{norm}%"))}
    for t in threads:
        if t is None:
            continue
        for r in conn.execute("SELECT body FROM sms WHERE thread_id=? AND body != ''",
                              (t,)):
            parts.append(r["body"])
        for r in conn.execute("SELECT subject FROM mms WHERE thread_id=? AND subject"
                              " IS NOT NULL", (t,)):
            parts.append(r["subject"] or "")
    return " ".join(parts).lower()


def score_contact(conn, address: str) -> tuple[str | None, int, str]:
    """(domain_label, hits, matched keywords) for one contact."""
    text = thread_text(conn, address)
    if not text.strip():
        return None, 0, ""
    best, best_hits, best_words = None, 0, []
    for label, words in SIGNALS.items():
        hits = [w for w in words if w in text]
        if len(hits) > best_hits:
            best, best_hits, best_words = label, len(hits), hits
    if best_hits < MIN_HITS:
        return None, best_hits, ", ".join(best_words[:4])
    return best, best_hits, ", ".join(best_words[:6])


def all_contacts(conn) -> list[str]:
    seen = set()
    for table, col in (("sms", "address"), ("mms", "address"), ("calls", "number")):
        for r in conn.execute(f"SELECT DISTINCT {col} a FROM {table} WHERE {col} IS NOT NULL"):
            a = (r["a"] or "").strip()
            if a and not _SHORTCODE.match(a):
                seen.add(a)
    return sorted(seen)


def assign(conn, dry_run: bool = False) -> dict:
    """Score every contact and record a domain, leaving the owner's choices alone."""
    owners = {r["address"] for r in conn.execute(
        "SELECT address FROM contact_domain WHERE assigned_by='owner'")}
    counts: dict[str, int] = {}
    unassigned = 0
    pending = []
    for addr in all_contacts(conn):
        norm = normalise(addr)
        if norm in owners or addr in owners:
            continue
        label, hits, words = score_contact(conn, addr)
        if not label:
            unassigned += 1
            continue
        counts[label] = counts.get(label, 0) + 1
        pending.append((norm, DOMAINS[label], f"{hits} signals: {words}", "rule",
                        float(hits), time.time()))

    # ONE transaction for the whole batch, not one per contact. Each write otherwise
    # contends with the running mail crawl, and a hundred separate lock acquisitions is
    # a hundred chances to lose the race.
    if pending and not dry_run:
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO contact_domain (address, account, reason,"
                " assigned_by, score, assigned_at) VALUES (?,?,?,?,?,?)", pending)
    return counts, unassigned


def apply_to_rows(conn) -> int:
    """Push the domain onto the phone rows themselves.

    The rows carry their own `account` so a query can scope to one domain without joining
    through the contact every time — and so the label survives even if a contact is later
    renamed or merged.
    """
    # RESET, THEN APPLY. The contact table is the source of truth, so a re-run after a
    # rule change has to be able to CORRECT a row rather than only fill blanks — but
    # doing that by removing the NULL guard alone was a disaster: `LIKE '%<addr>'` with
    # an address that normalises to an empty string degrades to a bare `%`, matches every
    # row in the table, and one bad contact relabels the entire database. It did exactly
    # that, and the report said it worked.
    #
    # Starting from NULL makes the pass idempotent and self-correcting: whatever survives
    # is what the current rules say, not an accumulation of every rule that has ever run.
    contacts = [(r["address"], r["account"]) for r in
                conn.execute("SELECT address, account FROM contact_domain")
                if len(r["address"] or "") >= 7]

    n = 0
    with conn:
        for table, col in (("sms", "address"), ("mms", "address"), ("calls", "number")):
            conn.execute(f"UPDATE {table} SET account=NULL")
            for addr, account in contacts:
                cur = conn.execute(
                    f"UPDATE {table} SET account=? WHERE {col} LIKE ? OR {col} LIKE ?",
                    (account, f"%{addr}", f"%{addr[-10:]}"))
                n += cur.rowcount
            # Whatever no contact claimed is the owner's own device traffic with no
            # matching contact — a number that was never saved. Owner decision
            # 2026-09-18: default it to the personal account rather than leave it
            # unattributed, so a per-account split has somewhere to put it.
            #
            # This runs AFTER the contact pass, and only fills rows that pass left
            # NULL, so it cannot relabel a row a contact claimed — the failure mode
            # the reset-then-apply design above exists to prevent. A one-off UPDATE
            # would not have held: this function resets to NULL on every run.
            cur = conn.execute(f"UPDATE {table} SET account=? WHERE account IS NULL",
                               (DEFAULT_ACCOUNT,))
            n += cur.rowcount
    return n


def set_domain(conn, address: str, label: str, reason: str = ""):
    if label not in DOMAINS:
        raise SystemExit(f"unknown domain {label!r}; one of {list(DOMAINS)}")
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO contact_domain (address, account, reason,"
            " assigned_by, score, assigned_at) VALUES (?,?,?,?,?,?)",
            (normalise(address), DOMAINS[label], reason, "owner", None, time.time()))
    print(f"  {address} -> {label} ({DOMAINS[label]})  [owner]")


def report(conn):
    rows = list(conn.execute("SELECT * FROM contact_domain ORDER BY account, address"))
    print(f"\n  {len(rows)} contacts assigned")
    by = {}
    for r in rows:
        by.setdefault(r["account"], []).append(dict(r))
    for acct, items in sorted(by.items()):
        label = next((k for k, v in DOMAINS.items() if v == acct), acct)
        print(f"\n  {label}  ({acct})  — {len(items)} contacts")
        for d in items[:8]:
            who = "OWNER" if d["assigned_by"] == "owner" else "rule "
            print(f"    [{who}] {d['address']:<14} {(d['reason'] or '')[:52]}")
        if len(items) > 8:
            print(f"    ... and {len(items) - 8} more")


def main(argv: list[str]) -> int:
    conn = db()
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "assign":
        counts, unassigned = assign(conn)
        print(f"  assigned: {counts or 'nothing'}")
        print(f"  left unassigned (no clear signal): {unassigned}")
        return 0
    if cmd == "apply":
        n = apply_to_rows(conn)
        print(f"  {n} phone rows labelled with a domain")
        return 0
    if cmd == "report":
        report(conn)
        return 0
    if cmd == "set":
        if len(argv) < 4:
            raise SystemExit("usage: domains.py set <number> <personal|family|property|brother> [--reason ...]")
        reason = argv[argv.index("--reason") + 1] if "--reason" in argv else ""
        set_domain(conn, argv[2], argv[3], reason)
        return 0
    raise SystemExit(f"unknown command {cmd!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
