CREATE TABLE IF NOT EXISTS routerinfo (
    router_hash TEXT PRIMARY KEY,
    version TEXT,
    caps TEXT,
    is_floodfill INTEGER,
    bandwidth TEXT,
    ntcp2 TEXT,
    ssu2 TEXT,
    ipv6 TEXT,
    published INTEGER,
    country TEXT,
    asn INTEGER,
    org TEXT,
    latitude REAL,
    longitude REAL,
    last_seen INTEGER
);

