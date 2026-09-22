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

# Label variants table for OCR vocabulary mismatches.
# This is expected to grow from measurement, not from guessing.
LABEL_VARIANTS = {
    'dutystation': ['duty station'],
    'homeaddress': ['home address'],
    'mos': ['mos'],  # Already in ALIAS_MAP, but kept for consistency
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

def keys_for(term: str) -> list[str]:
    if not isinstance(term, str):
        return []
    term = term.lower().strip()
    return ALIAS_MAP.get(term, []).copy()

def expand(terms: list[str]) -> list[str]:
    if not isinstance(terms, list):
        return []
    
    result = []
    seen = set()
    
    for term in terms:
        if not isinstance(term, str):
            continue
        
        # Add original term first
        original = term
        if original not in seen:
            result.append(original)
            seen.add(original)
        
        # Add expanded keys
        expanded = keys_for(term)
        for key in expanded:
            if key not in seen:
                result.append(key)
                seen.add(key)
    
    return result

def label_variants(label: str) -> list[str]:
    if not isinstance(label, str):
        return []
    label = label.lower().strip()
    return LABEL_VARIANTS.get(label, []).copy()
