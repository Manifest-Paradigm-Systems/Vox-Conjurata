"""Owner-vocabulary alias map: the phrase the owner speaks -> real fact key_name values."""
import re
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


# A phrase in the map is matched as a run of WORDS, so the separators the schema and the
# owner disagree about (underscore vs space) cannot hide a phrase: "duty_station" and
# "duty station" both reduce to the words duty, station.
_WORD = re.compile(r"[a-z0-9]+")


def resolve(text, mapping=None) -> tuple[List[str], set]:
    """Real key_names for every mapped phrase appearing in `text`, and the words they cover.

    Returns (keys, covered):
      keys     the real `key_name` values, in map order, de-duplicated
      covered  the surface words that took part in a match

    Phrases are tried LONGEST FIRST, so "pay entry base date" resolves as one phrase rather
    than being pre-empted by a shorter window inside it.
    """
    if not isinstance(text, str):
        return [], set()
    table = ALIAS_MAP if mapping is None else mapping
    words = _WORD.findall(text.lower())
    if not words:
        return [], set()
    widths = [len(p.split()) for p in table if p.strip()]
    max_n = min(max(widths) if widths else 1, len(words))

    keys: List[str] = []
    covered: set = set()
    seen: set = set()
    for n in range(max_n, 0, -1):
        for i in range(len(words) - n + 1):
            window = words[i:i + n]
            found = table.get(" ".join(window))
            if not found:
                continue
            covered.update(window)
            for key in found:
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
    return keys, covered


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

# Label variants table for OCR vocabulary mismatches: the key is the run-together token a
# scanned document's OCR pass produces, the value the real spaced label. Expected to grow
# from measurement, not from guessing.
OCR_LABEL_VARIANTS = {
    'dutystation': ['duty station'],
    'homeaddress': ['home address'],
    'payentrybasedate': ['pay entry base date'],
    'memberrank': ['rank'],
    'bloodtype': ['blood type'],
    'dateofbirth': ['date of birth'],
    'fullname': ['full name'],
    'stationnumber': ['station number'],
    'homestreet': ['home address'],
    'homecity': ['city'],
    'homezip': ['zip'],
}


def ocr_label_variants(label) -> List[str]:
    """Spelling variants recorded for a printed label that arrived run-together."""
    if not isinstance(label, str):
        return []
    return OCR_LABEL_VARIANTS.get(label.strip().lower(), []).copy()
