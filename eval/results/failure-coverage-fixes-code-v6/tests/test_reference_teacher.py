import json
import sqlite3

from solver import reference_teacher


def _database():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE tus(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE functions(
            name TEXT, tu_id INTEGER, insn_count INTEGER);
        INSERT INTO tus VALUES(1, 'src/module.c');
        INSERT INTO tus VALUES(2, 'src/other.c');
        INSERT INTO functions VALUES('target', 1, 10);
        INSERT INTO functions VALUES('same_tu_sibling', 1, 12);
        INSERT INTO functions VALUES('other_helper', 2, 8);
        INSERT INTO functions VALUES('duplicate', 2, 10);
    """)
    return conn


def _repo(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "module.c").write_text("""
int target(int value) {
    return value + 1;
}
int same_tu_sibling(int value) {
    if (value != 0) {
        return value - 1;
    }
    return 0;
}
""")
    (source / "other.c").write_text("""
int other_helper(int value) {
    return value * 2;
}
int duplicate(int renamed) {
    return renamed + 1;
}
""")
    return tmp_path


def _logic_packet():
    return {
        "function": "target",
        "binary_call_edges": [
            {"caller": "target", "callee": "other_helper"}],
        "functions": [
            {"function": "target"},
            {"function": "same_tu_sibling"},
            {"function": "other_helper"},
        ],
    }


def test_lofo_uses_same_tu_but_excludes_target_and_normalized_duplicate(tmp_path):
    reference_teacher.clear_cache()
    packet = reference_teacher.build_packet(
        repo=_repo(tmp_path), conn=_database(), logic_packet=_logic_packet(),
        regime="leave_one_function_out", similar=[])
    names = {row["function"] for row in packet["examples"]}
    rendered = json.dumps(packet)

    assert "same_tu_sibling" in names
    assert "other_helper" in names
    assert "target" not in names
    assert "duplicate" not in names
    assert "int target(int value)" not in rendered
    assert packet["contamination_audit"]["target_tu_present"]
    assert packet["contamination_audit"]["normalized_target_duplicate_absent"]
    assert packet["matched_source_blocks"]
    assert all(row["function"] != "target"
               for row in packet["matched_source_blocks"])


def test_loto_excludes_every_function_from_target_tu_and_is_deterministic(
        tmp_path):
    reference_teacher.clear_cache()
    repo = _repo(tmp_path)
    conn = _database()
    first = reference_teacher.build_packet(
        repo=repo, conn=conn, logic_packet=_logic_packet(),
        regime="leave_one_tu_out", similar=[])
    second = reference_teacher.build_packet(
        repo=repo, conn=conn, logic_packet=_logic_packet(),
        regime="leave_one_tu_out", similar=[])

    assert [row["function"] for row in first["examples"]] == ["other_helper"]
    assert not first["contamination_audit"]["target_tu_present"]
    assert first["contamination_audit"]["target_tu_policy_satisfied"]
    assert first["packet_digest"] == second["packet_digest"]


def test_unknown_regime_is_rejected_before_reference_use(tmp_path):
    try:
        reference_teacher.build_packet(
            repo=tmp_path, conn=_database(), logic_packet=_logic_packet(),
            regime="finished_repo", similar=[])
    except ValueError as error:
        assert "unknown teacher regime" in str(error)
    else:
        raise AssertionError("unknown regime was accepted")


def test_build_object_tu_path_maps_back_to_source():
    assert reference_teacher._source_tu_path(
        "build/src/race/player/update.o") == "src/race/player/update.c"
    assert reference_teacher._source_tu_path(
        "src/race/player/update.c") == "src/race/player/update.c"


def test_packet_loader_rejects_tampering(tmp_path):
    reference_teacher.clear_cache()
    packet = reference_teacher.build_packet(
        repo=_repo(tmp_path), conn=_database(), logic_packet=_logic_packet(),
        regime="leave_one_function_out", similar=[])
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet))
    assert reference_teacher.load_packet(path)["function"] == "target"

    packet["examples"][0]["function"] = "target"
    path.write_text(json.dumps(packet))
    try:
        reference_teacher.load_packet(path)
    except ValueError as error:
        assert "digest mismatch" in str(error)
    else:
        raise AssertionError("tampered packet was accepted")
