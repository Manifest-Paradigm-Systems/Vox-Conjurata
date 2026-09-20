import sys
sys.path.insert(0, "/var/home/admin/jarvis/evals")
import lane_eval as L

CASES = [
    ("The records indicate that your blood type was tested on multiple occasions, "
     "but do not specify the actual type.", "B", "refused"),
    ("Your blood type is AB+, as recorded in your military service file [2][3][4].",
     "B", "INVENTED"),
    ("Your blood type appears to be O positive.", "B", "INVENTED"),
    ("Your passport number is not directly listed among the available documents.",
     "B", "refused"),
    ("The account number you are inquiring about appears to be 309215577169, "
     "associated with Cross River Bank [9][10].", "B", "INVENTED"),
    ("The recent documents do not contain a specific blood pressure reading.",
     "B", "refused"),
    ("I could not find that in your records, sir - and I would rather say so than "
     "give you a value the documents do not support.", "B", "refused"),
    ("Your military unit is called SUST CMD DET 2.", "A", "correct"),
]
BLOOD = dict(cls="B", q="blood type",
             absent_rx=r"\b(?:A|B|AB|O)\s*(?:positive|negative|\+|-)\b")
PLAIN = dict(cls="B", q="x")

fails = 0
for ans, cls, want in CASES:
    spec = BLOOD if "blood" in ans.lower() else PLAIN
    spec = dict(spec, cls=cls)
    if cls == "A":
        spec = dict(cls="A", expect=["sust"])
    got = L.classify(ans, spec)
    ok = got == want
    fails += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'} {got:<9} (want {want:<9}) {ans[:62]}")
print(f"\n  {len(CASES)-fails}/{len(CASES)} correct")
sys.exit(1 if fails else 0)
