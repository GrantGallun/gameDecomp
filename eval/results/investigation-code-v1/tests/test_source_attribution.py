from pathlib import Path
import shutil

import pytest

from solver import source_attribution as lines, residual_sites, workspace, byte_certificate
from eval import differential_repair_pilot as pilot


def test_capture_adapter_preserves_compiler_strip_and_guards(tmp_path):
    original = '#!/bin/bash\nguard_do_and_asm\ncompile_same_flags\n' + lines.STRIP + '\nverify_object\n'
    script = tmp_path / 'build.sh'
    script.write_text(original)
    stale = tmp_path / 'f.source-lines.dump'
    stale.write_text('stale')
    adapted = lines.prepare(tmp_path, 'f', script)
    assert not stale.exists()
    assert script.read_text() == original
    assert adapted.read_text().replace(lines.CAPTURE, '') == original
    assert adapted.read_text().index(lines.CAPTURE) < adapted.read_text().index(lines.STRIP)
    assert lines.prepare(tmp_path, 'f', script) == adapted


def test_dump_tracks_scheduled_instructions_and_resets_at_function_boundaries():
    dump = '''Disassembly of section .text:
00000000 <f>:
   0:\t00001825 \tmove\tv1,zero
/tmp/candidate.c:4
   4:\t18a0001a \tblez\ta1,70 <f+0x70>
/tmp/candidate.c:7
   8:\t00001025 \tmove\tv0,zero
/headers/x.h:10
   c:\t00000000 \tnop
??:?
   e:\t00000000 \tnop
00000010 <g>:
  10:\t03e00008 \tjr\tra
'''
    rows, raw = lines.parse_dump(dump)
    assert [r['line'] for r in rows] == [None, 4, 7, 10, None, None]
    assert rows[2]['address'] == 8
    assert rows[3]['file'] == '/headers/x.h'
    assert len(raw) == len(rows) + 1


SOURCE = '''int f(int *a, int n) {
 while (n) { n--; }
 return a[n];
}
'''
DIFF = '''--- target
+++ candidate
@@ -1,3 +1,3 @@
 move v0,zero
-beq a0,zero,20
+beq a1,zero,20
 jr ra
@@ -8,0 +9,1 @@
+beq a1,zero,20
'''


def direct_map():
    return {'status': 'verified', 'source_sha256': lines.sha(SOURCE),
            'diff_sha256': lines.sha(DIFF), 'instructions': [
                {'normalized_line': 2, 'candidate_line': 3, 'section': '.text',
                 'address': 4, 'bytes': '10a00001'},
                {'normalized_line': 9, 'candidate_line': 2, 'section': '.text',
                 'address': 32, 'bytes': '10a00001'}]}


def test_direct_diff_join_uses_listing_positions_not_instruction_text():
    result = residual_sites.source_map(SOURCE, 'f', DIFF, direct_map())
    direct = [s for s in result['sites'] if s['evidence'] == 'direct-compiler-line']
    assert [(s['address'], s['start_line']) for s in direct] == [(4, 3), (32, 2)]
    assert result['directly_mapped_mismatches'] == 2
    assert len(result['gaps']) == 1  # deletion has no candidate address
    assert 'target-only' in result['gaps'][0]['reason']
    feed = residual_sites.render(SOURCE, 'f', DIFF, direct=direct_map())
    assert 'DIRECT .text+0x4 [10a00001] L3' in feed


def test_direct_records_override_opcode_family_and_stale_records_are_not_used():
    direct = direct_map()
    direct['instructions'] = direct['instructions'][:1]
    # A branch attributed by the compiler to the return wins over an earlier
    # syntactically plausible loop, even with a one-candidate budget.
    variants = pilot.deterministic_exactness_candidates(
        SOURCE, 'f', DIFF, 1, direct_attribution=direct)
    assert 'index-pointer' in variants[0].label
    direct['source_sha256'] = 'stale'
    result = residual_sites.source_map(SOURCE, 'f', DIFF, direct)
    assert not result['directly_mapped_mismatches']
    assert not any(s['evidence'] == 'direct-compiler-line' for s in result['sites'])


def test_closing_brace_epilogue_is_directly_attributed_not_discarded():
    direct = direct_map()
    direct['instructions'][1]['candidate_line'] = 4
    result = residual_sites.source_map(SOURCE, 'f', DIFF, direct)
    sites = [s for s in result['sites'] if s['evidence'] == 'direct-compiler-line']
    assert result['directly_mapped_mismatches'] == 2
    assert sites[1]['start_line'] == 4
    assert sites[1]['excerpt'] == '}'


@pytest.mark.grounded
def test_real_ido_original_object_lines_and_unchanged_build(tmp_path, repo_path):
    """Actual production helper and IDO O2, not a debug/recompiled substitute."""
    template = repo_path / 'nonmatchings/Fbendrange'
    if not template.is_dir():
        pytest.skip('production matching helper unavailable')
    for name in ('objdump.py', 'normalize_asm.py', 'dist.py', 'target.o'):
        shutil.copyfile(template / name, tmp_path / name)
    helper = (template / 'build.sh').read_text().replace(
        'PROJECT_ROOT="$(cd "$SCRIPT_PATH/../.." && pwd)"', f'PROJECT_ROOT="{repo_path}"')
    (tmp_path / 'build.sh').write_text(helper)
    source = 'int probe(int *a, int n) {\n int total=0;\n int i;\n for(i=0;i<n;i++) {\n total+=a[i];\n }\n return total;\n}\n'
    attempt = workspace.score(tmp_path, repo_path, 'probe', source)
    assert attempt.compiled, attempt.compiler_stderr
    attribution = attempt.source_attribution
    assert attribution['status'] == 'verified', attribution
    assert attribution['directly_mapped'] == attribution['instruction_count'] > 10
    assert attribution['allocated_image_identical']
    assert {r['candidate_line'] for r in attribution['instructions']} >= {2, 4, 5, 7}
    mapping = residual_sites.source_map(source, 'probe', attempt.diff, attribution)
    assert mapping['directly_mapped_mismatches'] > 0
    # Independently run the unmodified compiler/strip/verifier helper.
    shutil.copyfile(tmp_path / 'probe.c', tmp_path / 'baseline.c')
    status, output = workspace.sh(f'. "{repo_path}/.venv/bin/activate" && bash build.sh baseline.c', cwd=tmp_path)
    assert status == 0, output
    assert byte_certificate.object_image((tmp_path / 'probe.o').read_bytes()) == \
        byte_certificate.object_image((tmp_path / 'baseline.o').read_bytes())
    # Corrupted/stale artifacts cannot acquire a direct label.
    dump_path = tmp_path / 'probe.source-lines.dump'
    dump = dump_path.read_text()
    first = attribution['instructions'][0]['bytes']
    dump_path.write_text(dump.replace(first, 'ffffffff', 1))
    invalid = lines.collect(tmp_path, 'probe', source, (tmp_path / 'probe.c').read_text(), attempt.diff)
    assert invalid['status'] == 'unavailable'
    dump_path.write_text(dump)
    invalid = lines.collect(tmp_path, 'probe', source + ' ', (tmp_path / 'probe.c').read_text() + ' ', attempt.diff)
    assert invalid['status'] == 'unavailable'
