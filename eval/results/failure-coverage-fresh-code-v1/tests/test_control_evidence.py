import pytest

from solver import control_evidence as evidence
from solver import mips_differential as differential


@pytest.mark.parametrize("unsigned,signed,width", [("lhu", "lh", 2), ("lbu", "lb", 1)])
def test_load_interpretation_reports_upstream_branch_not_bad_pointer(unsigned, signed, width):
    assembly = """
        LOAD t0,0(a0)
        slt t1,t0,a2
        beqz t1,after
        nop
        li t2,1
        b store
        nop
    after:
        li t2,2
    store:
        sw t2,4(a0)
        jr ra
        nop
    """
    case = differential.TestCase("high-bit-field", 1,
        player_writes=((0, width, (1 << (width * 8)) - 1),), entry_registers=(("a2", 0),))
    result = differential.run_suite(assembly.replace("LOAD", unsigned),
                                    assembly.replace("LOAD", signed), (case,))[0]
    assert result.status == "failed"
    rows = evidence.contrasts([result, result])
    assert len(rows) == 1 and rows[0]["support_cases"] == 2
    assert rows[0]["target"]["dependent_branches"]
    assert rows[0]["candidate"]["loaded_value"] == "0xffffffff"
    assert "not evidence of a bad symbol relocation" in evidence.render([result])
    from eval import differential_repair_pilot as pilot
    source = "int f(void) { return 0; }"
    bundle = pilot._operation_feedback([result], source, "long trace\n" * 2000)
    feedback = pilot._prioritized_feedback([result], source, bundle)
    assert feedback.startswith("UPSTREAM SAME-ADDRESS LOAD CONTRASTS")
    assert feedback.count("UPSTREAM SAME-ADDRESS LOAD CONTRASTS") == 1
    assert "zero-extend" in feedback[:12000]
    signed_type = "s16" if width == 2 else "s8"
    unsigned_type = "unsigned short" if width == 2 else "unsigned char"
    code = f"/* ({signed_type}) */ int f(void) {{ return ({signed_type})x->field; }}"
    variants = evidence.source_variants([result], code)
    assert len(variants) == 1
    assert f"({unsigned_type})x->field" in variants[0].source
    assert f"/* ({signed_type}) */" in variants[0].source


def test_same_interpretation_and_passing_cases_do_not_invent_a_fault():
    assembly = "lhu t0,0(a0)\nsw t0,4(a0)\njr ra\nnop"
    result = differential.run_suite(assembly, assembly, (differential.TestCase("same", 1),))[0]
    assert evidence.render([result]) == ""
    assert evidence.source_variants([result], "int f(void) { return (s16)x; }") == ()
