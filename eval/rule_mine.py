"""Build the mined rule table (solver.rule_miner) from both engines.

    python -m eval.rule_mine a --corpus 250 --workers 8      # Engine A: compile rewrites on exact functions
    python -m eval.rule_mine b --workers 12                  # Engine B: logged campaign edges, no compiles
    python -m eval.rule_mine table                           # merge A, B and G (eval.rewrite_enum) into the table

Engine A's corpus is our own oracle-verified exact sources from both ledgers, excluding any exact that was copied
or recovered from the reference decomp. Its attempts are logged to their own trial database, never a ledger.
Both engines write raw JSONL under eval/results/rule-miner-20260929/ so the table can be rebuilt and audited.
"""
from __future__ import annotations

import argparse
import collections
import json
import multiprocessing
import random
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval import seal  # noqa: E402
OUT = ROOT / "eval" / "results" / "rule-miner-20260929"
REPO = Path("/home/grant/decomp/sbk1")
CAMPAIGN = Path("/home/grant/decomp/runs/resume-pipeline-20260908/campaign.sqlite")
KB = Path("/home/grant/decomp/kb-sbk1.sqlite")
TRIAL = Path("/home/grant/decomp/runs/rule-miner-20260929/engine-a.sqlite")
EXCLUDED_STRATEGY = ("recover", "history", "provenance", "reference", "relocated-oracle", "retrodiction")


# ---------------------------------------------------------------------------------------------- Engine A

def corpus(n: int, seed: int = 20260929) -> list[dict]:
    rows = {}
    sealed, sealed_tus = seal.sealed_in_sets(), seal.sealed_tus_in_sets()
    for ledger, path in (("campaign", CAMPAIGN), ("kb", KB)):
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        for name, aid, strategy, ic, tu in db.execute(
                "select f.name, a.id, a.strategy, f.insn_count, t.name from attempts a join functions f on f.addr=a.func_addr "
                "join tus t on t.id=f.tu_id where a.exact=1 and a.source_code is not null order by a.id"):
            if any(x in (strategy or "") for x in EXCLUDED_STRATEGY):
                continue
            if tu in sealed_tus or name in sealed:
                continue
            if name not in rows and (REPO / "nonmatchings" / name / "target.s").exists():
                rows[name] = {"name": name, "ledger": ledger, "attempt_id": aid, "strategy": strategy, "size": ic}
    names = sorted(rows)
    random.Random(seed).shuffle(names)
    return [rows[n_] for n_ in names[:n]]


def _init_trial():
    TRIAL.parent.mkdir(parents=True, exist_ok=True)
    if TRIAL.exists():
        return
    conn = sqlite3.connect(TRIAL)
    conn.executescript((ROOT / "kb/schema.sql").read_text())
    conn.execute("ATTACH DATABASE ? AS c", (CAMPAIGN.as_uri() + "?mode=ro",))
    for table in ("extraction", "tus", "functions"):
        conn.execute(f"INSERT INTO main.{table} SELECT * FROM c.{table}")
    conn.commit()
    conn.execute("DETACH DATABASE c")
    conn.close()


def probe(item: dict) -> dict:
    from solver import rewrite_library, rule_miner, workspace
    fn = item["name"]
    src_db = sqlite3.connect(f"file:{CAMPAIGN if item['ledger'] == 'campaign' else KB}?mode=ro", uri=True)
    source = src_db.execute("select source_code from attempts where id=?", (item["attempt_id"],)).fetchone()[0]
    conn = sqlite3.connect(TRIAL, timeout=600)
    run_id = f"rule-mine-a-{int(time.time())}-{fn}"
    out = {"function": fn, "records": []}
    try:
        ws = workspace.bootstrap(REPO, fn)

        def score(code, label):
            a = workspace.score(ws, REPO, f"{fn}_rulemine_{time.time_ns()}", code, conn=conn, func=fn,
                                strategy=f"rule-mine-a:{label}"[:120], run_id=run_id, run_kind="rule-mine-a")
            conn.commit()
            return a
        base = score(source, "exact-baseline")
        if not workspace.repair_complete(base):
            out["skipped"] = "exact source does not reproduce"
            return out
        import re as _re
        m = _re.search(r"-O([0-3])", json.dumps(base.compiler_recipe or {}))
        out["opt"] = f"O{m.group(1)}" if m else "O2"          # no recipe: build.sh's default -O2
        for rule, label, new in rewrite_library.all_variants(source, fn):
            a = score(new, f"{rule}:{label}")
            rec = {"rule": rule, "compiled": bool(a.compiled), "exact": workspace.repair_complete(a)}
            if a.compiled and not rec["exact"]:
                rec["features"] = dict(rule_miner.features(a.diff or ""))
            elif not a.compiled:
                rec["stderr"] = (a.compiler_stderr or "")[-200:]
            out["records"].append(rec)
    except Exception as exc:
        out["error"] = repr(exc)[:300]
    finally:
        conn.close()
    return out


def run_a(n: int, workers: int):
    _init_trial()
    items = corpus(n)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "engine-a-corpus.json").write_text(json.dumps(items, indent=1))
    path = OUT / "engine-a.jsonl"
    done = {json.loads(l)["function"] for l in path.read_text().splitlines()} if path.exists() else set()
    with multiprocessing.Pool(workers) as pool, path.open("a") as fh:
        for row in pool.imap_unordered(probe, [i for i in items if i["name"] not in done]):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            print(row["function"], row.get("opt"), len(row["records"]), row.get("skipped") or row.get("error") or "", flush=True)


# ---------------------------------------------------------------------------------------------- Engine B

def _edge(row):
    from patterns import equivalences
    from solver import rule_miner, signals
    eid, fn, psrc, csrc, pdiff, cdiff, cexact = row
    hunks = equivalences.directed_hunks(psrc, csrc)
    if hunks is None:
        return None
    pd, cd = signals.distances(pdiff or ""), signals.distances(cdiff or "")
    improved = bool(cexact) or cd < pd
    rec = {"eid": eid, "function": fn, "hunks": hunks, "improved": improved, "exact": bool(cexact),
           "changed": (pdiff or "") != (cdiff or "")}
    if improved:
        removed = rule_miner.features(pdiff or "") - rule_miner.features(cdiff or "")
        rec["removed"] = dict(removed)
    return rec


def _edges():
    conn = sqlite3.connect(f"file:{CAMPAIGN}?mode=ro", uri=True)
    sql = """select e.rowid, f.name, p.source_code, c.source_code, p.diff_summary, c.diff_summary, coalesce(c.exact,0)
             from attempt_edges e join attempts p on p.id = e.parent_attempt_id
             join attempts c on c.id = e.child_attempt_id join functions f on f.addr = c.func_addr
             where p.func_addr = c.func_addr and coalesce(p.compiled,0)=1 and coalesce(c.compiled,0)=1
               and coalesce(p.exact,0)=0 and p.source_code is not null and c.source_code is not null
               and p.source_code != c.source_code"""
    sealed, sealed_tus = seal.sealed_in_sets(), seal.sealed_tus_in_sets()
    tu_of = dict(conn.execute("select f.name, t.name from functions f join tus t on t.id=f.tu_id"))
    for row in conn.execute(sql):
        # sealed trajectories, and their TU-mates (shared structs/idioms), never feed rule mining
        if row[1] not in sealed and tu_of.get(row[1]) not in sealed_tus:
            yield row


def run_b(workers: int, limit: int | None = None):
    OUT.mkdir(parents=True, exist_ok=True)
    agg: dict[str, dict] = {}
    n = local = 0
    with multiprocessing.Pool(workers) as pool:
        import itertools
        source = itertools.islice(_edges(), limit) if limit else _edges()
        for rec in pool.imap_unordered(_edge, source, chunksize=64):
            n += 1
            if rec is None:
                continue
            local += 1
            key = json.dumps(rec["hunks"])
            e = agg.setdefault(key, {"applied": 0, "improved": 0, "exact": 0, "changed": 0, "functions": set(),
                                     "improved_functions": set(), "profile": collections.Counter()})
            e["applied"] += 1
            e["changed"] += rec["changed"]
            e["functions"].add(rec["function"])
            if rec["improved"]:
                e["improved"] += 1
                e["exact"] += rec["exact"]
                e["improved_functions"].add(rec["function"])
                e["profile"].update(set(rec.get("removed", {})))        # presence per example, as Engine A
            if n % 20000 == 0:
                print(f"edges {n} local {local} templates {len(agg)}", flush=True)
    rows = []
    for key, e in agg.items():
        if e["improved"] < 3 or len(e["improved_functions"]) < 2:
            continue
        rows.append({"hunks": json.loads(key), "applied": e["applied"], "improved": e["improved"], "exact": e["exact"],
                     "changed": e["changed"], "functions": len(e["functions"]),
                     "improved_functions": len(e["improved_functions"]),
                     "profile": dict(e["profile"].most_common(40))})
    (OUT / "engine-b.json").write_text(json.dumps({"edges": n, "local": local, "templates_all": len(agg),
                                                   "templates": rows}, indent=1))
    print(f"edges {n} local {local} templates {len(agg)} kept {len(rows)}")


# ---------------------------------------------------------------------------------------------- promotion (B -> A)

PROMOTE_PER_FUNCTION = 10


def _promotable():
    """Usable Engine B templates whose REVERSE can be instantiated (every name on its after side is bound by it)."""
    import re as _re
    data = json.loads((OUT / "engine-b.json").read_text())
    out = []
    for t in sorted(data["templates"], key=lambda t: -t["improved"]):
        if t["improved"] / max(1, t["applied"]) < 0.02:
            continue
        names = lambda side: {p for h in t["hunks"] for p in h[side] if _re.fullmatch(r"[IN]\d+", p)}
        if names(0) <= names(1):
            out.append(t)
    return out


def promote_probe(item: dict) -> dict:
    """Apply templates in reverse to one exact function (introduce the spelling each template repairs) and compile."""
    from patterns import equivalences
    from solver import rewrite_library, rule_miner, workspace
    fn = item["name"]
    src_db = sqlite3.connect(f"file:{CAMPAIGN if item['ledger'] == 'campaign' else KB}?mode=ro", uri=True)
    source = src_db.execute("select source_code from attempts where id=?", (item["attempt_id"],)).fetchone()[0]
    out = {"function": fn, "records": []}
    try:
        begin, stop = rewrite_library._body(source, fn)
    except Exception as exc:
        out["error"] = repr(exc)[:200]
        return out
    toks = [t for t in rule_miner._tokens_with_spans(source) if begin <= t[1] < stop]
    plans = []
    for t in item["templates"]:
        reverse = [[h[1], h[0]] for h in t["hunks"]]
        news = rule_miner.apply_template(source, begin, stop, reverse, limit=1, toks=toks)
        if news:
            key = "B:" + equivalences.Edit(tuple((tuple(a), tuple(b)) for a, b in t["hunks"])).key
            plans.append((key, news[0]))
        if len(plans) >= PROMOTE_PER_FUNCTION:
            break
    if not plans:
        return out
    conn = sqlite3.connect(TRIAL, timeout=600)
    run_id = f"rule-mine-p-{int(time.time())}-{fn}"
    try:
        ws = workspace.bootstrap(REPO, fn)

        def score(code, label):
            a = workspace.score(ws, REPO, f"{fn}_rulepromote_{time.time_ns()}", code, conn=conn, func=fn,
                                strategy=f"rule-mine-p:{label}"[:120], run_id=run_id, run_kind="rule-mine-p")
            conn.commit()
            return a
        base = score(source, "exact-baseline")
        if not workspace.repair_complete(base):
            out["skipped"] = "exact source does not reproduce"
            return out
        for key, new in plans:
            a = score(new, key)
            rec = {"template": key, "compiled": bool(a.compiled), "exact": workspace.repair_complete(a)}
            if a.compiled and not rec["exact"]:
                rec["features"] = dict(rule_miner.features(a.diff or ""))
            out["records"].append(rec)
    except Exception as exc:
        out["error"] = repr(exc)[:300]
    finally:
        conn.close()
    return out


def run_promote(workers: int):
    _init_trial()
    items = json.loads((OUT / "engine-a-corpus.json").read_text())
    templates = _promotable()
    for i in items:
        i["templates"] = templates
    path = OUT / "promote.jsonl"
    done = {json.loads(l)["function"] for l in path.read_text().splitlines()} if path.exists() else set()
    print(f"promotable templates {len(templates)}, corpus {len(items)}", flush=True)
    with multiprocessing.Pool(workers) as pool, path.open("a") as fh:
        for row in pool.imap_unordered(promote_probe, [i for i in items if i["name"] not in done]):
            fh.write(json.dumps(row) + "\n")
            fh.flush()


# ---------------------------------------------------------------------------------------------- table

def build_table(with_generated: bool = False):
    from patterns import equivalences
    from solver import rule_miner
    rules = {}
    a_path = OUT / "engine-a.jsonl"
    if a_path.exists():
        agg = collections.defaultdict(lambda: {"applied": 0, "compiled": 0, "changed": 0, "functions": set(),
                                               "profile": collections.Counter(), "by_opt": collections.Counter()})
        for line in a_path.read_text().splitlines():
            row = json.loads(line)
            for rec in row.get("records", []):
                e = agg[rec["rule"]]
                e["applied"] += 1
                e["functions"].add(row["function"])
                e["compiled"] += rec["compiled"]
                if rec["compiled"] and not rec["exact"]:
                    e["changed"] += 1
                    e["by_opt"][row.get("opt", "O?")] += 1
                    e["profile"].update(set(rec.get("features", {})))       # presence per example
        for rule, e in agg.items():
            effect = e["changed"] / max(1, e["compiled"])
            rules["A:" + rule] = {"engine": "A", "applied": e["applied"], "compiled": e["compiled"],
                                  "changed": e["changed"], "examples": e["changed"], "functions": len(e["functions"]),
                                  "effect_rate": round(effect, 4), "broke_rate": round(1 - e["compiled"] / max(1, e["applied"]), 4),
                                  "changed_by_opt": dict(e["by_opt"]), "profile": dict(e["profile"].most_common(60)),
                                  "pruned": effect < rule_miner.INERT or e["changed"] < 3}
    b_path = OUT / "engine-b.json"
    if b_path.exists():
        data = json.loads(b_path.read_text())
        for t in data["templates"]:
            hunks = [[list(a), list(b)] for a, b in t["hunks"]]
            key = "B:" + equivalences.Edit(tuple((tuple(a), tuple(b)) for a, b in hunks)).key
            precision = t["improved"] / max(1, t["applied"])
            left = " ... ".join(" ".join(a) for a, _ in hunks)
            right = " ... ".join(" ".join(b) for _, b in hunks)
            rules[key] = {"engine": "B", "hunks": hunks, "pattern": f"{left}  ->  {right}", "applied": t["applied"],
                          "improved": t["improved"], "examples": t["improved"], "exact": t["exact"],
                          "functions": t["functions"], "improved_functions": t["improved_functions"],
                          "precision": round(precision, 4), "profile": t["profile"], "pruned": precision < 0.02}
    p_path = OUT / "promote.jsonl"
    if p_path.exists():
        agg = collections.defaultdict(lambda: {"applied": 0, "compiled": 0, "changed": 0, "functions": set(),
                                               "profile": collections.Counter()})
        for line in p_path.read_text().splitlines():
            row = json.loads(line)
            for rec in row.get("records", []):
                e = agg[rec["template"]]
                e["applied"] += 1
                e["functions"].add(row["function"])
                e["compiled"] += rec["compiled"]
                if rec["compiled"] and not rec["exact"]:
                    e["changed"] += 1
                    e["profile"].update(set(rec.get("features", {})))
        for key, e in agg.items():
            if key not in rules:
                continue
            effect = e["changed"] / max(1, e["compiled"])
            rules[key]["promotion"] = {"applied": e["applied"], "compiled": e["compiled"], "changed": e["changed"],
                                       "functions": len(e["functions"]), "effect_rate": round(effect, 4)}
            if e["compiled"] >= 3 and effect < rule_miner.INERT:
                rules[key]["pruned"] = True              # inert on correct code: Ruler-style minimisation
                rules[key]["pruned_reason"] = "inert when introduced into exact functions"
            elif e["changed"] >= 3:
                # controlled probes on correct code give a cleaner profile than search history
                rules[key]["history_profile"] = rules[key]["profile"]
                rules[key]["profile"] = dict(e["profile"].most_common(60))
                rules[key]["examples"] = e["changed"]
                rules[key]["validated"] = True
    g_path = ROOT / "eval" / "results" / "rewrite-enum-20260930" / "rules.json"
    if with_generated and g_path.exists():
        # Engine G (eval/rewrite_enum.py) is opt-in: on the development frame it lost 1/4 against the table without
        # it (eval/results/rewrite-enum-20260930/RESULTS.md), so the default table leaves it out.
        rules.update(json.loads(g_path.read_text())["rules"])
    table = {"schema": 1, "built": time.strftime("%Y-%m-%d %H:%M"), "rules": rules}
    (ROOT / "patterns" / "mined_rules.json").write_text(json.dumps(table, indent=1))
    a = [k for k in rules if k.startswith("A:")]
    b = [k for k in rules if k.startswith("B:")]
    g = [k for k in rules if k.startswith("G:")]
    print(f"rules: A {len(a)} ({sum(not rules[k]['pruned'] for k in a)} usable), "
          f"B {len(b)} ({sum(not rules[k]['pruned'] for k in b)} usable), "
          f"G {len(g)} ({sum(not rules[k]['pruned'] for k in g)} usable)")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("engine", choices=("a", "b", "p", "table"))
    ap.add_argument("--corpus", type=int, default=250)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--with-generated", action="store_true", help="merge Engine G rules (eval.rewrite_enum)")
    args = ap.parse_args(argv)
    if args.engine == "a":
        run_a(args.corpus, args.workers)
    elif args.engine == "p":
        run_promote(args.workers)
    elif args.engine == "b":
        run_b(args.workers, args.limit)
    else:
        build_table(args.with_generated)


if __name__ == "__main__":
    main()
