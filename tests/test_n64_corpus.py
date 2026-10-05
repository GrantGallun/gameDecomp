"""Tests for the source-blind public N64 provenance index."""

import json
from pathlib import Path

import pytest

from tools import n64_corpus


SOURCE = r'''
// A declaration and control blocks are not functions.
int declaration(int x);
static float power(float x)
{
    const char *brace = "}";
    if (x == 0.0f) { return 1.0f; }
    return helper(x * x, 0x10u);
}

int randomish(int n) {
    /* } comment */
    while (n--) { n = n * 2; }
    return n;
}
'''


def test_extract_functions_ignores_declarations_controls_and_literal_braces():
    functions = list(n64_corpus.extract_functions(SOURCE))
    assert [function["name"] for function in functions] == ["power", "randomish"]
    assert functions[0]["line"] == 4
    assert '"}"' in functions[0]["body"]
    assert functions[0]["definition"].startswith("static float power(float x)")


def test_normalization_ignores_identifiers_but_preserves_constants_and_calls():
    left, constants, calls = n64_corpus.normalized_tokens(
        "{ out = helper(value * value, 0x10u); }")
    right, _, _ = n64_corpus.normalized_tokens(
        "{ renamed = other(input * input, 16); }")
    assert left == right
    assert constants == ["N16"]
    assert calls == ["helper"]


def test_sketch_similarity_separates_same_shape_from_unrelated():
    left, _, _ = n64_corpus.normalized_tokens(
        "{ if (x == 0) return 1; return f(x * x, 16); }")
    same, _, _ = n64_corpus.normalized_tokens(
        "{ if (a == 0) return 1; return g(a * a, 0x10); }")
    other, _, _ = n64_corpus.normalized_tokens(
        "{ while (p) { p = p->next; count++; } return count; }")
    left_sketch = n64_corpus.sketch(left)
    assert n64_corpus.sketch_similarity(left_sketch, n64_corpus.sketch(same)) == 1.0
    assert n64_corpus.sketch_similarity(left_sketch, n64_corpus.sketch(other)) < 0.5


def test_manifest_requires_source_prompt_guardrail(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "policy": {"direct_external_source_in_prompt": True},
        "repositories": [{"id": "x"}],
    }))
    with pytest.raises(ValueError, match="forbid direct"):
        n64_corpus.load_manifest(path)


def test_build_and_query_index_without_storing_source_bodies(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    source_dir = repo / "src"
    source_dir.mkdir(parents=True)
    (repo / ".git").mkdir()
    (source_dir / "functions.c").write_text(SOURCE)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "policy": {"direct_external_source_in_prompt": False},
        "repositories": [{
            "id": "fixture", "url": "https://example.invalid/repo",
            "local_path": str(repo), "source_roots": ["src"],
            "evaluation_policy": "dev_only",
        }],
    }))
    monkeypatch.setattr(n64_corpus, "ROOT", Path("/"))
    monkeypatch.setattr(n64_corpus, "_git", lambda *_args: "fixture-head")
    output = tmp_path / "index.sqlite"

    receipt = n64_corpus.build_index(manifest, output)
    hits = n64_corpus.query_symbol(output, "power")
    ranked = n64_corpus.query_body(
        output, "{ if (v == 0.0f) return 1.0f; return call(v*v,16); }")

    assert receipt["functions"] == 2
    assert hits[0]["repo_id"] == "fixture"
    assert ranked[0]["name"] == "power"
    with sqlite3_connect(output) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(functions)")}
    assert "body" not in columns and "source" not in columns


def sqlite3_connect(path):
    import sqlite3
    return sqlite3.connect(path)


def test_a_function_right_after_a_directive_is_extracted():
    """A `#define` between the last declaration and a function made its header start with `#`,
    and the function was dropped: 119 SBK1 and 18 SBK2 functions were invisible to the
    training-data contamination guard (shape of SBK1 initTitleDemoRaceIntro)."""
    source = (
        "extern void releaseMenuAssetHandles(void);\n\n"
        "#define RACE_PLAYER_REPLAY_SNAPSHOT(index) \\n"
        "    (((RacePlayerReplaySnapshot *)gRacePlayers)[index])\n\n"
        "void initTitleDemoRaceIntro(void) {\n    RacePlayer *players;\n}\n")
    (fn,) = n64_corpus.extract_functions(source)
    assert fn["name"] == "initTitleDemoRaceIntro"
    assert fn["definition"].startswith("void initTitleDemoRaceIntro")


def test_a_function_first_in_a_file_after_includes_is_extracted():
    source = '#include "common.h"\n#include "task.h"\n\ns32 first(s32 a) {\n    return a;\n}\n'
    (fn,) = n64_corpus.extract_functions(source)
    assert fn["name"] == "first" and "#include" not in fn["definition"]
