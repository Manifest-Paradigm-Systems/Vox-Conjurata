import sqlite3
from collections import defaultdict
from typing import List, Dict, Any
import json
import sys
import click


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
    # Query only current facts
    cursor = conn.execute(
        "SELECT key_name, value FROM records_facts WHERE is_current = 1"
    )
    rows = cursor.fetchall()

    # Group by value
    value_to_keys = defaultdict(list)
    for key, value in rows:
        value_to_keys[value].append(key)

    # Filter groups with more than one distinct key
    collisions = []
    for value, keys in value_to_keys.items():
        # Remove duplicates and sort keys to ensure deterministic output
        distinct_keys = sorted(list(set(keys)))
        if len(distinct_keys) > 1:
            collisions.append({'value': value, 'keys': distinct_keys})

    # Sort collisions by the keys list for deterministic output
    collisions.sort(key=lambda x: x['keys'])

    return collisions


@click.command()
@click.argument('db_path', type=click.Path(exists=True))
@click.option('--json', 'json_output', is_flag=True, help='Output as JSON')
def main(db_path: str, json_output: bool):
    conn = sqlite3.connect(db_path)
    try:
        collisions = find_collisions(conn)
        if json_output:
            # Redact values from output
            redacted = [{'keys': c['keys']} for c in collisions]
            print(json.dumps(redacted))
        else:
            for collision in collisions:
                keys_str = ', '.join(collision['keys'])
                print(f"{keys_str} ({len(collision['keys'])} keys)")
    finally:
        conn.close()


if __name__ == '__main__':
    main()