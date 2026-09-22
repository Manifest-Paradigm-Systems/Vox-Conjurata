"""Owner-vocabulary alias map: the phrase the owner speaks -> real fact key_name values."""
from typing import List

LABEL_VARIANTS = {
    # Expected to grow from measurement, not guessing
    "first name": ["firstname", "first_name"],
    "last name": ["lastname", "last_name"],
    "date of birth": ["dob", "date_of_birth"],
    "social security number": ["ssn", "social_security_number"],
    "home address": ["address", "home_address"],
    "phone number": ["phone", "telephone"],
    "email address": ["email"],
    "military occupational specialty": ["mos", "military_occupation_specialty"],
    "pay entry base date": ["pebd", "pay_entry_base_date"],
    "duty station": ["duty_location", "duty_station"],
}

# Owner phrasing -> real key_name values in google.db.records_facts.
# Carries BOTH the spoken (space) form and the schema (underscore) form, because the owner
# says the first and the specs and schema spell the second.
ALIAS_MAP = {
    'duty station': ['duty_location'],
    'duty_station': ['duty_location'],
    'home address': ['home_street'],
    'home_address': ['home_street'],
    'mos': ['member_occupation', 'military_occupation_code'],
    'military occupational specialty': ['member_occupation', 'military_occupation_code'],
    'pay entry base date': ['pebd'],
    'pay_entry_base_date': ['pebd'],
    'rank': ['member_rank'],
    'blood type': ['blood_type'],
    'date of birth': ['date_of_birth'],
    'date_of_birth': ['date_of_birth'],
    'full name': ['member_name'],
    'station number': ['station_number'],
    'station_number': ['station_number'],
    'address': ['home_street'],
    'city': ['home_city'],
    'zip': ['home_zip'],
}


def keys_for(term) -> List[str]:
    if not isinstance(term, str):
        return []
    return ALIAS_MAP.get(term.strip().lower(), []).copy()


def expand(terms) -> List[str]:
    if not isinstance(terms, list):
        return []
    result = []
    seen = set()
    for term in terms:
        if not isinstance(term, str):
            continue
        if term not in seen:
            result.append(term)
            seen.add(term)
        for key in keys_for(term):
            if key not in seen:
                result.append(key)
                seen.add(key)
    return result


def label_variants(label) -> List[str]:
    if not isinstance(label, str):
        return []
    return LABEL_VARIANTS.get(label.strip().lower(), []).copy()


class AliasMap:
    """Object form, kept for callers that use it; reads the shared ALIAS_MAP."""

    def __init__(self, mapping=None):
        self._map = ALIAS_MAP if mapping is None else mapping

    def keys_for(self, term):
        if not isinstance(term, str):
            return []
        return self._map.get(term.strip().lower(), []).copy()

    def expand(self, terms):
        if not isinstance(terms, list):
            return []
        result, seen = [], set()
        for term in terms:
            if not isinstance(term, str):
                continue
            if term not in seen:
                result.append(term)
                seen.add(term)
            for key in self.keys_for(term):
                if key not in seen:
                    result.append(key)
                    seen.add(key)
        return result

    def label_variants(self, label):
        if not isinstance(label, str):
            return []
        return LABEL_VARIANTS.get(label.strip().lower(), []).copy()


_aliast_map = AliasMap()
