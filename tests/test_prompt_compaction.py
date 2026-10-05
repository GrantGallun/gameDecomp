"""prompt_compaction removes splat annotations and pretty JSON losslessly, and never touches C comments."""
import json

from solver import prompt_compaction as pc

ASM = """glabel drawMenuGlyph
    /* 136E4 80012AE4 27BDFF60 */  addiu      $sp, $sp, -0xA0
    /* 136FC 80012AFC 15C0000B */  bnez       $t6, .L80012B2C
    /* 13700 80012B00 AFA700AC */   sw        $a3, 0xAC($sp)
  .L80012B2C:
    /* 13714 80012B14 84842130 */  lh         $a0, %lo(gAssetHandles)($a0)
endlabel drawMenuGlyph"""


def test_annotations_and_padding_are_removed_but_every_instruction_survives():
    compacted = pc.compact_assembly(ASM)
    assert "/*" not in compacted and len(compacted) < len(ASM) * 0.6
    for instruction in ("addiu $sp, $sp, -0xA0", "bnez $t6, .L80012B2C", "sw $a3, 0xAC($sp)",
                        "lh $a0, %lo(gAssetHandles)($a0)"):
        assert instruction in compacted
    assert "  .L80012B2C:" in compacted and compacted.startswith("glabel drawMenuGlyph")


def test_c_comments_and_c_blocks_are_untouched():
    source = "void f(void) {\n    /* Warning: struct X is not defined */\n    x = 1; /* 4 bytes */\n}\n{\n    g();\n}"
    assert pc.compact(source) == source


def test_pretty_json_is_reencoded_losslessly():
    value = {"obligations": [{"local": "sp10", "width": 4}], "note": "keep \"quotes\""}
    prompt = "STORAGE/TYPE RECONSTRUCTION INPUT (READ-ONLY):\n" + json.dumps(value, indent=2) + "\nNEXT"
    compacted = pc.compact(prompt)
    body = compacted.split("\n")[1]
    assert json.loads(body) == value and len(compacted) < len(prompt)
