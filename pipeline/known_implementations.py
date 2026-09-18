"""
Ground-truth I2P implementation identities for the two vantage-point routers
this project actually controls.

This is deliberately NOT a general-purpose implementation classifier for
arbitrary third-party routers observed on the network. Research into
distinguishing Java I2P / i2pd / I2P+ from passively-collected RouterInfo
data alone did not turn up a validated, reliable signal -- the router.version
field is a shared I2NP protocol version, not a per-implementation identifier
(confirmed directly: this project's own i2pd 2.60.0 instance and Java I2P
2.12.0 instance both report identical "0.9.69"). Fabricating a heuristic
without validation would be worse than disclosing the limitation honestly.

What this DOES provide: for exactly the two routers we control, we know
their implementation with certainty (we run the software). Every other
router in enriched_router_data.implementation is intentionally left NULL --
that gap is real and should be reported as a limitation, not papered over.
"""

import duckdb

from pipeline.router_snapshot_writer import parse_routerinfo
from config import JAVA_ROUTER_INFO, I2PD_ROUTER_INFO

DB_PATH = "data/i2p.duckdb"

# (self-published router.info path, implementation label)
KNOWN_ROUTERS = [
    (JAVA_ROUTER_INFO, "java_i2p"),
    (I2PD_ROUTER_INFO, "i2pd"),
]


def run_known_implementations():
    con = duckdb.connect(DB_PATH)

    con.execute("""
        CREATE TABLE IF NOT EXISTS known_implementations (
            router_hash TEXT PRIMARY KEY,
            implementation TEXT,
            note TEXT
        )
    """)
    con.execute("DELETE FROM known_implementations")

    for path, implementation in KNOWN_ROUTERS:
        meta = parse_routerinfo(path)
        router_hash = meta.get("router_hash") if meta else None

        if not router_hash:
            print(f"[KNOWN IMPL] Could not parse identity from {path}, skipping")
            continue

        con.execute("""
            INSERT INTO known_implementations (router_hash, implementation, note)
            VALUES (?, ?, ?)
        """, [router_hash, implementation, "ground truth -- this project's own vantage point"])
        print(f"[KNOWN IMPL] {implementation}: {router_hash}")

    con.close()


if __name__ == "__main__":
    run_known_implementations()
