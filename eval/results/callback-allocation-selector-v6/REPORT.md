# Final stage-derived assignment pair

The actual backend stages showed two consecutive parameter masks; the assembler removes one while preserving the temporary-allocation advance. Two final probes attempted to defer writing the parameter until after using a normalized local for dispatch:

```c
normalizedType = (u16)(s16)type;
t8 = normalizedType;
type = normalizedType;
```

A u16 local scored96.986; a u32 local scored95.390. Both compiled, neither was exact or retained. The stage-derived source relationship did not produce the required prologue.

The98.511 champion,98.156 persistent-register alternative, and97.979 prologue-only alternative remain preserved separately. Both diagnostic alternatives have76/76 verified semantic passes. Source probing stops here; no further changes were integrated.
