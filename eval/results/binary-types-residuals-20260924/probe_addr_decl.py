"""Hypothesis probe (one function, official certificate): an address-only global declared as a scalar object and
taken with `&` fixes the HI16/LO16 pairing that the byte-array declaration (`extern u8 sym[N]`, array decay) broke."""
import hashlib, json, re, sqlite3, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
PT = HERE.parent / "population-transfer-20260922"
FROZEN = json.loads((PT / "freeze.json").read_text())
sys.path.insert(0, FROZEN["code_root"])
from eval.campaign_workers import isolate
from eval.search_evolution import compile_logged
E = Path.home() / "decomp/experiments/binary-types-residuals-20260924"
REPO = Path.home() / "decomp/sbk1"


def rewrite(source: str) -> str:
    """`extern u8 S[N];` used only as a value -> `extern s32 S;` and every bare use -> `&S`."""
    for m in re.finditer(r"^extern u8 (\w+)\[\w*\];$", source, re.M):
        s = m.group(1)
        body = source[source.rfind("{", 0, len(source)):]
        source = source.replace(m.group(0), f"extern s32 {s};")
        source = re.sub(rf"(?<![&\w.>]){s}\b(?!\s*\[)", f"&{s}", source.split(f"extern s32 {s};", 1)[1])\
            .join([source.split(f"extern s32 {s};", 1)[0] + f"extern s32 {s};", ""]) if False else \
            source.split(f"extern s32 {s};", 1)[0] + f"extern s32 {s};" + \
            re.sub(rf"(?<![&\w.>]){s}\b(?!\s*\[)", f"&{s}", source.split(f"extern s32 {s};", 1)[1])
    return source


def main(name):
    conn = sqlite3.connect(FROZEN["kb"], timeout=120)
    src = conn.execute("select a.source_code from attempts a join functions f on f.addr=a.func_addr where f.name=? "
                       "and a.run_id like 'binary-types%' order by a.id desc limit 1", (name,)).fetchone()[0]
    new = rewrite(src)
    print("changed:", new != src)
    print(new[new.find(name + "(", new.rfind("*/")) - 10:][:300])
    repo = isolate(REPO, E / "probe" / name, name)
    v = compile_logged(repo / "nonmatchings" / name, repo, name, new, conn=conn,
        strategy="binary-types-20260924:source-independent", run_id="binary-types-addrdecl-probe-20260924",
        action="independent-confirmation", model="deterministic-binary-types", run_kind="compiler-model-steering",
        prompt="Probe: address-only global declared as a scalar object and taken with &. No reference source.",
        extra={"training_eligible": False, "assistance_tier": "source-independent", "repairs": ["address-only-object"],
               "source_sha256": hashlib.sha256(new.encode()).hexdigest()})
    print(name, "exact" if v["exact"] else "not exact", v.get("score"))


if __name__ == "__main__":
    main(sys.argv[1])
