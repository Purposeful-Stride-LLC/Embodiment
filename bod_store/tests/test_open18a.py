"""OPEN-18(a): NOVA reads only the operator snapshot, every open O_RDONLY (mode=ro&immutable=1),
never the live db/-wal/-shm, even with a writer holding an open transaction on a live WAL.
Open tracing: tests/trace/opentrace.c is compiled to an LD_PRELOAD shim that logs every
open/open64/openat/openat64/creat/fopen (SQLite's own opens included) with flags; the NOVA entry
points run in a subprocess under it. (strace is not installed on the box and cannot be installed
without root.) Fail-closed: missing / unreadable (chmod 000) / corrupt snapshot."""
import hashlib, json, os, shutil, sqlite3, subprocess, sys
from pathlib import Path
import pytest

HOME = Path(__file__).resolve().parents[1]
PY = sys.executable
GOOD_GOAL = {"pose_id": "BP1-ddcab2230df85dcc", "requested_by": "nova"}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

CHILD = r'''
import json, sys
sys.addaudithook(lambda ev, a: ev == "open" and a[1] and any(c in str(a[1]) for c in "wax+")
                 and "bod_store" in str(a[0]) and print("PYWRITE", a[0], file=sys.stderr))
import rag_adapter, triggers
goal = json.loads(sys.argv[1])
out = {"status": rag_adapter.body_status(), "block": rag_adapter.body_status_block(),
       "json": json.loads(rag_adapter.body_status_json()), "detect": triggers.detect(),
       "detect_goal": triggers.detect(goal=goal), "detect_bad": triggers.detect(goal={"pose_id": "WP1-bad"})}
print(json.dumps(out, default=str))
'''


@pytest.fixture(scope="module")
def shim(tmp_path_factory):
    if not shutil.which("gcc"):
        pytest.fail("gcc needed to build the open tracer")
    so = tmp_path_factory.mktemp("ot") / "opentrace.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-O2", "-o", str(so), str(HOME / "tests/trace/opentrace.c"), "-ldl"],
                   check=True)
    return so


def _hash(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else None


@pytest.fixture
def live(tmp_path):
    """bod_store copy; writer holds an open write txn on a live WAL; snapshot written by the writer."""
    home = tmp_path / "bod_store"
    shutil.copytree(HOME, home, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "*.bak",
                                                              "tests", "nova_snapshot.db*"))
    shutil.copy(HOME.parent / "bod_pose_state.examples.json", tmp_path)
    db = home / "data/bod_state.db"
    subprocess.run([PY, "init_db.py"], cwd=home, check=True, capture_output=True,
                   env={k: v for k, v in {**os.environ, "BOD_HOME": str(home)}.items()
                        if k not in ("BOD_DB_PATH", "BOD_SNAPSHOT_PATH", "BOD_CONFIG")})  # fresh store, no checked-in db
    for suf in ("-wal", "-shm"):
        Path(str(db) + suf).unlink(missing_ok=True)
    env = {**os.environ, "BOD_HOME": str(home)}
    for k in ("BOD_DB_PATH", "BOD_SNAPSHOT_PATH", "BOD_CONFIG"):
        env.pop(k, None)
    os.environ["BOD_HOME"] = str(home)
    for k in ("BOD_DB_PATH", "BOD_SNAPSHOT_PATH"):
        os.environ.pop(k, None)
    try:
        sys.path.insert(0, str(home))
        from store import SimStore
        st = SimStore(db_path=str(db))   # writes the snapshot (config default ./data/nova_snapshot.db)
        import time
        st.update_from_sim(json.loads((tmp_path / "bod_pose_state.examples.json").read_text())[0],
                           now=time.time(), verified=True)   # seed current_state in the fresh store
    finally:
        sys.path.remove(str(home))
    snap = home / "data/nova_snapshot.db"
    assert snap.exists() and snap.read_bytes()[18:20] == b"\x01\x01"   # non-WAL snapshot
    w = sqlite3.connect(db, isolation_level=None)
    w.execute("PRAGMA wal_autocheckpoint=0")
    w.execute("INSERT OR REPLACE INTO meta VALUES ('wal_probe', '1')")  # committed frame in -wal
    w.execute("BEGIN IMMEDIATE")
    w.execute("UPDATE current_state SET detail='uncommitted' WHERE 1")  # open write transaction
    assert Path(str(db) + "-wal").stat().st_size > 0 and Path(str(db) + "-shm").exists()
    yield {"home": home, "db": db, "snap": snap, "env": env, "writer": w, "st": st}
    w.execute("ROLLBACK"); w.close(); st.con.close()
    os.environ.pop("BOD_HOME", None)


def _run(live, shim, mutate_snap=None):
    home, db = live["home"], live["db"]
    if mutate_snap:
        mutate_snap(live["snap"])
    files = [db, Path(str(db) + "-wal"), Path(str(db) + "-shm")]
    before = {str(p): _hash(p) for p in files}
    ls_before = sorted(os.listdir(home / "data")), sorted(os.listdir(home))
    log = home.parent / "opens.log"
    env = {**live["env"], "LD_PRELOAD": str(shim), "PYTHONDONTWRITEBYTECODE": "1", "OPENTRACE_LOG": str(log), "OPENTRACE_MATCH": str(home)}
    r = subprocess.run([PY, "-c", CHILD, json.dumps(GOOD_GOAL)], cwd=home, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "PYWRITE" not in r.stderr
    opens = [ln.split("\t") for ln in log.read_text().splitlines()] if log.exists() else []
    data_opens = [(int(f), p) for _, f, p in opens if "/data/" in p]
    assert all(f & WRITE_FLAGS == 0 for f, _ in [(int(f), p) for _, f, p in opens]), opens   # no write opens at all
    live_names = {str(p) for p in files}
    assert not [p for _, p in data_opens if p in live_names], data_opens   # live db/-wal/-shm never opened
    assert {str(p): _hash(p) for p in files} == before
    assert (sorted(os.listdir(home / "data")), sorted(os.listdir(home))) == ls_before
    return json.loads(r.stdout), data_opens


def test_live_wal_writer_nova_reads_only_snapshot_ro(live, shim):
    out, data_opens = _run(live, shim)
    assert data_opens and {p for _, p in data_opens} == {str(live["snap"])}
    assert all(f & os.O_ACCMODE == os.O_RDONLY for f, _ in data_opens)
    assert out["status"]["snapshot_status"] == "ok" and out["status"]["pose_id"]
    assert "uncommitted" not in json.dumps(out)
    assert out["detect_bad"][-1]["safe_action"] == "REFUSE_MOVE"


def _chmod000(p):
    os.chmod(p, 0)


@pytest.mark.parametrize("name,mutate,status", [
    ("missing", lambda p: p.unlink(), "snapshot_missing"),
    ("unreadable", _chmod000, "snapshot_unreadable"),
    ("corrupt", lambda p: p.write_bytes(os.urandom(8192)), "snapshot_corrupt"),
])
def test_fail_closed(live, shim, name, mutate, status):
    if name == "unreadable" and os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    try:
        out, data_opens = _run(live, shim, mutate)
    finally:
        if live["snap"].exists():
            os.chmod(live["snap"], 0o644)
    s = out["status"]
    assert s["snapshot_status"] == status and s["stale"] and s["motion_allowed"] is False
    assert out["json"]["motion_allowed"] is False
    assert "NO SNAPSHOT" in out["block"] and status in out["block"] and "motion_allowed=false" in out["block"]
    for key in ("detect", "detect_goal", "detect_bad"):
        trig = out[key]
        assert any(t["trigger_event"] == "state_stale" and t["safe_action"] == "HOLD" for t in trig)
        assert not any(t["safe_action"] == "PASS_TO_VALIDATOR" for t in trig)
    for key in ("detect_goal", "detect_bad"):
        g = [t for t in out[key] if t["trigger_event"] == "goal_requested"][0]
        assert g["safe_action"] == "REFUSE_MOVE" and g["failed_checks"]
    assert [t for t in out["detect_goal"] if t["trigger_event"] == "goal_requested"][0]["failed_checks"] == [status]
    assert {p for _, p in data_opens} <= {str(live["snap"])}


def test_no_snapshot_configured_fails_closed(env, monkeypatch):
    import rag_adapter, triggers
    monkeypatch.setattr(rag_adapter, "snapshot_path", lambda cfg=None: None)
    s = rag_adapter.body_status()
    assert s["snapshot_status"] == "snapshot_missing" and s["stale"] and not s["motion_allowed"]
    g = triggers.detect(goal=GOOD_GOAL)
    assert g[-1]["safe_action"] == "REFUSE_MOVE"


def test_tracer_negative_control(live, shim):
    """The tracer does see SQLite's own opens: a plain mode=ro open of the live WAL db (the pre-fix
    behaviour) is logged opening -wal/-shm read-write."""
    log = live["home"].parent / "neg.log"
    code = f"import sqlite3; c=sqlite3.connect('file:{live['db']}?mode=ro', uri=True); c.execute('select 1 from meta').fetchall()"
    env = {**live["env"], "LD_PRELOAD": str(shim), "OPENTRACE_LOG": str(log), "OPENTRACE_MATCH": str(live["home"])}
    subprocess.run([PY, "-c", code], env=env, check=True)
    opens = [ln.split("\t") for ln in log.read_text().splitlines()]
    shm = [int(f) for _, f, p in opens if p.endswith("-shm")]
    assert shm and any(f & os.O_ACCMODE == os.O_RDWR for f in shm)


# ---- OPEN-18(a)-2: partial (single-page) snapshot corruption -----------------------------------
def _tables(snap):
    c = sqlite3.connect(f"{Path(snap).as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        ps = c.execute("PRAGMA page_size").fetchone()[0]
        t = c.execute("SELECT name, rootpage FROM sqlite_master WHERE type='table' AND rootpage>1 "
                      "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    finally:
        c.close()
    return ps, t


def _corrupt_page(snap, page, ps):
    b = bytearray(Path(snap).read_bytes())
    off = (page - 1) * ps
    b[off:off + ps] = bytes([0xA5]) * ps     # one page only; header page (1) untouched
    Path(snap).write_bytes(bytes(b))


def _assert_corrupt_fail_closed(out):
    s = out["status"]
    assert s["snapshot_status"] == "snapshot_corrupt" and s["stale"] and s["motion_allowed"] is False
    assert s["estop"] is None and out["json"]["estop"] is None
    assert "estop=UNKNOWN" in out["block"] and "snapshot_corrupt" in out["block"]
    for key in ("detect", "detect_goal", "detect_bad"):
        assert any(t["trigger_event"] == "state_stale" and t["safe_action"] == "HOLD" for t in out[key])
        assert not any(t["safe_action"] == "PASS_TO_VALIDATOR" for t in out[key])
    g = [t for t in out["detect_goal"] if t["trigger_event"] == "goal_requested"][0]
    assert g["safe_action"] == "REFUSE_MOVE" and "snapshot_corrupt" in g["failed_checks"]


def test_pose_library_page_corrupt_fails_closed(live, shim):
    ps, tabs = _tables(live["snap"])
    root = dict(tabs)["pose_library"]
    out, data_opens = _run(live, shim, lambda p: _corrupt_page(p, root, ps))
    _assert_corrupt_fail_closed(out)
    assert "quick_check" in out["status"]["snapshot_error"]   # caught at open, not by the catch path
    assert {p for _, p in data_opens} <= {str(live["snap"])}
    assert all(f & os.O_ACCMODE == os.O_RDONLY for f, _ in data_opens)


def test_every_table_root_page_corrupt_fails_closed(live, shim):
    ps, tabs = _tables(live["snap"])
    assert len(tabs) >= 3 and "pose_library" in dict(tabs)
    good = live["snap"].read_bytes()
    for name, root in tabs:
        live["snap"].write_bytes(good)
        out, data_opens = _run(live, shim, lambda p: _corrupt_page(p, root, ps))
        try:
            _assert_corrupt_fail_closed(out)
            assert "quick_check" in out["status"]["snapshot_error"]
        except AssertionError as e:
            raise AssertionError(f"table {name} (root {root}): {e}")
        assert {p for _, p in data_opens} <= {str(live["snap"])}


def test_quick_check_ok_snapshot_estop_bool(live, shim):
    out, _ = _run(live, shim)
    assert out["status"]["snapshot_status"] == "ok" and out["status"]["estop"] in (True, False)
    assert "estop=UNKNOWN" not in out["block"]


class _BoomCon:
    def __init__(self, real): self.real = real
    def execute(self, sql, *a):
        if "pose_library" in sql:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return self.real.execute(sql, *a)
    def close(self): self.real.close()


def test_mid_read_database_error_in_goal_path_fails_closed(env, monkeypatch):
    """Catch path independent of quick_check: snapshot passes, then the goal lookup raises."""
    import rag_adapter, triggers
    assert rag_adapter.body_status()["snapshot_status"] == "ok"
    real = rag_adapter.connect_ro
    monkeypatch.setattr(triggers, "connect_ro", lambda db_path=None: _BoomCon(real(db_path)))
    out = triggers.detect(goal=GOOD_GOAL)
    g = [t for t in out if t["trigger_event"] == "goal_requested"][0]
    assert g["safe_action"] == "REFUSE_MOVE" and g["failed_checks"] == ["snapshot_corrupt"]


@pytest.mark.parametrize("exc", [sqlite3.DatabaseError("database disk image is malformed"),
                                 sqlite3.OperationalError("disk I/O error")])
def test_mid_read_error_in_status_path_fails_closed(env, monkeypatch, exc):
    import rag_adapter, triggers
    real = rag_adapter.snapshot
    def boom(db_path=None):
        con, t = real(db_path)
        class C(_BoomCon):
            def execute(self, sql, *a):
                if "current_state" in sql: raise exc
                return self.real.execute(sql, *a)
        return C(con), t
    monkeypatch.setattr(rag_adapter, "snapshot", boom)
    s = rag_adapter.body_status()
    assert s["snapshot_status"] == "snapshot_corrupt" and s["estop"] is None and not s["motion_allowed"]
    assert "estop=UNKNOWN" in rag_adapter.body_status_block()
    out = triggers.detect(goal=GOOD_GOAL)
    assert any(t["trigger_event"] == "state_stale" and t["safe_action"] == "HOLD" for t in out)
    g = [t for t in out if t["trigger_event"] == "goal_requested"][0]
    assert g["safe_action"] == "REFUSE_MOVE" and "snapshot_corrupt" in g["failed_checks"]


def test_detect_never_raises_on_db_error(env, monkeypatch):
    import triggers
    def boom(*a, **k): raise sqlite3.DatabaseError("database disk image is malformed")
    monkeypatch.setattr(triggers, "body_status", boom)
    out = triggers.detect(goal=GOOD_GOAL)
    assert out[0]["safe_action"] == "HOLD" and out[-1]["safe_action"] == "REFUSE_MOVE"
    assert out[-1]["failed_checks"] == ["snapshot_corrupt"]


def test_multiline_quick_check_block_fields_are_one_line(env, monkeypatch):
    """PRAGMA quick_check multi-row / multi-line messages must not split status-block fields."""
    import rag_adapter
    real_connect = rag_adapter.sqlite3.connect

    class _QC:
        def fetchall(self):
            return [
                ("*** in database main ***\nTree 2 page 5 cell 0: freelist cycle",),
                ("Page 7: freelist leaf count wrong\r\non overflow page",),
                ("wrong page type in header\tdetail",),
            ]

    class _Wrap:
        def __init__(self, real):
            self._r = real
        def execute(self, sql, *a):
            if "quick_check" in sql.lower():
                return _QC()
            return self._r.execute(sql, *a)
        def backup(self, dest):
            return self._r.backup(dest)
        def close(self):
            self._r.close()

    def connect(*a, **k):
        c = real_connect(*a, **k)
        if a and isinstance(a[0], str) and "mode=ro" in a[0] and "immutable" in a[0]:
            return _Wrap(c)
        return c

    monkeypatch.setattr(rag_adapter.sqlite3, "connect", connect)
    real_load = rag_adapter.load_config

    def load():
        c = dict(real_load())
        c["calibration_measured_error"] = "duplicate key 'x'\nacross\tlines\r\n(measured.json)"
        return c

    monkeypatch.setattr(rag_adapter, "load_config", load)
    block = rag_adapter.body_status_block(now=1000)
    lines = block.split("\n")
    assert all("\r" not in ln and "\t" not in ln for ln in lines)
    no_snap = [ln for ln in lines if ln.startswith("NO SNAPSHOT")]
    assert len(no_snap) == 1, lines
    assert "quick_check" in no_snap[0] and "snapshot_corrupt" in no_snap[0]
    assert "freelist cycle" in no_snap[0] and "Tree 2 page 5" in no_snap[0]
    assert "overflow page" in no_snap[0] and "wrong page type" in no_snap[0]
    rej = [ln for ln in lines if "REJECTED" in ln]
    assert len(rej) == 1 and "duplicate key" in rej[0] and "measured.json" in rej[0]
    # Every block line parses as one field (header / NO SNAPSHOT / calibration: …)
    for ln in lines:
        assert ln == ln.strip()
