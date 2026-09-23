"""Jarvis memory worker — turn raw transcripts into things worth remembering.

The problem this solves: `brain.py` logs every turn faithfully, which means the
session log holds "are you there?" and "we decided X" with exactly equal weight.
Searching that store returns noise.

This worker runs out-of-band on a timer:

    new turns ──► window ──► discriminator (a local lane) ──► facts + archive
                                                     │
                                             consolidation vs. the existing pool

Two decisions worth stating plainly, because they are the difference between a
memory that gets better with use and one that rots:

1. **Consolidate on a lexical heuristic, not on the model's judgement.** The model's
   job is extraction: "is there a durable fact here?" It never sees the existing pool,
   so it never gets to decide what to overwrite. Merging is done here, deterministically,
   with an explainable rule — and since a superseded fact is *retired, never deleted*,
   a wrong merge is visible and reversible.
2. **Never advance the cursor on a partial window.** A model timeout or a malformed
   reply leaves the cursor where it was; the next tick retries the same window. Nothing
   is silently dropped, and nothing is half-written.

Run:
    python3 memory.py --once            # one pass
    python3 memory.py --once --dry-run  # show what it would store
    python3 memory.py --stats
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

import db as jarvis_db

LANE = os.getenv("JARVIS_MEMORY_LANE", "http://127.0.0.1:8082")
MODEL = os.getenv("JARVIS_MEMORY_MODEL", "coder")
WINDOW = int(os.getenv("JARVIS_MEMORY_WINDOW", "24"))       # turns per extraction call
MIN_CHARS = int(os.getenv("JARVIS_MEMORY_MIN_CHARS", "200"))  # skip trivial windows
CURSOR = "memory.turns"

# ------------------------------------------------------------------ discrimination

DISCRIMINATOR = """You are the Memory Subsystem of Jarvis, a private assistant running on the owner's own hardware.

Read the transcript segment and extract ONLY what is worth remembering long-term: \
durable facts, the owner's explicit preferences, system configurations, and decisions \
that were actually reached.

IGNORE AND FLUSH — never report these:
- conversational filler ("Are you there?", "Hello", "Thanks", "Can you hear me?")
- transient status checks and immediate one-off intents ("what time is it", "call the dentist now")
- speculation, brainstorming that reached no conclusion, and questions with no answer
- anything you are inferring rather than reading (EXCEPT the subject of a correction --
  see the correction rule at the end, where restoring it is exactly the job)
- ANYTHING JARVIS SAID ABOUT HIMSELF. His turns are labelled [JARVIS]. What he says about
  his OWN state, actions and progress — "the music generator is experiencing difficulties",
  "development is progressing smoothly", "voice reverted to the regular voice", "has applied
  DSP" — describes a moment, not the world, and is noise the moment it lands.
- BUT DO RECORD what he says about the WORLD. A place, a date, a name, a commitment, a
  reading he took from the calendar, mail or a tool — "the flight to Denver departs
  Tuesday", "the property inspection is on the 14th" — is a fact about the world no matter
  which of you said it, and losing it would be worse than keeping it. The test is the
  SUBJECT, not the speaker: Jarvis as a SOURCE is fine, Jarvis as the SUBJECT is not.
- an owner's momentary want. "I want X checked" is a TASK, not a preference and not a fact.
  A preference is durable and stated as one ("I prefer to be called Michael").

For a decision, record the DECISION, not the debate that preceded it. If the segment \
revisits a decision and CHANGES it, record the new position — a later worker resolves \
which one stands.

WHEN THE OWNER SAYS YOU WERE WRONG, THAT IS A FACT -- and it is the one kind of fact
whose subject lives in YOUR turn, not his. "No, that's not correct" has no subject by
itself, so you must restore it: read what he is rejecting out of the [JARVIS] turn above
it, and name that claim in "corrects".

  - If he rejects it AND states the truth, emit a normal entry for the corrected value with
    "corrects" set to the claim he rejected, quoted closely enough to be found again.
  - If he only rejects it and states nothing ("that's wrong", "I never said that"), emit ONE
    entry with "kind": "retraction" and "statement" = the claim being withdrawn.
  - NEVER emit a correction without "corrects" or "retraction". A correction whose referent
    is unnamed cannot retire the wrong belief, and you will keep repeating the error -- which
    is precisely the failure this rule exists to stop.
  - A correction is not a disagreement to be debated. Record what he says stands.

Reply with ONE JSON object and nothing else. No markdown fence, no commentary.

{
  "extracted_facts": [
    {"statement": "<one declarative sentence, self-contained, no pronouns>",
     "entity": "<one of: cerebro_system, workhorse, user, rental_property, dave_care, cinematome, fleet, other>",
     "topic": "<short snake_case subject, e.g. memory_architecture>",
     "kind": "<fact|preference|config|decision|retraction>",
     "corrects": "<the claim the OWNER just rejected, or null>"}
  ],
  "archive": {
    "worth_keeping": <true if this segment contains reasoning or alternatives that would matter later>,
    "title": "<short title>",
    "summary": "<2-4 sentences: what was discussed, what was chosen, and what was rejected and why>"
  }
}

If nothing qualifies, return {"extracted_facts": [], "archive": {"worth_keeping": false}}.

TRANSCRIPT SEGMENT:
"""

_FILLER = re.compile(
    r"^\s*(are you (there|awake)|hello|hi|hey|thanks?|thank you|ok(ay)?|yes|no|"
    r"good (morning|evening|afternoon)|can you hear me|you there|testing)\b[\s.!?]*$",
    re.IGNORECASE)


def looks_like_filler(text: str) -> bool:
    return bool(_FILLER.match(text or "")) or len((text or "").strip()) < 12


def window_is_trivial(rows) -> bool:
    """Cheap pre-filter: skip windows with nothing worth remembering.

    Saves a model call on the overwhelmingly common case, and keeps the
    extractor from having to be trusted on input it should never have seen.

    Deliberately counts EVERY role, not just the owner's. An earlier version
    counted the owner's turns alone, on the theory that his narration is the
    noise — but that also threw away windows where the owner was terse and
    Jarvis did the talking, which is exactly where a relayed date, place or
    booking lives. Filtering by WHO SPOKE is the wrong instrument; the noise is
    a property of what was SAID, and that is the output gate's job below.
    """
    if owner_corrects(rows):
        # A correction is SHORT BY NATURE -- "no, that's not correct" is 21 characters
        # -- so the char count below would skip it as filler. That is the one window
        # where a wrong belief gets retired, and skipping it is how Jarvis ends up
        # repeating the error he was just corrected on.
        return False
    substance = [r["content"] for r in rows if not looks_like_filler(r["content"])]
    return sum(len(c) for c in substance) < MIN_CHARS


# Statements that describe a moment rather than the world. The extractor is told
# not to emit these; this is the belt to that prompt's braces, because a prompt is
# a request and this is a guarantee. Deliberately narrow: a false positive here
# silently loses a real fact, which is worse than keeping one dud.
_ADVERBS = r"(?:\w+ly\s+|now\s+|currently\s+|still\s+|already\s+)*"
_TRANSIENT = re.compile(
    # Present progressive — "is experiencing", "are progressing", "is being tested".
    # The adverb slot matters: real sentences say "is FULLY functional", not
    # "is functional", and the first version of this missed every one of those.
    r"\b(?:is|are|was|were)\s+" + _ADVERBS +
    r"(?!(?:during|thing|things|something|anything|nothing|everything|morning|evening)\b)"
    r"\w{2,}ing\b"
    # Status adjectives, likewise adverb-tolerant.
    r"|\b(?:is|are|was|were)\s+" + _ADVERBS +
    r"(?:complete|stable|functional|operational|ready|available|offline|online|integrated)\b"
    # Completed-action narration.
    r"|\bhas\s+(?:applied|been|changed|updated|restarted|toggled|turned|stopped|started|run"
    r"|reverted|completed|finished|addressed)\b"
    r"|\bwas\s+(?:reduced|increased|changed|lowered|raised|disabled|enabled|set|completed)\b"
    # Explicit recency, and progress counters.
    r"|\b(?:currently|right\s+now|at\s+the\s+moment|temporarily|so\s+far|nearing\s+completion)\b"
    r"|\b\d+\s*%\s*complete\b"
    r"|\bprogressing\s+(?:well|smoothly|nicely)\b"
    # Deictic time. A durable fact does not contain "today" — anything that does
    # is a reading taken at a moment ("the weather today is clear", "no scheduled
    # events for the day") and is false by the time anyone reads it back.
    r"|\b(?:today|yesterday|tonight|tomorrow|this\s+(?:morning|afternoon|evening)|"
    r"for\s+the\s+day)\b",
    re.IGNORECASE)


def is_transient_statement(statement: str) -> bool:
    """True for status reports and action narration, which are not facts.

    Everything this catches describes a moment: "the generator is experiencing
    difficulties", "development is progressing smoothly", "voice reverted". They
    read as knowledge and behave as noise — true for an hour, quietly false
    forever, and recall keeps handing them back.
    """
    return bool(_TRANSIENT.search(statement or ""))


# FACTS ABOUT WHO THE OWNER IS MUST COME FROM THE OWNER.
#
# The rule further up says Jarvis as a SOURCE is fine — "a name, a date, a place is a
# fact about the world no matter which of you said it". That is right for a date he read
# off the calendar and wrong for the owner's own identity, because in the text an
# INVENTED name and a RETRIEVED one are indistinguishable. There is no phrasing that
# tells them apart; only the source does.
#
# Measured, 2026-09-20: the owner asked "what is my full name", Jarvis answered "Michael
# Corwin", and this extractor stored that as a fact at confidence 1.0 with entity=user.
# The word Corwin appears in ZERO owner turns anywhere in the transcript — he never said
# it — and the stored fact then fed itself back into every later conversation, so Jarvis
# grew more certain of a name that was never his. Two further facts recorded his own
# narration of where the name had come from, which is the same invention wearing a
# provenance story.
#
# So an identity statement is kept only when its distinctive words appear in an OWNER
# turn in the same window. Identity lives in the service record, the accounts table and
# the documents — all of which can be pointed at. Conversation cannot.
_IDENTITY = re.compile(
    r"\b(?:full name|owner'?s name|his name is|her name is|my name is|is named|"
    r"date of birth|birth ?date|born on|social security|\bssn\b|home address|"
    r"lives at|phone number|email address|"
    # SERVICE AFFILIATION, added 2026-09-20 after the same invention twice. Jarvis
    # asserted "you are currently a member of the 75th Ranger Regiment" from no source,
    # the owner corrected it two minutes later, and the extractor stored it anyway — then
    # stored a SECOND copy of it twenty-four hours afterwards. The only mention of a
    # Ranger anywhere in the service record is the phrase "Ranger Training" in an option
    # list on a personnel form; the real unit is a sustainment detachment.
    r"is a member of|member of the|joined the|served in|enlisted in|"
    r"unit is|assigned to|stationed at|deployed to|belongs to)\b", re.I)


_NEGATION = re.compile(
    r"\b(?:not|never|no|isn'?t|aren'?t|wasn'?t|weren'?t|don'?t|doesn'?t|didn'?t|without)\b", re.I)


def identity_from_nobody(statement: str, rows) -> bool:
    """True when a statement is about who the owner IS and he never said it.

    Deliberately requires a proper noun in the statement and that the owner used it.
    A statement made entirely of ordinary words ("the owner's date of birth is
    recorded") carries no name to check and is caught by the narration rules instead.
    """
    if not _IDENTITY.search(statement or ""):
        return False
    names = {w.lower() for w in re.findall(r"\b[A-Z][a-z]{2,}\b", statement or "")}
    if not names:
        return False

    # AND A CORRECTION MUST NOT COUNT AS SUPPORT. The first version of this asked only
    # "did the owner use these words", which the Ranger case defeats: he wrote "No, I'm
    # not a member of the Ranger Regiment", so the name is present in his turn and the
    # invention would have been waved through BY the sentence that denied it. So a
    # mention only counts when it is not inside a negation.
    for r in rows:
        if r["role"] != "user":
            continue
        text = (r["content"] or "").lower()
        for name in names:
            for m in re.finditer(re.escape(name), text):
                window = text[max(0, m.start() - 45):m.end() + 45]
                if not _NEGATION.search(window):
                    return False          # the owner asserted it; keep the fact
    return True


# ------------------------------------------------------------------ corrections
# THE OWNER SAYING "THAT'S NOT CORRECT" IS A FACT, AND IT NEEDS ITS CONTEXT.
#
# Measured 2026-09-23, from the owner's own report -- "Jarvis should learn when he is told
# something is not correct ... he seems to be forgetting the context":
#
#     Jarvis: "Your blood type is AB+."
#     Owner:  "No, that's wrong."
#
# Nothing retires that. The correction carries no subject of its own -- the subject lives in
# Jarvis's turn -- so a statement extracted from the owner's turn alone cannot match the
# wrong belief, and the belief stays active and keeps being recalled.
#
# Worse, when it DOES match, the merge runs the wrong way. `_words` keeps tokens of three or
# more characters, so "AB+" and "O-" both vanish:
#
#     "The owner's blood type is AB+"  ->  {the, owner, blood, type}
#     "The owner's blood type is O-"   ->  {the, owner, blood, type}
#
# Identical sets, Jaccard 1.0, which is above the 0.9 reinforce line in consolidate(). So the
# corrected fact does not replace the wrong one -- it REINFORCES it, and confidence goes UP
# by 0.1. The owner corrects Jarvis and Jarvis becomes more certain of the thing he was just
# told was wrong. Any value short enough to be dropped by _words does this: blood types,
# two-letter state codes, short codes, and every two-character difference generally.
#
# So a correction is NEVER a reinforcement. It is always the replacement of a named referent.
_CORRECTION_CUES = re.compile(
    r"(?:that'?s\s+(?:not|wrong|incorrect|the\s+wrong)|not\s+(?:correct|right|true|what)|"
    r"that\s+is\s+(?:not|wrong|incorrect)|(?:you'?re|that'?s|this\s+is)\s+wrong|"
    r"i\s+(?:never|didn'?t|did\s+not)\s+(?:say|said|tell|told)|"
    r"no,?\s+(?:it'?s|i'?m|that'?s|my|the|they|he|she|this)|"
    r"incorrect|not\s+what\s+i\s+said|stop\s+saying|"
    r"i\s+said|i\s+told\s+you|actually,?\s)", re.I)


def owner_corrects(rows) -> bool:
    """True when the owner rejects or repairs something in this window."""
    return any(_CORRECTION_CUES.search(r["content"] or "")
               for r in rows if r["role"] == "user")


def find_corrected_fact(conn, claim: str):
    """Locate the belief a correction refers to. Returns (row, score) or (None, 0.0).

    WIDER THAN find_similar_fact, ON PURPOSE. Two relaxations, both because a retraction
    that finds nothing is a SILENT no-op: the wrong belief survives and Jarvis repeats the
    error, which is the entire failure this section exists to stop.
      - no entity/topic filter, because the model's topic for a correction is a guess and
        an exact-topic miss means the search never even sees the row;
      - a second, lower pass, because a correction paraphrases what it is rejecting
        ("that Ranger thing").
    A wrong match is visible and reversible -- `status` is a column, not a delete -- while a
    missed one is invisible.
    """
    for th in (0.6, 0.35):
        row = jarvis_db.find_similar_fact(conn, claim, threshold=th)
        if row is not None:
            return row, th
    return None, 0.0


def apply_correction(conn, fact: dict, *, session: str, dry: bool, result: dict) -> None:
    """Retire the belief the owner rejected, and record what stands in its place.

    Three shapes, all of them the same event seen from different angles:
      - retraction, no replacement ("that's wrong")      -> retire, write nothing
      - retraction, referent not found                   -> report, write nothing
      - correction with a value ("no, it's O-")          -> supersede the referent
    """
    kind = fact.get("kind", "fact")
    statement = (fact.get("statement") or "").strip()
    claim = (fact.get("corrects") or "").strip() or statement
    target, score = find_corrected_fact(conn, claim)

    if target is None:
        # Report rather than invent. "The owner objected" tells us a belief is wrong; it
        # does not tell us what is true, and a fact invented to fill that gap is the exact
        # failure mode the identity gate was written for.
        result["unmatched"].append(
            f"correction names no belief we hold ({kind}): {claim[:70]}")
        if kind == "retraction" or not statement:
            return
        action, fid = consolidate(conn, fact, session=session, dry=dry)
    elif kind == "retraction":
        if not dry:
            with conn:
                conn.execute("UPDATE facts SET status='retired', updated=?"
                             " WHERE id=? AND status='active'", (time.time(), target["id"]))
        result["retracted"] += 1
        result["actions"].append(f"retract (match {score:.2f}): {claim[:70]}")
        return
    else:
        action, fid = consolidate(conn, fact, session=session, dry=dry, replaces=target)

    if action == "empty":
        return
    result["facts"] += 1
    result[action] = result.get(action, 0) + 1
    result["actions"].append(f"{action}: {statement[:90]}")


# ------------------------------------------------------------------ write gates
# A FACT MUST COME FROM SOMETHING THAT CAN BE SHOWN. The discriminator prompt tells the
# extractor "Jarvis as a SOURCE is fine" -- and it is, when he is REPORTING a reading,
# which the fetching lanes mark with an inline [N] citation. It is NOT fine when he is
# speaking from his own priors, because that is the model asserting, and there is a
# measured result for where that ends: 43 facts distilled from answers he had made up --
# including answers the eval was built to provoke AS inventions -- which sat in `facts`
# as "verified memory" until they were retired on 2026-09-23. The prompt cannot close
# this, because the next prompt is another chance to read it loosely.
#
# So: traceable to an OWNER turn -> keep. Traceable to a JARVIS turn that cites -> keep,
# it came from a document. Traceable to a JARVIS turn that cites nothing -> reject.
_CITATION = re.compile(r"\[\d{1,2}\]")

_DISTINCTIVE_RX = (
    re.compile(r"\b[A-Za-z]*\d[\w.\-/]*\b"),   # carries a digit: 1974, A22658008
    re.compile(r"\b[A-Z]{2,}[+-]?\b"),          # O+, AB, SSN, USMC
    re.compile(r"\b[A-Z][a-z]{2,}\b"),          # proper nouns
)
# Capitalised ordinary words are not evidence of anything: a sentence-initial "The" would
# otherwise make almost every statement "attributable" to whatever turn was nearby.
_NOT_DISTINCTIVE = {
    "the", "this", "that", "these", "those", "there", "their", "they", "then", "them",
    "what", "when", "where", "which", "while", "who", "whom", "why", "how", "his",
    "her", "she", "him", "and", "but", "for", "not", "was", "were", "are", "has",
    "have", "had", "owner", "jarvis", "from", "with", "about", "into", "over", "after",
    "before", "his", "its", "our", "you", "your", "one", "two",
}


def _distinctive(statement: str) -> list[str]:
    """The parts of a statement that can be traced back to a turn.

    Ordinary words are excluded on purpose -- they recur in every segment, so including
    them would make every statement attributable to something and the gate never fire.
    """
    found = set()
    for rx in _DISTINCTIVE_RX:
        for m in rx.finditer(statement or ""):
            tok = m.group(0).lower()
            if tok in _NOT_DISTINCTIVE:
                continue
            found.add(tok)
    return sorted(found)


def _mentions(text_low: str, tok: str) -> bool:
    """Whole-token match. A substring test would let "ab" match inside "about"."""
    return re.search(r"(?<!\w)" + re.escape(tok) + r"(?!\w)", text_low) is not None


def ungrounded_jarvis_claim(statement: str, rows) -> bool:
    """True when a statement traces to Jarvis's own turn and cites no evidence.

    CONSERVATIVE BY CONSTRUCTION: it returns False unless it can POSITIVELY show that the
    content came from an uncited Jarvis turn. A statement it cannot trace anywhere is left
    to the other gates rather than rejected here -- losing a fact the owner stated is
    worse than keeping one he did not, and this gate only ever sees candidates that
    already passed the filler and identity checks.
    """
    toks = _distinctive(statement)
    if not toks:
        return False

    user_text = "\n".join((r["content"] or "") for r in rows
                           if r["role"] == "user").lower()
    if any(_mentions(user_text, t) for t in toks):
        return False                      # he said it; his word is the source

    for r in rows:
        if r["role"] != "assistant":
            continue
        text = r["content"] or ""
        low = text.lower()
        if any(_mentions(low, t) for t in toks):
            return not _CITATION.search(text)   # a cited reading is evidence; a bare one is not
    return False


# ------------------------------------------------------------------ model call

def _extract_json(text: str) -> dict | None:
    """Pull a JSON object out of whatever the lane actually returned.

    Local lanes fence their JSON in markdown, prepend a sentence, or emit a
    trailing \x00. Being strict here would mean dropping real facts on the floor.
    """
    if not text:
        return None
    text = text.strip().replace("\x00", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except json.JSONDecodeError:
            pass
    # First {...} block, brace-matched.
    start = text.find("{")
    while start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def call_lane(prompt: str, timeout: int = 300) -> str:
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 1600, "temperature": 0.1}
    req = urllib.request.Request(f"{LANE}/v1/chat/completions",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read())
    msg = body["choices"][0]["message"]
    return ((msg.get("content") or "") + "\n" + (msg.get("reasoning_content") or "")).strip()


# ------------------------------------------------------------------ window -> facts

def fetch_window(conn, after_id: int, limit: int):
    return list(conn.execute(
        "SELECT id, ts, session, model, role, content FROM turns WHERE id > ?"
        " ORDER BY id LIMIT ?", (after_id, limit)))


CONTEXT_TURNS = int(os.getenv("JARVIS_MEMORY_CONTEXT", "6"))  # lookback for corrections


def fetch_context(conn, before_id: int, limit: int = CONTEXT_TURNS):
    """The turns immediately before a window, for a correction to refer to.

    A window is a HARD 24-turn boundary. A correction that lands on the first turn of a
    window has nothing above it -- the claim it rejects sits in the previous window, which
    this pass never sees -- so its subject cannot be restored and the correction is
    emitted with no referent at all. That is "he forgets the context" in its purest form:
    the words arrive, the thing they are about does not.

    READ-ONLY. These turns were already consolidated by the previous window; they are
    shown to the extractor and never consolidated again.
    """
    rows = list(conn.execute(
        "SELECT id, ts, session, model, role, content FROM turns WHERE id < ?"
        " ORDER BY id DESC LIMIT ?", (before_id, limit)))
    return rows[::-1]                       # back into chronological order


def transcript_of(rows) -> str:
    out = []
    for r in rows:
        who = {"user": "OWNER", "assistant": "JARVIS", "system": "SYSTEM"}.get(r["role"], r["role"])
        out.append(f"[{who}] {r['content']}")
    return "\n".join(out)


def consolidate(conn, fact: dict, *, session: str, dry: bool,
                replaces=None) -> tuple[str, int | None]:
    """Decide insert vs. duplicate vs. supersede. Returns (action, row_id).

    The rule, in order:
      - a near-identical statement is a duplicate  -> reinforce, don't duplicate rows
      - a similar subject with different content    -> a revision, so supersede
      - otherwise                                   -> a new belief
    Lexical, so it is auditable and cheap; and it errs toward *not* merging.

    `replaces` is the row the owner just corrected. When it is supplied the
    match is not searched for and the reinforce shortcut DOES NOT APPLY: a
    correction is always a replacement. Two statements differing only in a
    short value reduce to the same token set under _words, so without this the
    corrected fact would reinforce the one the owner just rejected.
    """
    statement = (fact.get("statement") or "").strip()
    if not statement:
        return "empty", None
    entity, topic = fact.get("entity"), fact.get("topic")
    match = (replaces if replaces is not None else
             jarvis_db.find_similar_fact(conn, statement, entity=entity, topic=topic))
    if match is None:
        if dry:
            return "insert", None
        fid = jarvis_db.add_fact(conn, statement, entity=entity, topic=topic,
                                 kind=fact.get("kind", "fact"), source_session=session)
        return "insert", fid

    a, b = jarvis_db._words(match["statement"]), jarvis_db._words(statement)
    overlap = len(set(a) & set(b)) / max(1, len(set(a) | set(b)))
    if overlap >= 0.9 and replaces is None:
        if not dry:
            with conn:
                conn.execute("UPDATE facts SET confidence=MIN(2.0, confidence+0.1), updated=?"
                             " WHERE id=?", (time.time(), match["id"]))
        return "reinforce", match["id"]
    if not dry:
        fid = jarvis_db.add_fact(conn, statement, entity=entity, topic=topic,
                                 kind=fact.get("kind", "fact"), supersedes=match["id"],
                                 source_session=session)
        return "supersede", fid
    return "supersede", match["id"]


def process_window(conn, rows, *, dry: bool, context=None) -> dict:
    result = {"facts": 0, "insert": 0, "reinforce": 0, "supersede": 0, "archived": 0,
              "retracted": 0, "unmatched": [], "actions": [], "rejected": [],
              "skipped": None}

    if window_is_trivial(rows):
        result["skipped"] = "filler-only window"
        return result

    transcript = transcript_of(rows)
    if context:
        # Labelled separately because these turns are NOT being consolidated:
        # they are here only so a correction can name the claim it rejects.
        transcript = ("EARLIER IN THIS CONVERSATION (already consolidated -- "
                      "shown only so a correction can name what it rejects):\n"
                      + transcript_of(context) + "\n\nCURRENT SEGMENT:\n" + transcript)
    reply = call_lane(DISCRIMINATOR + transcript)
    data = _extract_json(reply)
    if data is None:
        # No parse -> no cursor advance -> the next tick retries this window.
        result["skipped"] = "unparseable model reply"
        return result

    session = rows[0]["session"]
    for fact in (data.get("extracted_facts") or []):
        if not isinstance(fact, dict):
            continue
        statement = (fact.get("statement") or "").strip()
        if not statement:
            continue
        # CORRECTIONS COME FIRST, BEFORE EVERY REJECT GATE. A correction's subject
        # necessarily lives in the turn being corrected -- usually an uncited
        # [JARVIS] one -- so identity_from_nobody and ungrounded_jarvis_claim would
        # each discard it and leave the wrong belief standing. And "the owner says
        # this is wrong" is not a transient status report, which is what
        # is_transient_statement is for. His rejection IS the evidence.
        if (fact.get("corrects") or "").strip() or fact.get("kind") == "retraction":
            apply_correction(conn, fact, session=session, dry=dry, result=result)
            continue
        if is_transient_statement(statement):
            # Not written. Reported instead, so that a wrong call here is visible
            # and tunable rather than a silent deletion.
            result["rejected"].append(statement[:90])
            continue
        if identity_from_nobody(statement, rows):
            # About who the owner is, and he never said it. See the note above —
            # this is the gate that would have stopped "Michael Corwin".
            result["rejected"].append(f"identity, not from the owner: {statement[:70]}")
            continue
        if ungrounded_jarvis_claim(statement, rows):
            # Traces to Jarvis's own answer, which cites nothing. He is allowed to
            # be a source when he is reporting a reading; he is not allowed to be
            # one when he is talking. See the note on the gate.
            result["rejected"].append(f"from Jarvis, uncited: {statement[:70]}")
            continue
        action, fid = consolidate(conn, fact, session=session, dry=dry)
        if action == "empty":
            continue
        result["facts"] += 1
        result[action] = result.get(action, 0) + 1
        result["actions"].append(f"{action}: {(fact.get('statement') or '')[:90]}")

    arch = data.get("archive") or {}
    if arch.get("worth_keeping") and (arch.get("summary") or "").strip():
        if not dry:
            jarvis_db.add_chunk(conn, arch["summary"], title=arch.get("title") or "conversation",
                                session=session, ts_from=rows[0]["ts"], ts_to=rows[-1]["ts"])
        result["archived"] = 1

    return result


# ------------------------------------------------------------------ driver

def run_once(dry: bool = False, max_windows: int = 5, verbose: bool = True) -> dict:
    conn = jarvis_db.open_db()
    start = int(jarvis_db.get_cursor(conn, CURSOR, "0"))
    totals = {"windows": 0, "facts": 0, "insert": 0, "reinforce": 0, "supersede": 0,
              "archived": 0, "skipped": 0, "rejected": 0, "retracted": 0,
              "unmatched": 0, "last_id": start}

    for _ in range(max_windows):
        rows = fetch_window(conn, totals["last_id"], WINDOW)
        if not rows:
            break
        if verbose:
            print(f"  window #{totals['windows'] + 1}: turns {rows[0]['id']}..{rows[-1]['id']} "
                  f"({len(rows)} turns)")
        # Lookback only when someone is being corrected: it costs a query, and the
        # extractor does not need the past for an ordinary window.
        ctx = fetch_context(conn, rows[0]["id"]) if owner_corrects(rows) else None
        res = process_window(conn, rows, dry=dry, context=ctx)
        totals["windows"] += 1
        totals["facts"] += res["facts"]
        totals["insert"] += res["insert"]
        totals["reinforce"] += res["reinforce"]
        totals["supersede"] += res["supersede"]
        totals["archived"] += res["archived"]
        totals["rejected"] += len(res["rejected"])
        totals["retracted"] += res["retracted"]
        totals["unmatched"] += len(res["unmatched"])
        if res["skipped"]:
            totals["skipped"] += 1
            if verbose:
                print(f"    skipped: {res['skipped']}")
        for line in res["actions"]:
            if verbose:
                print(f"    {line}")
        for line in res["rejected"]:
            if verbose:
                print(f"    rejected (not a fact): {line}")
        for line in res["unmatched"]:
            if verbose:
                print(f"    UNMATCHED correction (nothing retired): {line}")
        totals["last_id"] = rows[-1]["id"]
        # A skipped window still advances: retrying filler forever would wedge the
        # queue behind it. Only an unparseable reply is worth a retry, and that
        # leaves the cursor alone.
        if res["skipped"] == "unparseable model reply":
            totals["last_id"] = rows[0]["id"] - 1 if rows[0]["id"] > start else start
            break
        if not dry:
            jarvis_db.set_cursor(conn, CURSOR, totals["last_id"])

    if dry and verbose:
        print("  (dry run — cursor not advanced)")
    return totals


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="one pass (default)")
    ap.add_argument("--dry-run", action="store_true", help="show, do not write")
    ap.add_argument("--max-windows", type=int, default=5)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    conn = jarvis_db.open_db()
    if a.stats:
        c = int(jarvis_db.get_cursor(conn, CURSOR, "0"))
        total = conn.execute("SELECT COALESCE(MAX(id),0) m FROM turns").fetchone()["m"]
        n_all = conn.execute("SELECT count(*) n FROM facts").fetchone()["n"]
        n_active = conn.execute("SELECT count(*) n FROM facts WHERE status='active'").fetchone()["n"]
        print(f"cursor at turn {c} of {total} ({max(0, total - c)} behind)")
        print(f"facts: {n_all} ({n_active} active, {n_all - n_active} retired)")
        for r in conn.execute("SELECT entity, topic, statement FROM facts WHERE status='active'"
                              " ORDER BY updated DESC LIMIT 15"):
            print(f"  {r['entity'] or '-'}/{r['topic'] or '-'}: {r['statement'][:100]}")
        return 0

    t0 = time.time()
    totals = run_once(dry=a.dry_run, max_windows=a.max_windows, verbose=not a.quiet)
    print(f"memory: {totals['windows']} window(s), {totals['facts']} fact(s) "
          f"({totals['insert']} new, {totals['reinforce']} reinforced, "
          f"{totals['supersede']} superseded, {totals['retracted']} retracted), "
          f"{totals['archived']} archived, {totals['skipped']} skipped, "
          f"{totals['unmatched']} unmatched correction(s) — {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
