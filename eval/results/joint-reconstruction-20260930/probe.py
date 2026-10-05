"""Throwaway, opt-in DEV spike. Never installs types, changes the KB, or deploys code."""
from __future__ import annotations

import argparse
import collections
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from solver import binary_type_context as bc, binary_type_identity as bi


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def names(value):
    return {x if isinstance(x, str) else x.get("function")
            for x in value if isinstance(x, (str, dict))} - {None}


def partitions():
    dev, sealed = set(), set()
    for path in (ROOT / "eval/sets").glob("*.json"):
        obj = json.loads(path.read_text())
        dev.update(names(obj.get("dev", [])))
        if obj.get("kind") == "logic-first-connected-dev-cluster":
            dev.update(names(obj.get("cluster", [])))
        for key in ("heldout", "test", "validation"):
            sealed.update(names(obj.get(key, [])))
    return dev - sealed, sealed


def links(bundle, metadata, eligible):
    by_addr = {r["addr"]: r for r in bundle["rows"]}
    uf = bi.solve(bundle["rows"])
    direct = collections.defaultdict(list)
    for row in bundle["rows"]:
        for raw, offset, width, signed, load, cls in row["accesses"]:
            node = bi.tup(raw)
            if node[0] == "P" and width:
                direct[node].append((offset, width, signed, load, cls))
    result = []
    for row in bundle["rows"]:
        caller = row["function"]
        if caller not in eligible:
            continue
        for address, slot, raw in row["calls"]:
            callee = by_addr.get(address)
            node = bi.tup(raw)
            if not callee or callee["function"] not in eligible or node[0] not in {"P", "D"}:
                continue
            param = ("P", callee["function"], slot)
            accesses = direct.get(param, [])
            if slot not in callee["arity_reads"] or not accesses:
                continue
            if not all(0 <= off < 0x20 for off, *_ in accesses):
                continue
            if uf.find(param) == uf.find(node):
                continue
            # A tiny fixed DEV comparison: same TU, direct observed flow, shallow consumer.
            if metadata.get(caller, {}).get("tu_id") != metadata.get(callee["function"], {}).get("tu_id"):
                continue
            result.append({"caller": caller, "callee": callee["function"], "slot": slot,
                           "source_node": node, "parameter_node": param,
                           "accesses": accesses, "target_address": address})
    unique = {json.dumps(r, sort_keys=True): r for r in result}
    return list(unique.values())


def scan(repo, output):
    output.mkdir(parents=True, exist_ok=False)
    prereg = {"kind": "joint-reconstruction-dev-spike", "status": "throwaway-not-deployed",
              "question": "Do witnessed shallow caller/callee pointer flows improve clean m2c drafts when shared as one layout hypothesis?",
              "baseline": "current fixed D32 cross-function policy",
              "treatment": "D32 plus explicit shallow links in one same-TU DEV cluster",
              "selection": "binary/DEV metadata only; rank by eligible shallow links before draft/compiler outcomes",
              "arms": ["baseline", "joint"], "max_functions": 6,
              "drafts_per_function_per_arm": 2, "compile_budget_per_function_per_arm": 2,
              "primary": "paired frontend-valid compiled and exact candidates",
              "secondary": "residual axes and transfer to another consumer",
              "acceptance": "existing object certificate plus frontend; no production promotion",
              "limitations": ["development-only, historically tuned D32 baseline", "not a general type theorem",
                             "no claim of valid runtime domains from compiler success"],
              "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write(output / "preregistration.json", prereg)
    elf = bc.find_elf(repo)
    model = bc.load(elf, output / "binary-cache")
    [cache] = list((output / "binary-cache").glob("*.json"))
    bundle = json.loads(cache.read_text())["bundle"]
    dev, heldout = partitions()
    db = Path("/home/grant/decomp/kb-sbk1.sqlite")
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    metadata = {r["name"]: dict(r) for r in conn.execute(
        "SELECT f.name,f.addr,f.tu_id,f.insn_count,t.name AS compile_target FROM functions f JOIN tus t ON f.tu_id=t.id")}
    exact = {r[0] for r in conn.execute("SELECT DISTINCT f.name FROM attempts a JOIN functions f ON f.addr=a.func_addr WHERE a.exact=1")}
    conn.close()
    candidates = links(bundle, metadata, dev)
    groups = collections.defaultdict(list)
    for link in candidates:
        groups[metadata[link["caller"]]["tu_id"]].append(link)
    ordered = sorted(groups.values(), key=lambda g: (-len({r["caller"] for r in g if r["caller"] not in exact}),
        -len(g), sorted({r["caller"] for r in g})))
    report = {"evidence": model.evidence, "dev_count": len(dev), "heldout_count": len(heldout),
              "eligible_links": len(candidates), "groups": [{"links": g,
                  "functions": sorted({r[k] for r in g for k in ("caller", "callee")}),
                  "already_exact": sorted({r[k] for r in g for k in ("caller", "callee")} & exact)} for g in ordered],
              "metadata": metadata, "heldout": sorted(heldout)}
    write(output / "census.json", report)
    print(json.dumps({k: report[k] for k in ("dev_count", "heldout_count", "eligible_links")}), flush=True)
    for group in report["groups"][:8]:
        print(json.dumps(group), flush=True)


def joint_model(bundle, evidence, selected_links):
    model = bc.ContextModel(bundle, evidence)
    for link in selected_links:
        model.uf.union(bi.tup(link["source_node"]), bi.tup(link["parameter_node"]))
    model.obs = collections.defaultdict(list)
    for row in bundle["rows"]:
        for node, off, width, signed, load, cls in row["accesses"]:
            if width:
                model.obs[model.uf.find(bi.tup(node))].append((off, width, signed, load, cls))
    return model


def controls():
    bundle = {"rows": [
        {"function": "caller", "addr": 100, "calls": [[200, 0, ["P", "caller", 0]]],
         "accesses": [[["P", "caller", 0], 0, 4, 1, True, "int"]],
         "returns": [], "unify": [], "arity_reads": [0]},
        {"function": "callee", "addr": 200, "calls": [],
         "accesses": [[["P", "callee", 0], 12, 2, 0, True, "int"]],
         "returns": [], "unify": [], "arity_reads": [0]}], "symbols": {}, "stack_args": {}}
    meta = {"caller": {"tu_id": 1}, "callee": {"tu_id": 1}}
    selected = links(bundle, meta, {"caller", "callee"})
    assert len(selected) == 1
    baseline = bc.ContextModel(bundle, {})
    joint = joint_model(bundle, {}, selected)
    assert baseline.uf.find(("P", "caller", 0)) != baseline.uf.find(("P", "callee", 0))
    assert joint.uf.find(("P", "caller", 0)) == joint.uf.find(("P", "callee", 0))
    assert "unkC" not in baseline.context("caller", "glabel caller")['declarations']
    assert "unkC" in joint.context("caller", "glabel caller")['declarations']
    assert not links(bundle, meta, {"caller"})
    altered = copy.deepcopy(meta)
    altered["callee"]["tu_id"] = 2
    assert not links(bundle, altered, set(meta))
    altered_bundle = copy.deepcopy(bundle)
    altered_bundle['rows'][1]['arity_reads'] = []
    assert not links(altered_bundle, meta, set(meta))
    return {"motivating_emission": True, "different_tu_declines": True,
            "excluded_callee_declines": True, "unread_slot_declines": True}


def assemble(repo, function, destination):
    from solver import target_intake
    resolved = target_intake.resolve(repo, function)
    if resolved.kind != "disassembly":
        raise ValueError("requires binary disassembly, not SDK source")
    original = resolved.path.read_text()
    if resolved.symbol != function:
        original = target_intake.rename_symbol(original, resolved.symbol, function)
    if len(__import__('re').findall(r'(?m)^\s*glabel\s+', original)) != 1:
        raise ValueError("multi-function target declined")
    target = destination / "target.s"
    target.write_text((repo / 'tools/claude-decomp-env/prelude.inc').read_text() + '\n' +
                      (repo / 'include/macro.inc').read_text() + '\n' + original)
    command = ['mips-linux-gnu-as', '-EB', '-march=vr4300', '-mtune=vr4300', '-Iinclude',
               '-o', str(destination / 'target.o'), str(target)]
    result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=60)
    write(destination / 'target-receipt.json', {'path': str(resolved.path),
        'original_sha256': hashlib.sha256(resolved.path.read_bytes()).hexdigest(),
        'command': command, 'returncode': result.returncode, 'stderr': result.stderr})
    if result.returncode:
        raise ValueError(result.stderr)
    return original


def draft(repo, model, function, original, folder, *, valid, context=None):
    from solver import binary_type_draft as bd, m2c_input, m2c_byte_view, repair_context
    folder.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    report = {'function': function, 'valid_syntax': valid, 'status': 'declined',
              'reference_source_used': False, 'assistance': 'binary-and-public-sdk'}
    source = None
    try:
        ctx = context or model.context(function, original)
        write(folder / 'context.json', ctx)
        assembly, aliases = m2c_input.normalize_o32_registers(original)
        preprocessed, meta = bd._preprocess(repo, bd.CLEAN_PRELUDE + ctx['declarations'], folder)
        result = bd._m2c(repo, assembly, preprocessed, folder, valid_syntax=valid)
        (folder / 'm2c.stdout').write_text(result.stdout)
        (folder / 'm2c.stderr').write_text(result.stderr)
        report.update(preprocessing=meta, aliases=aliases, m2c_returncode=result.returncode)
        if result.returncode:
            raise ValueError((result.stderr or result.stdout)[-2000:])
        text = result.stdout
        if valid:
            lowered = m2c_byte_view.lower(text, function, target_assembly=original)
            text = lowered['source'] if isinstance(lowered, dict) else lowered
        definition, end = repair_context.definition(text, function)
        body = text[definition.start():end]
        header = ctx['declarations'].replace(ctx['own_prototype'] + '\n', '', 1)
        source = bd.CLEAN_PRELUDE + header + '\n' + body + '\n'
        (folder / 'source.c').write_text(source)
        report.update(status='generated', source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                      body=body)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report['error'] = str(exc)
    report['seconds'] = time.monotonic() - started
    write(folder / 'generation.json', report)
    return source, report


def run(repo, output):
    from solver import binary_type_draft as bd, workspace, signals
    from eval.research_suite.compiler import NativeCompiler, environment
    import re
    census = json.loads((output / 'census.json').read_text())
    prereg = json.loads((output / 'preregistration.json').read_text())
    if (output / 'comparison.json').exists():
        raise ValueError('refusing to overwrite previous comparison')
    (output / 'probe-run.py').write_bytes(Path(__file__).read_bytes())
    write(output / 'implementation.json', {'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'binary_algorithm_sha256': bc.algorithm_digest(), 'status': 'frozen-before-drafts-and-compiles'})
    control_report = controls()
    write(output / 'controls.json', control_report)
    group = next(g for g in census['groups'] if not g['already_exact'])
    functions = group['functions'][:prereg['max_functions']]
    assert not set(functions) & set(census['heldout'])
    chosen_links = [r for r in group['links'] if r['caller'] in functions and r['callee'] in functions]
    [cache] = list((output / 'binary-cache').glob('*.json'))
    cached = json.loads(cache.read_text())
    bundle = cached['bundle']
    assert bc._digest(bundle) == cached['bundle_sha256']
    baseline = bc.ContextModel(bundle, census['evidence'])
    joint = joint_model(bundle, census['evidence'], chosen_links)
    targets = {fn: census['metadata'][fn]['compile_target'] for fn in functions}
    targets = {fn: path if path.startswith('build/') else 'build/' + str(Path(path).with_suffix('.o'))
               for fn, path in targets.items()}
    identity = environment(repo, list(targets.values()))
    assemblies = {}
    for fn in functions:
        folder = output / 'targets' / fn
        folder.mkdir(parents=True, exist_ok=False)
        assemblies[fn] = assemble(repo, fn, folder)
    # One emitter keeps identical hypotheses/names across every group member.
    emitter = bc._Emitter(joint)
    prototypes = {fn: emitter.prototype(fn) for fn in functions}
    symbols = set().union(*(set(re.findall(r'%(?:hi|lo)\(([A-Za-z_]\w*)', a)) |
                           set(re.findall(r'\bjal\s+([A-Za-z_]\w*)', a)) for a in assemblies.values()))
    head = list(prototypes.values())
    for fn in sorted((symbols & set(joint.by_name)) - set(functions)):
        head.append(emitter.prototype(fn))
    for name in sorted(symbols - set(joint.by_name)):
        declaration = emitter.global_decl(name)
        if declaration:
            head.append(declaration)
    shared = emitter.render([x for x in head if x])
    (output / 'joint-context.h').write_text(shared)
    write(output / 'selection.json', {'functions': functions, 'links': chosen_links,
        'heldout_overlap': [], 'selection_before_compiler_outcomes': True, 'identity': identity,
        'context_sha256': hashlib.sha256(shared.encode()).hexdigest()})
    conn = sqlite3.connect(output / 'attempts.sqlite')
    conn.executescript((ROOT / 'kb/schema.sql').read_text())
    for fn in functions:
        row = census['metadata'][fn]
        conn.execute('INSERT OR IGNORE INTO tus(id,name) VALUES (?,?)', (row['tu_id'], targets[fn]))
        conn.execute('INSERT INTO functions(addr,name,tu_id,insn_count) VALUES (?,?,?,?)',
                     (row['addr'], fn, row['tu_id'], row['insn_count']))
    conn.commit()
    rows = []
    for fn in functions:
        for arm, model in [('baseline', baseline), ('joint', joint)]:
            task = {'function': fn, 'compile_target': targets[fn],
                    'target_object': f'targets/{fn}/target.o', 'context': 'empty-context'}
            (output / 'empty-context').mkdir(exist_ok=True)
            compiler = NativeCompiler(repo, task, output, output / 'compiles' / fn / arm,
                                      budget=prereg['compile_budget_per_function_per_arm'], identity=identity)
            for valid in (False, True):
                folder = output / 'drafts' / fn / arm / ('valid' if valid else 'ordinary')
                ctx = {'declarations': shared, 'own_prototype': prototypes[fn],
                       'evidence': {**census['evidence'], 'links': chosen_links,
                                    'authority': 'joint compiler hypothesis, not KB evidence'}} if arm == 'joint' else None
                source, generation = draft(repo, model, fn, assemblies[fn], folder, valid=valid, context=ctx)
                row = {'function': fn, 'arm': arm, 'valid_syntax': valid,
                       'generation': {k: v for k, v in generation.items() if k != 'body'},
                       'compiled': False, 'frontend_passed': False, 'exact': False}
                if source is not None:
                    result = compiler(source, 'draft-valid' if valid else 'draft-ordinary')
                    measured = compiler.rows[-1]
                    row.update(compiled=result.compiled, frontend_passed=(measured.get('frontend') or {}).get('passed') is True,
                        exact=result.exact, receipt=str(compiler.output / measured['artifact'] / 'receipt.json'),
                        error=measured.get('error'), source_sha256=measured['source_sha256'])
                    if result.compiled:
                        row['faults'] = asdict(signals.analyse(result.diff, 0))
                    att = workspace.Attempt(result.compiled, 0, result.exact, result.diff or '',
                        measured.get('error') or '', '', verification=measured.get('verification'),
                        frontend=measured.get('frontend'), compiler_recipe=compiler.recipe)
                    row['attempt_id'] = workspace.record_attempt(conn, fn, source, att,
                        strategy='joint-reconstruction-dev-spike:' + arm + ':source-independent',
                        run_id=output.name, run_kind='dev-spike', wall_ms=int(measured['seconds'] * 1000),
                        extra={'generation': row['generation'], 'joint_links': chosen_links if arm == 'joint' else [],
                               'training_eligible': False, 'score_available': False})
                    conn.commit()
                rows.append(row)
                write(output / 'comparison.partial.json', {'rows': rows})
                print(json.dumps({k: row.get(k) for k in ('function', 'arm', 'valid_syntax', 'compiled', 'frontend_passed', 'exact', 'error')}), flush=True)
    conn.close()
    summary = {arm: {'drafts': sum(r['generation']['status'] == 'generated' for r in rows if r['arm'] == arm),
                     'compiled_functions': sorted({r['function'] for r in rows if r['arm'] == arm and r['compiled']}),
                     'frontend_valid_functions': sorted({r['function'] for r in rows if r['arm'] == arm and r['compiled'] and r['frontend_passed']}),
                     'exact_functions': sorted({r['function'] for r in rows if r['arm'] == arm and r['exact']})}
               for arm in ('baseline', 'joint')}
    write(output / 'comparison.json', {'kind': 'joint-reconstruction-dev-spike', 'summary': summary, 'rows': rows,
        'controls': control_report, 'deployed': False, 'model_calls': 0, 'production_kb_modified': False})
    print(json.dumps(summary), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["scan", "run"])
    ap.add_argument("--repo", type=Path, default=Path("/home/grant/decomp/sbk1"))
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    (scan if args.mode == 'scan' else run)(args.repo, args.output)


if __name__ == "__main__":
    main()
