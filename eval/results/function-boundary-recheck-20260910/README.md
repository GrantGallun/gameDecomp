# Production function-boundary replay

Final result: **23 function extents certified and revalidated**, comprising 18
previously pending functions and five previously function-exact functions.
See [validated.json](validated.json) for input hashes and full certificates.

The remaining debug-viewer function is rejected for allocated data/BSS and also
has a saved frontend failure. The two other frontend-only failures from the wider
21-function audit were not part of this 24-object boundary cohort.

All 23 successes have saved frontend passes bound to compiled source/object hashes;
frontend compilation was not rerun. This is exposed DEV evidence with assisted
project context, not a held-out recovery metric or whole-ROM verification.

Negative controls reject shifted/missing external symbols on all 22 applicable
functions. rmonPrintf has no relocations, so symbol mutations are inapplicable.
Incorrect function addresses and ROM annotation offsets are rejected on all 23.
56 boundary, object-certificate and campaign regression tests pass, including
different intact HI16/LO16 pairings that agree, and carry-sensitive pairs that do not.

The main-tree checker is implemented; the frozen campaign and its counts have not
been modified. These function certificates still require isolated TU integration.
Earlier report*.json files are development iterations; use validated.json.
