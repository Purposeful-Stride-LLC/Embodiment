"""Create the BOD store (WAL) and load pose_library from the examples. Never touches NOVA.db."""
import json, sqlite3, sys
from pathlib import Path
from config import load_config, install_dir

def connect_rw(db_path=None):
    cfg = load_config()
    p = Path(db_path or cfg["db_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con

def init_db(db_path=None, examples_path=None):
    cfg = load_config()
    con = connect_rw(db_path)
    con.executescript((install_dir() / "SCHEMA.sql").read_text())
    ex = Path(examples_path or cfg["examples_path"])
    n = 0
    if ex.exists():
        for rec in json.loads(ex.read_text()):
            con.execute("INSERT OR REPLACE INTO pose_library(pose_id,pose_name,body_profile,schema_version,record_json)"
                        " VALUES (?,?,?,?,?)", (rec["pose_id"], rec["pose_name"], rec["body_profile"],
                                                rec["schema_version"], json.dumps(rec, sort_keys=True)))
            n += 1
    con.commit()
    return con, n

if __name__ == "__main__":
    con, n = init_db(*(sys.argv[1:3]))
    print(f"BOD store ready (WAL={con.execute('PRAGMA journal_mode').fetchone()[0]}), pose_library={n}")
