"""Extra finite diagnostic inputs: nonzero selector and distinct tile halfwords.

These are synthetic cases, not captured game state or asserted C object bounds.
The 0x340-byte mapped span intentionally covers target and faulty doubled-offset
reads, making value disagreements observable rather than relying on a fault.
"""
from collections import Counter
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import semantic_lane
from solver import mips_differential as differential, workspace


def cases(base):
    contents = tuple(('gCharacterSelectCoursePreviewFrameTileMaps+0x%x' % offset, 2, 0x4000 + offset // 2)
                     for offset in range(0, 0x340, 2))
    # The corner symbol is a linked alias at tile-map base+0x28. Do not add
    # contradictory alias writes; use the same underlying halfwords.
    return tuple(replace(base, name='pointer-selector-' + label,
                         player_writes=base.player_writes + ((0x18, 2, 0), (0x1a, 2, 0), (0x1c, 2, selector)),
                         global_writes=base.global_writes + contents +
                         (('gRaceSplitscreenMode', 1, split), ('gRaceTypeSelection', 1, race_type)))
                 for label, selector, split, race_type in [('1', 1, 0, 0), ('9', 9, 0, 0), ('forced-9', 1, 1, 2)])


def evaluate(panel, candidate_object):
    """Use the initialized normal panel environment, but a separate case list."""
    if not panel.cases:
        raise ValueError('extra selector diagnostics require a completed normal-panel seed')
    linked = differential.Program.parse(panel.function, panel.target).symbol_addresses
    if (linked.get('gCharacterSelectCoursePreviewFrameTileMaps') != 0x800b5fc0 or
            linked.get('gCharacterSelectCoursePreviewFrameCornerTileMaps') != 0x800b5fe8):
        raise ValueError('extra selector diagnostics require the actual linked tile/corner addresses')
    selected = cases(panel.cases[0])
    obj = Path(candidate_object)
    assembly = workspace.semantic_assembly(obj.with_name(obj.stem + '_object_dump_normalized.s').read_text(), obj)
    raw = differential.run_suite(panel.target, assembly, selected, target_name=panel.function,
        candidate_name=panel.function + '-selector-diagnostic', call_arities=panel.arities,
        return_registers=panel.returns, max_steps=panel.max_steps, callee_environment=panel.callee_environment)
    classified = [semantic_lane.classify_unknown_direct_arguments(row, panel.call_contracts) for row in raw]
    def tiles(run):
        return [event.raw_arguments[3] for event in run.calls if event.callee == 'drawMenuSpriteTile']
    return {'scope': __doc__, 'cases': [asdict(case) for case in selected],
            'case_sha256': hashlib.sha256(json.dumps([asdict(case) for case in selected], sort_keys=True).encode()).hexdigest(),
            'panel_sha256': panel.identity, 'counts': dict(Counter(row.status for row in classified)),
            'results': [{'case': row.case, 'status': row.status, 'reasons': row.reasons,
                         'target_status': row.target.status, 'candidate_status': row.candidate.status,
                         'target_tiles': tiles(row.target), 'candidate_tiles': tiles(row.candidate),
                         'first_divergence': row.first_divergence}
                        for row in classified]}


if __name__ == '__main__':
    folder = Path(__file__).resolve().parent / 'selection/traversal-pool/drawCharacterSelectCoursePreviewFrame'
    # Bind addresses independently visible in pinned target instruction words:
    # lui/addiu 0x800b:0x5fc0 and lui/lhu 0x800b:0x5fe8.
    text = (folder / 'target-body.s').read_text()
    text += '\n# MIPS_DIFF_SYMBOL gCharacterSelectCoursePreviewFrameTileMaps 0x800b5fc0\n'
    text += '# MIPS_DIFF_SYMBOL gCharacterSelectCoursePreviewFrameCornerTileMaps 0x800b5fe8\n'
    target = differential.Program.parse('drawCharacterSelectCoursePreviewFrame', text)
    selected = cases(differential.TestCase('seed', 0))
    runs = [differential.execute_case(target, case, call_arities={'getRelocatableHeapBlockBase': 1,
        'drawMenuSpriteTile': 6, 'drawMenuSprite': 9}, max_steps=10000) for case in selected]
    output = {'scope': 'Target-only fixture check with explicit opaque call arities; no candidate claim',
              'rows': [{'case': case.name, 'status': run.status, 'error': run.error,
                        'tiles': [call.raw_arguments[3] for call in run.calls if call.callee == 'drawMenuSpriteTile']}
                       for case, run in zip(selected, runs)]}
    (folder / 'selector-target-check.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps(output))
