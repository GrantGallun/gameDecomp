# Predeclared assignment-order probe

The retained attempt 157771, privately reproduced as 157772, has hash
`24a97e50a489ad6763031ecf1c23a43678e3a8de234578a6240f584614434a72`.
Use that durable source, not the isolated workspace's unrelated `base.c` seed.
Its first state==2 arm assigns `var_v1 = 0x80` then `var_t0 = 0x80`.
The only normalized differences are those constant loads at indices 10 and 12,
on either side of an identical branch whose delay slot is index 12. Later uses
already have the target registers.

One proposal: reverse only those first-arm assignments to `var_t0` then `var_v1`.
Keep the outer else and every other source byte unchanged. If source order
controls scheduling of these two ready loads, their positions should reverse,
making the entire instruction stream equal. If not, record the result and stop;
do not infer a target live-range split from the mixed-vote diagnosis label.

One ordinary scoring call, logged as a child of 157772, with frontend and object
certificate required for acceptance. Also enumerate the existing full mutation
stream with the baseline's saved fresh evidence to determine whether this exact
source edit is already offered; enumeration performs no additional compiles.
No source import, live mutation, production generator change or integration.
