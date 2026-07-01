#!/usr/bin/env python3
"""
Scan the active Foundry VTT scene and create voice seeds for all actors.

This script:
1. Reads the Foundry v14 LevelDB to find the active scene and its tokens
2. For each token, finds the linked actor (from actors LevelDB or embedded delta)
3. Calls the vox-conjurata orchestrator's /api/ingest-actor endpoint to create voice seeds
"""

import json
import os
import sys
import shutil
import tempfile
import subprocess
import httpx
import time
import re

# Paths
FOUNDRY_WORLD_DIR = "/home/EvokeStudio/foundry/data/Data/worlds/pathfinder"
FOUNDRY_ACTORS_DB = os.path.join(FOUNDRY_WORLD_DIR, "data/actors")
FOUNDRY_SCENES_DB = os.path.join(FOUNDRY_WORLD_DIR, "data/scenes")
FOUNDRY_APP_NODE = "/home/EvokeStudio/foundry/app"
ORCHESTRATOR_URL = "http://localhost:8080"

# Node.js script to read LevelDB (we call it as a subprocess)
NODE_READ_SCRIPT = """
const { ClassicLevel } = require('classic-level');
const fs = require('fs');
const path = require('path');

async function main() {
    const action = process.argv[2];

    if (action === 'active-scene') {
        await readActiveScene();
    } else if (action === 'actors') {
        await readActors(process.argv[3]);
    } else if (action === 'scene-tokens') {
        await readSceneTokens(process.argv[3]);
    }
}

async function readActiveScene() {
    const tmpDir = '/tmp/foundry_scan_scenes_' + Date.now();
    const dbDir = process.argv[3] || '{scenes_db}';
    copyDb(dbDir, tmpDir);

    const db = new ClassicLevel(tmpDir, { valueEncoding: 'utf8', keyEncoding: 'utf8' });

    try {
        for await (const [key, value] of db.iterator()) {
            if (key.startsWith('!scenes!')) {
                const parsed = JSON.parse(value);
                if (parsed.active === true && parsed.name) {
                    // Get token IDs from the scene's tokens array
                    const result = {{
                        id: parsed._id,
                        name: parsed.name,
                        tokenIds: parsed.tokens || []
                    }};
                    console.log(JSON.stringify(result));
                    await db.close();
                    cleanup(tmpDir);
                    return;
                }
            }
        }
        console.log(JSON.stringify({{ error: 'No active scene found' }}));
    } catch(e) {{
        console.log(JSON.stringify({{ error: e.message }}));
    }}
    await db.close();
    cleanup(tmpDir);
}

async function readSceneTokens(sceneId) {{
    const tmpDir = '/tmp/foundry_scan_tokens_' + Date.now();
    const dbDir = process.argv[4] || '{scenes_db}';
    copyDb(dbDir, tmpDir);

    const db = new ClassicLevel(tmpDir, {{ valueEncoding: 'utf8', keyEncoding: 'utf8' }});
    const formatKey = (...parts) => parts.join('.');
    const tokenSublevel = db.sublevel(formatKey('scenes', 'tokens'));

    const tokens = [];
    const prefix = sceneId + '.';

    try {{
        for await (const [key, value] of tokenSublevel.iterator({{
            gte: prefix,
            lte: prefix + '~'
        }})) {{
            const parsed = JSON.parse(value);
            tokens.push({{
                id: parsed._id,
                name: parsed.name || 'Unknown',
                actorId: parsed.actorId || null,
                actorLink: parsed.actorLink || false,
                hasDelta: parsed.delta !== null && parsed.delta !== undefined,
                texture: parsed.texture?.src || null,
                x: parsed.x,
                y: parsed.y
            }});
        }}
    }} catch(e) {{
        console.log(JSON.stringify({{ error: e.message, tokens: tokens }}));
        await db.close();
        cleanup(tmpDir);
        return;
    }}

    const result = {{ tokens: tokens }};
    console.log(JSON.stringify(result));
    await db.close();
    cleanup(tmpDir);
}

async function readActors(actorIdsStr) {{
    const actorIds = JSON.parse(actorIdsStr);
    const tmpDir = '/tmp/foundry_scan_actors_' + Date.now();
    const dbDir = process.argv[4] || '{actors_db}';
    copyDb(dbDir, tmpDir);

    const db = new ClassicLevel(tmpDir, {{ valueEncoding: 'utf8', keyEncoding: 'utf8' }});
    const actors = [];

    try {{
        for await (const [key, value] of db.iterator()) {{
            if (key.startsWith('!actors!')) {{
                const parsed = JSON.parse(value);
                if (actorIds.includes(parsed._id)) {{
                    actors.push({{
                        id: parsed._id,
                        name: parsed.name || 'Unknown',
                        type: parsed.type || 'npc',
                        img: parsed.img || '',
                        system: parsed.system || {{}},
                        prototypeToken: parsed.prototypeToken || {{}},
                        flags: parsed.flags || {{}}
                    }});
                }}
            }}
        }}
    }} catch(e) {{
        console.log(JSON.stringify({{ error: e.message, actors: actors }}));
        await db.close();
        cleanup(tmpDir);
        return;
    }}

    console.log(JSON.stringify({{ actors: actors }}));
    await db.close();
    cleanup(tmpDir);
}

function copyDb(src, dst) {{
    fs.rmSync(dst, {{ recursive: true, force: true }});
    fs.mkdirSync(dst, {{ recursive: true }});
    for (const f of fs.readdirSync(src)) {{
        fs.copyFileSync(path.join(src, f), path.join(dst, f));
    }}
}}

function cleanup(dir) {{
    try {{ fs.rmSync(dir, {{ recursive: true, force: true }}); }} catch(e) {{}}
}}

main().catch(e => {{
    console.log(JSON.stringify({{ error: e.message }}));
    process.exit(1);
}});
"""

# Build the Node.js script with correct paths
def build_node_script():
    script = NODE_READ_SCRIPT.replace(
        "{scenes_db}", FOUNDRY_SCENES_DB
    ).replace(
        "{actors_db}", FOUNDRY_ACTORS_DB
    )
    return script

NODE_BIN = "/home/linuxbrew/.linuxbrew/bin/node"

def run_node(script_content, *args):
    """Execute a Node.js script with arguments and return parsed JSON."""
    # Write script to temp file
    fd, script_path = tempfile.mkstemp(suffix='.js')
    with os.fdopen(fd, 'w') as f:
        f.write(script_content)

    try:
        result = subprocess.run(
            [NODE_BIN, script_path, *args],
            capture_output=True,
            text=True,
            timeout=30,
            env={"NODE_PATH": f"{FOUNDRY_APP_NODE}/node_modules"}
        )

        if result.returncode != 0:
            print(f"Node.js error (stderr): {result.stderr[:500]}")
            return None

        # Parse the last line of stdout (which should be our JSON output)
        lines = result.stdout.strip().split('\n')
        for line in reversed(lines):
            line = line.strip()
            if line.startswith('{') or line.startswith('['):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue

        print(f"Could not find JSON in output: {result.stdout[:500]}")
        return None
    except subprocess.TimeoutExpired:
        print("Node.js subprocess timed out")
        return None
    except Exception as e:
        print(f"Error running Node.js: {e}")
        return None
    finally:
        try:
            os.unlink(script_path)
        except:
            pass

def get_actor_lore(actor_data):
    """Extract lore/description from actor data."""
    system = actor_data.get('system', {})

    # Try various PF2e lore field locations
    lore = (
        system.get('details', {}).get('biography', {}).get('value', '') or
        system.get('details', {}).get('publicBiography', '') or
        system.get('description', {}).get('value', '') or
        system.get('details', {}).get('description', '') or
        ''
    )

    return lore

def get_actor_stats(actor_data):
    """Extract stats from actor data."""
    system = actor_data.get('system', {})
    details = system.get('details', {})

    stats = {
        "race": details.get('race', {}).get('name', '') or details.get('race', '') or system.get('race', ''),
        "gender": details.get('gender', '') or details.get('sex', '') or '',
        "level": details.get('level', {}).get('value', 0) or details.get('level', 0) or 0,
    }

    return stats

def is_monster_actor(actor_data):
    """Determine if an actor is a monster."""
    actor_type = actor_data.get('type', '')
    if actor_type == 'character':
        return False

    # Check if it's an NPC with non-humanoid type
    system = actor_data.get('system', {})
    details = system.get('details', {})
    creature_type = details.get('type', {})
    if isinstance(creature_type, dict):
        type_value = creature_type.get('value', '')
    else:
        type_value = str(creature_type)

    return actor_type == 'npc' and type_value.lower() != 'humanoid'

def get_actor_image(actor_data):
    """Get the actor's image/art path."""
    return actor_data.get('img', '') or actor_data.get('prototypeToken', {}).get('texture', {}).get('src', '')

def ingest_actor(actor_id, name, lore, stats, is_monster, art_path):
    """Call the orchestrator's ingest-actor endpoint."""
    payload = {
        "actorId": actor_id,
        "name": name,
        "lore": lore or "No bio available.",
        "stats": stats,
        "artPath": art_path or "icons/svg/mystery-man.svg",
        "isMonster": is_monster,
        "userId": "gm"
    }

    try:
        # Use the orchestrator directly (not through Caddy, since we're on the host)
        resp = httpx.post(
            f"{ORCHESTRATOR_URL}/api/ingest-actor",
            json=payload,
            timeout=120.0
        )
        if resp.status_code == 200:
            result = resp.json()
            status = result.get('status', 'unknown')
            if status == 'cached':
                return 'cached'
            elif status == 'created':
                return 'created'
            elif status == 'error':
                return f'error: {result}'
            return f'unknown: {result}'
        else:
            return f'http_{resp.status_code}'
    except httpx.TimeoutException:
        return 'timeout'
    except Exception as e:
        return f'exception: {e}'

def main():
    print("=" * 60)
    print("Vox-Conjurata Scene Voice Scanner")
    print("=" * 60)

    node_script = build_node_script()

    # Step 1: Find the active scene
    print("\n[1/4] Finding active scene...")
    scene_data = run_node(node_script, 'active-scene', FOUNDRY_SCENES_DB)

    if not scene_data:
        print("ERROR: Could not read Foundry scene data")
        sys.exit(1)

    if 'error' in scene_data:
        print(f"ERROR: {scene_data['error']}")
        sys.exit(1)

    scene_id = scene_data['id']
    scene_name = scene_data['name']
    print(f"  Active scene: '{scene_name}' (ID: {scene_id})")

    if not scene_data.get('tokenIds') or len(scene_data['tokenIds']) == 0:
        print("  No tokens found in active scene.")
        sys.exit(0)

    print(f"  Token count: {len(scene_data['tokenIds'])}")

    # Step 2: Get full token data
    print("\n[2/4] Reading token data...")
    token_result = run_node(node_script, 'scene-tokens', scene_id, FOUNDRY_SCENES_DB)

    if not token_result or 'error' in token_result:
        print(f"ERROR: Could not read token data: {token_result}")
        sys.exit(1)

    tokens = token_result['tokens']
    print(f"  Found {len(tokens)} tokens with actor data")

    # Separate linked actors and synthetic tokens
    linked_actor_ids = []
    synthetic_tokens = []

    for t in tokens:
        if t.get('actorLink') and t.get('actorId'):
            linked_actor_ids.append(t['actorId'])
        elif not t.get('actorLink') and t.get('hasDelta'):
            synthetic_tokens.append(t)

    print(f"  Linked actors: {len(linked_actor_ids)}")
    print(f"  Synthetic tokens (embedded): {len(synthetic_tokens)}")

    # Step 3: Read actor data from actors LevelDB
    actors_map = {}

    if linked_actor_ids:
        print("\n[3/4] Reading actor data from database...")
        actor_ids_json = json.dumps(linked_actor_ids)
        actor_result = run_node(node_script, 'actors', actor_ids_json, FOUNDRY_ACTORS_DB)

        if actor_result and 'actors' in actor_result:
            for a in actor_result['actors']:
                actors_map[a['id']] = a
            print(f"  Found {len(actor_result['actors'])} actors in database")
        else:
            print(f"  WARNING: Could not read actors: {actor_result}")

    # Build the list of actors to ingest
    actors_to_ingest = []

    for t in tokens:
        actor_id = t.get('actorId')
        token_name = t.get('name', 'Unknown')

        if not actor_id:
            print(f"  SKIP: Token '{token_name}' has no actor ID")
            continue

        if actor_id in actors_map:
            # Linked actor
            a = actors_map[actor_id]
            actors_to_ingest.append({
                "actorId": actor_id,
                "name": a.get('name', token_name),
                "lore": get_actor_lore(a),
                "stats": get_actor_stats(a),
                "isMonster": is_monster_actor(a),
                "artPath": get_actor_image(a)
            })
        elif t.get('hasDelta'):
            # Synthetic token with embedded delta (try to extract basic info)
            actors_to_ingest.append({
                "actorId": actor_id,
                "name": token_name,
                "lore": f"A {token_name} in the scene '{scene_name}'.",
                "stats": {"race": "", "gender": "", "level": 0},
                "isMonster": True,
                "artPath": t.get('texture', '') or "icons/svg/mystery-man.svg"
            })
        else:
            # Token with actorId but not found in actors DB (might be a compendium actor)
            actors_to_ingest.append({
                "actorId": actor_id,
                "name": token_name,
                "lore": f"A character in the scene '{scene_name}'.",
                "stats": {"race": "", "gender": "", "level": 0},
                "isMonster": False,
                "artPath": t.get('texture', '') or "icons/svg/mystery-man.svg"
            })

    print(f"\n  Total actors to process: {len(actors_to_ingest)}")

    # Step 4: Ingest each actor into the orchestrator
    print("\n[4/4] Creating voice seeds via orchestrator...")
    print()

    results = {"created": [], "cached": [], "skipped": [], "failed": []}

    for i, actor in enumerate(actors_to_ingest, 1):
        name = actor['name']
        print(f"  [{i}/{len(actors_to_ingest)}] {name}...", end=' ', flush=True)

        result = ingest_actor(
            actor['actorId'],
            actor['name'],
            actor['lore'],
            actor['stats'],
            actor['isMonster'],
            actor['artPath']
        )

        if result == 'created':
            print("✅ Voice seed created")
            results['created'].append(name)
        elif result == 'cached':
            print("⏭️  Already cached")
            results['cached'].append(name)
        else:
            print(f"❌ {result}")
            results['failed'].append((name, result))

        # Small delay between ingest calls to not overwhelm the orchestrator
        if i < len(actors_to_ingest):
            time.sleep(0.5)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  ✅ Created:  {len(results['created'])}")
    print(f"  ⏭️  Cached:   {len(results['cached'])}")
    print(f"  ❌ Failed:   {len(results['failed'])}")
    if results['failed']:
        for name, reason in results['failed']:
            print(f"     - {name}: {reason}")

    print("\nDone!")

if __name__ == "__main__":
    main()
