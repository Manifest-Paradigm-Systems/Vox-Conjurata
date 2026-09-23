import json
import os

def store(path, data, overwrite=False):
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(f"Baseline file {path} already exists.")
    
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def _cases(series: dict) -> dict:
    """{model|mode|question-index|class: verdict} — one case per QUESTION, not per class.

    The index is load-bearing. Keying on (model|mode|class) alone makes every question of a
    class collide on one key, so the dict keeps only the last verdict in that class and a
    change to any earlier question reads as "no change".
    """
    cases = {}
    for key, value in (series or {}).items():
        for i, (class_name, verdict) in enumerate(value or []):
            cases[f"{key}|{i}|{class_name}"] = verdict
    return cases


def diff(baseline, new):
    # Convert both sides to {model|mode|index|class} -> verdict. One case per QUESTION:
    # keying on (model|mode|class) made all 8 class-A questions collide on one key, so the
    # dict kept only the last verdict of each class and a change to any other read as none.
    baseline_cases = _cases(baseline)
    new_cases = _cases(new)
    
    # Find changes, additions, and removals
    changed = []
    added = []
    removed = []
    
    all_cases = set(baseline_cases.keys()) | set(new_cases.keys())
    
    for case in sorted(all_cases):
        baseline_verdict = baseline_cases.get(case)
        new_verdict = new_cases.get(case)
        
        if baseline_verdict is not None and new_verdict is not None:
            if baseline_verdict != new_verdict:
                changed.append({'case': case, 'was': baseline_verdict, 'now': new_verdict})
        elif baseline_verdict is not None:
            removed.append({'case': case})
        elif new_verdict is not None:
            added.append({'case': case})
    
    return {
        'changed': changed,
        'added': added,
        'removed': removed
    }