import json
import sqlite3

import pytest

from solver import compiler_recipe as recipe


MAKE = """
BUILD_DIR = build
TOOLS_DIR = tools
CC = $(TOOLS_DIR)/ido-recomp/linux/cc
AS = $(CROSS)as
ASFLAGS = -G 0 -mips3
IDO_CC = $(CC)
C_MIPS = -mips1
C_OPT = -O2
CFLAGS = -c $(C_MIPS)
C_OBJ_POSTPROCESS = :
$(BUILD_DIR)/src/ultra/%.o: C_MIPS = -mips2
$(BUILD_DIR)/src/ultra/special.o: C_OPT = -O3
unrelated = $(shell touch BAD)
all:
\ttouch BAD
"""


def test_postprocessor_requirement_preserves_exact_resolved_evidence(tmp_path,monkeypatch):
    from types import SimpleNamespace
    values={'IDO_CC':'tools/ido-recomp/linux/cc','CFLAGS':'-c -mips1',
            'C_OPT':'-O2','C_MIPS':'-mips1','ASFLAGS':'-G 0 -mips3',
            'C_OBJ_POSTPROCESS':'python3 tools/trim_elf_section_tail.py build/src/f.o .data 16'}
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0,stderr='',stdout='\n'.join(
            f'__DECOMP_{key}__={value}' for key,value in values.items()))
    monkeypatch.setattr(recipe.subprocess,'run',run)
    with pytest.raises(recipe.ObjectBackendRequired) as raised:
        recipe._resolve(str(tmp_path),MAKE,'build/src/f.o','mips-linux-gnu-')
    evidence=raised.value.evidence
    assert evidence['postprocess']==values['C_OBJ_POSTPROCESS']
    assert evidence['makefile_sha256']==recipe.sha(MAKE.encode())
    assert not evidence['command_executed']
    assert len(calls)==1 and calls[0][0]=='make'


def test_projection_keeps_make_target_settings_not_original_recipes():
    projected = recipe.projection(MAKE, "build/src/ultra/special.o", "mips-linux-gnu-")
    assert "touch BAD" not in projected
    assert "C_OPT = -O3" in projected
    assert "src/ultra/%.o: C_MIPS = -mips2" in projected
    assert "__DECOMP_CFLAGS__=$(CFLAGS)" in projected


@pytest.mark.parametrize("target", ["../outside.o", "build/src/../../oops.o", "build/src/x;echo.o"])
def test_rejects_unsafe_target(target):
    with pytest.raises(ValueError):
        recipe.projection(MAKE, target, "mips-linux-gnu-")


def test_rejects_active_make_functions_and_missing_variables():
    for text in (MAKE.replace("C_OPT = -O2", "C_OPT = $(shell touch BAD)"),
                 MAKE.replace("C_OPT = -O2", "C_OPT = $(MISSING)")):
        with pytest.raises(ValueError):
            recipe.projection(text, "build/src/f.o", "mips-linux-gnu-")


def test_rejects_conditional_settings_instead_of_ignoring_branches():
    text = MAKE + "\nifdef FAST\nC_OPT = -O3\nendif\n"
    with pytest.raises(ValueError, match="conditional"):
        recipe.projection(text, "build/src/f.o", "mips-linux-gnu-")


def test_adapter_changes_only_compilation_and_preserves_guards():
    helper = 'guard against asm\n' + recipe.INVOCATION + '\nverify exact bytes\n'
    adapted = recipe.adapt_helper(helper, {"command": ["cc", "-mips2", "-O1", "space path"]})
    assert adapted.startswith('guard against asm\n')
    assert adapted.endswith('\nverify exact bytes\n')
    assert "'space path'" in adapted
    with pytest.raises(ValueError):
        recipe.adapt_helper("changed helper", {"command": ["cc"]})


@pytest.mark.parametrize('isa', ['-mips1', '-mips2', '-mips3 -32'])
def test_explicit_supported_isa_abi_reaches_compiler_unchanged(tmp_path, monkeypatch, isa):
    from types import SimpleNamespace
    values = {'IDO_CC': 'tools/ido-recomp/linux/cc', 'CFLAGS': '-c ' + isa,
              'C_OPT': '-O1', 'C_MIPS': isa, 'ASFLAGS': '-G 0 -mips3 -mabi=32',
              'C_OBJ_POSTPROCESS': ':'}
    monkeypatch.setattr(recipe.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=0, stderr='', stdout='\n'.join(
            f'__DECOMP_{key}__={value}' for key, value in values.items())))
    resolved = recipe._resolve(str(tmp_path), MAKE, 'build/src/ultra/libc/ll.o', 'mips-linux-gnu-')
    assert resolved['settings']['C_MIPS'] == isa
    assert resolved['command'] == ['tools/ido-recomp/linux/cc', '-c', *isa.split(),
                                   '-O1', '-Isrc/ultra/libc']
    adapted = recipe.adapt_helper('guard\n' + recipe.INVOCATION + '\nverify\n', resolved)
    assert isa in adapted
    assert adapted.startswith('guard\n') and adapted.endswith('\nverify\n')


@pytest.mark.parametrize('isa', ['-mips3', '-mips3 -n32', '-mips3 -64', '-mips4 -32'])
def test_unmeasured_isa_abi_pairs_still_decline(tmp_path, monkeypatch, isa):
    from types import SimpleNamespace
    values = {'IDO_CC': 'tools/ido-recomp/linux/cc', 'CFLAGS': '-c ' + isa,
              'C_OPT': '-O1', 'C_MIPS': isa, 'ASFLAGS': '-G 0 -mips3',
              'C_OBJ_POSTPROCESS': ':'}
    monkeypatch.setattr(recipe.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=0, stderr='', stdout='\n'.join(
            f'__DECOMP_{key}__={value}' for key, value in values.items())))
    with pytest.raises(ValueError, match='unsupported compiler ISA/ABI'):
        recipe._resolve(str(tmp_path), MAKE, 'build/src/ultra/libc/ll.o', 'mips-linux-gnu-')


def test_prepared_recipe_survives_unlogged_deterministic_compile(tmp_path, monkeypatch):
    ws = tmp_path / "nonmatchings/f"
    ws.mkdir(parents=True)
    (tmp_path / "Makefile").write_text(MAKE)
    original = "guard\n" + recipe.INVOCATION + "\nverify\n"
    (ws / "build.sh").write_text(original)
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE functions(name,tu_id); CREATE TABLE tus(id,name); "
                      "INSERT INTO functions VALUES('f',1); INSERT INTO tus VALUES(1,'build/src/f.o');")
    monkeypatch.setattr(recipe, "resolve", lambda repo, target: {
        "target": target, "command": ["cc", "-mips2"], "settings": {"C_MIPS": "-mips2"}})
    first = recipe.prepare(tmp_path, ws, conn, "f")
    second = recipe.prepare(tmp_path, ws, None, "")
    assert first == second
    assert (ws / "build.sh").read_text() == original
    assert json.loads((ws / ".compiler-target.json").read_text())["target"] == "build/src/f.o"
    first[0].write_text("tampered")
    with pytest.raises(ValueError, match="changed outside"):
        recipe.prepare(tmp_path, ws, None, "")


def test_workspace_logs_recipe_in_nonexact_attempt(tmp_path, monkeypatch):
    from solver import workspace
    script = tmp_path / "chosen.sh"
    script.write_text("unused")
    chosen = {"settings": {"C_MIPS": "-mips2", "C_OPT": "-O1"}}
    monkeypatch.setattr(recipe, "prepare", lambda *a: (script, chosen))
    monkeypatch.setattr(workspace, "_candidate_compile_source", lambda r, code: code)
    commands, logged = [], []
    def build(command, **kw):
        commands.append(command)
        return 0, "Score: 54.0%\nVerified exact match: no\n"
    monkeypatch.setattr(workspace, "sh", build)
    monkeypatch.setattr(workspace, "record_attempt", lambda *a, **kw: logged.append(kw))
    attempt = workspace.score(tmp_path, tmp_path, "f", "int f(void){return 0;}", conn=object(), func="f")
    assert "chosen.sh" in commands[0]
    assert attempt.compiler_recipe == chosen
    assert logged[0]["extra"]["compiler_recipe"] == chosen
    assert not attempt.exact
