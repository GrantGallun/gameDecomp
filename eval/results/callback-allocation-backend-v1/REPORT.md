# Callback compiler-stage allocation evidence

The production callback remains **98.511**. This investigation obtained real IDO optimizer/code-generator intermediates instead of predicting their decisions from the final assembly. Diagnostic compilation reproduces the production object's allocated sections and relocations exactly; [verification.json](verification.json) records `images_equal: true` and production attempt 32649.

## Observed causes

The candidate's pool index is already allocated to architectural register 3 (`v1`) in the optimized Ucode entering the code generator. The new-task pointer is already register 8 (`t0`). In the initial tree, `mtype=R` offsets are four times architectural register numbers. The pool-index load is emitted as `zlhu xr3` after the count store, and the pool load as `zlw xr8`. The final assembler forwards the stored halfword into the index load, producing the visible `andi v1,t8,0xffff` in the final residual. Thus the final `andi` destination originates from an optimizer-allocated load, not merely the code generator picking the wrong scratch register.

The prologue's narrowing operations remain expression-tree nodes entering code generation. The code generator emits their temporary results as `xr14` (`t6`) and `xr15` (`t7`), followed by decrement temporaries `xr24` (`t8`) and `xr25` (`t9`). These are a different mechanism from the already allocated `v1`/`t0` values. A final-assembly model that assumes all of these are globally colored live ranges conflates two stages.

Evidence: [complete code-generator tree dump](ugen.dump), [instruction emission trace](ugen-dump.log), [optimized Ucode](baselineplain.O), and [frontend Ucode](baseline.B). The important trace entries are `emit_rab: zlhu xr3 ... xr9`, `emit_rri: zmul xr25 xr3 4`, and `emit_rab: zlw xr8 ... xr25`.

The parallel source tests support a narrower causal conclusion: a volatile halfword reload leaves the index in `v1` and prevents assembler forwarding; it does **not** demote the optimizer's allocation. Inlining the index can free `v1`, but then the predecessor pointer takes it and the new-task pointer gets `a3`. Therefore removing one allocated value alone does not establish the target's remaining allocation order.

## Diagnostic validity and limits

The diagnostic command is the existing production `-O2 -mips1 -G 0 -non_shared` recipe, with the same include/define and assembler settings, plus diagnostic-only stage output. The driver stage commands are preserved in [diagnostic-command.json](diagnostic-command.json). Direct isolated stages used:

```text
uopt -v -G 0 -EB -g0 -O2 baseline.B baselineplain.O -t baseline.T baselineplain.T
ugen -v -G 0 -EB -g0 -O2 baselineplain.O -o diagnostic.G -l diagnostic.s -t baseline.T -temp diagnostic.temp -d -e ugen.dump
```

The native driver with `-Wc,-d` produced an object with the same allocated sections and relocations as the ordinary production helper. `diagnostic.s` is also byte-identical to the nondiagnostic `baseline.s`.

The allocator's detailed rank trace uses `-zdbug:6`; that syntax is documented in the [upstream OoT IDO guide](https://github.com/n64decomp/oot/blob/master/docs/guides/-O2%20decompilation%20(for%20IDO%205.3).md#uopt-debug-traces). The installed recompilation crashes while formatting the trace at `libc_impl.c:1128`, `wrapper_ecvt: Assertion '0' failed`. The partial [level-six trace](uopt-level6.txt) and [failure log](alloc6.log) are retained. Its generated Ucode is byte-identical to the nondiagnostic output, but the save/rank information is incomplete. **Exact spill scores, priority ordering, and why one eligible web outranks another remain unknown.** No compiler patch or diagnostic switch was applied to production scoring.

The existing `solver/uopt.py` model explicitly guesses its `totalsave` numerator and reports only 102/129 concordant ordering pairs in `eval/results/uopt_validate.json`; it is not a substitute for this missing trace. This investigation makes no claim that its predicted ranking is authoritative.

Reproducer: `eval/experiments/code-shape-search/callback_allocation_backend.py` with `--compile`, `--stages`, `--allocation`, and `--verify` in order. All diagnostic artifacts stay in this directory. No game reference source, production source integration, compiler changes, or production build-guard changes were involved.
