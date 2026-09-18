import os
from ingest.ingest_routerinfo import ingest_netdb
from config import JAVA_NETDB_PATH as NETDB_PATH

def main():
    print("[INFO] Starting netDb ingestion…")
    print(f"[INFO] Using netDb path: {NETDB_PATH}")

    if not os.path.isdir(NETDB_PATH):
        print(f"[ERROR] netDb directory not found: {NETDB_PATH}")
        return

    ingest_netdb(NETDB_PATH)

    print("[INFO] Ingestion complete.")

if __name__ == "__main__":
    main()

