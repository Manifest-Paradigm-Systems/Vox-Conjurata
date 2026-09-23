"""Unit tests for the memory worker's write gates and correction path.

Run on a scratch file — never the live DB. The lane is stubbed, so these exercise
`process_window` end to end without a model.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as jarvis_db  # noqa: E402
import memory  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# A turn long enough that the window clears MIN_CHARS (200) and the gates actually run.
# Without it the window is skipped as filler and a "nothing was written" assertion passes
# for the wrong reason — which is exactly how these two tests failed the first time.
_LONG = ("I am going through the deployment paperwork this evening and I need the medical "
         "section filled in correctly, so tell me what the service record actually says "
         "rather than guessing at any of it.")


def _rows(*pairs, session="test-session"):
    """Rows shaped like the `turns` table, as process_window sees them."""
    return [{"id": i + 1, "ts": 1000.0 + i, "session": session, "model": "x",
             "role": role, "content": text}
            for i, (role, text) in enumerate(pairs)]


def _stub_lane(payload):
    """Replace the extractor with a scripted reply."""
    memory.call_lane = lambda prompt, timeout=300: json.dumps(payload)


def _status(conn, fid):
    return conn.execute("SELECT status FROM facts WHERE id=?", (fid,)).fetchone()["status"]


def run():
    print("\n-- owner_corrects: the phrasings that mean 'you are wrong' --")
    for text in ("No, that's not correct.",
                 "That is wrong.",
                 "No, I'm not a member of the Ranger Regiment.",
                 "I never said that.",
                 "it's not what I said",
                 "Actually, my station is Fort Lewis.",
                 "No, it's O-.",
                 "that's the wrong one"):
        check(f"corrects: {text[:44]!r}", memory.owner_corrects(_rows(("user", text))))
    for text in ("What is my blood type?",
                 "Deploy the new build to cerebro.",
                 "Thanks, that helps.",
                 "The inspection is on the 14th."):
        check(f"quiet:    {text[:44]!r}", not memory.owner_corrects(_rows(("user", text))))

    print("\n-- window_is_trivial: a terse correction is still worth a model call --")
    terse = _rows(("assistant", "Your blood type is AB+."), ("user", "No, that's wrong."))
    check("a short correction window is NOT skipped",
          not memory.window_is_trivial(terse),
          f"substance={sum(len(r['content']) for r in terse)}")
    filler = _rows(("user", "hey"), ("assistant", "Hello."), ("user", "you there"))
    check("ordinary filler is still skipped", memory.window_is_trivial(filler))

    print("\n-- the measured bug: a short value is invisible to _words --")
    wrong = "The owner's blood type is AB+."
    right = "The owner's blood type is O-."
    check("_words cannot tell the two values apart",
          set(jarvis_db._words(wrong)) == set(jarvis_db._words(right)),
          f"{sorted(set(jarvis_db._words(wrong)))}")

    path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = jarvis_db.open_db(path)
    fid = jarvis_db.add_fact(conn, wrong, entity="user", topic="medical")
    action, _ = memory.consolidate(conn, {"statement": right, "entity": "user", "topic": "medical"},
                                   session="s", dry=True)
    check("WITHOUT the correction, the wrong fact is REINFORCED — the bug", action == "reinforce",
          f"got {action!r}; the owner is corrected and Jarvis grows more certain")

    print("\n-- a retraction retires what it names, and writes nothing --")
    conn = jarvis_db.open_db(os.path.join(tempfile.mkdtemp(), "t2.db"))
    fid = jarvis_db.add_fact(conn, wrong, entity="user", topic="medical")
    rows = _rows(("user", "what's my blood type"),
                 ("assistant", "Your blood type is AB+."),
                 ("user", "No, that's wrong."))
    _stub_lane({"extracted_facts": [{"statement": wrong, "entity": "user", "topic": "medical",
                                     "kind": "retraction", "corrects": wrong}],
                "archive": {"worth_keeping": False}})
    res = memory.process_window(conn, rows, dry=False)
    check("the wrong belief is retired", _status(conn, fid) == "retired", f"got {_status(conn, fid)}")
    check("nothing new was invented to fill the gap",
          conn.execute("SELECT count(*) c FROM facts").fetchone()["c"] == 1)
    check("it is counted and shown", res["retracted"] == 1 and len(res["actions"]) == 1)

    print("\n-- the gates must NOT swallow a correction --")
    check("ungrounded_jarvis_claim alone WOULD reject this statement",
          memory.ungrounded_jarvis_claim(wrong, rows) is True,
          "its subject lives in the uncited [JARVIS] turn — so ordering is load-bearing")
    check("yet the correction still ran", _status(conn, fid) == "retired")

    print("\n-- a correction carrying the value replaces, and never reinforces --")
    conn = jarvis_db.open_db(os.path.join(tempfile.mkdtemp(), "t3.db"))
    fid = jarvis_db.add_fact(conn, wrong, entity="user", topic="medical")
    before = conn.execute("SELECT confidence FROM facts WHERE id=?", (fid,)).fetchone()["confidence"]
    rows = _rows(("assistant", "Your blood type is AB+."),
                 ("user", "No, it's O-."))
    _stub_lane({"extracted_facts": [{"statement": right, "entity": "user", "topic": "medical",
                                     "kind": "fact", "corrects": wrong}],
                "archive": {"worth_keeping": False}})
    res = memory.process_window(conn, rows, dry=False)
    new = conn.execute("SELECT id, statement, confidence FROM facts WHERE status='active'").fetchall()
    check("the corrected value is the only active belief",
          len(new) == 1 and new[0]["statement"] == right,
          f"active={[r['statement'] for r in new]}")
    check("the rejected fact is superseded", _status(conn, fid) == "superseded")
    check("its confidence was NOT bumped",
          conn.execute("SELECT confidence FROM facts WHERE id=?", (fid,)).fetchone()["confidence"] == before)
    check("the new fact points back at what it replaced",
          conn.execute("SELECT supersedes FROM facts WHERE status='active'").fetchone()["supersedes"] == fid)
    check("reported as a supersede, not a reinforce", res["supersede"] == 1 and res["reinforce"] == 0)

    print("\n-- a correction finds its target across a topic mismatch --")
    conn = jarvis_db.open_db(os.path.join(tempfile.mkdtemp(), "t4.db"))
    fid = jarvis_db.add_fact(conn, wrong, entity="user", topic="service_record")
    row, score = memory.find_corrected_fact(conn, wrong)
    check("found despite the caller's topic guess differing",
          row is not None and row["id"] == fid, f"score={score}")
    check("reports how confident the match was", score >= 0.6, f"score={score}")

    print("\n-- a correction that matches nothing is reported, not invented --")
    conn = jarvis_db.open_db(os.path.join(tempfile.mkdtemp(), "t5.db"))
    _stub_lane({"extracted_facts": [{"statement": "The owner's favourite colour is chartreuse.",
                                     "entity": "user", "topic": "prefs",
                                     "kind": "retraction",
                                     "corrects": "The owner's favourite colour is chartreuse."}],
                "archive": {"worth_keeping": False}})
    res = memory.process_window(conn, _rows(("user", "No, that's wrong.")), dry=False)
    check("nothing was written", conn.execute("SELECT count(*) c FROM facts").fetchone()["c"] == 0)
    check("and it is visible rather than silent", len(res["unmatched"]) == 1, f"{res['unmatched']}")

    print("\n-- a correction on a window boundary keeps its referent --")
    conn = jarvis_db.open_db(os.path.join(tempfile.mkdtemp(), "t5b.db"))
    fid = jarvis_db.add_fact(conn, wrong, entity="user", topic="medical")
    ctx = _rows(("assistant", "Your blood type is AB+."))
    rows = _rows(("user", "No, that's wrong."))      # FIRST turn of a window: nothing above it
    seen = {}

    def _cap(prompt, timeout=300):
        seen["prompt"] = prompt
        return json.dumps({"extracted_facts": [{"statement": wrong, "entity": "user",
                                                "topic": "medical", "kind": "retraction",
                                                "corrects": wrong}],
                           "archive": {"worth_keeping": False}})

    memory.call_lane = _cap
    res = memory.process_window(conn, rows, dry=False, context=ctx)
    check("the rejected claim is shown as earlier context",
          "EARLIER IN THIS CONVERSATION" in seen.get("prompt", "")
          and "AB+" in seen.get("prompt", ""), "the extractor could not name the referent")
    check("the correction still retires it", _status(conn, fid) == "retired")
    check("the context turns are not themselves consolidated",
          conn.execute("SELECT count(*) c FROM facts").fetchone()["c"] == 1)

    print("\n-- no regression: an uncited Jarvis claim is still rejected --")
    conn = jarvis_db.open_db(os.path.join(tempfile.mkdtemp(), "t6.db"))
    _stub_lane({"extracted_facts": [{"statement": "The owner's blood type is AB+.",
                                     "entity": "user", "topic": "medical", "kind": "fact"}],
                "archive": {"worth_keeping": False}})
    res = memory.process_window(conn, _rows(("user", _LONG),
                                            ("assistant", "Your blood type is AB+.")), dry=False)
    check("rejected, nothing written",
          conn.execute("SELECT count(*) c FROM facts").fetchone()["c"] == 0)
    check("rejection names the gate", any("uncited" in r for r in res["rejected"]), f"{res['rejected']}")

    print("\n-- no regression: a cited Jarvis reading is still kept --")
    conn = jarvis_db.open_db(os.path.join(tempfile.mkdtemp(), "t7.db"))
    _stub_lane({"extracted_facts": [{"statement": "The property inspection is on the 14th.",
                                     "entity": "rental_property", "topic": "inspection",
                                     "kind": "fact"}],
                "archive": {"worth_keeping": False}})
    memory.process_window(conn, _rows(("user", _LONG),
                                      ("assistant", "The inspection is on the 14th [1].")),
                          dry=False)
    check("kept — he was reporting a reading",
          conn.execute("SELECT count(*) c FROM facts").fetchone()["c"] == 1)

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
