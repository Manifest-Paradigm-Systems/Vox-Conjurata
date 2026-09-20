"""Which part of the owner's life does this contact belong to?

The four Google accounts are four domains — the owner's own, and one each for the other
parts of his life that have their own mailbox. Email and calendar arrive already labelled
by which account they came from. **Phone data does not**: a call and a text come from the handset, with no idea
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

# WHERE THE ACCOUNT ADDRESSES LIVE NOW.
#
# They used to be literals in this file: four addresses with the label beside each one,
# saying which mailbox is for a family member's medical care and which is for the rental
# property. This repository is published on GitHub, so that was a table of the owner's
# addresses and what each one is for, in a public place.
#
# The mapping was never really code. It is exactly the contents of the `accounts` table,
# which every indexer already reads, which is not in git, and which is backed up. So it is
# read from there. No new config file, and nothing new to keep in step — the database
# already knew, and the source file was a second copy waiting to drift.
#
# JARVIS_GOOGLE_DEFAULT_ACCOUNT still overrides the default for a one-off run.


def domains_map(conn) -> dict[str, str]:
    """Label -> account email, from the accounts table."""
    try:
        rows = conn.execute("SELECT label, email FROM accounts ORDER BY rowid").fetchall()
    except sqlite3.Error as exc:
        raise SystemExit(f"cannot read the accounts table: {exc}")
    out = {r["label"]: r["email"] for r in rows if r["label"] and r["email"]}
    if not out:
        raise SystemExit(
            "the accounts table is empty. domains.py needs the label -> address map, and"
            " it lives there now rather than in this file — see google/schema.sql.")
    return out


def default_account(conn) -> str:
    """Where phone rows land when no contact claims them.

    Owner decision 2026-09-18: the owner's own device traffic defaults to the personal
    account. Resolved here rather than written down, for the reason above.
    """
    override = os.environ.get("JARVIS_GOOGLE_DEFAULT_ACCOUNT")
    if override:
        return override
    domains = domains_map(conn)
    return domains.get("personal") or next(iter(domains.values()))

# What a thread has to talk about to belong to a domain, read from the database.
#
# THESE LISTS USED TO BE HERE, and they were the largest piece of personal material in
# this repository: the names the family mailboxes are about, and the health words that
# appear in the threads concerning them. Together they describe who the owner's family
# are and what is medically going on with them — in a file that is published.
#
# Like the account addresses, this was never really code. The lists are data the
# classifier consults, they change when the owner's life changes rather than when the
# software does, and putting them in the database means tuning a keyword no longer
# needs a commit to a public repository. Seed and edit them with:
#
#     python3 -c "import control; ..."     # or plain SQL against domain_signals
#
# Shape is the same either way: label -> [words], and the matching code below is
# unchanged.
SIGNALS_TABLE = "domain_signals"


def signals_map(conn) -> dict[str, list[str]]:
    """Label -> the words that suggest it, from the domain_signals table."""
    try:
        rows = conn.execute(
            f"SELECT label, keyword FROM {SIGNALS_TABLE} ORDER BY label, rowid").fetchall()
    except sqlite3.Error as exc:
        raise SystemExit(f"cannot read {SIGNALS_TABLE}: {exc}")
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["label"], []).append(r["keyword"])
    if not out:
        raise SystemExit(
            f"{SIGNALS_TABLE} is empty. The keyword lists live in the database now rather"
            " than in this file — see google/schema.sql for the table and the module"
            " docstring for how they got there.")
    return out


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


def score_contact(conn, address: str, signals: dict | None = None
                  ) -> tuple[str | None, int, str]:
    """(domain_label, hits, matched keywords) for one contact.

    `signals` is passed in by the caller that loops over every contact, so the
    keyword lists are read once per run rather than once per number.
    """
    text = thread_text(conn, address)
    if not text.strip():
        return None, 0, ""
    if signals is None:
        signals = signals_map(conn)
    best, best_hits, best_words = None, 0, []
    for label, words in signals.items():
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
    domains = domains_map(conn)
    signals = signals_map(conn)
    owners = {r["address"] for r in conn.execute(
        "SELECT address FROM contact_domain WHERE assigned_by='owner'")}
    counts: dict[str, int] = {}
    unassigned = 0
    pending = []
    for addr in all_contacts(conn):
        norm = normalise(addr)
        if norm in owners or addr in owners:
            continue
        label, hits, words = score_contact(conn, addr, signals)
        if not label:
            unassigned += 1
            continue
        counts[label] = counts.get(label, 0) + 1
        pending.append((norm, domains[label], f"{hits} signals: {words}", "rule",
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
                               (default_account(conn),))
            n += cur.rowcount
    return n


def set_domain(conn, address: str, label: str, reason: str = ""):
    domains = domains_map(conn)
    if label not in domains:
        raise SystemExit(f"unknown domain {label!r}; one of {list(domains)}")
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO contact_domain (address, account, reason,"
            " assigned_by, score, assigned_at) VALUES (?,?,?,?,?,?)",
            (normalise(address), domains[label], reason, "owner", None, time.time()))
    print(f"  {address} -> {label} ({domains[label]})  [owner]")


def report(conn):
    rows = list(conn.execute("SELECT * FROM contact_domain ORDER BY account, address"))
    print(f"\n  {len(rows)} contacts assigned")
    by = {}
    for r in rows:
        by.setdefault(r["account"], []).append(dict(r))
    domains = domains_map(conn)
    for acct, items in sorted(by.items()):
        label = next((k for k, v in domains.items() if v == acct), acct)
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
