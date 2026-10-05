import json, sqlite3, sys, hashlib
from pathlib import Path
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from eval import probe_source
from solver import function_boundary
sha = lambda b: hashlib.sha256(b).hexdigest()
REPO = Path("/home/grant/decomp/sbk1")
fn = sys.argv[1]
r = [json.loads(l) for l in open("/mnt/c/Code/gameDecomp/eval/results/loop-shape-20260930/rescore_fb.jsonl") if json.loads(l)["name"] == fn][0]
src = sqlite3.connect(r["ledger"]).execute("select source_code from attempts where id=?", (r["attempt_id"],)).fetchone()[0]
a = probe_source.probe(fn, src, "dbg")
c = a.verification; b = c["function_boundary"]
cand = Path(b["inputs"]["candidate"]["path"]).with_suffix(".c")
print("exact", c.get("exact"), "fe", b.get("function_exact"))
print("revalidate", function_boundary.revalidate(b))
print("cand sha keys", b["inputs"]["candidate"]["sha256"] == c.get("candidate_sha256"), b["inputs"]["target"]["sha256"] == c.get("target_sha256"))
print("rom", b["inputs"]["rom"]["sha256"] == sha((REPO / "snowboardkids.z64").read_bytes()))
print("c file sha == source_sha256", cand.exists() and sha(cand.read_text(encoding="utf-8").encode()) == c.get("source_sha256"))
print("candidate_hash", c.get("candidate_source_sha256", c.get("source_sha256")) == sha(cand.read_text(encoding="utf-8").encode()) if cand.exists() else None)
print("receipt id", a.receipt_id)
