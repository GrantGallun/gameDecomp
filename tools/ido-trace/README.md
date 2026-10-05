# ido-trace: IDO 5.3 uopt that can print its register allocation

Stock ido-static-recomp v1.0 aborts in `wrapper_ecvt` (`libc_impl.c:1128`, a bare
`assert(0)`) the first time `uopt -zdbug:5` or `-zdbug:6` formats a float. Those are
the traces that print each live range's priority (`adjsave`), forbidden colours,
interference, and the order and outcome of every colouring decision. That crash
is why `eval/results/callback-allocation-backend-v1` recorded the allocator's
priorities as unknown.

`ecvt-fcvt.patch` implements `ecvt` and `fcvt` with the host libc, copying the
digits into one emulated static buffer, as IRIX's versions return. Before the
patch any run that reached these calls aborted, so a compile without trace flags
cannot take a different path. The gate below checks that anyway, because the
recompiled C is also built with a different host GCC (13.3 against 9.4).

## Build

    tools/ido-trace/build.sh            # -> ~/decomp/tools-src/ido-trace

Only `uopt` differs from the stock directory. Use it through the driver:

    ~/decomp/tools-src/ido-trace/cc <normal flags> -Wo,-zdbug:5 file.c   # writes ./uoptlist
    ~/decomp/tools-src/ido-trace/cc <normal flags> -Wo,-zdbug:6 file.c

Parse with `solver/uopt_trace.py`; `eval/uopt_trace_census.py` runs it over a tree.

## Gate (2026-09-14, uopt sha256 `2776d48b1654ff6a74498311b376ad4959eda875c710ae97f0fc98f9a09dff15`)

- **ROM.** A copy of the SBK1 tree with only `uopt` swapped, built from scratch
  (217 objects), produces the stock ROM: sha256 `58870ea6...dcfb3c`, `shasum --check` OK.
- **Trace mode changes nothing.** Every game C file (193) was recompiled at
  `-zdbug:5` and at `-zdbug:6` with the build's own post-processing. All 386
  objects are byte-identical to the build's; the tree still verifies afterwards.
- **The traces complete.** Stock uopt stops at line 225 of the callback level-5
  trace and 226 of level 6; the patched one writes all 900 and 273 lines.

A rebuild has a new hash (the build date is embedded). Rerun the ROM gate before use.

## Known limits

`ugen -d` (driver: `-Wc,-d,-e,PATH`, the tree dump `solver/uopt_calls.py` reads) is not
code-neutral. On SBK1 it renumbered ugen temporaries (`t6` -> `t8` in struct copies) in
27 of 193 TUs, although uopt's decisions, which come first, are unchanged. Take the dump
in a compile of its own and never use that compile's object; `eval/uopt_trace_census.py
--ugen` does this. The uopt trace flags (`-Wo,-zdbug:N`) changed no object in 386 compiles.

A NaN priority prints as `-.nan\x000000e-01`. The host `ecvt` returns `"nan"`, and
IRIX's formatter copies nine digits past the terminator. `uopt_trace.parse_real`
reads it as NaN. Only the text of the trace is affected, never the object.
