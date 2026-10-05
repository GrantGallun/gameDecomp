"""Patch eval/applicability_census.py: load per-attempt evidence (compiler recipe) and pass it where accepted."""
from pathlib import Path

root = Path(__file__).resolve().parents[3]
p = root / 'eval/applicability_census.py'
s = p.read_text(encoding='utf-8')

s = s.replace('''    "branch_shape": ("solver.branch_shape", "variants", True),''',
              '''    "branch_shape": ("solver.branch_shape", "variants", True),      # also takes evidence (compiler recipe)''', 1)
s = s.replace('''MEANING = re.compile''', '''TAKES_EVIDENCE = {"branch_shape"}
MEANING = re.compile''', 1)

old = '''        source, diff = ledgers[start["ledger"]].execute(
            "SELECT source_code, diff_summary FROM attempts WHERE id=?", (start["attempt_id"],)).fetchone()
'''
new = '''        source, diff, sampling = ledgers[start["ledger"]].execute(
            "SELECT source_code, diff_summary, sampling FROM attempts WHERE id=?", (start["attempt_id"],)).fetchone()
        try:
            recorded = json.loads(sampling or "{}")
        except ValueError:
            recorded = {}
        evidence = {k: recorded[k] for k in ("compiler_recipe", "frontend", "source_attribution") if k in recorded}
        if "compiler_recipe" not in evidence:
            missing_evidence.append(row["function"])       # a decline here may be missing evidence, not a non-fire
'''
assert old in s
s = s.replace(old, new, 1)
s = s.replace('''    meaning, per_function = [], {}''', '''    meaning, per_function, missing_evidence = [], {}, []''', 1)
old = '''                produced = _texts(fn(source, name, diff or "") if GENERATORS[gen][2] else fn(source, name))'''
new = '''                if gen in TAKES_EVIDENCE:
                    produced = _texts(fn(source, name, diff or "", evidence))
                else:
                    produced = _texts(fn(source, name, diff or "") if GENERATORS[gen][2] else fn(source, name))'''
assert old in s
s = s.replace(old, new, 1)
s = s.replace('''    return {"functions": n, "meaning_present_any_spelling"''', '''    return {"functions": n, "starts_without_recorded_compiler_recipe": len(missing_evidence),
            "meaning_present_any_spelling"''', 1)
s = s.replace('''    print(json.dumps({k: report[k] for k in ("functions", "meaning_present_any_spelling", "generators", "any_generator_fires")}, indent=1))''',
              '''    print(json.dumps({k: report[k] for k in ("functions", "starts_without_recorded_compiler_recipe",
                                             "meaning_present_any_spelling", "generators", "any_generator_fires")}, indent=1))''', 1)
p.write_text(s, encoding='utf-8')

t = root / 'tests/test_applicability_census.py'
s = t.read_text(encoding='utf-8')
s = s.replace('''    conn.execute("CREATE TABLE attempts(id INTEGER PRIMARY KEY, source_code TEXT, diff_summary TEXT)")
    conn.execute("INSERT INTO attempts VALUES(1, ?, ?)", (AI, MORE_B_FRAME))''',
              '''    conn.execute("CREATE TABLE attempts(id INTEGER PRIMARY KEY, source_code TEXT, diff_summary TEXT, sampling TEXT)")
    conn.execute("INSERT INTO attempts VALUES(1, ?, ?, ?)", (AI, MORE_B_FRAME, json.dumps({"compiler_recipe": O1})))''', 1)
s = s.replace("from test_branch_shape import AI, MORE_B_FRAME", "from test_branch_shape import AI, MORE_B_FRAME, O1", 1)
s = s.replace("import sqlite3\n", "import json\nimport sqlite3\n", 1)
# other fixtures gain the sampling column
s = s.replace('CREATE TABLE attempts(id INTEGER PRIMARY KEY, source_code TEXT, diff_summary TEXT)")\n    conn.execute("INSERT INTO attempts VALUES(1, ?, \'\')", (source,))',
              'CREATE TABLE attempts(id INTEGER PRIMARY KEY, source_code TEXT, diff_summary TEXT, sampling TEXT)")\n    conn.execute("INSERT INTO attempts VALUES(1, ?, \'\', NULL)", (source,))')
s = s.replace('''CREATE TABLE attempts(id INTEGER PRIMARY KEY, source_code TEXT, diff_summary TEXT)")
    rows = []''', '''CREATE TABLE attempts(id INTEGER PRIMARY KEY, source_code TEXT, diff_summary TEXT, sampling TEXT)")
    rows = []''')
s = s.replace('''conn.execute("INSERT INTO attempts VALUES(?, ?, '')", (i + 1, NARROW_MOTIVATING.replace("randomNextObject", name)))''',
              '''conn.execute("INSERT INTO attempts VALUES(?, ?, '', NULL)", (i + 1, NARROW_MOTIVATING.replace("randomNextObject", name)))''')
t.write_text(s, encoding='utf-8')
