import pytest
from alias_map import keys_for, expand, label_variants


def test_duty_station_maps_to_duty_location():
    assert "duty_location" in keys_for("duty_station")

def test_home_address_maps_to_home_street():
    assert "home_street" in keys_for("home_address")

def test_mos_maps_to_both_occupation_keys():
    result = keys_for("mos")
    assert "occupation" in result
    assert "occupation_code" in result

def test_pay_entry_base_date_maps_to_pebd():
    result = keys_for("pay_entry_base_date")
    assert "pebd" in result
    assert "pay_entry_base_date" not in result


def test_unknown_term_passes_through_unchanged():
    # An unknown term should pass through expand() unchanged
    input_terms = ["unknown_term"]
    result = expand(input_terms)
    assert result == input_terms


def test_lookup_is_case_insensitive():
    # Lookup should be case-insensitive
    result = keys_for("DUTY_STATION")
    assert "duty_location" in result


def test_lookup_is_whitespace_insensitive():
    # Lookup should be whitespace-insensitive
    result = keys_for("  home_address  ")
    assert "home_street" in result


def test_multi_word_phrase_matches_as_phrase():
    # Multi-word phrase should match as a phrase
    result = keys_for("pay entry base date")
    assert "pebd" in result


def test_duplicate_mappings_deduped():
    # Two different input phrases that map to the same key should not duplicate that key
    result = expand(["mos", "military occupational specialty"])
    # Should only contain one "occupation" key
    assert result.count("occupation") == 1
    # Should also contain one "occupation_code"
    assert result.count("occupation_code") == 1
    # Should not contain duplicates
    assert len(result) == 2


def test_expand_does_not_mutate_input():
    # expand() should not mutate its input list
    input_terms = ["mos", "home_address"]
    original_input = input_terms.copy()
    expand(input_terms)
    assert input_terms == original_input
