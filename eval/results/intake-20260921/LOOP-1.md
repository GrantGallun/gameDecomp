# The loop, iteration 1: three fixes, one of them +6 on the frame

Frozen 200-state frame, `eval/results/intake-20260921/`. Every iteration keeps its own receipt
(`wide-intake-<tag>.json`, `fault-history-<tag>.json`), so a later round can be compared against any
earlier one. One command per iteration: `.cache/recon/loop.sh <tag> <previous-tag>`.

| receipt | IDO compiled | IDO+frontend | byte-exact |
|---|---:|---:|---:|
| `acceptance2` | 32 | 19 | 2 |
| `orphan2` (absent-type fix, corrected) | 32 | 19 | 2 |
| **`globals`** | **38** | **21** | 2 |

No state lost compilation or frontend acceptance in either step.

## What was done, and what it bought

### 1. The absent-type gap — reproduced, fixed, and neutral

`opaque_variant` asked the headers for a tag, found none, and moved on. With `UnseenActor *arg0`
dereferenced at 0xC the planner returns

```c
typedef struct { char pad00[0xc]; s32 unkC; } UnseenActor;
```

valid C, correct offsets, naming the type the source already spells — and the action discarded it while
repairing the equivalent `void *` case. The lookup is for COMPLETING A TAG THE HEADERS DECLARED, not a
precondition for declaring anything.

Now accepted, and **measurably neutral on this frame**: the branch fires on 21 states, and each of the 15
that still do not compile carries two to five other classes. Closing a real coverage gap moved nothing,
which is the frontier-versus-distance lesson again, on a fix of my own.

### 2. Two defects the fix exposed, both mine

* **A base type was being declared as a struct.** `typedecl.plan` will plan for a parameter written
  `s32 *arg0` and dereferenced, and `typedef struct { ... } s32;` is not a repair. Letting it through cost
  a compiling state — `osEPiRawReadIo` 79.15 → **0.0** — because the collision guard then abandoned the
  whole action, including the `void *` declaration that state needed.
* **The collision guard was all-or-nothing.** It now drops the offending declaration and keeps the rest,
  and every drop carries a reason into the receipt (`declined` is in the runner's detail whitelist now,
  which is also what made the regression visible in one line rather than in an afternoon).

Both fixed; `orphan2` restores 32/19/2 exactly.

### 3. The globals starved by a truncated diagnostic list — **+6 / +2**

`globals_variant` names the symbols it will declare from the compiler's diagnostic text, and the compiler
on that path is cfe, **which stops at the first error**. So on any candidate whose first error is a
placeholder or a syntax error, the `undeclared identifier 'gRegionAllocPtr'` line is not in the text, the
action's `wanted` set is empty, and it reported "produced no change" — indistinguishable from an action
with nothing to do.

The frontend reports every independent blocker in one pass; that is the whole reason
`solver/frontend_diagnostics` exists, and nothing was feeding its names to this action. Merged in:

| | before | after |
|---|---:|---:|
| states where `globals_variant` changed the source | 5 / 200 | **140 / 200** |
| IDO compiled | 32 | **38** |
| IDO + frontend | 19 | **21** |

Gained, with no losses: `updateCharacterSelectMenu`, `initRaceItemTextureEffects`,
`updateBouncingItemProjectile`, `updateCloseRangeHomingItemProjectile`,
`drawCharacterSelectCourseStatsBadge`, `drawMainMenuModeSelectIcons` — and
`drawTwoPlayerRaceHud` and `drawCharacterSelectCourseStatsBadge` also pass the frontend.

## What the triage says the remaining names are

`name-triage.json`: 374 unresolved names across 181 states, classified by **why** each one is unresolved.
The first version of this classifier merged `unknown type name 'X'` with `use of undeclared identifier 'X'`
and put 314 value-position names into the type buckets — `bitwise` was about to be "repaired" by declaring
an opaque struct. The kind decides which questions get asked:

| bucket | names | what it needs |
|---|---:|---|
| **`unresolved-global`** | **211** | an extern declaration typed from the target's accesses — **`globals_variant`**, now repaired |
| `unknown-identifier` | 66 | nothing known about it; abstain |
| `known-function` | 32 | an extern prototype from symbol evidence |
| `undeclared-local` | 19 | m2c used its own temporary (`var_v0`, `temp_v1`) without declaring it |
| `m2c-dialect` | 18 | `bitwise`, `unaligned`, `sp` — a REWRITE; no header can ever supply them |
| `opaque-is-enough` | 17 | a tag declaration, no layout |
| `needs-layout` | 8 | offsets and widths from the binary |
| `header-exists-not-included` | 3 | resolve the include dependency |
| `tag-without-alias` | 0 | — |

The largest bucket and the fix that moved the frame are the same bucket, which is the loop working as
intended. The triage is a residual measurement, not a distance and not a difficulty ranking.

## Next

1. **`m2c-dialect` (18 names) is a pure rewrite** and nobody owns it: `unaligned` has no handler at all,
   and `bitwise` has one (`solver/m2c_context.lower_bitcasts`) that is reachable through
   `repair_context.normalize` but is not an action.
2. **`undeclared-local` (19)** is a draft defect — m2c used `temp_v1` without declaring it — and the
   declaration is mechanical.
3. **`known-function` (32)** should be one extern prototype each from the `functions` table, and
   `header_variant` may already cover some of them.
4. Re-run the triage after each fix, because the buckets move: the `globals` run removed
   `incompatible-int-pointer` from 21 states and ADDED `undeclared-identifier` in 29, which is the frontier
   advancing, not a regression.
