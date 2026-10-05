"""Recover the two files' task-only diff from their observed pre-task state."""
from pathlib import Path
import difflib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
BEFORE = ROOT / 'eval/results/delivery-20260926/before-main'

# Before this task, main fast_campaign was byte-identical to the frozen file.
fast_before = BEFORE / 'eval/fast_campaign.py'
frozen_fast = ROOT / 'eval/results/resume-pipeline-20260908/code/eval/fast_campaign.py'
if fast_before.read_bytes() != frozen_fast.read_bytes():
    raise RuntimeError('frozen fast controller changed before deployment')

# The pre-existing test file had the three cache/artifact tests below; all of
# this task's tests were inserted as one contiguous block after state().
tests = ROOT / 'tests/test_campaign_fast.py'
source = tests.read_text(encoding='utf-8')
start = source.index('def test_deterministic_dispatch_filters_model_jobs_in_pipeline_and_wave')
end = source.index('def test_incremental_roundtrip_and_previous_pointer', start)
pre = source[:start] + source[end:]
if not all(name in pre for name in (
    'def test_build_artifacts_are_only_what_this_build_wrote',
    'def test_artifact_encoding_roundtrips_and_old_hex_entries_still_replay',
    'def test_compile_cache_excludes_workspace_debris_and_replays_old_entries')):
    raise RuntimeError('pre-existing cache/artifact tests were not preserved')
test_before = BEFORE / 'tests/test_campaign_fast.py'
test_before.parent.mkdir(parents=True, exist_ok=True)
test_before.write_text(pre, encoding='utf-8')

patch = []
for name, old, new in (
    ('eval/fast_campaign.py', fast_before, ROOT / 'eval/fast_campaign.py'),
    ('tests/test_campaign_fast.py', test_before, tests),
):
    patch.extend(difflib.unified_diff(old.read_text(encoding='utf-8').splitlines(True),
                                      new.read_text(encoding='utf-8').splitlines(True),
                                      fromfile='a/' + name, tofile='b/' + name))
(HERE / 'task4-only.patch').write_text(''.join(patch), encoding='utf-8')
print('Captured pre-task test file and task4-only.patch')
