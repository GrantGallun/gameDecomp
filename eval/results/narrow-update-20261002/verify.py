"""Capture reproducible focused checks and the current native validation limit."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
files = ['tests/test_narrow_update.py', 'tests/test_narrow_update_semantics.py',
         'tests/test_regalloc_mutations.py', 'tests/test_regalloc_search.py',
         'tests/test_regalloc_evolvability.py', 'tests/test_scalar_coalesce.py',
         'tests/test_scoped_field.py']
command = [sys.executable, '-m', 'pytest', '-q', *files,
           '--basetemp=.pytest-narrow-update-receipt', '--tb=short']
run = subprocess.run(command, cwd=PROJECT, capture_output=True, text=True, timeout=60)
(HERE / 'verification-tests.log').write_text(run.stdout + run.stderr)
passed = re.search(r'(\d+) passed', run.stdout)
service = subprocess.run(['powershell', '-NoProfile', '-Command',
                          '(Get-Service WslService).Status.ToString()'],
                         capture_output=True, text=True, timeout=10) if sys.platform == 'win32' else None
pins = ['solver/narrow_update.py', 'solver/regalloc_mutations.py', 'solver/regalloc_search.py',
        *files, 'eval/results/narrow-update-20261002/native_replay.py',
        'eval/results/narrow-update-20261002/protocol.json']
result = {
    'status': 'focused_checks_passed_native_validation_pending' if run.returncode == 0 else 'focused_checks_failed',
    'focused_command': command, 'returncode': run.returncode,
    'passed': int(passed[1]) if passed else None,
    'host_behavior': {'byte_values': 256, 'halfword_values_per_counter': 65536,
                      'counters_tested_separately': 2, 'all_pairs_tested': False,
                      'compiler': 'host Clang; not IDO matching validation'},
    'native': {'wsl_service': service.stdout.strip() if service else 'not queried',
               'new_generator_object_certificates': 0, 'new_generator_rom_checks': 0,
               'development_panel_frozen': False, 'development_panel_run': False,
               'default_activation': False, 'campaign_deployment': False},
    'review': {'independent_reviewer': 'review_narrow_update',
               'resolved_with_regressions': ['rest-of-final-expression effects', 'nested writes',
                   'declared integer address-base writes', 'member names mistaken for local references',
                   'indirect calls during index forwarding', 'bare dereference postfix precedence']},
    'broader_suite': {
        'bare_pytest': '11 collection errors on Windows, 2 skipped; full suite not passing',
        'collection_error_files': [
            'external/snowboardkids-decomp/tools/test_' + name + '.py' for name in
            ['course_definitions', 'course_display_list', 'course_graphics', 'course_sprite_table',
             'course_surface_data', 'find_inconsistent_global_types', 'find_oversized_symbols',
             'find_struct_unions', 'fix_redundant_externs', 'sk1_to_sk2_course']
        ] + ['tests/test_campaign_service.py'],
        'causes': 'External tools import namespace conflicts and missing Linux fcntl on Windows',
        'main_tests_without_campaign_service': 'Interrupted after progress stopped; complete result unverified'},
    'source_sha256': {p: hashlib.sha256((PROJECT / p).read_bytes()).hexdigest() for p in pins},
    'training_eligible': False, 'native_discovery_claim': False,
    'verified_at_utc': datetime.now(timezone.utc).isoformat(),
}
(HERE / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: result[k] for k in ('status', 'passed', 'native')}, indent=2))
assert run.returncode == 0, run.stdout + run.stderr
