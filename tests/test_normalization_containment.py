"""A malformed child source must be a declined normalization, never a parked function.

On 2026-09-14, 23 campaign functions sat parked with `unbalanced function body` or
`requires one ordinary function definition`, although their stored sources parse.
In 20 of them the latest attempt was a model child that no longer parses (here:
bootThreadMain attempt 97273, whose edit replaced the closing brace with `;`).
normalize() ran frontend_repair.propose on it, repair_context.definition raised,
the ValueError left modelrepair.search, and the worker parked the whole function.
"""
from solver import modelrepair, workspace

BOOT_CHILD = """#include "common.h"
extern void *D_80328480;
extern OSThread gGameThread;

void bootThreadMain(void *arg) {
    osCreatePiManager(0x96, &gPiManagerQueue, &gPiManagerMessages, 0xC8);
    osCreateThread(&gGameThread, 2, gameThreadMain, arg, &D_80328480, 0xA);
    osStartThread(&gGameThread);
    osSetThreadPri(NULL, 0);
loop_1:
    goto loop_1;
;
"""


def test_unparseable_child_is_declined_by_normalization_not_raised(monkeypatch, tmp_path):
    root = workspace.Attempt(False, 0, False, '', 'Syntax Error', '', 97273,
                             frontend={'passed': False, 'diagnostics': 'error: expected }'})
    monkeypatch.setattr(workspace, 'target_asm', lambda *a: 'glabel bootThreadMain\njr ra\nnop')
    monkeypatch.setattr(workspace, 'assert_uncontaminated', lambda *a: None)
    monkeypatch.setattr(workspace, 'score', lambda *a, **k: root)
    result = modelrepair.search(tmp_path, 'bootThreadMain', BOOT_CHILD, tmp_path, model='test', endpoint='none',
                                base_attempt=root, resilient=True, max_calls=0)
    assert result.best_attempt is root
    assert any('normalization declined' in line and 'unbalanced function body' in line for line in result.log)
