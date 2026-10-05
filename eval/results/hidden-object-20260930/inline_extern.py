"""Lever probe: an `extern const char X[]` the target keeps as a local literal.

For each HI16 relocation where the target names `.rodata`+addend and the candidate names an external X at the same
.text offset, read the NUL-terminated string at target .rodata[addend] (evidence: the target object), drop X's
extern declaration and replace uses of X with that literal. Then re-score and report the certificates.
Trial DB only.
"""
import json, re, sqlite3, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; ROOT = HERE.parents[2]; sys.path.insert(0, str(ROOT))
from solver import workspace, byte_certificate as bc
REPO = Path("/home/grant/decomp/sbk1"); TRIAL = "/home/grant/decomp/runs/lead3-20260930/trial.sqlite"


def cstr(b):
    s = b[:b.index(0)].decode("latin-1")
    return '"' + "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "\\%03o" % ord(c) for c in s) + '"'


def rewrite(src, target_o, cand_o):
    ti, ci = bc.object_image(target_o)["sections"], bc.object_image(cand_o)["sections"]
    rod = bc.section_contents(target_o).get(".rodata", b"")
    tmap = {at: ident for at, kind, ident in ti[".text"]["relocations"] if kind == 5}
    names = {}
    for at, kind, ident in ci[".text"]["relocations"]:
        t = tmap.get(at)
        if kind == 5 and ident[0] == "external" and t and t[0] == "section" and t[1] == ".rodata":
            names[ident[1]] = cstr(rod[t[2]:])
    for n, lit in names.items():
        src = re.sub(r"^extern[^;\n]*\b" + re.escape(n) + r"\b[^;\n]*;\n", "", src, flags=re.M)
        src = re.sub(r"\b" + re.escape(n) + r"\b", lit, src)
    return src, names


def score(ws, name, src, label):
    conn = sqlite3.connect(TRIAL, timeout=600)
    n = f"{name}_inl_{label}_{time.time_ns()}"
    a = workspace.score(ws, REPO, n, src, conn=conn, func=name, strategy="lead3:inline-extern", run_kind="lead3")
    conn.commit()
    v = a.verification or {}; fb = v.get("function_boundary") or {}
    return a, n, dict(score=a.score, object_exact=bool(a.exact), complete=workspace.repair_complete(a),
                      status=v.get("status"), function_exact=fb.get("function_exact"),
                      fb_error=fb.get("schema_3_error") or fb.get("error"))


for name, ledger, aid in [(x.split(":")[0], x.split(":")[1], int(x.split(":")[2])) for x in sys.argv[1:]]:
    src = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True).execute(
        "select source_code from attempts where id=?", (aid,)).fetchone()[0]
    ws = workspace.bootstrap(REPO, name)
    a, n, before = score(ws, name, src, "orig")
    new, names = rewrite(src, (ws / "target.o").read_bytes(), (ws / f"{n}.o").read_bytes())
    _, _, after = score(ws, name, new, "inl")
    print(json.dumps(dict(function=name, attempt=aid, inlined=names, before=before, after=after)), flush=True)
