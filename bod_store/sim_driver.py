"""SIM ONLY. Replays the 8 example records, then a power cut, a rehome, and a fault.
Prints body_status_block and triggers at each step. Uses a fake clock."""
import json, os, sys, tempfile
from pathlib import Path
from config import load_config
from init_db import init_db
from store import SimStore
from rag_adapter import body_status_block
from triggers import detect

def main(db_path=None):
    cfg = load_config()
    db_path = db_path or cfg["db_path"]
    init_db(db_path)
    t = [1_000_000.0]
    st = SimStore(db_path, clock=lambda: t[0])
    recs = json.loads(Path(cfg["examples_path"]).read_text())
    def show(label, goal=None):
        print(f"\n=== {label} ===")
        print(body_status_block(db_path=db_path, now=t[0]))
        print("triggers:", [(x["trigger_event"], x["safe_action"]) for x in detect(db_path, now=t[0], goal=goal)])
    for i, r in enumerate(recs, 1):
        t[0] += 1; st.update_from_sim(r); show(f"E{i} replay {r['pose_name']}")
    st.update_from_sim(recs[0]); t[0] += 1
    st.mark_unknown(None, reason="power_cut"); show("power cut", goal={"pose_id": recs[1]["pose_id"]})
    t[0] += 1; st.rehome(recs[0]["pose_id"]); show("rehome to rest")
    t[0] += 1; st.raise_fault("rightArm.shoulder", "stall", "sim overcurrent"); show("fault rightArm.shoulder")
    t[0] += 30; show("30 s later, no update")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
