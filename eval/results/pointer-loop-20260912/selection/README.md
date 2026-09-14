# Pointer-loop candidate selection

Immutable checkpoint 6136. Short read-only queries fetched only checkpoint-selected
attempt rows; each source hash and function address was verified, as were raw
target.s pins. No reference C, live database copies or campaign changes.

The ten compiled nonexact functions at least 1 KiB and scoring at least 90 had
no clear pointer traversal suitable for this experiment. Their highest-scoring
explicit loops were scalar sprite-coordinate counters. The expanded search used
a score floor of 75; candidate sources remain current checkpoint selections.

The final three actual traversals are in pointer-shortlist.json and
traversal-pool/FUNCTION/{source.c,target.s,target-body.s,diff.txt,metadata.json}:

| Function | Bytes | Score | Selected attempt | Recorded semantic status |
|---|---:|---:|---:|---|
| drawCharacterSelectCoursePreviewFrame | 1,224 | 83.379 | 53873 | observed_pass |
| osPfsReadWriteFile | 1,024 | 84.730 | 25192 | observed_pass |
| updateRaceHud | 1,760 | 86.736 | 52990 | observed_pass_with_execution_debt |

The preferred frame-rendering candidate traverses u16 tile entries in loops of
16 and 2 iterations. Its initial pointer expression scales selector*42 as u16
elements, whereas the target adds selector*42 bytes. The retained object diff
shows the candidate's extra left shift before adding the table base. The corner
lookup has the same scaling concern. This is a concrete target/source arithmetic
discrepancy despite the broad synthetic panel's recorded observed_pass.

The private pilot's selector_cases.py adds three explicit finite test inputs:
selector 1, selector 9, and the split-screen/race-type branch forcing selector 9.
Distinct halfwords make doubled indexing observable in drawMenuSpriteTile's
fourth argument. The synthetic mapped span covers both candidate and target
reads so the test does not depend on a memory fault. It does not claim the span
is the real C array extent or real captured game state. The fixture requires the
binary-bound tile/corner addresses, which alias at an offset of 40 bytes.

Target-only fixture execution returned normally for all three inputs with 21
tile calls each. Candidate comparison belongs to the separate compiler pilot;
this selection audit makes no candidate-correctness claim.
