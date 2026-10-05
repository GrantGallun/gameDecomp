"""Lead 3: object differences the normalized asm diff cannot show, over every unsolved function's best candidate.

The certificate (and so every logged object image) only runs on near-exact attempts, so the receipts say nothing about
far candidates. This compiles each unsolved function's best candidate once (frame = loop-shape rescore frame: both
ledgers, sealed 50 excluded), reads target.o and the candidate .o directly, and records every difference outside
.text -- .rodata/.data/.bss presence, size, bytes, alignment -- which no line of the text diff can express.
Trial DB only; nothing reaches the campaign or ledgers.

    python3 eval/results/hidden-object-20260930/census.py [workers]
"""
import json, multiprocessing, sqlite3, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval/results/loop-shape-20260930"))
REPO = Path("/home/grant/decomp/sbk1")
TRIAL = Path("/home/grant/decomp/runs/lead3-20260930/trial.sqlite")
NONTEXT = (".rodata", ".data", ".bss", ".sdata", ".sbss", ".late_rodata")


def sections(path):
    from solver import byte_certificate as bc
    data = path.read_bytes()
    img = bc.object_image(data)["sections"]
    raw = bc.section_contents(data)
    return img, raw


def one(item):
    from solver import workspace
    db = sqlite3.connect(f"file:{item['ledger']}?mode=ro", uri=True)
    source = db.execute("select source_code from attempts where id=?", (item["attempt_id"],)).fetchone()[0]
    db.close()
    conn = sqlite3.connect(TRIAL, timeout=600)
    try:
        ws = workspace.bootstrap(REPO, item["name"])
        name = f"{item['name']}_lead3_{time.time_ns()}"
        a = workspace.score(ws, REPO, name, source, conn=conn, func=item["name"], strategy="lead3:census",
                            run_kind="lead3")
        conn.commit()
        out = {**item, "compiled": bool(a.compiled), "score": a.score, "exact": bool(a.exact)}
        cand = ws / f"{name}.o"
        if not a.compiled or not cand.is_file() or not (ws / "target.o").is_file():
            return {**out, "note": "no objects"}
        (ti, tr), (ci, cr) = sections(ws / "target.o"), sections(cand)
        rows = []
        for sec in sorted((set(ti) | set(ci)) - {".text"}):
            a_, b_ = ti.get(sec), ci.get(sec)
            if a_ is None:
                rows.append({"section": sec, "kind": "extra", "cand_size": b_["size"]})
            elif b_ is None:
                rows.append({"section": sec, "kind": "missing", "target_size": a_["size"]})
            else:
                if a_["size"] != b_["size"]:
                    rows.append({"section": sec, "kind": "size", "target_size": a_["size"], "cand_size": b_["size"]})
                elif a_.get("sha256") != b_.get("sha256"):
                    ta, ca = tr.get(sec, b""), cr.get(sec, b"")
                    diffs = [i for i in range(min(len(ta), len(ca))) if ta[i] != ca[i]]
                    rows.append({"section": sec, "kind": "bytes", "size": a_["size"], "n_bytes": len(diffs),
                                 "first": diffs[:4], "target_hex": ta.hex()[:96], "cand_hex": ca.hex()[:96]})
                for f in ("alignment", "flags"):
                    if a_.get(f) != b_.get(f):
                        rows.append({"section": sec, "kind": f, "target": a_.get(f), "cand": b_.get(f)})
        text_same = ti.get(".text", {}).get("sha256") == ci.get(".text", {}).get("sha256")
        for p in ws.glob(f"{name}*"):
            try: p.unlink()
            except OSError: pass
        return {**out, "text_sha_equal": text_same, "nontext": rows,
                "target_sections": sorted(ti), "cand_sections": sorted(ci)}
    except Exception as exc:
        return {**item, "error": repr(exc)[:300]}
    finally:
        conn.close()


def main():
    import rescore
    sys.path.insert(0, "/mnt/c/Users/grant/AppData/Local/Temp/claude/c--Code-gameDecomp/fd0b9dce-04c3-4b15-ad97-bd7429ab0812/scratchpad")
    import probe_bss
    probe_bss.trial()
    items = rescore.frame()
    out = HERE / "census.jsonl"
    done = {json.loads(l)["name"] for l in out.read_text().splitlines()} if out.exists() else set()
    print(f"frame {len(items)} done {len(done)}", flush=True)
    with multiprocessing.Pool(int(sys.argv[1]) if len(sys.argv) > 1 else 4) as pool, out.open("a") as fh:
        for i, row in enumerate(pool.imap_unordered(one, [x for x in items if x["name"] not in done])):
            fh.write(json.dumps(row) + "\n"); fh.flush()
            if i % 50 == 0: print(i, flush=True)


if __name__ == "__main__":
    main()
