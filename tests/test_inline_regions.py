from solver import inline_regions as r, cfg, modelrepair, workspace


BODY = '\n'.join(['lw t0,0(a0)','addiu t1,t0,1','andi t2,t1,255',
                  'sw t2,0(a0)','lw t3,4(a0)','addu t4,t3,t2',
                  'xor t5,t4,t0','sw t5,4(a0)'])


def test_repeated_region_and_whole_helper_are_detected():
    renamed = BODY.replace('t0','s0').replace('t1','s1').replace('a0','a2')
    report = r.analyse({'helper':BODY+'\njr ra\nnop',
                        'large':renamed+'\n'+('nop\n'*128)})
    assert report['pattern_count'] == 1
    assert report['embedded_helper_count'] == 1
    assert report['embedded_helpers'][0]['caller'] == 'large'
    assert report['patterns'][0]['occurrences'][0]['assembly_sha256']


def test_reuse_constants_and_symbols_are_not_erased():
    def sig(text): return r.signature(cfg.parse_assembly(text)[0])
    assert sig(BODY) != sig(BODY.replace('t4,t0','t4,t1'))
    assert sig(BODY) != sig(BODY.replace('255','127'))
    assert sig('lui t0,%hi(first)') != sig('lui t0,%hi(second)')
    assert sig('lui t0,%hi(t0)') != sig('lui t1,%hi(t1)')
    assert sig('lw t0,0(a0)') != sig('sw t0,0(a0)')


def test_control_flow_delay_slots_stack_and_labels_split_regions():
    for separator in ('jal helper\nnop','beqz a0,L\nnop','sw t0,4(sp)', 'L:'):
        altered = BODY.replace('sw t2,0(a0)', 'sw t2,0(a0)\n'+separator)
        assert not r.analyse({'small':altered,'large':BODY+'\n'+('nop\n'*128)})['patterns']
    assert not r.runs('jal x\nlw t0,0(a0)')[1]


def test_nontrivial_return_delay_slot_not_treated_as_complete_helper():
    report = r.analyse({'helper':BODY+'\njr ra\naddiu v0,a0,1',
                        'large':BODY+'\n'+('nop\n'*128)})
    assert report['patterns']
    assert not report['embedded_helpers']


def test_prompt_is_bounded_and_wired_to_real_builder():
    asm = (BODY+'\nnop\n')*16
    text = r.prompt(asm)
    assert 'REPEATED REGION HYPOTHESES' in text
    assert len(text)<2000
    attempt = workspace.Attempt(True,90,False,'','','')
    built = modelrepair.build_prompt(asm,'void f(void) {}',attempt)
    assert text in built
    assert not r.prompt(BODY)
