"""Only bound readonly initial bytes enter tests; candidate-owned errors survive."""
import copy
import hashlib

import pytest

from solver import binary_data as b, data_memory, mips_differential as d


TARGET = '''glabel loadConstant
/* 100 80002000 3C088000 */ lui t0,%hi(constant)
/* 104 80002004 8D021000 */ lw v0,%lo(constant)(t0)
/* 108 80002008 03E00008 */ jr ra
/* 10C 8000200C 00000000 */ nop
'''


def catalog(section='.rodata', address=0x80001000, rom_bound=True):
    text = f'.section {section}\ndlabel constant\n/* 0 {address:08X} 12345678 */ .word 0x12345678\n'
    raw = bytes.fromhex('12345678')
    return b.build({'data.s': text}, {'data.s': b.digest(text.encode())},
                   **({'rom': raw, 'rom_sha256': b.digest(raw)} if rom_bound else {}))


def context():
    return data_memory.select(catalog(), TARGET, b.digest(TARGET.encode()))


def resign(ctx):
    ctx['sha256'] = b.identity({k: v for k, v in ctx.items() if k != 'sha256'})
    return ctx


def test_select_requires_matching_target_and_catalog_checksums():
    cat = catalog()
    with pytest.raises(ValueError, match='assembly'):
        data_memory.select(cat, TARGET+'\n', b.digest(TARGET.encode()))
    cat['regions'][0]['bytes_hex'] = 'ffffffff'
    with pytest.raises(ValueError, match='catalog'):
        data_memory.select(cat, TARGET, b.digest(TARGET.encode()))


@pytest.mark.parametrize('section,address,bound', [
    ('.data', 0x80001000, True), ('.rodata', 0xA4040010, True),
    ('.rodata', 0x90001000, True), ('.rodata', 0x80001000, False),
])
def test_mutable_mmio_unmapped_and_unverified_regions_are_not_admitted(section, address, bound):
    ctx = data_memory.select(catalog(section, address, bound), TARGET, b.digest(TARGET.encode()))
    assert not ctx['regions'] and ctx['declines']


def test_bss_is_not_seeded_and_selection_budget_is_bounded():
    text = '.section .bss\ndlabel constant\n/* 80001000 */ .space 4\n'
    cat = b.build({'bss.s': text}, {'bss.s': b.digest(text.encode())})
    ctx = data_memory.select(cat, TARGET, b.digest(TARGET.encode()))
    assert not ctx['regions']
    assert not data_memory.select(catalog(), TARGET, b.digest(TARGET.encode()), max_bytes=3)['regions']


def test_apply_checks_context_hash_target_hash_and_byte_hash():
    ctx = context()
    changed = copy.deepcopy(ctx)
    changed['regions'][0]['bytes_hex'] = 'ffffffff'
    with pytest.raises(ValueError, match='checksum'):
        data_memory.apply(TARGET, changed, raw_target=TARGET)
    with pytest.raises(ValueError, match='another target'):
        data_memory.apply(TARGET, ctx, raw_target=TARGET+'\n')
    with pytest.raises(ValueError, match='invalid static data'):
        data_memory.apply(TARGET, resign(changed), raw_target=TARGET)


def test_apply_rejects_conflicting_static_aliases():
    ctx = context()
    other = {**ctx['regions'][0], 'symbol': 'alias', 'bytes_hex': 'ffffffff',
             'bytes_sha256': hashlib.sha256(bytes.fromhex('ffffffff')).hexdigest()}
    ctx['regions'].append(other)
    with pytest.raises(ValueError, match='conflicting static data aliases'):
        data_memory.apply(TARGET, resign(ctx), raw_target=TARGET)


def test_apply_rejects_existing_symbol_at_different_address():
    with pytest.raises(ValueError, match='symbol address'):
        data_memory.apply(TARGET+'# MIPS_DIFF_SYMBOL constant 0x80003000\n', context(), raw_target=TARGET)


def test_real_interpreter_reads_rom_value_and_candidate_fallback_without_owned_data():
    admitted, report = data_memory.apply(TARGET, context(), raw_target=TARGET)
    assert report['mapped_bytes'] == 4
    # Candidate uses an absolute address and owns no ELF constant. The existing
    # differential fallback supplies the target's externally admitted bytes.
    candidate = 'li t0,0x80001000\nlw v0,0(t0)\njr ra\nnop\n'
    result = d.run_suite(admitted, candidate, (d.TestCase('rom-data', 7),), return_registers=('v0',))[0]
    assert result.status == 'passed', result.first_divergence
    assert result.target.return_values['v0'] == result.candidate.return_values['v0'] == 0x12345678


@pytest.mark.parametrize('owned_name', ['constant', 'candidateAlias', 'D_80001000'])
def test_real_interpreter_wrong_owned_candidate_bytes_remain_a_failure(owned_name):
    target, _ = data_memory.apply(TARGET, context(), raw_target=TARGET)
    binding = '' if owned_name == 'D_80001000' else f'# MIPS_DIFF_SYMBOL {owned_name} 0x80001000\n'
    candidate = (TARGET + binding
                 + f'# MIPS_DIFF_BYTES {owned_name} ffffffff\n')
    candidate, report = data_memory.apply(candidate, context(), raw_target=TARGET)
    result = d.run_suite(target, candidate, (d.TestCase('owned-data-error', 7),), return_registers=('v0',))[0]
    assert result.status == 'failed', result.first_divergence
    assert result.target.return_values['v0'] == 0x12345678
    assert result.candidate.return_values['v0'] == 0xffffffff
    assert report['declines']

