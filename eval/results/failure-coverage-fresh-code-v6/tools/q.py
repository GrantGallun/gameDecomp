"""Tiny query helper -- sqlite3 CLI isn't installed in the WSL image.

    python3 tools/q.py <db> "SELECT ..."
"""

import sqlite3
import sys


def main() -> None:
    db, sql = sys.argv[1], sys.argv[2]
    conn = sqlite3.connect(db)
    cur = conn.execute(sql)
    if cur.description is None:
        conn.commit()
        return

    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    fmt = lambda v: "NULL" if v is None else str(v)
    widths = [max(len(c), *(len(fmt(r[i])) for r in rows)) if rows else len(c)
              for i, c in enumerate(cols)]

    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(fmt(v).ljust(w) for v, w in zip(r, widths)))
    print(f"\n({len(rows)} rows)")


if __name__ == "__main__":
    main()
