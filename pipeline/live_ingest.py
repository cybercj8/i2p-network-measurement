import sys
import os
import json
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from parse.routerinfo_parser import RouterInfoParser
from parse.routerinfo_fallback import fallback_parse
from database.db import insert_routerinfo, init_db

NETDB_PATH = os.path.expanduser("~/Library/Application Support/i2p/netDb")
STATE_FILE = os.path.join(PROJECT_ROOT, "pipeline", ".ingest_state.json")

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def ingest_incremental():
    print("[INGEST] Initializing database…")
    init_db()

    print(f"[INGEST] Scanning netDb at: {NETDB_PATH}")

    state = load_state()
    seen = state.get("files", {})

    total = 0
    new_files = 0
    updated_files = 0
    success = 0
    fallback_used = 0
    failed = 0

    for root, dirs, files in os.walk(NETDB_PATH):
        for f in files:
            if not f.startswith("routerInfo"):
                continue

            total += 1
            path = os.path.join(root, f)
            mtime = os.path.getmtime(path)

            if f not in seen:
                new_files += 1
                process = True
            elif seen[f] < mtime:
                updated_files += 1
                process = True
            else:
                process = False

            if not process:
                continue

            print(f"\n[INGEST] Processing: {path}")

            parser = RouterInfoParser(path)
            parsed = parser.parse()

            if parsed is None:
                print("  → Main parser failed, using fallback")
                fallback_used += 1
                parsed = fallback_parse(path)

            if parsed:
                router_hash = f.replace("routerInfo-", "").replace(".dat", "")
                parsed["router_hash"] = router_hash

                insert_routerinfo(parsed)
                success += 1
                print(f"  → Inserted: {router_hash}")
            else:
                failed += 1
                print("  → FAILED to parse")

            seen[f] = mtime

    state["files"] = seen
    save_state(state)

    print("\n------------------------------------------------------------")
    print("[INGEST] Incremental ingestion complete")
    print(f"Total routerInfos scanned: {total}")
    print(f"New files:                {new_files}")
    print(f"Updated files:            {updated_files}")
    print(f"Successfully parsed:      {success}")
    print(f"Fallback used:            {fallback_used}")
    print(f"Failed:                   {failed}")
    print("------------------------------------------------------------")

if __name__ == "__main__":
    ingest_incremental()

