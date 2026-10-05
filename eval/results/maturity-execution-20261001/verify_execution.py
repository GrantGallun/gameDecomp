"""Verify the first execution milestone from fresh receipts and process state."""
from pathlib import Path
import hashlib
import json
import time

import snapshot

HERE = Path(__file__).resolve().parent
CONTROL = snapshot.CONTROL
REVISION = CONTROL / 'revisions/20261001-preparer-names'


def main():
    snapshot.main()
    current = json.loads((HERE / 'latest-snapshot.json').read_bytes())
    baseline = json.loads((HERE / 'baseline-snapshot.json').read_bytes())
    amendment = json.loads((REVISION / 'amendment.json').read_bytes())
    tests = json.loads((REVISION / 'stage-test.json').read_bytes())
    stage = (REVISION / 'stage.json').read_bytes()
    installed = {rel: hashlib.sha256((CONTROL / 'code' / rel).read_bytes()).hexdigest()
                 == row['new_sha256'] for rel, row in amendment['changed'].items()}
    service = current['service']
    active = {}
    for role in ('pid', 'worker_pid'):
        pid = service[role]
        command = Path(f'/proc/{pid}/cmdline')
        active[role] = command.exists() and (
            b'campaign_service' if role == 'pid' else b'eval.fast_campaign') in command.read_bytes()
    checks = {
        'fresh_tests_passed_for_staged_manifest': tests['passed'] and tests['manifest_sha256']
                                                == hashlib.sha256(stage).hexdigest(),
        'four_installed_files_match_reviewed_amendment': len(installed) == 4 and all(installed.values()),
        'checkpoint_advanced_after_install': current['checkpoint'] > amendment['result']['commit'],
        'object_exact_ratchet_preserved': current['summary']['object_exact_or_integrated']
                                        >= baseline['summary']['object_exact_or_integrated'],
        'previous_verified_union_preserved': set(baseline['integration']['verified_union'])
                                            <= set(current['integration']['verified_union']),
        'fresh_integration_finished_rom_exact': current['integration']['status'] == 'rom_exact'
                                               and current['integration']['checkpoint']
                                               >= amendment['result']['commit'],
        'completed_repair_item_after_restart': current['performance']['completed_items']
                                             > baseline['performance']['completed_items'],
        'revised_operand_lane_completed': current['repair_yield']['by_profile']
            .get('operand_repair@2bd1874f065432fd', {}).get('work_items', 0)
            > baseline['repair_yield']['by_profile']
            .get('operand_repair@2bd1874f065432fd', {}).get('work_items', 0),
        'no_new_exact_losses': current['repair_yield']['totals']['exact_functions_lost']
                              == baseline['repair_yield']['totals']['exact_functions_lost'],
        'both_pause_markers_cleared': not any(exists for _, exists in current['pause_markers']),
        'supervisor_and_worker_alive': all(active.values()),
        'service_running_without_retry_failure': service['status'] == 'running'
                                                and service['consecutive_failures'] == 0,
    }
    report = {'verified_at': time.time(), 'passed': all(checks.values()), 'checks': checks,
              'checkpoint': current['checkpoint'], 'summary': current['summary'],
              'completed_repair_items_since_restart': current['performance']['completed_items']
                                                       - baseline['performance']['completed_items'],
              'integrated_delta': current['summary']['integrated'] - baseline['summary']['integrated'],
              'exact_delta': current['summary']['object_exact_or_integrated']
                             - baseline['summary']['object_exact_or_integrated'],
              'scope': 'First execution milestone; ongoing campaign and future research remain open'}
    (HERE / 'execution-verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
