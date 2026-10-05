"""Packing source attribution in attempts.sampling is lossless, resumable and reversible."""
import json
import sqlite3

import pytest

from eval import attempt_compact
from solver import source_attribution as sa

ROWS = [{"normalized_line": i, "candidate_line": i * 2, "text": f"lw    t{i % 8},0x{i:x}(a0)"} for i in range(200)]


def sampling(instructions=ROWS):
    return json.dumps({"run_id": "r", "verification": {"exact": False},
                       "source_attribution": {"status": "ok", "source_sha256": "ab", "instructions": instructions},
                       "generation": {"sampling": {"role": "repair"}}}, sort_keys=True)


def test_pack_round_trips_byte_for_byte_and_shrinks():
    text = sampling()
    packed = attempt_compact.pack_text(text)
    assert isinstance(packed, str) and len(packed) < len(text) / 3
    assert attempt_compact.unpack_text(packed) == text
    assert sa.instructions_of(json.loads(packed)["source_attribution"]) == ROWS
    assert sa.instructions_of(json.loads(text)["source_attribution"]) == ROWS
    assert attempt_compact.pack_text(sampling([])) is None


def test_non_canonical_text_is_refused_not_rewritten():
    text = json.dumps(json.loads(sampling()), sort_keys=True, separators=(",", ":"))
    assert attempt_compact.pack_text(text) is False


def test_run_packs_resumes_and_expands_exactly(tmp_path):
    db = tmp_path / "campaign.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE attempts(id INTEGER PRIMARY KEY, sampling TEXT)")
    originals = {1: sampling(), 2: json.dumps({"run_id": "x"}, sort_keys=True), 3: sampling([]),
                 4: json.dumps(json.loads(sampling()), sort_keys=True, separators=(",", ":")), 5: sampling(ROWS[:50])}
    conn.executemany("INSERT INTO attempts VALUES (?,?)", originals.items())
    conn.commit()
    dry = attempt_compact.run(db, apply=False)
    assert dry["rewritten"] == 2 and dry["refused"] == 1
    assert dict(conn.execute("SELECT id, sampling FROM attempts")) == originals           # dry run wrote nothing
    report = attempt_compact.run(db, apply=True)
    assert report["rewritten"] == 2 and report["refused_ids"] == [4]
    stored = dict(conn.execute("SELECT id, sampling FROM attempts"))
    assert stored[2] == originals[2] and stored[3] == originals[3] and stored[4] == originals[4]
    assert "instructions_packed" in stored[1] and "instructions_packed" in stored[5]
    conn.execute("INSERT INTO attempts VALUES (6, ?)", (sampling(),)); conn.commit()
    again = attempt_compact.run(db, apply=True)
    assert again["since_id"] == 5 and again["rewritten"] == 1                               # only the new row
    attempt_compact.run(db, apply=True, expand=True)
    assert dict(conn.execute("SELECT id, sampling FROM attempts")) == {**originals, 6: sampling()}


def test_unknown_codec_is_an_error_not_an_empty_list():
    with pytest.raises(ValueError):
        sa.instructions_of({"instructions_codec": "lz4", "instructions_packed": "x"})
