"""Reading the model's reply — including the replies that are not in the format.

The parser prefers JSON, but the prompt never used to ask for it, so the common reply was
prose. That prose used to be discarded whole, description and all, which left the
resolver with nothing to work from on any object without part numbers.
"""
import json

import pytest

from visual_lookup.read import build_prompt, parse_vision_response

PROSE = ("I can see a person in a suit and tie, but there are no markings or part "
         "numbers to read.")


def test_parses_the_json_it_asks_for():
    parsed = parse_vision_response(
        '{"identification": "cellar spider", "markings": [], "description": "long legs"}')
    assert parsed == {"identification": "cellar spider", "markings": [],
                      "description": "long legs"}


def test_parses_json_the_model_wrapped_in_a_sentence():
    """Asked for only-JSON, this model still says 'According to the image, ... {...}.'"""
    parsed = parse_vision_response(
        'According to the image, the answer is {"identification": "buck converter", '
        '"markings": ["LM2596S"], "description": "a small board"}.')
    assert parsed["identification"] == "buck converter"
    assert parsed["markings"] == ["LM2596S"]


def test_parses_the_markings_prefix_form():
    assert parse_vision_response("markings: LM2596S")["markings"] == ["LM2596S"]


def test_prose_is_kept_as_the_description():
    """The regression: a prose reply is the only signal we have, so it must survive."""
    parsed = parse_vision_response(PROSE)
    assert parsed["markings"] == []
    assert parsed["description"] == PROSE


def test_a_reply_missing_identification_still_parses():
    parsed = parse_vision_response('{"markings": ["A"], "description": "b"}')
    assert parsed["markings"] == ["A"]
    assert parsed["identification"] == ""


def test_empty_reply_is_empty_and_not_an_error():
    assert parse_vision_response("   ") == {"identification": "", "markings": [],
                                            "description": ""}


def test_a_dict_is_a_programming_error_not_a_case_to_absorb():
    """Handing it an already-parsed dict is how the CLI silently broke once."""
    with pytest.raises(TypeError):
        parse_vision_response({"markings": [], "description": ""})


def test_the_prompt_asks_for_a_name_and_a_parseable_shape():
    """A description alone is a poor search query; the name is what the wiki can match.
    And it must ask for the *specific* name: "Spider" searches to a television series."""
    prompt = build_prompt("what is this?")
    assert "json" in prompt.lower()
    assert "identification" in prompt
    assert "markings" in prompt
    assert "what is this?" in prompt
    assert "most specific" in prompt


def test_a_sentence_in_the_markings_field_is_not_a_marking():
    """Asked to read markings, the model answers with their absence in the same field —
    and that phrase would otherwise go to the encyclopedia as a search term."""
    parsed = parse_vision_response(json.dumps({
        "identification": "spider",
        "markings": ["No visible markings or part numbers on the spider", "none"],
        "description": "a spider"}))
    assert parsed["markings"] == []


def test_a_real_part_number_is_not_mistaken_for_a_negative():
    """The absent-marking filter must not eat a marking that merely starts with "NO"."""
    parsed = parse_vision_response(json.dumps({
        "identification": "chip", "markings": ["LM2596S", "NO-123"], "description": "x"}))
    assert parsed["markings"] == ["LM2596S", "NO-123"]
