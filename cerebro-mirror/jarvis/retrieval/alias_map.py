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

def keys_for(term: str) -> List[str]:
    return _aliast_map.keys_for(term)

def expand(terms: List[str]) -> List[str]:
    return _aliast_map.expand(terms)

def label_variants(label: str) -> List[str]:
    return _aliast_map.label_variants(label)
