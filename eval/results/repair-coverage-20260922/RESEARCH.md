# Repair coverage experiment

User-authorized objective: increase useful repair coverage, then establish whether
the existing search can select successful paths. Use generated drafts and target
assembly only; do not inspect reference function bodies or train on this exposed
development panel. All compiles use native isolated workspaces and explicit logs.

The first inspection found a concrete routing gap: `reconstruct_helper` already
handles eight wide runtime helper shapes through compile recovery, but neither
the tool registry nor the regalloc-only scheduler exposes it. Three frozen
development states (`__ll_rem`, `__ull_rem`, `__ull_div`) immediately reach exact
through this existing mechanism. This is reused capability, not new discovery.

Two additional target streams motivated new recipes: quotient/remainder stored
through two pointers with a wide argument and a trailing halfword; and signed
remainder adjusted to the divisor's sign. In 21 logged exploratory compiles,
both reached object-exact with frontend acceptance. The `u16` divisor candidate
matches the first stream despite its signed halfword load; the complete compiler
output, not the isolated load mnemonic, adjudicates this type hypothesis.

Bounded implementation: extend only whole-stream helper reconstruction; keep
callee/runtime admission unchanged. Register a noncompiling `reconstruct-wide`
source transformation through the existing tool API and scripted order. Its
preconditions require target ELF ABI and the configured MIPS III/o32 recipe.
Every candidate must still pass the normal compiler/frontend/object gates.

Validation: tests must fire on both motivating binary streams and reject changed
memory offsets, operations, traps and ABI. Fresh public-action runs compare the
old repair menu with the expanded menu at equal ceilings. Separate sibling helper
functions test route coverage after the generator is frozen; they are explicitly
development transfer cases, not independent algorithm discoveries. Independently
recompile all successful sources, then append correctly tiered inventory receipts.
Preserve production source and the global match ratchet.
