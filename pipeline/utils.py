import duckdb

DB_PATH = "data/i2p.duckdb"

def load_df(query: str):
    con = duckdb.connect(DB_PATH, read_only=True)
    return con.execute(query).fetchdf()

def execute(query: str):
    con = duckdb.connect(DB_PATH)
    con.execute(query)

