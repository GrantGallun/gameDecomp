"""The header-context wiring must FIRE on the residual it was added for.

Fifth rule, quoted in CLAUDE.md: "every generator needs a test that asserts it FIRES on its
motivating residual, not only tests of when it declines."

The residual, measured 2026-09-16: 44 of the 199 functions that have never compiled fail on
`'X' undefined`. `suspendGameTask` is one -- it writes the global at 0x8012370C, the KB block
describes that as "4 byte int, signed", and the prompt then asks the model to invent the callee
types it was not given. The model invents `GameTask`, the right name, and the TU rejects it
because `common.h` includes PR/mbi.h and game/math/geometry.h and nothing under game/engine/.

`solver/project_headers.py` already solved this -- 250 call sites, including `prompt_context`,
whose whole job is "render bounded project declarations without reading target C source". It was
simply not wired into the resident `build_prompt` path. These tests pin the wiring, and pin that
it stays opt-in, because those headers are the header-assisted tier.
"""
import sqlite3

import pytest

from solver import pipeline

HEADER = """#ifndef GAME_TASK_SCHEDULER_H
#define GAME_TASK_SCHEDULER_H

typedef struct GameTask {
    /* 0x00 */ struct GameTask *prev;
    /* 0x04 */ struct GameTask *next;
} GameTask;

extern GameTask *gCurrentGameTask;

void suspendGameTask(s32 taskId);

#endif
"""

ASM = "glabel suspendGameTask\n    lw $2, 0x8012370C\n    jr $31\n    nop\n"


def _repo(tmp_path):
    repo = tmp_path / 'sbk1'
    inc = repo / 'include' / 'game' / 'engine'
    inc.mkdir(parents=True)
    (inc / 'game_task_scheduler.h').write_text(HEADER)
    (repo / 'symbol_addrs.txt').write_text(
        'gCurrentGameTask = 0x8012370C; // type:data\n')
    return repo


def _kb(tmp_path):
    conn = sqlite3.connect(tmp_path / 'kb.sqlite')
    conn.executescript("""
        CREATE TABLE functions (addr INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE evidence (id INTEGER PRIMARY KEY, kind TEXT, func_addr INTEGER,
            op TEXT, base TEXT, offset INTEGER, width INTEGER, signed INTEGER,
            class TEXT, access TEXT, is_load INTEGER, target_addr INTEGER);
        CREATE TABLE inference (id INTEGER PRIMARY KEY, kind TEXT, subject TEXT,
            value TEXT, status TEXT, confidence REAL, origin TEXT);
        CREATE TABLE attempts (id INTEGER PRIMARY KEY, func_addr INTEGER, compiled INTEGER,
            compiler_stderr TEXT);
    """)
    conn.execute('INSERT INTO functions (addr, name) VALUES (?, ?)', (0x80012345, 'suspendGameTask'))
    conn.execute("""INSERT INTO evidence (kind, func_addr, op, base, offset, width, signed,
                    class, access, is_load) VALUES ('mem_access', ?, 'lw',
                    'global:0x8012370C', 0, 4, 1, 'int', 'full', 1)""", (0x80012345,))
    conn.commit()
    return conn


def _prompt(repo, conn, declarations):
    return pipeline.build_prompt(repo, conn, 'suspendGameTask', ASM, 'void suspendGameTask(s32 taskId) {}',
                                 'retype', False, declarations=declarations)


def test_it_fires_on_the_motivating_residual(tmp_path):
    """The declaration the compiler said was missing must reach the prompt."""
    repo, conn = _repo(tmp_path), _kb(tmp_path)
    prompt = _prompt(repo, conn, True)
    assert 'typedef struct GameTask' in prompt, prompt
    assert 'void suspendGameTask(s32 taskId);' in prompt


def test_the_header_is_named_so_a_receipt_can_show_where_it_came_from(tmp_path):
    repo, conn = _repo(tmp_path), _kb(tmp_path)
    prompt = _prompt(repo, conn, True)
    assert 'game/engine/game_task_scheduler.h' in prompt


def test_it_declines_by_default(tmp_path):
    """Header-assisted context must not enter the default path silently.

    On SBK1 these headers are the decomp team's reconstruction -- CLAUDE.md's third tier, which
    is 'neither a copied body nor something the pipeline could reach from binary evidence' and
    has to be reported separately from SOLVED. eval/zero_token_harvest.py excludes the same
    module deliberately.
    """
    repo, conn = _repo(tmp_path), _kb(tmp_path)
    prompt = _prompt(repo, conn, False)
    assert 'typedef struct GameTask' not in prompt
    assert 'game/engine/game_task_scheduler.h' not in prompt


def test_the_evidence_block_survives_either_way(tmp_path):
    """Turning declarations on must add context, not replace the binary-derived facts."""
    repo, conn = _repo(tmp_path), _kb(tmp_path)
    off, on = _prompt(repo, conn, False), _prompt(repo, conn, True)
    assert 'OBSERVED MEMORY ACCESSES' in off
    assert 'OBSERVED MEMORY ACCESSES' in on
    assert len(on) > len(off)
