import pytest
from solver import dataflow


@pytest.mark.parametrize('opcode',['mfc0','mfc1','mfc2','dmfc0','dmfc1','dmfc2','cfc0','cfc1','cfc2'])
def test_coprocessor_read_kills_stale_argument(opcode):
    flow=dataflow.analyse(f'''{opcode} a2, f8
jalr t9
nop
''')
    call=next(iter(flow.callsites.values()))
    assert call.arguments[2] is None
    assert call.arguments[3]==dataflow.Value.address('param3')


def test_delay_slot_coprocessor_read_kills_call_argument():
    flow=dataflow.analyse('jalr t9\nmfc1 a2, f8\n')
    assert next(iter(flow.callsites.values())).arguments[2] is None
