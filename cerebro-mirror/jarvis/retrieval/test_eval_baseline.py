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
    assert result['changed'][0] == {'case': 'm1|blind|c1', 'was': 'wrong', 'now': 'correct'}

def test_diff_reports_a_regression():
    baseline = {'m1|blind': [['c1', 'correct']]}
    new = {'m1|blind': [['c1', 'wrong']]}
    result = diff(baseline, new)
    assert len(result['changed']) == 1
    assert result['changed'][0] == {'case': 'm1|blind|c1', 'was': 'correct', 'now': 'wrong'}

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
    assert result['added'] == [{'case': 'm1|blind|c2'}]
    assert result['removed'] == [{'case': 'm1|blind|c1'}]

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
    assert result['changed'][0] == {'case': 'm1|grounded|c1', 'was': 'correct', 'now': 'wrong'}
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
