# Real Project64 entry-capture pilot — September 12, 2026

Two actual Snowboard Kids (U) entries were captured from an unmodified ROM in
Project64, imported through the new adapter, and replayed successfully. A
deliberately wrong return value failed both comparisons.

| Natural argument a0 | Self replay v0 | Deliberately wrong v0 | Result |
| --- | --- | --- | --- |
| 0 | 0x80160480 | 0x80160481 | self passed; wrong failed |
| 5 | 0x801fefb0 | 0x801fefb1 | self passed; wrong failed |

The function is `getRelocatableHeapBlockBase`, entry `0x80043040`, 32 bytes at
ROM offset `0x43c40`. The full original ROM SHA256 is
`58870ea67d49f778e7a7607eb270ad1d3a081a4733b337b2d607de2606dcfb3c`.
The annotated eight-instruction target and concrete symbol address came from
the project's existing binary extraction, not reference C. Replay independently
reassembled the instructions with the existing MIPS toolchain and bound them to
the full original ROM using `linked_callee.bind`.

## What is proven, and what is not

Each export contains two separately read, identical snapshots taken after
`EMU_DEBUG_PAUSED`, with `debug.paused === true` and the exact entry PC. It includes
all 32 low and high GPR halves, both halves of HI/LO, PC, the actual instruction
window, and 128 KiB of actual RAM starting at `0x80110000`. The leaf reads the
alias table at `0x801101a0`; it does not touch the stack. No register, RAM or
instruction writes were made by the capture script. The nonzero capture only
filters the hook until the game supplies a nonzero argument.

The adapter preserves all high halves and rejects values outside the existing
canonical o32 backend contract. It checks both snapshots, exact register mapping,
loaded ROM header CRCs, independently supplied ROM hash and instruction extent,
bounded RAM identity and byte lengths, then applies `runtime_capture.verify`.
The replay keeps existing integer-leaf restrictions: calls, FPU/64-bit operations,
missing memory and unbound symbols cannot become a passing comparison. All target
symbols here have concrete addresses. The wrong control inserts `xori v0,v0,1`
after the load; it fails on the actual returned value, not an unmapped-memory fault.

These are two entry-state comparisons for one leaf, not whole-game equivalence,
candidate C yield, or canonical import. The observed entry states came from the
emulator; self/wrong function execution occurred in the bounded interpreter, not
by replacing the live emulator function. RAM range/classification are explicit
assumptions. Device state, FPU, other threads, caller continuation, allocations,
callbacks and external effects are not captured. Export checksums preserve
provenance; they do not authenticate a hostile producer. The adapter is a main-tree
pilot and was not injected into the frozen running campaign.

## Exact executable and startup findings

The installed Project64 3.0.1.5664 (`2df3434`) supports scripts through its debugger
UI but has no script autorun startup code. Computer Use failed after its documented
recovery attempts because the native pipe was unavailable. No alternate UI
automation was used.

A separate official portable build was downloaded from the
[Project64 development builds page](https://www.pj64-emu.com/nightly-builds):
`Dev-4.0.0-6769-6f7612b`, published August 15, 2026. The original ZIP is retained as
`project64-dev.zip`; SHA256 is
`14b7f8804df3c259bf43030e90db41d8d6ffc10f7f0ee424e5b44d732b07c17b`.
Executable SHA256 is
`f8b954eaa879da5f5fca2fce7ed5081a969eef4acc46e306b26f81709de8bfe7`.
`artifact-hashes.json` records the exact executable, scripts, ROM and raw exports.

The version-specific interface was checked against official source revision
`6f7612b`, with copies retained as `pj64-4-*.txt`:

- [Startup and autorun](https://github.com/project64/project64/blob/6f7612b/Source/Project64/main.cpp)
- [Script autorun implementation](https://github.com/project64/project64/blob/6f7612b/Source/Project64/UserInterface/Debugger/ScriptSystem.cpp)
- [Configuration keys](https://github.com/project64/project64/blob/6f7612b/Source/Project64-core/Settings.cpp)
- [Debugger pause event](https://github.com/project64/project64/blob/6f7612b/Source/Project64/UserInterface/Debugger/Debugger.cpp)
- [JavaScript API](https://github.com/project64/project64/blob/6f7612b/JS-API-Documentation.html)

Necessary portable configuration in `Config/Project64.cfg`:

```ini
[Settings]
Current Language=English
Basic Mode=0
Auto Start=1
Force Interpreter CPU=1

[Debugger]
Debugger=1
Autorun Scripts=capture-entry.js

[Audio-Settings]
Volume=0
```

Playback is now muted in the portable capture configuration at the user's
request. Keep `Volume=0` in future copied capture setups. Audio emulation remains
enabled; only the output level changes. The pre-mute configuration and change
hashes are preserved in `mute-playback/`. This setting was checked against the
exact revision's AudioSettings, plugin-settings section mapping, and DirectSound
driver, then read back from the INI; the emulator was not relaunched for an
audible check. Existing capture receipts describe the earlier runs unchanged.

The archived configuration also selects bundled GLideN64, Project64 Audio, Input
and RSP plugins. The portable RDB Snowboard Kids entry explicitly selects
`CPU Type=Interpreter`. **The global Force Interpreter setting is needed:** an
autorun `events.onexec` hook registers before the ROM-specific core selection and
otherwise throws `this feature requires the interpreter core`. Passing the ROM
as the final command-line argument started the actual game; launching without
the argument did not reach script/ROM execution in this pilot.

The other actual bridge issue was Duktape's `Buffer.toString('hex')`: it returned
text rather than Node-style hexadecimal encoding. The final exporter converts
each buffer byte explicitly. The failed output is preserved as
`launch-5-nonhex-raw.json`; it was never admitted as a verified capture.

## Reproduce or inspect

Focused adapter tests, from the Windows project root:

```powershell
python -m pytest -q tests/test_project64_capture.py --basetemp=.pytest-tmp-project64-capture-v2
```

The 20 tests use explicitly synthetic fixtures and cover valid provenance,
independent second-snapshot PC/register/RAM/code changes, noncanonical high halves,
wrong ROM/code, missing memory, invalid maps and malformed register halves. They
are separate from the two genuine captures above.

Replay the retained real captures (requires the existing WSL project and MIPS
assembler/linker/objcopy; it performs no emulator or campaign mutation):

```powershell
wsl.exe -d Ubuntu -- bash -lc 'cd /mnt/c/Code/gameDecomp && python3 -m eval.results.runtime-capture-20260912.replay-pilot'
wsl.exe -d Ubuntu -- bash -lc 'cd /mnt/c/Code/gameDecomp && python3 -m eval.results.runtime-capture-20260912.replay-pilot --nonzero'
```

Each invocation reads the retained input and exclusively creates a new
`replay-<time_ns>/` directory beneath the selected input directory. It reports
that path and writes its new plan, capture and traces there. The original accepted
receipts and raw exports are never overwritten by replay.
The expected ROM hash is read from the retained admitted `capture-plan.json`;
it is never recomputed from a potentially changed ROM to approve that same ROM.

For a fresh actual capture, preserve this directory, copy the portable directory
to a new task directory and use a new empty output directory. Set `outputRoot`
and the initial heartbeat path in the copied `capture-entry.js` to that new
directory, then put it in the copied portable `Scripts` directory. Use the main
exporter for the first entry or `nonzero/capture-entry.js` to wait for a nonzero
argument. The script is ES5 and uses only the documented Project64 API.

Launch the copied emulator through its supported command line with the ROM as
the final argument (substitute the new copied paths):

```powershell
$pilot = Start-Process -FilePath $portableExe -WorkingDirectory (Split-Path $portableExe) -WindowStyle Hidden -ArgumentList $romPath -PassThru
```

Wait for the new directory's `bridge-status.json` to say `captured`; inspect
`project64-entry-raw.json` and never reuse a stale output as a new capture. Import
with `solver.project64_capture.import_export(raw, plan, rom)`, using the explicit
plan shown in `capture-plan.json` and `project64_capture.register_map()`. Then call
`runtime_capture.replay` exactly as `replay-pilot.py` demonstrates. A different
function needs its own independently derived address, extent, ROM offset, symbol
bindings and RAM plan; this script does not infer those assumptions.

## Receipts and cleanup

- First accepted pair: `project64-entry-raw.json`, `capture-plan.json`,
  `capture.json`, `replay-results.json`, `self.s`, `known-wrong.s`.
- Second accepted pair: the same names under `nonzero/`, plus its exporter,
  launch receipt and emulator log. Argument 5 arose naturally during startup.
- Startup history: `launch*.json`, `launch*.log`, registration-error status files,
  and `launch-5-nonhex-raw.json`. Replay preamble/filter failures were retained as
  `replay-1-macro-error.json` and `replay-2-filter-error.json` before correction.
- `rom-identity.json`, original `target.s`, exact source/API copies and
  `artifact-hashes.json` retain binary, interface and exporter provenance.
- `process-cleanup.json` records stopping only pilot-owned PID 3248 after checking
  its exact portable executable path. No campaign restart, model calls, canonical
  edits, live database copy or candidate import occurred.
