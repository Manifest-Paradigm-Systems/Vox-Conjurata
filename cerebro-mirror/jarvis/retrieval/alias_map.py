from typing import List

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
        # For now, return the label itself
        return [label]

# Create a singleton instance
_alias_map = AliasMap()

def keys_for(term: str) -> List[str]:
    return _alias_map.keys_for(term)

def expand(terms: List[str]) -> List[str]:
    return _alias_map.expand(terms)

def label_variants(label: str) -> List[str]:
    return _alias_map.label_variants(label)
