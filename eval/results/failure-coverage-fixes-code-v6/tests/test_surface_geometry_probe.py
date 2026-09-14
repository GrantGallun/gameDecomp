import importlib


def test_surface_fixture_covers_orientations_flags_and_nonempty_records():
    probe=importlib.import_module('eval.experiments.campaign-gap-audit.surface_geometry_probe')
    rows=probe.cases()
    assert len(rows)==28
    assert len({row.name for row in rows})==28
    assert {dict((off,value) for off,width,value in row.player_writes)[22] for row in rows}=={1}
    assert {dict((off,value) for off,width,value in row.global_writes)['@arg1+0x7'] for row in rows}=={0,1}
    assert {dict((off,value) for off,width,value in row.global_writes)['@arg1+0x2'] for row in rows}=={1,2}
    assert all(len(row.entry_registers)==3 for row in rows)
    assert all(len(row.global_writes)==16 for row in rows)
