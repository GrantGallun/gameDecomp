"""Commands consume real JSON inputs and leave replayable experiment artifacts."""
import json

import pytest

from eval.research_suite.__main__ import main


def test_panel_command_retains_counterexample_for_next_candidate_batch(tmp_path, capsys):
    target = 'lw t0,0(a0)\nsw t0,4(a0)\njr ra\nnop'
    masked = 'lw t0,0(a0)\nandi t0,t0,1\nsw t0,4(a0)\njr ra\nnop'
    request = {'function': 'f', 'target_assembly': target,
               'candidates': [{'id': 'masked', 'source': 'synthetic', 'assembly': masked}],
               'base_cases': [{'name': 'one', 'seed': 7, 'player_writes': [[0, 4, 1]]}],
               'extra_cases': [{'name': 'two', 'seed': 7, 'player_writes': [[0, 4, 2]]}]}
    input_path, output = tmp_path / 'input.json', tmp_path / 'panel.json'
    input_path.write_text(json.dumps(request))
    main(['panels', '--input', str(input_path), '--output', str(output)])
    result = json.loads(output.read_text())
    assert result['base_pass_extra_checked'] == result['base_pass_extra_falsified'] == 1
    assert result['retained_cases'][1]['player_writes'] == [[0, 4, 2]]
    with pytest.raises(FileExistsError):
        main(['panels', '--input', str(input_path), '--output', str(output)])


def test_proposals_command_emits_two_step_source_and_evidence_constraints(tmp_path, capsys):
    request = {'function': 'f', 'source': 'unsigned int f(unsigned int x) { return (x + 1U) + 2U; }'}
    source, output = tmp_path / 'input.json', tmp_path / 'proposals.json'
    source.write_text(json.dumps(request))
    main(['proposals', '--input', str(source), '--output', str(output)])
    result = json.loads(output.read_text())
    assert any(len(p['path']) == 2 and 'x + 3U' in p['source'] for p in result['composed'])
    assert result['types'] == []
