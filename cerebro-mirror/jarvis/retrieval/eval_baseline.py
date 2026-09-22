import json
import os

def store(path, data, overwrite=False):
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(f"Baseline file {path} already exists.")
    
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def diff(baseline, new):
    # Convert baseline and new to sets of (model|mode|class) -> verdict
    baseline_cases = {}
    for key, value in baseline.items():
        for class_name, verdict in value:
            case = f"{key}|{class_name}"
            baseline_cases[case] = verdict
    
    new_cases = {}
    for key, value in new.items():
        for class_name, verdict in value:
            case = f"{key}|{class_name}"
            new_cases[case] = verdict
    
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