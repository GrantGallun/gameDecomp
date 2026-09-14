from solver.callback_selector_alternatives import candidates

SOURCE='''Node *f(u16 type) {
    u8 t8;
    Node *newTask;
    type = (u16)type;
    t8 = type;
    switch (t8) {
    case 0:
        if (gFreeCallbackTaskType0Count == 0) return NULL;
        gFreeCallbackTaskType0Count--;
        break;
    default: return NULL;
    }
    newTask->type = type;
    return newTask;
}'''

def test_relationship_probes_preserve_other_functions():
    prefix='int unrelated(void) { return 7; }\n'
    rows=candidates(prefix+SOURCE,'f')
    assert rows and all(v.source.startswith(prefix) for v in rows)
    assert any('full-type-carrier' in v.label for v in rows)
    assert any('savedType = (u16)type;' in v.source for v in rows)

def test_declines_incomplete_shape_and_name_collision():
    assert not candidates(SOURCE.replace('t8 = type;','t8 = other;'),'f')
    assert not candidates(SOURCE.replace('u8 t8;','u8 t8; u16 savedType;'),'f')

def test_bounded_deterministic_deduplication():
    assert candidates(SOURCE,'f',0)==[]
    assert len(candidates(SOURCE,'f',3))==3
    assert candidates(SOURCE,'f')==candidates(SOURCE,'f')
    rows=candidates(SOURCE,'f')
    assert len({v.source for v in rows})==len(rows)
