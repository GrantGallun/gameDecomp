# Single predicted lifetime probe

Retained parent: campaign attempt 108368, SHA-256 `a393535d050115637b0582fceb7ff52c8c1efa34c3850dcafce3bec6d257f43f`, score 99.615. In the `temp_v0 == NULL` arm, replace the sole `gActiveSoundHandleListTail = NULL;` with `gActiveSoundHandleListTail = temp_v0;`. This is semantically equivalent in that arm, since `temp_v0` is tested for null by the surrounding branch.

Prediction before compile: if uopt retains that use as part of `temp_v0`'s live range, `temp_v0` must remain live through the inner `temp_v1` branch, making color 1 (`v0`) unavailable when coloring the `temp_v1` range. The second `4(a0)` load, its `beqz`, and `sw` base should then change together from `v0` to `v1`. The probe fails if those three operands remain `v0`. Inspect all other instruction and operand differences as collateral: the tail store may become `sw v0` instead of `sw zero`, or the compiler may fold the null fact and leave allocation unchanged. Exactness and frontend checks decide whether any change is usable.

This is a source-lifetime experiment, not a claim that the target had this C expression. One normal private compile; retain the source, output, diff, and SQLite attempt row regardless of outcome. No campaign or KB write.
