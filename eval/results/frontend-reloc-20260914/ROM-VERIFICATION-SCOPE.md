# Scope: ROM-backed certification for functions with their own rodata (2026-09-14)

## Where it fails today

`solver/function_boundary.certify` is the existing ROM-backed certificate. It lets a candidate that is not
section-exact still reach `function_exact_pending_integration`, and from there the whole-ROM integration gate. It
refuses on the first check whenever either object has a section besides `.text`:
`function-only check does not support allocated data/BSS`.

`boundary_probe.py` → `boundary-probe.json` covers all 7 functions that `relocation_names` brought to score 100:
- **Literal source:** all 7 refused on that check. The target `.rodata` is 8–24 bytes (asm-built); the candidate
  `.rodata` is 16–32 bytes (IDO pads it to 16-byte alignment).
- **Extern source:** 6 refused on the same check, because the target object has `.rodata` even when the candidate
  does not. waitForTitleDemoRaceIntroStart was refused with `unsupported relocation symbol`, since its target
  relocates against `.late_rodata`.

The relocation identities show why a per-site resolution works. At the same text sites:

| object | `%hi/%lo` relocation identity |
|---|---|
| target (asm-built) | local label in the section: `('section', '.rodata', 12, 1, 0)` or `.late_rodata` |
| literal candidate | section symbol with the addend in the instruction: `('section', '.rodata', 0, 0, 0)` |
| extern candidate | named symbol: `('external', 'D_800E176C', 1, 0)` |

## Proposed extension (function_boundary schema v3)

Keep every existing check. Add one more admissible relocation class: data relocations resolved per site against the
ROM.
1. **Allow data sections.** Permit `.rodata` and `.late_rodata` in either object. BSS and writable data stay refused.
2. **Resolve target sites.** Resolve each target data site `(section, value)` to a VRAM address using the rodata labels
   in `target.s` and `symbol_addrs.txt`.
3. **Resolve candidate sites.**
   - A section-relative site's offset is its HI/LO addend.
   - An external site's address comes from `symbol_addrs.txt` and must equal the target site's address.
4. **Check the datum.** For each site, the candidate datum must equal the ROM bytes at the resolved address:
   - the candidate's own `.rodata` bytes at its offset (string through NUL, or 4/8 bytes for `lwc1`/`ldc1`);
   - for an external site, only the address equality in step 3.

   Padding from section alignment is outside every datum and never compared.
5. **Relocate and compare.** Relocate both functions with the resolved addresses (existing `relocate`, extended to
   data groups) and require equality with the ROM function bytes. The status stays
   `function_exact_pending_integration`.
6. **Leave TU placement to integration.** The certificate claims the function bytes and the data it reads. It does not
   claim that the data lands at that address after a TU build. `integration_gate` already proves that by rebuilding the
   whole ROM, and it does not change.

Tests follow the house rule: a fire test on each motivating residual (literal, extern, `.late_rodata` float), plus
declines for a wrong datum, a wrong address, writable data, and a section-relative site outside the datum.

## Expected reach (measured, checkpoint 16902)

Not large, and this is not a new source of matches. It converts near-misses the pipeline already finds.
- **7** offline relocation-name functions at score 100. They need `relocation_names` in the campaign too, a second
  small amendment.
- **10** pending campaign nodes at score 100 that are not certified:
  - 2 `allocated data/BSS` (drawTitleScreenStartPrompt: target `.rodata` 48 bytes, candidate external `D_800E11F0`);
  - 5 `text attributes or relocation expressions differ` (e.g. waitEndingJamPhase17), cause not yet separated;
    diagnose before claiming;
  - 1 `unsupported relocation or relocation outside function`, where `.text` is 144 vs 128 bytes (a padding
    difference);
  - 2 with no boundary record.
- **3** register-allocation closures that are byte-identical but not object-exact (libultra register-symbol
  relocations, TU padding nops). Check them against v3 before assuming coverage.

Order of work: build v3 with tests, then replay the certificate on the 17 cases above offline, and run the whole-ROM
integration gate on whatever v3 admits. Only then amend the campaign.
