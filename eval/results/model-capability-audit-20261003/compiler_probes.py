"""Small synthetic IDO audit, in WSL /tmp; no game answers or GPU use.

Compare normalized instruction equality with object certificates; demonstrate
that a missing typedef can change a same/different label for identical prompts.
"""
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import repair_dataset_synth as rds
from solver import byte_certificate

REPO = Path.home() / "decomp/sbk1"


def main():
    recipe = rds.resolve_recipe(REPO)
    path = sorted((REPO / "nonmatchings").glob("*/objdump.py"))[0]
    spec = importlib.util.spec_from_file_location("audit_normalizer", path)
    norm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(norm)
    out = {"recipe": recipe["provenance"], "normalizer": str(path), "pairs": {}}
    with tempfile.TemporaryDirectory(prefix="capability-audit-") as td:
        work = Path(td)

        def compile_one(source, tag):
            result = rds.compile_unit(REPO, recipe["resolved"], tag, source, work)
            if not result["compiled"]:
                raise RuntimeError(result["stderr"])
            obj = work / (tag + ".o")
            raw = subprocess.run([norm.find_objdump_executable(), *norm.OBJDUMP_ARGS, str(obj)],
                                 check=True, capture_output=True, text=True).stdout.splitlines()
            inside, selected = False, []
            for line in raw:
                m = re.match(r"^([0-9a-f]+) <([^>]+)>:$", line)
                if m:
                    inside = m.group(2) == "probe"
                elif inside:
                    selected.append(line)
            normalized = norm.process_objdump_lines(["skip"] + selected)
            masked = [re.sub(r"%(hi|lo)\([^)]*\)", r"%\1(R)", line) for line in normalized if line.strip()]
            return obj, normalized, masked

        pairs = {
            "changed_global": ("extern int ga, gb; int probe(void) { return ga; }",
                               "extern int ga, gb; int probe(void) { return gb; }"),
            "changed_float_data": ("float probe(void) { return 1.25f; }", "float probe(void) { return 2.5f; }"),
        }
        for name, (a, b) in pairs.items():
            oa, na, ma = compile_one(a, name + "_a")
            ob, nb, mb = compile_one(b, name + "_b")
            cert = byte_certificate.certify(oa, ob, source=b)
            out["pairs"][name] = {"public_normalizer_same": na == nb, "ladder_mask_same": ma == mb,
                                    "certificate": cert, "a_listing": na, "b_listing": nb}
        a, b = "int probe(T x) { return x / 2; }", "int probe(T x) { return x >> 1; }"
        out["hidden_type"] = {"visible_a": a, "visible_b": b, "contexts": {}}
        for typ in ["int", "unsigned int"]:
            prelude = "typedef " + typ + " T;\n"
            tag = typ.replace(" ", "_")
            oa, na, _ = compile_one(prelude + a, tag + "_a")
            ob, nb, _ = compile_one(prelude + b, tag + "_b")
            out["hidden_type"]["contexts"][typ] = {
                "normalized_same": na == nb,
                "certificate_exact": byte_certificate.certify(oa, ob, source=prelude + b)["exact"],
            }
    Path(__file__).with_name("compiler-probes.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({"pairs": {k: {"public_normalizer_same": v["public_normalizer_same"],
                                     "ladder_mask_same": v["ladder_mask_same"],
                                     "certificate_exact": v["certificate"]["exact"]}
                               for k, v in out["pairs"].items()}, "hidden_type": out["hidden_type"]}, indent=2))


if __name__ == "__main__":
    main()
