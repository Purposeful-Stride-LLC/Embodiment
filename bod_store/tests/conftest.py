import json, sys
from pathlib import Path
import pytest
HOME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HOME))

@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "data" / "bod_state.db"
    monkeypatch.setenv("BOD_DB_PATH", str(db))
    monkeypatch.setenv("BOD_SNAPSHOT_PATH", str(tmp_path / "data" / "nova_snapshot.db"))
    from config import load_config
    from init_db import init_db
    from store import SimStore
    init_db()
    t = [1000.0]
    st = SimStore(clock=lambda: t[0])
    recs = json.loads(Path(load_config()["examples_path"]).read_text())
    return {"db": str(db), "st": st, "t": t, "recs": recs}
