from solver import hardware_environment as h
from eval import semantic_lane
from types import SimpleNamespace
import importlib


def test_register_macro_and_memory_operand_both_required(tmp_path):
    header = tmp_path/'include/PR/rcp.h'
    header.parent.mkdir(parents=True)
    header.write_text('#define SI_DATA_REG (BASE+4)\n#define OTHER_REG 12\n')
    assembly = 'lui t0,%hi(SI_DATA_REG)\nsw v0,%lo(SI_DATA_REG)(t0)'
    report = h.obligations(tmp_path, assembly)
    assert [r['symbol'] for r in report['register_references']] == ['SI_DATA_REG']
    assert report['header_sha256'] and report['assembly_sha256']
    assert h.obligations(tmp_path, 'lui t0,%hi(SI_DATA_REG)') is None
    assert h.obligations(tmp_path, 'sw v0,%lo(UNKNOWN_REG)(t0)') is None


def test_deferred_panel_preserves_structured_environment_evidence(monkeypatch, tmp_path):
    evidence = {'kind':'hardware-register-environment-required',
                'register_references':[{'symbol':'SI_DATA_REG'}]}
    def factory(*args):
        raise h.Required(evidence)
    monkeypatch.setattr(semantic_lane, 'Panel', factory)
    panel = semantic_lane.DeferredPanel(tmp_path,tmp_path,'f')
    state = SimpleNamespace(source='int f(void) {return 0;}',
        attempt=SimpleNamespace(compiled=True,frontend={'passed':True}))
    result = panel(state)
    assert result['status'] == 'unavailable'
    assert result['environment_obligations'] == evidence
    assert not result['authoritative']


def test_campaign_accounting_routes_obligation_without_claiming_completion():
    audit = importlib.import_module('eval.experiments.campaign-gap-audit.summarize')
    evidence = {'kind':'hardware-register-environment-required',
        'next_action':'admit device effects', 'register_references':[{'symbol':'SI_DATA_REG'}]}
    report = audit.accounting({'semantic_validation':{'status':'unavailable',
        'environment_obligations':evidence}}, 'semantic_untested_or_unavailable', '')
    assert report['findings'][0]['accounting_status'] == 'explicit_environment_gap'
    assert report['findings'][0]['evidence'] == evidence
    assert not report['causal_accounting_complete']
