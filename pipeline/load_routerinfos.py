import sys
import os

# ------------------------------------------------------------
# Ensure project root is on sys.path
# ------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ------------------------------------------------------------
# Imports (now guaranteed to work)
# ------------------------------------------------------------
from parse.routerinfo_parser import RouterInfoParser
from parse.routerinfo_fallback import fallback_parse
from database.db import insert_routerinfo, init_db

import time

# ------------------------------------------------------------
# Path to your local I2P netDb
# ------------------------------------------------------------
NETDB_PATH = os.path.expanduser("~/Library/Application Support/i2p/netDb")

# ------------------------------------------------------------
# Main loader function
# ------------------------------------------------------------
def load_all():
    print("Initializing database...")
    init_db()

    print(f"Scanning netDb at: {NETDB_PATH}")
    total = 0
    success = 0
    fallback_used = 0
    failed = 0

    for root, dirs, files in os.walk(NETDB_PATH):
        for f in files:
            if not f.startswith("routerInfo"):
                continue

            total += 1
            path = os.path.join(root, f)
            print(f"\nParsing: {path}")

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

    print("\n------------------------------------------------------------")
    print("Loading complete")
    print(f"Total routerInfos found: {total}")
    print(f"Successfully parsed:     {success}")
    print(f"Fallback used:           {fallback_used}")
    print(f"Failed:                  {failed}")
    print("------------------------------------------------------------")

# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------
if __name__ == "__main__":
    load_all()

