import importlib.util
import sqlite3
from pathlib import Path


SPEC = importlib.util.spec_from_file_location('effect_route_canary',
                                             Path(__file__).with_name('route_canary.py'))
route = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(route)


SCHEMA = '''
CREATE TABLE attempts (id INTEGER PRIMARY KEY, parent_attempt_id INTEGER,
  source_sha256 TEXT, run_id TEXT);
CREATE TABLE attempt_runs (id TEXT PRIMARY KEY);
CREATE TABLE attempt_edges (parent_attempt_id INTEGER, child_attempt_id INTEGER,
  relation TEXT, action TEXT, feedback TEXT, created_at INTEGER);
'''


def test_imports_only_retained_baseline_and_its_parent_edge(tmp_path):
    source_db = tmp_path / 'source.sqlite'
    with sqlite3.connect(source_db) as src:
        src.executescript(SCHEMA)
        src.execute("INSERT INTO attempt_runs VALUES ('run')")
        src.execute("INSERT INTO attempts VALUES (10,NULL,'native','run')")
        src.execute("INSERT INTO attempts VALUES (11,10,'retained','run')")
        src.execute("INSERT INTO attempts VALUES (12,11,'winning-child','run')")
        src.execute("INSERT INTO attempt_edges VALUES (10,11,'derive','baseline','',1)")
        src.execute("INSERT INTO attempt_edges VALUES (11,12,'derive','winner','',2)")
    with sqlite3.connect(':memory:') as dst:
        dst.executescript(SCHEMA)
        dst.execute("INSERT INTO attempt_runs VALUES ('run')")
        dst.execute("INSERT INTO attempts VALUES (10,NULL,'native','run')")
        route.import_retained_baseline(dst, source_db, 11, 'retained')
        assert dst.execute('SELECT id,source_sha256 FROM attempts ORDER BY id').fetchall() == [
            (10, 'native'), (11, 'retained')]
        assert dst.execute('SELECT parent_attempt_id,child_attempt_id FROM attempt_edges').fetchall() == [
            (10, 11)]
