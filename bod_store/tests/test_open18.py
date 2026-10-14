"""OPEN-18: zero-write NOVA reads (snapshot), both apply invocations, duplicate-key rejection."""
import hashlib, json, os, shutil, sqlite3, subprocess, sys, time
from pathlib import Path
import pytest

HOME = Path(__file__).resolve().parents[1]
PY = sys.executable


def _tree(d):
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(d).rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


@pytest.fixture
def copy_home(tmp_path, monkeypatch):
    """A clean copy of bod_store (sans .venv) with a WAL db whose -wal/-shm are absent."""
    dst = tmp_path / "bod_store"
    shutil.copytree(HOME, dst, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "*.bak", "tests"))
    ex = HOME.parent / "bod_pose_state.examples.json"
    if ex.exists():
        shutil.copy(ex, tmp_path / ex.name)
    subprocess.run([PY, "init_db.py"], cwd=dst, env={k: v for k, v in {**os.environ, "BOD_HOME": str(dst)}.items() if k not in ("BOD_DB_PATH", "BOD_SNAPSHOT_PATH", "BOD_CONFIG")}, check=True,
                   capture_output=True)  # build a fresh store; no checked-in db
    for suf in ("-wal", "-shm"):
        Path(str(dst / "data/bod_state.db") + suf).unlink(missing_ok=True)
    monkeypatch.setenv("BOD_HOME", str(dst))
    monkeypatch.delenv("BOD_DB_PATH", raising=False)
    return dst


def _read_entry_points():
    import rag_adapter, triggers
    rag_adapter.body_status()
    rag_adapter.body_status_block()
    rag_adapter.body_status_json()
    triggers.detect()
    triggers.detect(goal={"pose_id": "BP1-ddcab2230df85dcc", "requested_by": "nova"})
    triggers.detect(goal={"pose_id": "WP1-bad"})


def test_reads_zero_write_wal_absent(copy_home):
    db = copy_home / "data/bod_state.db"
    assert db.read_bytes()[18:20] == b"\x02\x02"  # WAL-mode header
    before_ls, before = sorted(os.listdir(copy_home / "data")), _tree(copy_home)
    _read_entry_points()
    assert sorted(os.listdir(copy_home / "data")) == before_ls
    assert not Path(str(db) + "-wal").exists() and not Path(str(db) + "-shm").exists()
    assert _tree(copy_home) == before


def test_reads_zero_write_configured_snapshot(copy_home, monkeypatch):
    from store import SimStore
    snap = copy_home / "data" / "nova_snapshot.db"
    st = SimStore(db_path=str(copy_home / "data/bod_state.db"))
    st.write_snapshot(snap); st.con.close()
    for suf in ("-wal", "-shm"):
        Path(str(copy_home / "data/bod_state.db") + suf).unlink(missing_ok=True)
    monkeypatch.setenv("BOD_SNAPSHOT_PATH", str(snap))
    before = _tree(copy_home)
    _read_entry_points()
    assert _tree(copy_home) == before


def test_old_snapshot_fresh_joints_is_stale(env, tmp_path, monkeypatch):
    import rag_adapter, triggers
    st, t = env["st"], env["t"]
    now = time.time()
    t[0] = now
    st.update_from_sim(env["recs"][0], now=now, verified=True)
    snap = tmp_path / "snap.db"
    st.write_snapshot(snap)
    os.utime(snap, (now - 3600, now - 3600))   # snapshot copied an hour ago
    monkeypatch.setenv("BOD_SNAPSHOT_PATH", str(snap))
    s = rag_adapter.body_status(now=now + 1)  # joints look 1 s old
    assert s["oldest_joint_verified_at"] == pytest.approx(now)
    assert s["snapshot_stale"] and s["stale"] and s["snapshot_age_s"] >= 3600
    assert "snapshot_age=" in rag_adapter.body_status_block(now=now + 1)
    trig = [x for x in triggers.detect(now=now + 1) if x["trigger_event"] == "state_stale"]
    assert trig and "snapshot age" in trig[0]["reason"]


@pytest.mark.parametrize("argv", [["calibration/apply_overrides.py"], ["-m", "calibration.apply_overrides"]])
def test_both_invocations(copy_home, argv):
    r = subprocess.run([PY, *argv], cwd=copy_home, capture_output=True, text=True,
                       env={**os.environ, "BOD_HOME": str(copy_home)})
    assert r.returncode == 0, r.stderr
    assert "logged" in r.stdout


def test_invocation_from_other_cwd(copy_home, tmp_path):
    r = subprocess.run([PY, str(copy_home / "calibration/apply_overrides.py")], cwd=tmp_path,
                       capture_output=True, text=True, env={**os.environ, "BOD_HOME": str(copy_home)})
    assert r.returncode == 0, r.stderr


DUP = '{"inmoov2arm": {"_note": "x", "rightArm.bicep": {"min": 10, "max": 80, "rest": 20},' \
      ' "rightArm.bicep": {"min": 0, "max": 90, "rest": 5}}}'


def test_duplicate_keys_reader_flags_no_write(copy_home):
    import rag_adapter
    (copy_home / "calibration/measured.json").write_text(DUP)
    before = _tree(copy_home)
    s = rag_adapter.body_status()
    assert s["measured_error"] and "duplicate key" in s["measured_error"]
    b = rag_adapter.body_status_block()
    assert "REJECTED" in b and b.count("measured.json") == 1
    assert _tree(copy_home) == before


def test_duplicate_keys_apply_refuses(copy_home):
    (copy_home / "calibration/measured.json").write_text(DUP)
    log = copy_home / "calibration/overrides.log"
    existed = log.read_bytes() if log.exists() else None
    for argv in (["calibration/apply_overrides.py"], ["-m", "calibration.apply_overrides"]):
        r = subprocess.run([PY, *argv], cwd=copy_home, capture_output=True, text=True,
                           env={**os.environ, "BOD_HOME": str(copy_home)})
        assert r.returncode != 0 and "duplicate key" in r.stderr
    assert (log.read_bytes() if log.exists() else None) == existed
    from calibration.loader import MeasuredError
    from calibration.apply_overrides import apply_overrides
    with pytest.raises(MeasuredError):
        apply_overrides()
