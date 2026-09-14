# Automatic muted Project64 capture runner

`eval.project64_runner` is the Windows worker for fresh emulator entry captures.
It uses the audited Project64 `6f7612b` API and the existing
`solver.project64_capture` admission gates. The campaign orchestration and current
compiled-candidate replay are owned separately by `eval.campaign_runtime`.

Two fresh worker launches succeeded:

| Receipt directory | Invocation | Natural a0 | Duration | Cleanup |
| --- | --- | --- | --- | --- |
| `first-1789240562222` | Windows Python CLI | 0 | 3.19 s | owned PID 27708 terminated and waited |
| `nonzero-1789240679637` | WSL → Windows Python, UNC original ROM | 5 | 2.88 s | owned PID 44940 terminated and waited |

Both copied emulator configurations still contain `[Audio-Settings] Volume=0`
after capture. Audio emulation remains enabled; the documented Project64 Audio
volume setting mutes playback. `smoke-summary.json` retains the success and failure
summary; each directory contains the full job, launch inputs, script, raw export,
validated capture, emulator logs and final receipt. Two preliminary process-
inventory failures occurred before any emulator launch and are preserved as well.

## Contract

Windows command (run from the code root):

```powershell
C:\Python314\python.exe -m eval.project64_runner --job C:\absolute\job.json
```

JSON job schema:

```json
{
  "schema_version": 1,
  "plan": {"...": "complete retained capture-plan.json object"},
  "rom_path": "C:\\absolute\\original.z64",
  "portable_dir": "C:\\absolute\\audited-portable-project64-v4",
  "output_dir": "C:\\absolute\\new-output-directory",
  "selector": "first",
  "timeout_seconds": 30
}
```

The exact working jobs are `job-first.json` and `job-nonzero.json`, and immutable
copies of each admitted job are in its output directory. Use a **new** output
directory for every invocation; a preexisting directory is rejected and never
overwritten. The selector is either `first` or `a0_nonzero`; arbitrary script
expressions are not accepted. A plan must preserve the exact full-width physical
register map, ordered nonoverlapping cached RAM windows and retained ROM identity.
Automatic script capture is limited to 512 KiB RAM and 5–120 seconds.

The original ROM may be an absolute Windows UNC path. The second real launch used
`\\wsl.localhost\Ubuntu\home\grant\decomp\sbk1\snowboardkids.z64`. The runner verifies
the retained plan's expected hash and copies those bytes to `input.z64` before
launch. It never derives a new expected hash from the current ROM to approve it.

The CLI writes a single final JSON receipt to stdout and `output_dir/receipt.json`.
Only `status: "captured"` with successful owned-process cleanup admits a capture.
That receipt supplies `capture_path`, `raw_path`, `plan_path`, capture and raw-file
hashes, timing and cleanup evidence. Existing user emulators produce
`blocked_existing_emulator`; timeout, exporter error, validation error or cleanup
failure cannot silently become success. The caller must inspect the status rather
than infer success from the CLI exit code. Malformed jobs with no usable new output
directory can only return an error on stdout.

## Isolation and provenance

The worker does not run the supplied directory in place. It creates a fresh
portable directory and copies only the eleven assets listed in
`eval/project64_assets.json`; every copied asset must match its fixed SHA256. This
includes the exact executable, selected audio/input/RSP/graphics plugins, English
language and required RDB/graphics configuration. It excludes user saves,
screenshots, scripts and the source application's mutable main configuration.
The manifest and exporter template themselves have fixed hashes in the worker.
Releases must include `eval/project64_capture_entry.js` explicitly; its hash is
verified before launch even where generic runtime pin enumeration excludes `.js`.

The worker writes a new configuration with forced interpreter mode, autorun,
English, selected bundled plugins and volume zero. Only JSON values are prefixed
to the fixed ES5 exporter. The exporter observes a natural function entry, halts
there and independently reads two complete snapshots. It never writes game RAM,
registers or code. Import verifies unchanged snapshots, code/ROM binding and full
register widths through the existing adapter. The raw export remains available
even when import declines.

Before copying or launching, the worker reads the process inventory and refuses
to run if another `Project64` process exists. It does not pause, close or attach to
that process. Its own subprocess is launched with hidden-window flags and muted
configuration; the GUI application may still control its own window visibility.
Cleanup uses only the returned `Popen` process handle, never a name-wide kill or a
previously recorded arbitrary PID. All completion/failure paths terminate a still
running owned child and wait up to five seconds. Cleanup failure is explicit.

The timeout starts at worker entry, before inventory, file verification and copy.
If setup has consumed it, the worker declines before starting an emulator. The
capture loop uses the same deadline, leaving the caller's additional timeout grace
for cleanup. Process-inventory errors fail closed. The worker does not change the
campaign, its databases, model budgets or canonical game files.

## WSL interop finding

This distro's `WSLInterop` binfmt registration was absent, so direct execution of
`/mnt/c/Python314/python.exe` failed with `Exec format error`. The existing interop
server was healthy. The explicit supported handler layout worked without changing
system configuration:

```sh
cd /mnt/c/Code/gameDecomp
/init /mnt/c/Python314/python.exe /mnt/c/Python314/python.exe \
  -m eval.project64_runner --job C:/absolute/new-job.json
```

The repeated executable preserves the argv layout expected by WSL's binfmt
handler. Microsoft's [interop documentation](https://github.com/microsoft/WSL/blob/master/doc/docs/technical-documentation/interop.md)
and [init implementation](https://github.com/microsoft/WSL/blob/master/src/linux/init/init.cpp)
describe this dispatch. The campaign caller falls back to this form only on
`ENOEXEC`; it does not edit WSL configuration or register a new system handler.

## Verification and limits

`python -m pytest -q tests/test_project64_runner.py tests/test_project64_capture.py`
passed **37 tests**. Runner fixtures explicitly use fake processes and synthetic
exports; they are separate from the actual launches above. Tests cover fixed
resource pins, fresh output, source-bundle preservation, mute configuration,
existing-emulator refusal, timeout/failure cleanup, selector validation, identity
changes and invalid jobs. The adapter tests cover snapshot and ROM/RAM/register
admission gates.

This worker obtains real entry states; it does not prove a candidate or a whole
game correct. Behavioral comparison remains bounded to the existing integer-leaf
backend and explicit RAM assumptions. Calling functions, FPU/64-bit semantics,
external effects and missing memory still require supported execution contracts.
