import pytest

from solver import target_intake, workspace


def test_symbol_alias_resolution_never_needs_reference_c(tmp_path):
    (tmp_path / 'asm').mkdir()
    (tmp_path / 'symbol_addrs.txt').write_text('alias = 0x8000;\ncanonical = 0x8000;\n')
    assembly = tmp_path / 'asm' / 'unrelated-filename.s'
    assembly.write_text('glabel canonical\njr ra\nnop\nendlabel canonical\n')
    found = target_intake.resolve(tmp_path, 'alias')
    assert (found.symbol, found.address, found.path) == ('canonical', 0x8000, assembly)
    assert found.kind == 'disassembly'


def test_ambiguous_alias_is_not_selected_arbitrarily(tmp_path):
    (tmp_path / 'asm').mkdir()
    (tmp_path / 'symbol_addrs.txt').write_text('alias = 0x8000;\na = 0x8000;\nb = 0x8000;\n')
    for name in ('a', 'b'):
        (tmp_path / 'asm' / (name + '.s')).write_text('glabel ' + name + '\n')
    with pytest.raises(RuntimeError, match='ambiguous'):
        target_intake.resolve(tmp_path, 'alias')


def test_sdk_assembly_is_explicitly_routed_not_reported_as_missing(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/cache.s').write_text('LEAF(cacheFlush)\ncache 0,0(a0)\n')
    assert target_intake.resolve(tmp_path, 'cacheFlush').kind == 'sdk_assembly'
    with pytest.raises(target_intake.AssemblyBackendRequired, match='separate assembly/hardware backend'):
        target_intake.bootstrap_resolved(tmp_path, 'cacheFlush')


@pytest.mark.parametrize('name', ['../bad', 'x;touch file', 'x y', ''])
def test_workspace_rejects_non_identifiers_before_shell(tmp_path, name):
    with pytest.raises(ValueError):
        workspace.bootstrap(tmp_path, name)


def test_fallback_preserves_existing_incomplete_workspace(tmp_path):
    (tmp_path / 'asm').mkdir()
    (tmp_path / 'asm/f.s').write_text('glabel f\n')
    existing = tmp_path / 'nonmatchings/f'
    existing.mkdir(parents=True)
    (existing / 'user.c').write_text('keep me')
    with pytest.raises(RuntimeError, match='refusing to replace'):
        target_intake.bootstrap_resolved(tmp_path, 'f')
    assert (existing / 'user.c').read_text() == 'keep me'


def test_alias_rename_preserves_data_strings_comments_and_other_symbols():
    source = ('glabel canonical\njal canonical\nlui a0,%hi(canonical)\n'
              '.ascii "canonical"\n.word canonical.extra\n# canonical\n')
    renamed = target_intake.rename_symbol(source, 'canonical', 'alias')
    assert 'glabel alias\njal alias\nlui a0,%hi(alias)' in renamed
    assert '.ascii "canonical"\n.word canonical.extra\n# canonical\n' in renamed
