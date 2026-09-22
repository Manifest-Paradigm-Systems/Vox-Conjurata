import sqlite3
import pytest
from adjudicate import find_collisions

def test_planted_collision():
    # Create an in-memory SQLite database
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    
    # Create the required table named records_facts
    cursor.execute('''
        CREATE TABLE records_facts (
            key_name TEXT,
            value TEXT,
            is_current INTEGER
        )
    ''')
    
    # Insert the planted collision: two keys with the same value
    cursor.execute("INSERT INTO records_facts VALUES ('key1', 'shared_value', 1)")
    cursor.execute("INSERT INTO records_facts VALUES ('key2', 'shared_value', 1)")
    
    # Insert decoy rows that should NOT be reported
    # Two keys with different values
    cursor.execute("INSERT INTO records_facts VALUES ('key3', 'different_value1', 1)")
    cursor.execute("INSERT INTO records_facts VALUES ('key4', 'different_value2', 1)")
    
    # Same key twice with same value (not a collision)
    cursor.execute("INSERT INTO records_facts VALUES ('key5', 'same_value', 1)")
    cursor.execute("INSERT INTO records_facts VALUES ('key5', 'same_value', 1)")
    
    # Retired row with is_current = 0 whose value also appears under a current key
    cursor.execute("INSERT INTO records_facts VALUES ('key6', 'shared_value', 0)")
    
    conn.commit()
    
    # Find collisions
    collisions = find_collisions(conn)
    
    # Assert that exactly one collision is found
    assert len(collisions) == 1
    
    # Assert the collision details
    collision = collisions[0]
    assert collision['value'] == 'shared_value'
    assert 'key1' in collision['keys']
    assert 'key2' in collision['keys']
    
    # Ensure decoy rows are not included
    for collision in collisions:
        for key in collision['keys']:
            assert key not in ['key3', 'key4', 'key5', 'key6']
    
    conn.close()