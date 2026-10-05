"""Record deployment acceptance and refresh the existing maturity assessment."""
from pathlib import Path
import hashlib
import json
import shutil

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
CONTROL = HERE.parent / 'resume-pipeline-20260908'
NATIVE = Path('/home/grant/decomp/runs/resume-pipeline-20260908')
MAP = Path('/mnt/c/Users/grant/.codex/visualizations/2026/10/02/01a0f9f2-c7b1-7d73-9835-0fee3fe422a7/mechanism-maturity.html')


def main():
    result = json.loads((HERE / 'rollout-result.json').read_text())
    assert result['passed'] and result['candidate_status'] == 'integrated'
    snapshot = json.loads((HERE.parent / 'maturity-execution-20261001/latest-snapshot.json').read_text())
    assert snapshot['checkpoint'] >= result['final_checkpoint']
    assert snapshot['summary']['object_exact_or_integrated'] >= result['after']['object_exact_or_integrated']
    assert snapshot['verified_union_count'] >= 35
    assert not any(present for _, present in snapshot['pause_markers'])
    service = json.loads((CONTROL / 'service.json').read_text())
    assert service['status'] == 'running'
    supervisor = Path(f'/proc/{service["pid"]}/cmdline')
    assert supervisor.exists() and b'campaign_service' in supervisor.read_bytes()
    if service.get('worker_pid'):
        worker = Path(f'/proc/{service["worker_pid"]}/cmdline')
        assert worker.exists() and b'eval.fast_campaign' in worker.read_bytes()
    amendment = json.loads((CONTROL / 'revisions/20261002-integration-headers/amendment.json').read_text())
    expected = amendment['changed']['eval/prepare_integration.py']['new_sha256']
    for path in [PROJECT / 'eval/prepare_integration.py', CONTROL / 'code/eval/prepare_integration.py']:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
    assert amendment['unchanged_pins_verified'] == 3340
    losses = snapshot['repair_yield']['totals']['exact_functions_lost']
    assert losses == 0
    private = json.loads((HERE / 'callback-proof.json').read_text())
    assert private['whole_rom_verified'] and not private['imported_into_campaign']
    manifest_path, receipt_path = Path(private['manifest']), Path(private['receipt'])
    private['manifest_sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    private['receipt_sha256'] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    private['private_attempt_ledger'] = '/home/grant/decomp/experiments/integration-callback-20261002-v2/trial.sqlite'
    shutil.copyfile(receipt_path, HERE / 'callback-integration.json')
    private['earlier_harness_run'] = {
        'work': '/home/grant/decomp/experiments/integration-callback-20261002',
        'logged_attempts': 2,
        'reason': 'Harness incorrectly required object-exact before the ordinary source-bound function/ROM integration path; no ROM build or campaign import occurred',
    }
    private['total_logged_attempts_across_runs'] = 4
    (HERE / 'callback-proof.json').write_text(json.dumps(private, indent=2) + '\n')
    shutil.copyfile(HERE.parent / 'maturity-execution-20261001/latest-snapshot.json', HERE / 'accepted-snapshot.json')
    protocol = json.loads((HERE / 'protocol.json').read_text())
    protocol['deployment_status'] = 'Applied at checkpoint 37479; normal controller integrated checkMainMenuSecretCode; unattended supervisor resumed'
    protocol['controller_acceptance'] = result
    protocol['frozen_tests'] = {'passed': 175, 'skipped': 1, 'preexisting_failure_deselected_and_reproduced': 1}
    protocol['private_next_callback_proof'] = 'callback-proof.json; 36-function ROM-exact union; not imported'
    protocol['failed_setup'].append({'directory': '/home/grant/decomp/experiments/integration-headers-20261002-v3', 'reason': 'Successful ROM proof but backlog census exposed 13 preparation regressions; final unavailable-context fallback removed them', 'superseded_by': 'v4'})
    (HERE / 'protocol.json').write_text(json.dumps(protocol, indent=2) + '\n')
    plan_path = HERE.parent / 'maturity-execution-20261001/plan.json'
    plan = json.loads(plan_path.read_text())
    plan['continued_execution'] = {'authorization': protocol['authorization'], 'evidence': '../integration-headers-20261002/rollout-result.json', 'live_summary': snapshot['summary'], 'callback_private_proof': '../integration-headers-20261002/callback-proof.json', 'next_priority': 'Candidate-owned callback value compatibility, with a positive generator test and ordinary controller union gate before promotion'}
    (plan_path).write_text(json.dumps(plan, indent=2) + '\n')
    count = snapshot['summary']['object_exact_or_integrated']
    integrated = snapshot['summary']['integrated']
    pending = snapshot['summary']['function_exact_pending_integration']
    html = MAP.read_text()
    changes = {
        'assessed October 1, 2026': 'updated October 2, 2026',
        'no campaign changes made': 'declaration amendments applied; unattended campaign running',
        'Campaign · checkpoint 37425': f'Campaign · checkpoint {snapshot["checkpoint"]}',
        '1,074 exact or integrated / 2,051': f'{count:,} exact or integrated / 2,051',
        'Not added to the 1,074.': f'Not added to the {count:,}.',
        '33 integrated; 53 function-exact pending': f'{integrated} integrated; {pending} function-exact pending',
        '33 is already within the 1,074. The 53': f'{integrated} is already within the {count:,}. The {pending}',
        '<td>Stopped</td><td>No campaign worker seen at read. Saved health: needs_repair after model endpoint timeout; checkpoint index: paused_budget.</td>': '<td>Running</td><td>Endpoint restored; declaration amendments applied with retained-node checks. Normal supervisor and worker verified after controller integration; zero lost exact functions.</td>',
        '<td>Prepared; no apply receipt found</td>': '<td>Applied at checkpoint 37426</td>',
    }
    for old, new in changes.items():
        assert old in html, old
        html = html.replace(old, new)
    lines = html.splitlines()
    for i, line in enumerate(lines):
        if "{id:'integrate'," in line:
            lines[i] = "      {id:'integrate',layer:'Certification & delivery',name:'ROM integration / shared declarations',delivery:'Working; verified expansion',gap:'large',leverage:'Highest immediate delivery',ceiling:'Safely place exact, linkable C and shared declarations in the real build with a whole-ROM ratchet.',evidence:'Live: " + str(integrated) + " integrated, " + str(pending) + " function-exact pending. Both declaration amendments applied. Unchanged checkMainMenuSecretCode joined the complete ROM-exact union; private callback proposal also passed a 36-function union.',missing:'Callback value compatibility and other shared declarations remain gated. Six literal-form candidates still lack ordinary admission certificates. Private callback proof is not a live match.',next:'Build a guarded callback-value repair with motivating positive tests; preserve direct-call behavior and require source-bound certificates plus the ordinary union ROM gate.',source:'integration-headers-20261002/rollout-result.json; callback-proof.json; PIPELINE_MAP.md'},"
        elif "{n:'0'," in line:
            lines[i] = "      {n:'0',title:'Unattended operation restored',scope:'Completed operational prerequisite',why:'Model endpoint recovered; frozen input identities and retained nodes verified. Supervisor resumed after the integration canary.',action:'Keep the normal controller running and use immutable receipts to distinguish model progress, function certification and complete ROM delivery.',gate:'Bounded work completion, retained receipts and zero lost exact functions verified.',dependency:'Completed; monitor meaningful changes.'},"
        elif "{n:'1'," in line:
            lines[i] = "      {n:'1',title:'Close remaining certification → integration gaps',scope:'Highest demonstrated delivery leverage',why:'" + str(pending) + " function-exact candidates remain pending. Active-header support integrated one unchanged candidate; callback proposal has a private 36-function ROM proof.',action:'Next build guarded candidate-owned callback value compatibility. Then recover linkable label/data representations and resolve source-bound literal admission.',gate:'Positive motivating tests, source-bound receipts, zero new preparation blocks and byte equality of the complete retained ROM union.',dependency:'Both declaration amendments applied. Private proofs remain separate from controller promotion.'},"
    html = '\n'.join(lines) + '\n'
    html = html.replace('      </tbody>\n    </table></div>\n  </div>\n  <script>', '        <tr><td>Active header conflicts</td><td><code>20261002-integration-headers</code></td><td>Applied at checkpoint 37479; controller ROM gate passed</td></tr>\n      </tbody>\n    </table></div>\n  </div>\n  <script>')
    MAP.write_text(html)
    acceptance = {'passed': True, 'snapshot_checkpoint': snapshot['checkpoint'], 'summary': snapshot['summary'], 'supervisor_pid': service['pid'], 'worker_pid': service.get('worker_pid'), 'source_sha256': expected, 'unchanged_pins': 3340, 'exact_losses': losses, 'tests_passed': 175, 'private_callback_union': 36, 'callback_imported': False}
    (HERE / 'acceptance.json').write_text(json.dumps(acceptance, indent=2) + '\n')
    print(json.dumps(acceptance, indent=2))


if __name__ == '__main__':
    main()
