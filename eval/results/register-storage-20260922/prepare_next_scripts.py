"""Version the second experiment without modifying the completed first run."""
from pathlib import Path

OUT = Path(__file__).resolve().parent


def copy(source, target, changes):
    text = (OUT / source).read_text()
    assert not (OUT / target).exists()
    for before, after in changes:
        assert before in text, before
        text = text.replace(before, after)
    (OUT / target).write_text(text)


def main():
    copy("freeze.py", "freeze_next.py", [
        ('code-v1"', 'code-v2"'), ('freeze.json', 'freeze-v2.json'),
        ('"__osPopThread", "osGetThreadPri", "osGetThreadId",\n            "osSetThreadPri", "osSetTime", "osGetTime", "osSetEventMesg", "osAiGetLength"',
         '"osStopThread", "osDestroyThread", "osStartThread", "osVirtualToPhysical",\n            "osViSwapBuffer", "osViSetMode", "osCreateMesgQueue", "osPiGetCmdQueue"')])
    copy("prepare_panel.py", "prepare_next_panel.py", [
        ('freeze.json', 'freeze-v2.json'), ('panel.json', 'panel-v2.json'),
        ('"inputs"', '"inputs-v2"'), ('register-storage-20260922/drafts"', 'register-storage-20260922/drafts-v2"')])
    copy("measure.py", "measure_next.py", [
        ('freeze.json', 'freeze-v2.json'), ('panel.json', 'panel-v2.json'), ('paired-v1', 'paired-v2'),
        ('NEW = {"register_storage", "address_reuse"}', 'NEW = {"parameter_reuse"}'),
        ('"loadMusicSequenceBank", "__MusIntProcessWobble"]', '"loadMusicSequenceBank", "__MusIntProcessWobble", "__osDequeueThread"]'),
        ('["__osDequeueThread", *KNOWN, *NEGATIVE]', '["osGetThreadPri", *KNOWN, *NEGATIVE]'),
        ('world = json.loads(next(PRIOR.glob(f"*--{name}--breadth.world.json")).read_text())["world"]',
         'path = (SHARED / "paired-v1/followup--osGetThreadPri--control.world.json") if name == "osGetThreadPri" else next(PRIOR.glob(f"*--{name}--breadth.world.json"))\n        world = json.loads(path.read_text())["world"]'),
        ('name == "__osDequeueThread"', 'name == "osGetThreadPri"'),
        ('1024', '1280'), ('panel_functions=17', 'panel_functions=18'),
        ('storage-repair:', 'storage-repair-v2:')])
    copy("audit.py", "audit_next.py", [
        ('paired-v1', 'paired-v2'), ('measure.py', 'measure_next.py'), ('panel.json', 'panel-v2.json'),
        ('{"register_storage", "address_reuse"}', '{"parameter_reuse"}')])


if __name__ == "__main__":
    main()
