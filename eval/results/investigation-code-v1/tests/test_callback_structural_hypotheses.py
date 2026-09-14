from solver.callback_structural_hypotheses import candidates

SOURCE='''int f(int priority) {
    Node *prev;
    Node *cur;
    Node *newTask;
    prev = &sentinel;
    newTask = pool[idx];
    if (sentinel.next != NULL) {
        cur = prev->next;
        for (;;) {
            if (cur->priority < priority) break;
            prev = cur;
            cur = cur->next;
            if (cur == NULL) break;
        }
    }
    return prev->priority;
}'''

def test_direct_predecessor_walk_and_scope():
    prefix='int other(void) { Node *cur; return 0; }\n'
    result=candidates(prefix+SOURCE,'f')
    assert len(result)==1
    assert result[0].source.startswith(prefix)
    assert 'while (prev->next != NULL)' in result[0].source
    assert 'if (prev->next->priority < priority) break;' in result[0].source
    assert 'prev = prev->next;' in result[0].source

def test_dead_cursor_required():
    assert not candidates(SOURCE.replace('return prev->priority','return cur->priority'),'f')
    assert not candidates(SOURCE.replace('prev = &sentinel;','save(&cur); prev = &sentinel;'),'f')

def test_reaching_head_and_effects():
    assert not candidates(SOURCE.replace('prev = &sentinel;','prev = &other;'),'f')
    assert not candidates(SOURCE.replace('newTask = pool[idx];','newTask = lookup();'),'f')
    assert not candidates(SOURCE.replace('newTask = pool[idx];','sentinel.next = NULL;'),'f')

def test_declines_altered_loop_effects_and_slot():
    assert not candidates(SOURCE.replace('prev = cur;','cur->priority++; prev = cur;'),'f')
    assert not candidates(SOURCE.replace('sentinel.next != NULL','sentinel.prev != NULL'),'f')

def test_deterministic_budget():
    assert candidates(SOURCE,'f',0)==[]
    assert candidates(SOURCE,'f')==candidates(SOURCE,'f')
