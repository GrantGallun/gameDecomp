"""Commit history is evidence only when the miner can point to its source."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from patterns.commit_provenance import (  # noqa: E402
    build_report,
    function_definitions,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: Path, subject: str, body: str = "") -> str:
    _git(repo, "add", "--all")
    args = ["commit", "-q", "-m", subject]
    if body:
        args += ["-m", body]
    _git(repo, *args)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def history_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "history"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "History Test")
    _git(repo, "config", "user.email", "history@example.invalid")
    _git(repo, "remote", "add", "origin",
         "git@github.com:example/local-decomp.git")

    _write(repo, "README.md", "fixture\n")
    _commit(repo, "Initial project")

    _write(repo, "src/game/widgets.c", """
s32 siblingShape(s32 x) {
    return x + 2;
}
""".lstrip())
    commits = {"sibling": _commit(repo, "Decompile siblingShape")}

    _write(repo, "src/game/widgets.c", """
s32 siblingShape(s32 x) {
    return x + 2;
}

s32 targetShape(s32 x) {
    return x;
}
""".lstrip())
    commits["introduce"] = _commit(
        repo, "Decompile targetShape", "Add a structurally correct first C body.")

    _write(repo, "src/game/widgets.c", """
s32 siblingShape(s32 x) {
    return x + 2;
}

s32 targetShape(s32 x) {
    return x + 1;
}
""".lstrip())
    commits["improve"] = _commit(
        repo,
        "Improve targetShape match to 98.5%",
        "The operand order moved the score.\n\n"
        "Claude-Session: https://claude.ai/code/session_fixture123",
    )

    _write(repo, "src/game/widgets.c", """
s32 siblingShape(s32 x) {
    return x + 2;
}

s32 targetShape(s32 x) {
    return x + 2;
}
""".lstrip())
    commits["match"] = _commit(
        repo,
        "Match targetShape (widget)",
        "targetShape is a twin of siblingShape. Body copied verbatim, "
        "matching 100% on the first attempt.",
    )

    _write(repo, "src/ultra/io/pfs.c", """
s32 osPfsInitPak(s32 channel) {
    return channel;
}
""".lstrip())
    commits["sdk"] = _commit(
        repo,
        "Match ultralib pfs segment",
        "Port upstream pfs.c for VERSION_I; omit a call absent from the target.",
    )

    _write(repo, "src/game/ported.c", """
s32 portedShape(void) {
    return 7;
}
""".lstrip())
    commits["ported"] = _commit(
        repo,
        "Match portedShape",
        "Ported from other/project-decomp commit "
        "0123456789abcdef0123456789abcdef01234567.",
    )

    (repo / "src/game/ui").mkdir(parents=True)
    _git(repo, "mv", "src/game/widgets.c", "src/game/ui/widgets.c")
    commits["rename"] = _commit(repo, "Move widget source")
    return repo, commits


def _by_name(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return {item["function"]: item for item in report["functions"]}


def test_function_lexer_ignores_calls_comments_literals_and_macros():
    source = r'''
// s32 fake(s32 x) { return x; }
#define WRAP(x) { x; }
const char *text = "s32 quoted(void) {";

s32 real(s32 x) {
    if (helper(x)) {
        return x;
    }
    return 0;
}
'''
    assert function_definitions(source) == {
        "real": "s32 real(s32 x) {\n"
                "    if (helper(x)) {\n"
                "        return x;\n"
                "    }\n"
                "    return 0;\n}"
    }


def test_report_preserves_incremental_reasoning_and_exact_source_diff(history_repo):
    repo, commits = history_repo
    report = build_report(repo, functions=["targetShape"], include_source=True)
    target = _by_name(report)["targetShape"]

    assert target["current_paths"] == ["src/game/ui/widgets.c"]
    assert target["component"]["kind"] == "game"
    assert target["origin"]["kind"] == "same_game_sibling"
    assert target["origin"]["sibling_refs"] == ["siblingShape"]
    assert target["first_c_commit"]["sha"] == commits["introduce"]
    assert target["first_c_commit"]["path"] == "src/game/widgets.c"

    assert [event["action"] for event in target["history"]] == [
        "introduce", "improve", "match"]
    assert target["history"][1]["scores"] == [98.5]
    assert target["reasoning_record"]["availability"] == "linked_sessions"
    assert target["reasoning_record"]["claude_sessions"] == [
        "https://claude.ai/code/session_fixture123"]

    exact = [transition for transition in target["source_transitions"]
             if transition["sha"] == commits["match"]]
    assert len(exact) == 1
    assert exact[0]["produced_exactness"] is True
    assert "-    return x + 1;" in exact[0]["diff"]
    assert "+    return x + 2;" in exact[0]["diff"]


def test_component_and_origin_are_independent_and_conservative(history_repo):
    repo, _ = history_repo
    report = build_report(repo)
    functions = _by_name(report)

    sdk = functions["osPfsInitPak"]
    assert sdk["component"]["kind"] == "sdk"
    assert sdk["origin"]["kind"] == "upstream_unspecified"
    assert sdk["first_c_status"] == "resolved"

    ported = functions["portedShape"]
    assert ported["component"]["kind"] == "game"
    assert ported["origin"]["kind"] == "explicit_upstream"
    assert ported["origin"]["upstream_refs"] == [{
        "repo": "other/project-decomp",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "url": None,
    }]

    # A game path is not evidence that the source came from another game.
    sibling = functions["siblingShape"]
    assert sibling["component"]["kind"] == "game"
    assert sibling["origin"]["kind"] == "unknown"


def test_cli_writes_a_filtered_deep_report(history_repo, tmp_path: Path):
    repo, commits = history_repo
    output = tmp_path / "target-provenance.json"
    result = subprocess.run(
        [sys.executable, "-m", "patterns.mine_commits",
         "--repo", str(repo), "--function", "targetShape",
         "--include-source", "--output", str(output)],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "wrote 1 functions" in result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    target = report["functions"][0]
    assert target["function"] == "targetShape"
    assert target["first_c_commit"]["sha"] == commits["introduce"]
    assert any(item["produced_exactness"]
               for item in target["source_transitions"])
