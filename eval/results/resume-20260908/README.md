# Career-fair metrics — September 8, 2026

This is a game-wide census of **existing development candidates** for Snowboard Kids (N64), followed by fresh recompilation. It is not a fresh end-to-end generation benchmark, gameplay test, or verification of a reconstructed ROM.

## Measured results

| Metric | Result |
|---|---:|
| Function records inventoried | 2,113 |
| Total inventoried function bytes | 730,296 |
| Functions with saved candidates checked | 502 (23.76% of inventory) |
| Functions without saved candidates | 1,611 |
| Candidates compiled | 302 / 502 (60.16%) |
| Compilation failures | 189 |
| Unsupported backend/recipe errors | 11 |
| Exact allocated object sections and relocations | 162 |
| Exact candidates also passing the C frontend | 149 |
| Exact + frontend pass, excluding known reference-recovered/header-assisted tags | **101** |
| Binary-derived evidence records in the KB | **72,845** |
| Historical attempts logged before this audit | 31,094 |
| Full regression suite after syntax-scan scope correction | **1,997 passed**, 0 failed, 33.49 seconds |
| Census wall time, four compiler workers, existing workspaces | 42.37 seconds |

Provenance breakdown is essential:

| Recorded category | Exact objects | Also frontend accepted | Bytes in exact functions |
|---|---:|---:|---:|
| Other development candidates | 105 | **101** | 6,812 |
| Explicitly header-assisted | 10 | 9 | 992 |
| Reference-recovered | 47 | 39 | 29,628 |
| Total | 162 | 149 | 37,432 |

The broad exact-function count is **162 / 2,113 = 7.67%**. The size-weighted coverage is **37,432 / 730,296 = 5.13%**. The latter counts all bytes belonging to exact functions; it does not count coincidentally equal bytes in nonmatching functions. It includes reference recoveries and therefore is not an autonomous decompilation percentage. The 101 development candidates passing both checks account for 6,580 bytes, or 0.90% of inventoried function bytes.

## Resume wording

Recommended concise bullet:

> Built an AI-assisted N64 decompilation pipeline spanning 2,113 functions and 72,845 binary-derived evidence records; validated 101 C candidates with exact object-section, relocation, and compiler frontend checks.

Optional testing bullet:

> Maintained a 1,997-test regression suite covering binary analysis, candidate generation, compiler verification, and repair workflows.

For an interview, explain that these are accumulated development results in a known compiler/header environment. The 101 excludes the functions identified by the existing strategy tags as reference-recovered or explicitly header-assisted. These tags are not an exhaustive source-lineage or contamination audit, and “other development” does not mean binary-only, autonomous, or held-out. Project headers are available during compilation.

Prefer the counts above to a vaguely defined “byte accuracy” percentage. Do not claim the full game was decompiled, a ROM was rebuilt from generated C, or that 162 functions were independently solved by the system.

## Method and receipts

- [Machine-readable census](report.json) includes all 2,113 outcomes and per-candidate certificates/errors. Unattempted functions and failed candidates remain in the denominator.
- [Frozen inventory](inventory.json) records selection policy, pre-run counts, start time, and Git HEAD. The working tree had existing uncommitted development changes; HEAD alone is not a complete source snapshot.
- [Audit runner](../../resume_audit_20260908.py) selects one saved candidate per function, ordered by historical exact flag, compilation, similarity, and newest attempt ID. It does not search alternate candidates when the selected candidate fails. Consequently this is measured coverage of the selected candidates, not an upper bound on all saved work.
- The runner snapshots the production KB, logs verification to a disposable copy, and leaves the production attempts and game source unchanged. Per-function JSON receipts retain original attempt IDs, source hashes, frontend results, and available exactness certificates.
- Exactness means equality of allocated ELF text/data/BSS sections and relocation expressions under the same link environment. Debug/ABI metadata and final link layout are excluded. No whole-ROM claim is made.
- Eleven errors comprise one object-postprocessing requirement and ten unsupported `-mips3 -32` runtime-helper recipes.
- [Initial regression output](../resume-20260908-tests.log): 1,996 passed, one failed in 261.95 seconds. The syntax-scan test encountered a cached third-party Python 2 Ghidra script. Its traversal was corrected to exclude caches and generated snapshots before a full rerun; the original failure is preserved.
- [Final regression output](../resume-20260908-tests-final.log) and [JUnit results](../resume-20260908-tests-final.xml): **1,997 passed in 33.49 seconds**, exit code 0. The only production-test change is source-discovery scope in `tests/test_units.py`; compiler and correctness assertions are unchanged.

Run the census with the WSL virtual environment from the project root:

```sh
/home/grant/decomp/sbk1/.venv/bin/python -m eval.resume_audit_20260908
```

The runner refuses to overwrite its receipt directory. Use a new output directory in the runner for another census. All original selected sources remain identifiable by immutable attempt ID and SHA-256 in the production KB.
