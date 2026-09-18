import duckdb
import os

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(BASE_DIR, "data", "i2p.duckdb")

def get_conn(read_only=True):
    return duckdb.connect(DB_PATH, read_only=read_only)

