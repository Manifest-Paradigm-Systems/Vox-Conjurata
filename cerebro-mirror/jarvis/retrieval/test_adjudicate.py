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

def test_no_collisions():
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
    
    # Insert rows with unique values
    cursor.execute("INSERT INTO records_facts VALUES ('key1', 'value1', 1)")
    cursor.execute("INSERT INTO records_facts VALUES ('key2', 'value2', 1)")
    cursor.execute("INSERT INTO records_facts VALUES ('key3', 'value3', 1)")
    
    # Insert retired rows (should be ignored)
    cursor.execute("INSERT INTO records_facts VALUES ('key4', 'value4', 0)")
    cursor.execute("INSERT INTO records_facts VALUES ('key5', 'value5', 0)")
    
    conn.commit()
    
    # Find collisions
    collisions = find_collisions(conn)
    
    # Assert that no collisions are found
    assert len(collisions) == 0
    
    conn.close()

def test_cli_no_values_printed():
    import subprocess
    import sys
    
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
    
    # Insert a collision with a sentinel value
    cursor.execute("INSERT INTO records_facts VALUES ('key1', 'SENTINEL_VALUE', 1)")
    cursor.execute("INSERT INTO records_facts VALUES ('key2', 'SENTINEL_VALUE', 1)")
    
    conn.commit()
    
    # Save database to a temporary file
    import tempfile
    import os
    
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
        tmp_path = tmp.name
    
    # Copy the in-memory database to a file
    with sqlite3.connect(tmp_path) as tmp_conn:
        conn.backup(tmp_conn)
    
    # Run adjudicate.py with the database
    result = subprocess.run([
        sys.executable, 'adjudicate.py', tmp_path
    ], capture_output=True, text=True)
    
    # Assert that the sentinel value is not printed
    assert 'SENTINEL_VALUE' not in result.stdout
    
    # Assert that the key names are printed
    assert 'key1' in result.stdout
    assert 'key2' in result.stdout
    
    # Clean up
    os.unlink(tmp_path)
    conn.close()
