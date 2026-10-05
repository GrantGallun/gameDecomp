"""Campaign operand repair: accepted edits, lineage, and retention gates."""
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from solver import workspace


def _fixture(tmp_path, source):
    repo = tmp_path / 'repo'
    ws = repo / 'nonmatchings' / 'audioThreadMain'
    ws.mkdir(parents=True)
    (ws / 'target.s').write_text('glabel audioThreadMain\n  jr ra\n  nop\nendlabel audioThreadMain\n')
    (repo / 'build').mkdir()
    (repo / 'build/snowboardkids.map').write_text('')
    source_path = tmp_path / 'incumbent.c'
    source_path.write_text(source)
    db = tmp_path / 'private.sqlite'
    conn = sqlite3.connect(db)
    conn.executescript((Path(__file__).resolve().parents[1] / 'kb/schema.sql').read_text())
    conn.execute('insert into tus(id,name) values(1,?)', ('src/audio.c',))
    conn.execute('insert into functions(addr,name,tu_id,state) values(?,?,?,?)',
                 (0x8009FC0C, 'audioThreadMain', 1, 'attempted'))
    conn.commit()
    incumbent = workspace.Attempt(True, 97.778, False,
                                  '@@ -70,7 +70,7 @@\n-li s0,1\n+move s0,s4\n', '', '',
                                  frontend={'passed': True})
    parent_id = workspace.record_attempt(conn, 'audioThreadMain', source, incumbent,
                                         strategy='native-retained', run_id='test-native')
    conn.close()
    node = {'status': 'pending', 'source': str(source_path),
            'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
            'attempt_id': parent_id, 'score': 97.778,
            'residual': {'compiled': True, 'frontend': {'passed': True}},
            'verification': None,
            'semantic_validation': {'status': 'unavailable',
                                    'source_sha256': hashlib.sha256(source.encode()).hexdigest()}}
    return repo, ws, db, node


def _compiler(monkeypatch, ws, judge):
    """Replace only the external compile; real attempt logging remains active."""
    monkeypatch.setattr(workspace, 'bootstrap', lambda repo, function: ws)
    observed = []

    def score(_ws, _repo, _name, code, conn=None, func='', **metadata):
        compiled, value, exact, passed = judge(code)
        att = workspace.Attempt(compiled, value, exact,
                                '@@ -70,7 +70,7 @@\n-li s0,1\n+move s0,s4\n' if compiled else '',
                                'synthetic compile failure' if not compiled else '', '',
                                verification={'exact': exact, 'status': 'object_sections_exact'} if exact else None,
                                frontend={'passed': passed} if compiled else None)
        if conn is not None:
            workspace.record_attempt(conn, func, code, att, **metadata)
        observed.append((code, att))
        return att

    monkeypatch.setattr(workspace, 'score', score)
    return observed


def test_audio_residual_generates_and_certifies_local_type(monkeypatch, tmp_path):
    # Break caught: the operand stream stops offering the measured type edit.
    from eval import operand_repair

    source = 'void audioThreadMain(void) {\n    s32 var_s0;\n    var_s0 = 0;\n}\n'
    repo, ws, db, node = _fixture(tmp_path, source)
    _compiler(monkeypatch, ws, lambda code: (True, 100.0, True, True)
              if 'u32 var_s0;' in code else (True, 97.778, False, True))
    monkeypatch.setattr(operand_repair.rodata_symbol, 'variants', lambda *a, **k: ())
    monkeypatch.setattr(operand_repair.diffrepair, 'repair', lambda code, diff: (code, False, {}))

    result = operand_repair.run(repo=repo, db=db, function='audioThreadMain', node=node,
                                out=tmp_path / 'audio.json')

    assert result['exact'] is True
    assert result['score'] == 100.0
    assert 'u32 var_s0;' in Path(result['source']).read_text()
    assert result['verification']['status'] == 'object_sections_exact'
    assert result['semantic_validation'] is None
    conn = sqlite3.connect(db)
    exact = conn.execute('select id,parent_attempt_id,strategy from attempts where exact=1').fetchone()
    assert exact[0] == result['attempt_id']
    assert exact[1] != node['attempt_id']  # child of the logged baseline, not an invented root
    assert 'local_type' in exact[2]
    assert conn.execute('select count(*) from attempt_edges where child_attempt_id=?', (exact[0],)).fetchone()[0] == 1


def test_worker_log_receipts_remap_after_other_worker_import(monkeypatch, tmp_path):
    from eval import campaign_workers, operand_repair

    source = 'void audioThreadMain(void) {\n    s32 var_s0;\n}\n'
    repo, ws, db, node = _fixture(tmp_path, source)
    _compiler(monkeypatch, ws, lambda code: (True, 97.778, False, True))
    result = operand_repair.run(repo=repo, db=db, function='audioThreadMain', node=node,
                                out=tmp_path / 'audio.json', budget=0)
    with sqlite3.connect(db) as conn:
        worker_ids = [row[0] for row in conn.execute('select id from attempts order by id')]
    # A previous worker's import consumed canonical IDs, so these IDs must move.
    mapping = {old: old + 1000 for old in worker_ids}
    merged = campaign_workers.remap(result, mapping, {})
    assert merged['log'][0]['receipt_id'] == mapping[worker_ids[-1]]
    assert merged['log'][0]['parent_attempt_id'] == mapping[node['attempt_id']]
    assert 'receipt' not in merged['log'][0]


def test_rejected_exact_and_compile_failure_keep_incumbent_but_log_children(monkeypatch, tmp_path):
    # Break caught: raw object exactness or a failed compile displaces working C.
    from eval import operand_repair

    source = 'void audioThreadMain(void) {\n    s32 var_s0;\n}\n'
    repo, ws, db, node = _fixture(tmp_path, source)
    _compiler(monkeypatch, ws, lambda code: (False, 0.0, False, None)
              if 'failure' in code else
              (True, 100.0, True, False) if 'bad_exact' in code else
              (True, 97.778, False, True))
    monkeypatch.setattr(operand_repair.rodata_symbol, 'variants', lambda *a, **k: ())
    monkeypatch.setattr(operand_repair.diffrepair, 'repair', lambda code, diff: (code, False, {}))
    monkeypatch.setattr(operand_repair.regalloc_mutations, 'variants',
                        lambda code, *a, **k: iter((('bad_exact', 'local_type', code + ' bad_exact'),
                                                   ('failure', 'local_type', code + ' failure'))))

    result = operand_repair.run(repo=repo, db=db, function='audioThreadMain', node=node,
                                out=tmp_path / 'audio.json', budget=2)

    assert result['attempt_id'] == node['attempt_id']
    assert result['source_sha256'] == node['source_sha256']
    assert result['exact'] is False
    assert result['semantic_validation'] == node['semantic_validation']
    conn = sqlite3.connect(db)
    rows = conn.execute('select id,parent_attempt_id,compiled,exact from attempts order by id').fetchall()
    baseline = rows[1]
    assert baseline[1] == node['attempt_id']
    assert rows[2][1] == baseline[0]
    assert rows[3][1] == baseline[0]
    assert rows[2][2:] == (1, 1)
    assert rows[3][2:] == (0, 0)
    assert result['proposal_compiles'] == 2


def test_budget_caps_logged_proposals(monkeypatch, tmp_path):
    # Break caught: generator breadth overruns the explicit controller budget.
    from eval import operand_repair

    source = 'void audioThreadMain(void) {\n    s32 var_s0;\n}\n'
    repo, ws, db, node = _fixture(tmp_path, source)
    _compiler(monkeypatch, ws, lambda code: (True, 97.778, False, True))
    monkeypatch.setattr(operand_repair.rodata_symbol, 'variants', lambda *a, **k: ())
    monkeypatch.setattr(operand_repair.diffrepair, 'repair', lambda code, diff: (code, False, {}))
    monkeypatch.setattr(operand_repair.regalloc_mutations, 'variants',
                        lambda code, *a, **k: ((str(i), 'local_type', code + str(i)) for i in range(20)))

    result = operand_repair.run(repo=repo, db=db, function='audioThreadMain', node=node,
                                out=tmp_path / 'audio.json', budget=3)

    assert result['proposal_compiles'] == 3
    assert sqlite3.connect(db).execute('select count(*) from attempts').fetchone()[0] == 5


def test_incumbent_hash_must_match_durable_attempt(monkeypatch, tmp_path):
    # Break caught: a changed source path is treated as the retained candidate.
    from eval import operand_repair

    source = 'void audioThreadMain(void) {\n    s32 var_s0;\n}\n'
    repo, ws, db, node = _fixture(tmp_path, source)
    Path(node['source']).write_text(source + 'changed')
    _compiler(monkeypatch, ws, lambda code: (True, 97.778, False, True))

    with pytest.raises(ValueError, match='source hash'):
        operand_repair.run(repo=repo, db=db, function='audioThreadMain', node=node,
                           out=tmp_path / 'audio.json')
    assert sqlite3.connect(db).execute('select count(*) from attempts').fetchone()[0] == 1


def test_address_taken_rodata_is_proposed_from_the_incumbents_object(monkeypatch, tmp_path):
    # Break caught: the address-taken branch never reaches the stream (it needs the incumbent's object, which the
    # diff-driven generators do not), so functions whose .text already matches stay parked with no proposal.
    from eval import operand_repair

    source = 'void audioThreadMain(void) {\n    s32 var_s0;\n    var_s0 = 0;\n}\n'
    repo, ws, db, node = _fixture(tmp_path, source)
    named = source.replace('var_s0 = 0;', 'var_s0 = (s32)D_800E1070;')
    _compiler(monkeypatch, ws, lambda code: (True, 100.0, True, True)
              if 'D_800E1070' in code else (True, 97.778, False, True))
    monkeypatch.setattr(operand_repair.rodata_symbol, 'variants', lambda *a, **k: ())
    seen = []

    def address_variants(src, function, *, target_obj, candidate_obj):
        seen.append((target_obj.name, candidate_obj.name.startswith('audioThreadMain_operand_')))
        yield 'rodata_address:D_800E1070', named
    monkeypatch.setattr(operand_repair.rodata_symbol, 'address_variants', address_variants)
    monkeypatch.setattr(operand_repair.diffrepair, 'repair', lambda code, diff: (code, False, {}))

    result = operand_repair.run(repo=repo, db=db, function='audioThreadMain', node=node,
                                out=tmp_path / 'address.json')

    assert seen and seen[0] == ('target.o', True)
    assert result['exact'] is True
    assert any(row.get('label') == 'rodata_address:D_800E1070' for row in result['log'])


def test_proposals_include_named_global_relocation_repairs_but_not_literal_externs(tmp_path):
    # 2026-09-30: relocation_names had no caller. field_names fires on updateRaceSetupPlayerCountPrompt's residual;
    # literal_names is excluded because its extern D_800E... form cannot link (fadeInRaceGameplayViewports).
    from types import SimpleNamespace
    from eval import operand_repair
    FIELD_SOURCE = ('extern RaceSetupMenuSubState gRaceSetupMenuSubState;\n'
                    'void updateRaceSetupPlayerCountPrompt(Actor *actor) {\n'
                    '    gRaceSetupMenuSubState.state = actor->state;\n'
                    '    gRaceSetupMenuSubState.alpha = actor->alpha;\n}\n')
    FIELD_DIFF = ('@@ -1,6 +1,6 @@\n-lui    at,%hi(gRaceSetupPlayerCountPromptAlpha)\n'
                  '-sh    t7,%lo(gRaceSetupPlayerCountPromptAlpha)(at)\n'
                  '+lui    at,%hi(gRaceSetupMenuSubState)\n'
                  '+sh    t7,%lo(gRaceSetupMenuSubState+2)(at)\n')
    attempt = SimpleNamespace(diff=FIELD_DIFF, source_attribution=None, frontend=None, compiler_recipe=None, verification=None)
    log = []
    out = operand_repair._proposals(FIELD_SOURCE, 'updateRaceSetupPlayerCountPrompt', attempt, repo=tmp_path, ws=tmp_path,
                                    map_text='', log=log)
    names = [label for label, kind, _ in out if kind == 'relocation_name']
    assert any(label.startswith('field_names:gRaceSetupMenuSubState.alpha') for label in names)
    literal = ('void f(void) { g(4.0f / 3.0f); }\n',
               '@@ -1,2 +1,2 @@\n-lui    at,%hi(D_800E10C4)\n-lwc1    $f4,%lo(D_800E10C4)(at)\n+lui    at,%hi(.rodata)\n+lwc1    $f4,%lo(.rodata)(at)\n')
    out = operand_repair._proposals(literal[0], 'f', SimpleNamespace(**{**attempt.__dict__, 'diff': literal[1]}),
                                    repo=tmp_path, ws=tmp_path, map_text='', log=[])
    assert not [1 for label, kind, _ in out if kind == 'relocation_name']
