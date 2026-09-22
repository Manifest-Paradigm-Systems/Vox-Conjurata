import sqlite3
from collections import defaultdict
from typing import List, Dict, Any


def find_collisions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Find facts that contradict each other in the database.

    Groups current facts by their value, and reports every group where the SAME value
    appears under DIFFERENT keys. These are the pairs where at most one extraction
    can be right.

    Args:
        conn: A SQLite database connection.

    Returns:
        A list of dictionaries, each with a 'value' and a 'keys' list.
        The result is sorted deterministically by the keys list.
    """
    # Query all rows from the facts table
    cursor = conn.execute("SELECT key_name, value FROM records_facts")
    rows = cursor.fetchall()

    # Group by value
    value_to_keys = defaultdict(list)
    for key, value in rows:
        value_to_keys[value].append(key)

    # Filter groups with more than one key
    collisions = []
    for value, keys in value_to_keys.items():
        if len(keys) > 1:
            # Sort keys to ensure deterministic output
            keys.sort()
            collisions.append({'value': value, 'keys': keys})

    # Sort collisions by the keys list for deterministic output
    collisions.sort(key=lambda x: x['keys'])

    return collisions
