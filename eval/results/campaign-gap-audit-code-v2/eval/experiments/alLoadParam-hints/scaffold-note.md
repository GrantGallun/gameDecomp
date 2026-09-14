This arm starts from a SUPERVISOR-TYPED SCAFFOLD. All local declarations, f and p,
and the public signature are ALREADY corrected. Do not insert or redeclare them.
Only replace the member-access statements still containing unk fields using the
field map. Use CURRENT source slots, never earlier drafts' line numbers.
Correct ALL remaining member accesses in one transaction (about 37 lines).
In particular f->filter.handler, NOT f->handler; waveform->len, NOT ->state.
The source already restores the reference TU's diagnostic push / ignore-return-
type / pop scope, which was read as metadata only. Preserve it. Do not add any
return expressions: the target has legacy unspecified return-register behavior.
The ordinary compiler, all other frontend checks, and byte verification remain
active. Passing this scoped policy is not a proof of well-defined return values.
