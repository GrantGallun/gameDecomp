import sqlite3
import difflib
import re
import shutil
import subprocess

import pytest

from solver import code_shapes, residual_sites as sites
from eval import differential_repair_pilot as pilot


SOURCE = '''int f(int *a, int n) {
    while (n > 3) { n--; }
    return a[n];
}
'''
MEMORY = '-lw v0,0(a0)\n+lw v0,4(a0)'


def test_late_memory_region_wins_before_candidate_budget_truncation():
    variants = pilot.deterministic_exactness_candidates(SOURCE, 'f', MEMORY, max_variants=1)
    assert len(variants) == 1
    assert 'index-pointer' in variants[0].label
    mapping = sites.source_map(SOURCE, 'f', MEMORY)
    assert [s['start_line'] for s in mapping['sites']] == [3]
    assert not mapping['instruction_ownership_proven']
    assert {r['side'] for r in mapping['mismatches']} == {'target', 'candidate'}


def test_duplicate_rows_are_counted_and_removed_rows_are_identified():
    diff = MEMORY + '\n+lw v0,4(a0)'
    probe = sites.intervention(SOURCE, SOURCE.replace('a[n]', '*(a+n)'), diff, MEMORY,
                               'code-shape:index-pointer@50', compiled=True,
                               semantic_clean=True, exact=False)
    assert probe['removed_count'] == 1
    assert probe['added_count'] == 0
    assert probe['region']['start_line'] == 3
    assert len({r['id'] for r in sites.mismatches(diff)}) == 3


def test_persistent_interventions_do_not_cross_source_or_target_residual(tmp_path):
    db = tmp_path / 'receipts.sqlite'
    conn = sqlite3.connect(db)
    assert not sites.load(conn, SOURCE, MEMORY)
    probe = sites.intervention(SOURCE, SOURCE.replace('a[n]', '*(a+n)'), MEMORY, '',
                               'code-shape:index-pointer@50', compiled=True,
                               semantic_clean=True, exact=True)
    sites.record(conn, 10, probe)
    conn.close()
    conn = sqlite3.connect(db)
    assert sites.load(conn, SOURCE, MEMORY) == [{'source_intervention': probe}]
    assert not sites.load(conn, '\n' + SOURCE, MEMORY)
    assert not sites.load(conn, SOURCE, MEMORY.replace('4(a0)', '8(a0)'))
    conn.close()


def test_only_semantically_clean_source_bound_evidence_reprioritizes():
    variants = code_shapes.candidates(SOURCE, 'f')
    loop = variants[0]
    pointer = next(v for v in variants if 'index-pointer' in v.label)
    mapping = sites.source_map(SOURCE, 'f', MEMORY)
    assert sites.rank(SOURCE, pointer, mapping) < sites.rank(SOURCE, loop, mapping)
    probe = sites.intervention(SOURCE, loop.source, MEMORY, '', loop.label,
                               compiled=True, semantic_clean=True, exact=False)
    history = [{'source_intervention': probe}]
    assert sites.rank(SOURCE, loop, mapping, history) < sites.rank(SOURCE, pointer, mapping, history)
    probe['semantic_clean'] = False
    assert sites.rank(SOURCE, loop, mapping, history) == sites.rank(SOURCE, loop, mapping)
    probe['semantic_clean'] = True
    probe['parent_diff_sha256'] = 'stale'
    assert sites.rank(SOURCE, loop, mapping, history) == sites.rank(SOURCE, loop, mapping)


def test_compile_failure_is_never_reported_as_residual_elimination():
    probe = sites.intervention(SOURCE, 'bad C', MEMORY, '', 'oss', compiled=False,
                               semantic_clean=False, exact=False)
    assert probe['outcome'] == 'compile-failed'
    assert probe['removed_count'] == probe['added_count'] == 0


def test_comments_strings_and_other_functions_do_not_create_source_associations():
    source = 'int g(int *a) { return a[0]; }\nint f(int n) {\n/* a[n] */\nreturn n;\n}'
    assert not sites.source_map(source, 'f', MEMORY)['sites']
    feed = sites.render(source, 'f', MEMORY)
    assert 'No localized source association' in feed


def test_real_mips_compiler_intervention_links_loop_edit_to_changed_residual(tmp_path):
    clang = shutil.which('clang')
    if clang is None:
        pytest.skip('clang unavailable')
    source = 'int f(int n) { int s=0; while(n>0) {s+=n; n--;} return s; }'
    variant = next(v for v in code_shapes.candidates(source, 'f') if 'guarded-do' in v.label)

    def compile_assembly(code, name):
        src, out = tmp_path / f'{name}.c', tmp_path / f'{name}.s'
        src.write_text(code)
        proc = subprocess.run([clang, '-target', 'mips-unknown-linux-gnu', '-O0',
                               '-S', str(src), '-o', str(out)], capture_output=True, text=True)
        if 'No available targets' in proc.stderr:
            pytest.skip('installed clang has no MIPS backend')
        assert proc.returncode == 0, proc.stderr
        return [re.sub(r'\s+', ' ', line.split('#', 1)[0].strip())
                for line in out.read_text().splitlines()
                if re.match(r'^\s+[a-z][a-z0-9.]*\s', line)]

    original = compile_assembly(source, 'original')
    alternative = compile_assembly(variant.source, 'alternative')
    diff = '\n'.join(difflib.unified_diff(alternative, original, 'target', 'candidate'))
    assert original != alternative
    mapping = sites.source_map(source, 'f', diff)
    assert mapping['sites'] and mapping['mismatches']
    probe = sites.intervention(source, variant.source, diff, '', variant.label,
                               compiled=True, semantic_clean=True, exact=False)
    assert probe['removed_count'] == len(mapping['mismatches'])
    assert probe['removed_count'] > 0
    assert probe['region']['start_line'] == 1
    assert probe['outcome'] == 'residual-changed'
    # This tests compiler influence, not the project's IDO object certificate.
    assert not probe['exact']
