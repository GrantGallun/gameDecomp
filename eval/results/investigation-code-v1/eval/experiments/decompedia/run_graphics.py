"""Decode constant packet candidates from the two cited graphics functions.

Recompile each decoded static macro with the target's headers/feature defines
and compare its data bytes with the two target-derived words. This checks the
packet representation, not CPU-function matching or dynamic-argument recovery.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from solver import compiler_recipe, gfx_packets


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    repo = Path.home() / "decomp/sbk1"
    frozen = ROOT / "eval/experiments/decompedia/current-claims-v1"
    state = json.loads((frozen / "receipt.json").read_text())
    functions = [r for r in state["functions"] if r["name"] in {
        "drawPulsingAssetTableSprite", "drawMenuSpriteWithAlphaClipped"}]
    import pygfxd
    report = {"scope": "constant packet representations only; not CPU-function repairs",
        "model_calls": 0, "heldout_used": False, "target_c_bodies_read": False,
        "pygfxd_version": importlib.metadata.version("pygfxd"),
        "decoder_sha256": sha(Path(pygfxd.lgfxd._name).read_bytes()),
        "extractor_sha256": sha(Path(gfx_packets.__file__).read_bytes()), "rows": []}
    def save():
        (args.output / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
    save()
    for item in functions:
        name = item["name"]
        assert not item["heldout"]
        raw = (frozen / f"{name}.s").read_text()
        assembly = raw.split("glabel " + name, 1)[1].split("endlabel " + name, 1)[0]
        result = gfx_packets.extract(assembly, pointer_symbol="gRegionAllocPtr")
        recipe = compiler_recipe.resolve(repo, item["tu"])
        result.update(function=name, recipe=recipe, gbi_sha256=sha((repo / "include/PR/gbi.h").read_bytes()))
        assert "-DF3DEX_GBI" in recipe["settings"]["CFLAGS"]
        flags = shlex.split(recipe["settings"]["CFLAGS"]) + shlex.split(recipe["settings"]["C_OPT"])
        with tempfile.TemporaryDirectory(prefix="gfxd-roundtrip-") as temporary:
            temp = Path(temporary)
            for index, packet in enumerate(result["packets"]):
                if packet["status"] != "constant":
                    continue
                words = [s["word"] for s in packet["stores"]]
                decoded = gfx_packets.decode(words, microcode="f3dex")
                packet["decoded"] = decoded
                if decoded["returncode"] != 0 or not decoded["macro"].startswith("gs"):
                    continue
                code = '#include "common.h"\nGfx packet[] = { ' + decoded["macro"] + ' };\n'
                src, obj = temp / "packet.c", temp / "packet.o"
                src.write_text(code)
                proc = subprocess.run([str(repo / "tools/ido-recomp/linux/cc"), *flags,
                    str(src), "-o", str(obj)], cwd=repo, text=True, capture_output=True, timeout=60)
                (args.output / f"{name}-packet-{index}.c").write_text(code)
                packet["roundtrip"] = {"returncode": proc.returncode,
                    "source_sha256": sha(code.encode()), "diagnostics": proc.stdout + proc.stderr}
                if proc.returncode == 0:
                    relocations = subprocess.run(["mips-linux-gnu-readelf", "-r", str(obj)],
                        capture_output=True, text=True, check=True).stdout
                    no_data_relocations = not re.search(r"Relocation section '\.rela?\.data'", relocations)
                    data_path = temp / "packet.bin"
                    subprocess.run(["mips-linux-gnu-objcopy", "-O", "binary", "-j", ".data",
                        str(obj), str(data_path)], check=True)
                    data = data_path.read_bytes()
                    wanted = b"".join(w.to_bytes(4, "big") for w in words)
                    (args.output / f"{name}-packet-{index}.o").write_bytes(obj.read_bytes())
                    packet["roundtrip"].update(equal=no_data_relocations and len(data) >= 8 and data[:8] == wanted and not any(data[8:]),
                        no_data_relocations=no_data_relocations, relocations=relocations,
                        expected_hex=wanted.hex(), compiled_data_hex=data.hex(),
                        scope="one eight-byte Gfx packet plus zero section padding")
        report["rows"].append(result)
        save()
        print(name, "pairs", len(result["packets"]), "constant",
            sum(p["status"] == "constant" for p in result["packets"]),
            "roundtrip", sum(p.get("roundtrip", {}).get("equal", False) for p in result["packets"]), flush=True)
        for packet in result["packets"]:
            if "decoded" in packet:
                print(packet["decoded"], flush=True)


if __name__ == "__main__":
    main()
