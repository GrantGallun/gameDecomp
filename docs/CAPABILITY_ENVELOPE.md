# What the existing machinery should be capable of

The capability envelope describes the intended behavior of existing components
under correct implementation and explicit prerequisites. It is independent of
whether a particular attempt succeeded. It covers a declared subset of 18
components, with implementation, test and caller references pinned by hashes.
Unlisted machinery remains unassessed.

The [actual map](../eval/results/capability-envelope-20260922/analysis/MAP.md)
applies those contracts to 100 recorded compiler observations across 30 worlds.
The [result](../eval/results/capability-envelope-20260922/RESULT.md) explains the
three unresolved timer cases. No new compiler trials were needed for this audit.

## Mathematical representation

For component `i`, retain a contract:

`C_i = (domain D_i, prerequisites P_i, output O_i, callers W_i, limitations L_i)`.

For a particular source/target/compiler state `s`, evaluate each predicate as
**true, false or unknown**. When its domain and prerequisites are true, a correct
implementation should supply the declared kind of output. Caller wiring is a
separate condition for whether the selected pipeline can invoke it.

`D_i(s) AND P_i(s) AND connected(i, caller) => expected output of kind O_i`.

This is a reviewable specification, not a proved implication about the code.
Candidate constructors promise candidates within a restricted representation;
they do not promise compiler acceptance or exactness. A checker promises an
assessment of a supplied candidate; it does not construct one.

The composition question is whether one component's output meets the next
component's domain and prerequisites. For example, producing a byte-view draft
can remove member errors while leaving the draft outside a named-field wide-value
repair's grammar. Individual component coverage does not establish a complete
path from the binary to matching C.

The implementation records these obligations and compares them with observations.
The generated-potential layer below composes contracts within explicit depth and
node bounds. It does not implement complete reachability or fit success probabilities.
The existing empirical transition model remains a separate source of observations
about which actions have worked.

## Interpreting a component assessment

| Status | Meaning | Action |
|---|---|---|
| `expected-within-contract` | Every declared domain and prerequisite predicate is true | Expect the specified output; investigate a recorded failure to emit it |
| `outside-declared-domain` | A domain predicate is false | Change representation, select another constructor, or investigate an overbroad generator guard |
| `missing-prerequisite` | A required condition is known false | Establish it before expecting this component to work |
| `undetermined` | At least one required fact is unknown, with no established domain violation or missing prerequisite | Measure the missing fact |
| `available-not-connected` | A needed component exists elsewhere but this caller does not invoke it | Inspect wiring and prerequisites together |

Each row retains `applicability` and `connected` separately. A disconnected
component can still have missing or unknown prerequisites; adding a call alone
does not establish that it will solve the target.

When proposal inspection records no candidates despite an expected constructor,
the map emits `expected-construction-not-observed`. A candidate emitted outside
the intended domain yields `outside-contract-construction`. These identify a
disagreement to investigate, not a proved implementation bug. The contract itself
can be incomplete and must remain reviewable.

Negative compiler feedback stays attached to the tested source and context. It
does not erase the intended contract or establish global impossibility. A child
with fewer errors in one class can still have no net improvement if it introduces
other errors. The map retains both counts and newly introduced blocker classes.

## Current component inventory

The catalogue in [capability_contracts.py](../solver/capability_contracts.py)
contains the exact predicates and reference hashes.

| Contract | Intended output | Important boundary |
|---|---|---|
| `cfg` | Control-flow facts | Unresolved indirect successors remain unknown |
| `dataflow` | Access and call facts | Does not recover arbitrary types or addresses |
| `m2c_draft` | C draft | Supported assembly subset; ABI composition and validation remain obligations |
| `header_signature` | Public-signature candidate | Supported corresponding widths and ordinary edit sites |
| `void_members` | Member-access candidate | Matching target access and unambiguous use |
| `scalar_members` | Indexed-access candidate | Indexable base and divisible element width |
| `global_fields` | Global-field candidate | Included declaration and matching symbol, offset and width |
| `frontend_abi` | Diagnostic-driven ABI candidate | Closed supported idioms |
| `byteview_redraft` | Fresh C draft with public ABI | May leave calls unresolved or leave another repair's grammar |
| `type_layouts` | Measured layout candidates | Requires target compiler layout probes; assignments remain hypotheses |
| `wide_parameters` | Wide-parameter candidate | One named split parameter; not arbitrary mixed signatures |
| `wide_operations` | Wide-value operation candidate | Measured unsigned fields and closed named high/low idioms |
| `wide_returns` | Wide-return candidate | Admitted callee evidence and accounted temporary uses |
| `call_arity` | Call projection candidate | Word-sized arguments; not general wide ABI reconstruction |
| `storage_search` | Storage/address/parameter variants | Bounded search |
| `model_edits` | Model-proposed candidate edits | Correct implementation does not imply omniscient weights or complete search |
| `semantic_check` | Finite behavioral evidence | Bounded execution is not all-input equivalence |
| `object_check` | Exact-object decision | Requires a compiled candidate; supplies no synthesis guarantee |

Caller labels refer to the three assessed entrypoints: `theory`,
`compile-recovery`, and `model-repair`. They are not a repository-wide call graph.
Some tools are also callable independently.

## Whole-function ceiling

The existing constructors and search policies do not provide a complete inverse
compiler. Perfect implementation therefore does not imply a numerical exact-match
ceiling of 100%. The map keeps four distinct questions:

| Goal | Evidence sufficient for the current report |
|---|---|
| Exact C reachability | A concrete source-bound exact object certificate is a witness |
| Semantic C behavior | An exact object witnesses the same machine behavior in the same linked context; finite execution checks alone do not prove all-input equivalence |
| Original source recovery | These observations do not identify unique original source |
| Binary preservation | A retained original target object supplies a fallback, without counting as C reconstruction |

For a nonexact target, construction, repair composition, search completeness and
all-input equivalence remain explicit unresolved obligations. This does not mean
that all-input proof is required before trying another repair: a later exact
object witness can resolve the practical matching goal directly.

The instruction inventory is conservative and is not an ISA semantics proof. It
does not expand assembler macros. In the recorded panel, macro definitions and
`nonmatching`/`endlabel` wrappers leave coverage unknown, even for functions with
known exact C witnesses. Those entries are representation/normalization gaps,
not evidence that the CPU instructions are unsupported. Floating-point or unknown
operations likewise do not silently become covered.

## APIs and integration

`solver.capability_map.assess` takes a source, function, verdict, assembly,
context, catalogue, predicate facts and caller. `validate_assessment` reconstructs
all derived fields from retained inputs. `compare` evaluates parent/child
observations; `inspect_expectations` compares recorded proposal inspections with
constructor expectations. Caller-supplied facts remain assertions until grounded
by their producer; checksums alone do not authenticate arbitrary evidence.

`workspace_assessment` reads target assembly/object identity and included header
declarations. It does not read reference implementation bodies, generate C, probe
layouts or compile. Unsupported or unmeasured conditions stay unknown.

`eval.theory_planner.TheoryOnline` accepts an optional `capability_assessor`
callback with signature `(source, verdict) -> assessment`. Bind the callback to
the world's existing context, repository, workspace and catalogue. The planner
checks full verdict/context/source identity and retains assessments, transitions
and expectation conflicts in `theory_summary()['capability_envelope']`.
Without this callback, prior output and action ordering are unchanged. These
diagnostics do not automatically mutate a generator, change priorities or retry.

The retrospective command is:

```text
python -m eval.capability_map --report PATH/paired/report.json --output NEW_DIRECTORY --caller theory
```

Run it in native WSL with the recorded workspaces available. It verifies the
original frozen artifacts, compiler/header/assembly identities, compiler worlds
and read-only attempt receipts before exporting JSON and Markdown. The output
directory must be new. Retained proposal observations are explicitly compared
against the current intended catalogue, not mislabeled as past expectations.

The [frozen run](../eval/results/capability-envelope-20260922/RESULT.md) includes
reproducible code identities, Windows/WSL test receipts and a separate export
audit. All outputs remain training-ineligible. Production acceptance still
requires the ordinary compiler/object certificate.

## Generated map: operation schema and potential

`solver.capability_potential` implements bounded conditional reachability.
`solver.capability_operations` instantiates transition rules from the existing
18-component assessment. Primitive contracts and effect/frame specifications
remain authored; paths and potential are computed, not manually enumerated.
The adapter uses the catalogue's reviewed caller declarations; this is not an
automatic discovery of every Python function or runtime call edge.

The generated map evaluates operations against the current target,
representation, evidence and available machinery. Its operation schema includes:

| Field | Meaning |
|---|---|
| `requires` | Input representation and evidence needed for the operation |
| `produces` | Immediate outputs specified by its contract |
| `preserves` | Properties the operation promises to maintain |
| `invalidates` | Assumptions that must be checked again after the operation |
| `potential` | Further capabilities its outputs could unlock through conditional downstream paths |

Potential is generated for the particular state and goal by composing operation
contracts. It is not a fixed optimism score or a synonym for observed success.
Each generated goal path retains:

- `goal`: the downstream capability or goal it unlocks.
- `via`: the supporting sequence of operations and contract references.
- `conditions`: assumed prerequisite values with the operation and path step
  where each must be established. Current values live in the corresponding node.
- `gaps`: empty for generated paths; blocked transitions and goal summaries retain
  unmet predicates and declared producer IDs, including an empty list when there
  is no declared producer. A listed producer need not itself be applicable.
- `evidence_status`: `contract-inference`, or `supplied-input` for a root goal
  already established by input facts. These labels never certify the supplied
  facts or turn modeled outputs into compiler observations.
- `next_test`: the concrete experiment that would resolve an outstanding condition
  or test an expected construction.

For example, a compatible field representation plus measured layout evidence
could enable the existing wide-operation constructor. That potential remains
conditional on its named-field and closed-idiom requirements. A byte-view draft
does not satisfy those requirements merely because it removed member errors.
Any missing conversion must appear as a gap, not an invented executable edge.

A failed experiment updates the affected condition or contract discrepancy; it
does not automatically erase every alternative path. Conversely, a violated
condition prevents that path from being reported as currently executable. Search
bounds and unexamined paths remain explicit, so an empty generated set cannot
establish impossibility. Potential alone neither confirms a pattern nor changes
production behavior or compiler acceptance.

### Mathematical semantics

An abstract state is a map from predicate names to `true`, `false` or `unknown`.
The current API instantiates predicates for one target/context; it does not
implement arbitrary quantified formulas or a general theorem prover.

For operation `r`, `T_r(x,x')` requires its preconditions in `x`, assigns its
produced facts in `x'`, and carries only explicitly preserved facts. Invalidated
and otherwise unspecified facts become unknown. Unknown preconditions permit a
conditional branch with an explicit assumption; known opposite values block it.

```text
R_0(x) = {x}
R_(h+1)(x) = R_h(x) union {z | some y in R_h(x), some r: T_r(y,z)}

Potential_h(r,x) = {g | some x': T_r(x,x') and
                       some y in R_(h-1)(x'): g(y)}
```

Branches retain their own facts and assumption history. No state union pools
facts across incompatible alternatives. Preserved assumptions cannot later take
the opposite value without an intervening change or invalidation. A goal must be
present as an input fact or produced output, not merely assumed as a prerequisite.
Directly reasserting the same prerequisite assumption does not establish it.
Every conditional path keeps its full assumptions even after subsequent outputs.

The finite breadth-first search defaults to depth 3 and at most 256 nodes.
Repeated states along a path are suppressed. Depth and node cutoffs are explicit;
reported paths are not an exhaustive list beyond those limits. The `potential`
dictionary groups goal paths by their first operation. Paths are possibilities
under contracts, not probabilities, causal gains or a ranking by experiment cost.

### Concrete adapter boundaries

Each instantiated operation includes hashed owner/test/caller references. Its
`output.<id>` predicate denotes that component's declared artifact on the modeled
path. It does not mean compiled, semantically equivalent or exact C. Only needed
component outputs become default goals, but all components remain available as
possible prerequisite suppliers.

Read-only analyses preserve existing facts. The measured-layout contract can
produce `measured_layouts`, enabling the wide-operation contract when its other
conditions hold. C mutations preserve fixed target/tool facts, forget source
shape and layout assumptions, and set `compiled_candidate` and
`source_bound_feedback` false because the new source has no corresponding
receipt. This does not assert that the new candidate cannot compile.

The adapter does not synthesize a successful compile transition, exactness
certificate or arbitrary representation conversion. Diagnostics-dependent
continuations need a new actual assessment after compilation. Other unmodeled
effects remain unknown. Potential is useful for choosing what to investigate;
the ordinary machinery still has to execute the operation and validate its output.

### Running the generator

```python
from solver.capability_operations import from_assessment

generated = from_assessment(assessment, max_depth=3, max_nodes=256)
```

For custom instantiated Boolean rules, use
`solver.capability_potential.generate(facts, operations, goals, context=...)`.
`validate(report)` recomputes every derived node, path and potential entry from
the retained inputs. This detects altered derivations, not forged input evidence.

To attach generation to live laboratory planner reports, pass both
`capability_assessor=callback` and `capability_potential=True` to `TheoryOnline`.
Generated reports appear under `capability_envelope['potential'][node_id]`.
The option defaults off and adds no compiler calls or action-priority changes.

To analyze an existing assessment or select a node from an assessment-world file:

```text
python -m eval.capability_potential --assessment FILE --node root --output NEWFILE --max-depth 3 --max-nodes 256
```

The command refuses to overwrite output. It reuses retained evidence, without
refreshing workspace facts or claiming that an older contract describes changed
code. See the [generated-potential experiment](../eval/results/generated-potential-20260922/RESULT.md)
for the symbolic composition test, timer analyses, and native/Windows verification.
