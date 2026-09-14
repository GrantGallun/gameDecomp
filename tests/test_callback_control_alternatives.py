from solver.callback_control_alternatives import candidates

SOURCE='''struct Node { struct Node *next; int priority; };
struct Node sentinel;
int f(struct Node *prev, int bound) {
    struct Node *cur;
    prev = &sentinel;
    cur = prev->next;
    while (cur != NULL) {
        if (cur->priority < bound) break;
        prev = cur;
        cur = cur->next;
    }
    return prev->priority;
}'''

def test_guarded_bottom_check_and_scope():
    prefix='int untouched(void) { return 1; }\n'
    variants=candidates(prefix+SOURCE,'f')
    code=next(v.source for v in variants if v.label=='pointer-walk:guard-top-break')
    assert code.startswith(prefix)
    assert 'if (prev->next != NULL)' in code
    assert 'for (;;)' in code
    assert 'if (cur == NULL) break;' in code
    assert 'do {' not in code

def test_declines_live_cursor_and_float_comparison():
    assert not candidates(SOURCE.replace('return prev->priority','return cur->priority'),'f')
    assert not candidates(SOURCE.replace('int priority;','float priority;'),'f')

def test_sentinel_alias_must_still_be_current():
    code=SOURCE.replace('cur = prev->next;','prev = another;\n    cur = prev->next;')
    assert all('sentinel-' not in v.label for v in candidates(code,'f'))

def test_bounded_and_deterministic():
    assert candidates(SOURCE,'f',0)==[]
    assert len(candidates(SOURCE,'f',2))==2
    assert candidates(SOURCE,'f')==candidates(SOURCE,'f')
