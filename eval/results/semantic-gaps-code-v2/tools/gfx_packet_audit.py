"""Emit a read-only libgfxd packet audit from annotated target assembly."""
import argparse
import json
from pathlib import Path
from solver import gfx_packets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assembly", type=Path)
    parser.add_argument("--function", required=True)
    parser.add_argument("--pointer-symbol", required=True)
    parser.add_argument("--microcode", choices=["f3d", "f3db", "f3dex", "f3dexb", "f3dex2"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raw = args.assembly.read_text()
    marker, end = "glabel " + args.function, "endlabel " + args.function
    if raw.count(marker) != 1 or end not in raw:
        parser.error("expected one explicitly named function in annotated assembly")
    body = raw.split(marker, 1)[1].split(end, 1)[0]
    report = gfx_packets.extract(body, pointer_symbol=args.pointer_symbol)
    for packet in report["packets"]:
        if packet["status"] == "constant":
            packet["decoded"] = gfx_packets.decode([s["word"] for s in packet["stores"]], microcode=args.microcode)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote {len(report['packets'])} candidate packets to {args.out}")


if __name__ == "__main__":
    main()
