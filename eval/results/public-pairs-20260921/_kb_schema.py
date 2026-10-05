"""Where do function NAMES live? Printed so the near-miss list can be made runnable."""
import sqlite3
import sys
from pathlib import Path

KB = Path.home() / "decomp/kb-sbk1.sqlite"
conn = sqlite3.connect(f"file:{KB}?mode=ro", uri=True)
try:
    for (name,) in conn.execute("select name from sqlite_master where type='table' order by name"):
        columns = [row[1] for row in conn.execute(f"pragma table_info({name})")]
        count = conn.execute(f"select count(*) from {name}").fetchone()[0]
        print(f"{name:26} {count:>9}  {columns}")
finally:
    conn.close()
print()
sys.path.insert(0, "/mnt/c/Code/gameDecomp")
from solver import callsig
print("callsig helpers:", [n for n in dir(callsig) if not n.startswith("_")][:20])
