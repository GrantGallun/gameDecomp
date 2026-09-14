from solver import wide_reconstruction as wide


LAYOUT={'Record':[{'member':'value','canonical':'unsigned long long','width':8,'offset':16},
                  {'member':'interval','canonical':'unsigned long long','width':8,'offset':8}]}
SOURCE='''void f(void) {
Record *p;
u32 hi;
u32 lo;
u32 elapsed;
u32 ih;
u32 il;
hi = p->unk10;
if ((hi >= 0U) && ((hi > 0U) || (elapsed < (u32) p->unk14))) {
lo = p->unk14;
p->unk14 = (u32) (lo - elapsed);
p->unk10 = (u32) ((p->unk10 - 0) - (lo < elapsed));
setTime(/* u64+0x0 */ p->unk10, /* u64+0x4 */ p->unk14);
}
ih = p->unk8;
il = p->unkC;
if ((ih != 0) || (il != 0)) {
p->unk10 = ih;
p->unk14 = il;
}
}'''


def test_all_four_closed_idioms_fire_without_function_specific_names():
    candidate,report=wide.reconstruct(SOURCE,LAYOUT,function='f')
    assert {c['kind'] for c in report['changes']}=={'unsigned-wide-compare','unsigned-wide-borrow',
        'annotated-wide-call-argument','unsigned-wide-nonzero-copy'}
    assert not report['remaining_word_accesses']
    for text in ('if (p->value > elapsed)','p->value -= elapsed;','setTime(p->value)',
                 'if (p->interval != 0)','p->value = p->interval;'):
        assert text in candidate


def test_ambiguous_or_signed_fields_do_not_lift():
    for members in ([{**f,'canonical':'long long'} for f in LAYOUT['Record']],
                    [*LAYOUT['Record'],*LAYOUT['Record']]):
        candidate,report=wide.reconstruct(SOURCE,{'Record':members},function='f')
        assert candidate==SOURCE and not report['changes']
    volatile_source=SOURCE.replace('Record *p;','volatile Record *p;')
    candidate,report=wide.reconstruct(volatile_source,LAYOUT,function='f')
    assert candidate==volatile_source and not report['changes']


def test_live_temporaries_signed_elapsed_and_wrong_borrow_decline():
    for changed,kind in ((SOURCE.replace('hi = p->unk10;','use(hi); hi = p->unk10;'),'unsigned-wide-compare'),
                         (SOURCE.replace('u32 elapsed;','s32 elapsed;'),'unsigned-wide-borrow'),
                         (SOURCE.replace('(lo < elapsed)','(lo <= elapsed)'),'unsigned-wide-borrow')):
        _,report=wide.reconstruct(changed,LAYOUT,function='f')
        assert kind not in {r['kind'] for r in report['changes']}


def test_volatile_and_global_temporaries_are_not_eliminated():
    for source in (SOURCE.replace('u32 hi;','volatile u32 hi;'),
                   'u32 hi;\n'+SOURCE.replace('u32 hi;','')):
        _,report=wide.reconstruct(source,LAYOUT,function='f')
        assert 'unsigned-wide-compare' not in {r['kind'] for r in report['changes']}


def test_other_function_is_not_rewritten():
    source=SOURCE+'\n'+SOURCE.replace('void f(','void other(')
    candidate,_=wide.reconstruct(source,LAYOUT,function='f')
    assert candidate[candidate.index('void other('):]==source[source.index('void other('):]
