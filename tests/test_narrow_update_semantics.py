"""Compile the actual proposals and exercise overflow and conditional counters.

Host C execution checks the visible transformation's meaning, not IDO byte
matching. The separate native replay remains the matching acceptance gate.
"""
import ctypes
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from test_narrow_update import IDLE, RANDOM, SIGNED, generated


def _library(folder: Path, name: str, source: str, extra: str = ''):
    compiler = shutil.which('clang')
    if not compiler:
        pytest.skip('host clang unavailable; native IDO replay is separate')
    folder.mkdir()
    prefix = '''typedef unsigned char u8;
typedef unsigned short u16;
typedef signed short s16;
typedef signed char s8;
typedef signed int s32;
typedef unsigned int u32;
static u8 table_bytes[256];
#define table table_bytes[0]
static void consume(void *p) { (void)p; }
'''
    export = '__declspec(dllexport)' if sys.platform == 'win32' else '__attribute__((visibility("default")))'
    source = source.replace('u8 randomNextObject(', export + ' u8 randomNextObject(')
    source = source.replace('void ' + name + '(', export + ' void ' + name + '(')
    source += f'\n{export} void initialize(void) {{ unsigned i; for (i=0;i<256;i++) table_bytes[i]=(u8)(i*73+19); }}\n'
    path = folder / 'candidate.c'
    path.write_text(prefix + extra.replace('EXPORT', export) + source)
    output = folder / ('candidate.dll' if sys.platform == 'win32' else 'candidate.so')
    command = [compiler, '-O2', '-shared', '-nostdlib', '-fno-builtin', '-fno-strict-aliasing']
    command += ['-fuse-ld=lld', '-Wl,/noentry'] if sys.platform == 'win32' else ['-fPIC']
    run = subprocess.run(command + [str(path), '-o', str(output)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    library = ctypes.CDLL(str(output.resolve()))
    library.initialize()
    function = getattr(library, name)
    function.argtypes = [ctypes.c_void_p]
    function.restype = ctypes.c_ubyte if name == 'randomNextObject' else None
    return library, function


def test_byte_increment_proposal_matches_original_for_every_byte_value(tmp_path):
    name = 'randomNextObject'
    variants = generated(RANDOM, name)
    assert variants
    original_lib, original = _library(tmp_path / 'original', name, RANDOM)
    new_lib, proposed = _library(tmp_path / 'proposed', name, variants[0])
    for value in range(256):
        before = (ctypes.c_ubyte * 0x520)()
        after = (ctypes.c_ubyte * 0x520)()
        before[0x518] = after[0x518] = value
        assert original(before) == proposed(after) == (((value + 1) & 255) * 73 + 19) & 255
        assert bytes(before) == bytes(after)
        assert after[0x518] == (value + 1) & 255


def test_counter_proposal_matches_original_at_all_halfword_values(tmp_path):
    name = 'updateEndingCreditsIdleSparkle'
    variants = generated(IDLE, name)
    assert variants
    original_lib, original = _library(tmp_path / 'original', name, IDLE)
    new_lib, proposed = _library(tmp_path / 'proposed', name, variants[0])
    before = (ctypes.c_uint16 * 32)()
    after = (ctypes.c_uint16 * 32)()
    for position in (0x1E // 2, 0x1C // 2):
        for value in range(65536):
            before[0x1E // 2] = after[0x1E // 2] = 3
            before[0x1C // 2] = after[0x1C // 2] = 4
            before[position] = after[position] = value
            old_outer, old_inner = before[0x1E // 2], before[0x1C // 2]
            original(before)
            proposed(after)
            assert bytes(before) == bytes(after), (position, value)
            expected_outer = (old_outer + 1) & 65535
            expected_inner = old_inner
            if expected_outer == 4:
                expected_outer = 0
                expected_inner = (old_inner + 1) & 65535
                if expected_inner == 5:
                    expected_inner = 0
            assert after[0x1E // 2] == expected_outer
            assert after[0x1C // 2] == expected_inner


def test_signed_masked_counter_preserves_all_values_and_a_later_aliasing_reload(tmp_path):
    source = SIGNED.replace('phase = 9;', '*phase_ptr = 9;').replace('consume(p);', 'signed_consume(p);')
    variants = generated(source)
    assert variants
    extra = '''static u16 *phase_ptr;
static int observed;
static void signed_consume(void *p) { observed = *(u16 *)((u8 *)p + 0x2A); }
EXPORT void setup(void *p, int alias) {
    phase_ptr = (u16 *)((u8 *)p + (alias ? 0x2A : 0x2C));
    observed = -1;
}
EXPORT int observation(void) { return observed; }
'''
    original_lib, original = _library(tmp_path / 'signed-original', 'f', source, extra)
    proposed_lib, proposed = _library(tmp_path / 'signed-proposed', 'f', variants[0], extra)
    for lib in (original_lib, proposed_lib):
        lib.setup.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.observation.restype = ctypes.c_int
    before, after = (ctypes.c_uint16 * 32)(), (ctypes.c_uint16 * 32)()
    for alias in (0, 1):
        for value in range(65536):
            before[0x2A // 2] = after[0x2A // 2] = value
            before[0x2C // 2] = after[0x2C // 2] = 0xACE1
            original_lib.setup(before, alias)
            proposed_lib.setup(after, alias)
            original(before)
            proposed(after)
            assert bytes(before) == bytes(after), (alias, value)
            assert original_lib.observation() == proposed_lib.observation(), (alias, value)
