import json
import os
from pathlib import Path

import pytest

from eval_baseline import store, diff

def test_diff_reports_a_fix():
    baseline = {'m1|blind': [['c1', 'wrong']]}
    new = {'m1|blind': [['c1', 'correct']]}
    result = diff(baseline, new)
    assert len(result['changed']) == 1
    assert result['changed'][0] == {'case': 'm1|blind|0|c1', 'was': 'wrong', 'now': 'correct'}

def test_diff_reports_a_regression():
    baseline = {'m1|blind': [['c1', 'correct']]}
    new = {'m1|blind': [['c1', 'wrong']]}
    result = diff(baseline, new)
    assert len(result['changed']) == 1
    assert result['changed'][0] == {'case': 'm1|blind|0|c1', 'was': 'correct', 'now': 'wrong'}

def test_unchanged_case_is_not_reported():
    baseline = {'m1|blind': [['c1', 'correct']]}
    new = {'m1|blind': [['c1', 'correct']]}
    result = diff(baseline, new)
    assert result['changed'] == []
    assert result['added'] == []
    assert result['removed'] == []

def test_added_and_removed_are_their_own_buckets():
    baseline = {'m1|blind': [['c1', 'correct']]}
    new = {'m1|blind': [['c2', 'correct']]}
    result = diff(baseline, new)
    assert result['changed'] == []
    assert result['added'] == [{'case': 'm1|blind|0|c2'}]
    assert result['removed'] == [{'case': 'm1|blind|0|c1'}]

def test_case_identity_includes_model_and_mode():
    baseline = {
        'm1|blind': [['c1', 'correct']],
        'm1|grounded': [['c1', 'correct']]
    }
    new = {
        'm1|blind': [['c1', 'correct']],
        'm1|grounded': [['c1', 'wrong']]
    }
    result = diff(baseline, new)
    assert len(result['changed']) == 1
    assert result['changed'][0] == {'case': 'm1|grounded|0|c1', 'was': 'correct', 'now': 'wrong'}
    assert result['added'] == []
    assert result['removed'] == []

def test_store_refuses_to_overwrite_baseline(tmp_path):
    path = tmp_path / 'baseline.json'
    data = {'m1|blind': [['c1', 'correct']]}
    store(path, data)
    # Try to overwrite without overwrite=True
    with pytest.raises(FileExistsError):
        store(path, data)
    # Check file contents unchanged
    assert json.loads(path.read_text()) == data
    # Overwrite with overwrite=True
    store(path, data, overwrite=True)
    assert json.loads(path.read_text()) == data


def test_diff_sees_a_change_in_a_non_final_question():
    """The regression this shipped with: a class holding several questions kept only the
    last verdict, so a change to any earlier question was reported as no change at all."""
    baseline = {'m1|blind': [['A', 'correct'], ['A', 'correct'], ['A', 'correct']]}
    new = {'m1|blind': [['A', 'correct'], ['A', 'wrong'], ['A', 'correct']]}
    result = diff(baseline, new)
    assert len(result['changed']) == 1
    assert result['changed'][0] == {'case': 'm1|blind|1|A', 'was': 'correct', 'now': 'wrong'}


def test_every_question_of_a_class_is_its_own_case():
    """Eight class-A questions must be eight cases, as in the real 13-question set."""
    baseline = {'m1|blind': [['A', 'correct']] * 8}
    new = {'m1|blind': [['A', 'correct']] * 7 + [['A', 'wrong']]}
    result = diff(baseline, new)
    assert len(result['changed']) == 1
    assert result['changed'][0] == {'case': 'm1|blind|7|A', 'was': 'correct', 'now': 'wrong'}


def test_fixing_every_question_of_a_class_is_reported():
    """The real case: the wiring fixes every INVENTED verdict. That must NOT read as
    'no change' — which is exactly what the per-class key did."""
    baseline = {'jarvis-ask|lane': [['B', 'INVENTED'], ['B', 'wrong'], ['B', 'refused']]}
    new = {'jarvis-ask|lane': [['B', 'refused'], ['B', 'refused'], ['B', 'refused']]}
    result = diff(baseline, new)
    assert len(result['changed']) == 2
    assert sorted(c['case'] for c in result['changed']) == ['jarvis-ask|lane|0|B',
                                                            'jarvis-ask|lane|1|B']
