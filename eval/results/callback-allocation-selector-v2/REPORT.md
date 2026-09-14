# Backend-guided counter load probes

Thirteen additional builds targeted the promoted free-count reload identified in the backend dump. None improved98.511.

Volatile-load diagnostics did **not** demote the reload from v1 or move the pool result away from t0. They merely prevented the assembler from forwarding the stored value into the reload: an actual `lhu v1` remained, scoring92.979. The signed volatile view also added sign/mask work, scoring96.028. These are diagnostics only because volatile changes the source memory-effect contract.

Plain pointer casts, signed views cast back to unsigned, and array/struct/union declarations of the same counter were inert at98.511. Keeping an explicit pointer to the counter across its check, decrement, and reload regressed to88–92.

No candidate was retained or integrated.
