from solver.callback_address_hypotheses import probes,candidates

SOURCE='''typedef struct CallbackTask CallbackTask;
struct CallbackTask { CallbackTask *prev; CallbackTask *next; int priority; };
extern CallbackTask gCallbackTaskActiveListSentinel;
CallbackTask *f(void (*callback)(void *), u16 type, s32 priority) {
    CallbackTask *cur;
    CallbackTask *insertAfter;
    insertAfter = &gCallbackTaskActiveListSentinel;
    cur = insertAfter->next;
    return cur;
}
int untouched(void) { return 11; }
'''

def test_synthetic_probes_keep_compilation_context_and_unrelated_body():
    variants=probes(SOURCE,'f')
    assert len(variants)==22
    assert len({v.source for v in variants})==22
    assert all(v.source.endswith('int untouched(void) { return 11; }\n') for v in variants)
    assert all('f(void (*callback)(void *), u16 type, s32 priority)' in v.source for v in variants)
    union=next(v.source for v in variants if v.label=='union-view')
    assert 'a.p=&gCallbackTaskActiveListSentinel;' in union
    assert 'cur=((CallbackTask *)a.n)->next;' in union

def test_transfers_are_bounded_and_refuse_ambiguous_reload():
    variants=candidates(SOURCE,'f',3)
    assert len(variants)==3
    assert all(v.source.endswith('int untouched(void) { return 11; }\n') for v in variants)
    assert not candidates(SOURCE,'f',0)
    assert not candidates(SOURCE,'missing')
    assert not candidates(SOURCE.replace('cur = insertAfter->next;','cur = insertAfter->next; cur = insertAfter->next;'),'f')
    assert not probes(SOURCE,'missing')
