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

ALIAS_MAP = {
    'duty station': ['duty_location'],
    'home address': ['home_street'],
    'mos': ['member_occupation', 'military_occupation_code'],
    'pay entry base date': ['pebd'],
    'rank': ['member_rank'],
    'blood type': ['blood_type'],
    'date of birth': ['date_of_birth'],
    'full name': ['member_name'],
    'station number': ['station_number'],
    'address': ['home_street'],
    'city': ['home_city'],
    'zip': ['home_zip'],
}


def keys_for(term) -> List[str]:
    if not isinstance(term, str):
        return []
    term = term.strip().lower()
    return ALIAS_MAP.get(term, []).copy()


def expand(terms) -> List[str]:
    if not isinstance(terms, list):
        return []
    result = []
    seen = set()
    for term in terms:
        if not isinstance(term, str):
            continue
        # Add original term if not already present
        if term not in seen:
            result.append(term)
            seen.add(term)
        # Add expanded keys
        for key in keys_for(term):
            if key not in seen:
                result.append(key)
                seen.add(key)
    return result


def label_variants(label: str) -> List[str]:
    return LABEL_VARIANTS.get(label.strip().lower(), [])


class AliasMap:
    def __init__(self):
        self._map = {
            "duty_station": ["duty_location"],
            "home_address": ["home_street"],
            "mos": ["occupation", "occupation_code"],
            "military occupational specialty": ["occupation", "occupation_code"],
            "pay_entry_base_date": ["pebd"],
        }

    def keys_for(self, term: str) -> List[str]:
        # Normalize the term: strip whitespace and convert to lowercase
        normalized = term.strip().lower()
        return self._map.get(normalized, [term])

    def expand(self, terms: List[str]) -> List[str]:
        result = []
        for term in terms:
            result.extend(self.keys_for(term))
        # Remove duplicates while preserving order
        seen = set()
        deduped = []
        for item in result:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped

    def label_variants(self, label: str) -> List[str]:
        # Return variants for OCR label mismatches
        return LABEL_VARIANTS.get(label.strip().lower(), [])

# Create a singleton instance
_aliast_map = AliasMap()

# Preserve the module-level functions for compatibility
# (These are expected to be used by other modules)

def keys_for(term: str) -> List[str]:
    return _aliast_map.keys_for(term)

def expand(terms: List[str]) -> List[str]:
    return _aliast_map.expand(terms)

def label_variants(label: str) -> List[str]:
    return _aliast_map.label_variants(label)
