"""A proposed execution case is evidence only after the target completes."""

from pathlib import Path

import pytest

from solver import execution_experiment


class Panel:
    target = 'addiu v0,a1,1\njr ra\nnop\n'
    arities = {}
    returns = ('v0',)
    max_steps = 32
    callee_environment = None
    identity = 'fixed-target-panel'


def object_with_assembly(folder: Path, name: str, assembly: str) -> Path:
    obj = folder / f'{name}.o'
    obj.write_bytes(b'object identity')
    (folder / f'{name}_object_dump_normalized.s').write_text(assembly)
    return obj


def test_target_failure_is_recorded_but_not_reused(tmp_path):
    panel = Panel()
    panel.target = 'lw v0,0(a0)\njr ra\nnop\n'
    obj = object_with_assembly(tmp_path, 'child', panel.target)
    tool = execution_experiment.Tools(tmp_path, tmp_path, 'f', tmp_path / 'receipts', panel)

    result = tool.propose({'entry_registers': [['a0', 0]]}, 's32 f(void) { return 0; }', obj)

    assert result['status'] == 'target_inconclusive'
    assert result['target']['status'] != 'returned'
    assert result['candidate'] is None
    assert tool.cases == ()
    assert result['receipt'].endswith('.json')


def test_divergent_case_replays_on_next_candidate(tmp_path):
    wrong = object_with_assembly(tmp_path, 'wrong', 'addiu v0,a1,2\njr ra\nnop\n')
    right = object_with_assembly(tmp_path, 'right', Panel.target)
    tool = execution_experiment.Tools(tmp_path, tmp_path, 'f', tmp_path / 'receipts', Panel())

    first = tool.propose({'entry_registers': [['a1', 7]]}, 'wrong source', wrong)
    assert first['status'] == 'observed_failure'
    assert first['candidate']['return_values']['v0'] == '0x00000009'
    assert first['target']['return_values']['v0'] == '0x00000008'
    assert first['candidate_assembly_sha256']
    assert len(tool.cases) == 1

    reused = tool.evaluate('next source', right)
    assert reused['status'] == 'observed_pass_with_execution_debt'
    assert reused['cases'][0]['input'] == first['input']
    assert reused['cases'][0]['comparison'] == 'passed'
    assert reused['source_sha256'] != first['source_sha256']
    assert 'synthetic' in reused['scope']
    assert reused['domain_validated'] is False
    assert reused['candidate_binding'] == 'caller-supplied source and object; hashes are identity only'
    assert reused['execution_debt']


def test_case_schema_rejects_expected_outputs_and_invented_memory(tmp_path):
    tool = execution_experiment.Tools(tmp_path, tmp_path, 'f', tmp_path / 'receipts', Panel())
    obj = object_with_assembly(tmp_path, 'child', Panel.target)
    with pytest.raises(ValueError, match='unsupported case field'):
        tool.propose({'entry_registers': [['a1', 7]], 'expected_return': 8}, 'src', obj)
    with pytest.raises(ValueError, match='admitted writable RAM'):
        tool.propose({'global_writes': [['InventedRegister', 4, 1]]}, 'src', obj)
    unavailable = execution_experiment.Tools(tmp_path, tmp_path, 'f', tmp_path / 'no-panel')
    with pytest.raises(ValueError, match='unsupported case field'):
        unavailable.propose({'expected_return': 8}, 'src', obj)
    assert tool.cases == ()


@pytest.mark.parametrize('payload', [
    {'player_writes': [[0, 1.0, 1]]},
    {'player_writes': [[0, True, 1]]},
    {'player_writes': [[0, 2, 1.0]]},
    {'player_writes': [[0.0, 2, 1]]},
    {'player_writes': [None]},
    {'player_writes': [0]},
    {'global_writes': [None]},
    {'entry_registers': [[['a0'], 1]]},
    {'entry_registers': [['a0', 1.0]]},
    {'entry_registers': [None]},
])
def test_malformed_case_values_decline_with_value_error(tmp_path, payload):
    tool = execution_experiment.Tools(tmp_path, tmp_path, 'f', tmp_path / 'receipts', Panel())
    with pytest.raises(ValueError):
        tool.propose(payload, 'source', None)
    assert not (tmp_path / 'receipts').exists()


def test_bad_candidate_dump_keeps_completed_target_case(tmp_path):
    bad = object_with_assembly(tmp_path, 'bad', 'invalid opcode here\n')
    tool = execution_experiment.Tools(tmp_path, tmp_path, 'f', tmp_path / 'receipts', Panel())

    result = tool.propose({'entry_registers': [['a1', 7]]}, 'bad candidate', bad)

    assert result['status'] == 'inconclusive'
    assert result['target']['status'] == 'returned'
    assert len(tool.cases) == 1


def test_unwraps_existing_semantic_panel_wrappers(tmp_path):
    class Wrapper:
        def __init__(self, base):
            self.base = base

    obj = object_with_assembly(tmp_path, 'child', Panel.target)
    tool = execution_experiment.Tools(tmp_path, tmp_path, 'f', tmp_path / 'receipts',
                                      Wrapper(Wrapper(Panel())))
    result = tool.propose({'entry_registers': [['a1', 7]]}, 'source', obj)
    assert result['status'] == 'observed_pass_with_execution_debt'


@pytest.mark.parametrize('name,address,extent', [
    ('D_A4400000', '0xA4400000', 256),  # hardware MMIO
    ('f', '0x80000000', 256),           # function text
    ('gUnknownExtent', '0x80010000', None),
])
def test_global_writes_need_independent_writable_ram_extent(tmp_path, name, address, extent):
    panel = Panel()
    panel.target = (f'# MIPS_DIFF_SYMBOL {name} {address}\n'
                    + (f'# MIPS_DIFF_EXTENT {name} {extent}\n' if extent else '')
                    + panel.target)
    obj = object_with_assembly(tmp_path, 'child', panel.target)
    tool = execution_experiment.Tools(tmp_path, tmp_path, 'f', tmp_path / 'receipts', panel)

    with pytest.raises(ValueError, match='admitted writable RAM'):
        tool.propose({'global_writes': [[name, 4, 1]]}, 'source', obj)
    assert tool.cases == ()


def test_completed_case_records_synthetic_scope_and_existing_execution_debt(tmp_path):
    panel = Panel()
    panel.debt = ['opaque callee contract unresolved']
    panel.execution_obstructions = [{'status': 'unsupported', 'error': 'outside model'}]
    obj = object_with_assembly(tmp_path, 'child', panel.target)
    tool = execution_experiment.Tools(tmp_path, tmp_path, 'f', tmp_path / 'receipts', panel)

    result = tool.propose({'entry_registers': [['a1', 0xffffffff]]}, 'source', obj)

    assert result['status'] == 'observed_pass_with_execution_debt'
    assert result['target']['status'] == 'returned'
    assert result['domain_validated'] is False
    assert 'synthetic' in result['scope']
    assert 'opaque callee contract unresolved' in result['execution_debt']
    assert result['execution_obstructions'] == panel.execution_obstructions
    assert result['candidate_binding'] == 'caller-supplied source and object; hashes are identity only'
    assert result['target']['receipt_limits']['instruction_trace'] == 200
    assert result['target']['instruction_trace_count'] >= len(result['target']['instruction_trace'])
