"""Copy experiment code/inputs to native WSL storage without touching shared work."""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEST = Path.home() / "decomp/experiments/search-evolution-20260922/code-v3"
SUFFIXES = {".py", ".json", ".sql", ".yaml", ".yml", ".sh", ".inc", ".h", ".c", ".toml"}


def main():
    DEST.mkdir(parents=True, exist_ok=False)
    files = []
    for package in ("solver", "eval", "kb", "patterns", "oracle", "miner", "mcpserver", "tools"):
        for directory, children, names in os.walk(ROOT / package):
            children[:] = [p for p in children if not p.startswith(".") and p not in {"results", "__pycache__", "node_modules"}]
            for name in names:
                path = Path(directory) / name
                if not name.startswith(".") and path.suffix in SUFFIXES:
                    files.append(path)
    for relative in ("eval/results/search-evolution-20260922/run.py", "eval/results/dream-search-20260922/pilot.py",
                     "eval/results/dev-set-20260921/dev-set.json"):
        files.append(ROOT / relative)
    files += list((ROOT / "eval/results/dev-set-20260921/sources").glob("*.c"))
    files += list((ROOT / "eval/results/repair-mechanisms-20260922/inputs").glob("*/initial.c"))
    rows = {}
    for path in sorted(set(files)):
        relative = path.relative_to(ROOT)
        data = path.read_bytes()
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        rows[str(relative)] = hashlib.sha256(data).hexdigest()
    for relative, digest in rows.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest, relative
    (DEST / "snapshot.json").write_text(json.dumps({"origin": str(ROOT), "files": rows}, indent=2))
    print(json.dumps({"snapshot": str(DEST), "files": len(rows)}))


if __name__ == "__main__":
    main()
