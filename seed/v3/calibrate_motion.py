# calibrate v3/motion_gate.py on the takes the 00:16 strict review flagged (F) vs the ones it did not (N)
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent)); import motion_gate as mg
OWN = Path(__file__).resolve().parent.parent
lst = dict(l.split() for l in (OWN / "qc/v3gate/list.txt").read_text().splitlines())
def take(i):
    p = OWN / "state" / f"{i}.json"
    if p.exists():
        s = json.loads(p.read_text()); return s["takes"][s["accepted"]]["final"]
    return lst[i]
F = "1-04n 2-04b 2-08bn 2-05c 4-02n 4-04n 1-01b 3-04b2 3-04e 5-02b 4-11 5-01 4-01 2-19n 1-08 5-03n".split()
N = "2-01 2-03c 2-03d 3-00 3-04f 1-03n 4-06b 5-01m".split()
out = []
for tag, ids in (("F", F), ("N", N)):
    for i in ids:
        r = mg.run(take(i)); r["tag"] = tag; r["id"] = i; out.append(r)
        print(tag, i, *(f"{k}={r[k]}" for k in ("score", "subject_med", "area_med", "area_p75", "change", "travel", "faces_found", "camera_px")))
(OWN / "qc/v3gate/motion_cal.json").write_text(json.dumps(out, indent=1))
