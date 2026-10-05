"""Add a `site_edits_first` treatment to eval/seal_run.native_arm_factory.

Control: regalloc_search.search at budget B (B+1 compiles). Treatment: site_edits.search at ~B/2 from the pinned
start, then regalloc_search.search at ~B/2 from site_edits' best node. Same per-function cap; the claim is
"site_edits adds value in front of the current search", not "engine X beats engine Y".
"""
from pathlib import Path

p = Path(__file__).resolve().parents[3] / 'eval/seal_run.py'
s = p.read_text(encoding='utf-8')

old_start = s.index('        def evaluate(code, tag, parent_source=None):')
old_end = s.index('    return arm\n', old_start)
new_block = '''        def score_attempt(code, tag, parent_source=None):
            count[0] += 1
            attempt = workspace.score(ws, repo, f"seal_{count[0]:04d}", code, conn=trial, func=name,
                strategy=f"seal-run:{label}:{tag}"[:120], run_id="seal-run",
                parent_attempt_id=(ids.get(parent_source) or (None,))[0], relation="candidate-construction",
                extra={"training_eligible": False, "arm": label})
            trial.commit()
            exact = workspace.repair_complete(attempt)
            ids[code] = (attempt.receipt_id, float(attempt.score or 0.0), exact)
            return attempt, exact

        def evaluate(code, tag, parent_source=None):
            attempt, exact = score_attempt(code, tag, parent_source)
            normalized = ws / f"seal_{count[0]:04d}_object_dump_normalized.s"
            dump = normalized.read_text() if attempt.compiled and normalized.exists() else None
            return rs.Compiled(attempt.compiled, exact, dump, attempt.diff,
                evidence={"compiled": attempt.compiled, "frontend": attempt.frontend,
                          "source_attribution": attempt.source_attribution,
                          "compiler_recipe": attempt.compiler_recipe})

        kwargs = dict(kwargs)
        site_first = kwargs.pop("site_edits_first", False)
        search_budget, start_source = budget, source
        if site_first:
            from solver import site_edits
            half = budget // 2 - 1                      # baseline + edits + (rs baseline + edits) stays <= budget + 1
            found = site_edits.search(score_attempt_adapter(score_attempt), source, name, budget=half)
            start_source, search_budget = found["source"], budget - half - 1
        baseline = evaluate(start_source, "baseline") if start_source not in ids else evaluate(start_source, "rebaseline")
        outcome = rs.search(name, start_source, lambda c, l: evaluate(c, l), target, compile_with_parent=evaluate,
                            baseline=baseline, budget=search_budget, depth=4, beam=3, **kwargs)
        best = pick_best(ids)
        return {"ref": {"ledger": "trial", "attempt_id": ids[best][0]}, "compiles": count[0],
                "exact": ids[best][2], "score": ids[best][1],
                "sources": [hashlib.sha256(c.encode()).hexdigest()[:16] for c in ids]}
'''
s = s[:old_start] + new_block + s[old_end:]
s = s.replace('''def native_arm_factory(''', '''def score_attempt_adapter(score_attempt):
    """site_edits.search wants score(code, label, parent_code) -> workspace.Attempt."""
    return lambda code, label, parent_code: score_attempt(code, label, parent_code)[0]


def native_arm_factory(''', 1)
p.write_text(s, encoding='utf-8')
