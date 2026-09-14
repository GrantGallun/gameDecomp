"""Opt-in early edit-addressing instructions; validators remain authoritative."""

GUIDANCE = '''PATCH ADDRESSING — read before investigating the C:
Every edit needs exactly ONE location: a slot, or a nonempty unique old substring.
For insertion, NEVER use "old":"". Use the DECLARATIONS slot for file-scope
declarations. For a new local, replace the function-opening line slot with that
complete line followed by the local declaration. Do not put locals at file scope.

JSON edit forms (placeholders illustrate format; do not copy them as C):
{"slot":"DECLARATIONS","new":"<supported file-scope declarations>\\n"}
{"slot":"L23","new":"<replacement for the COMPLETE current line 23>"}
{"old":"<nonempty text occurring exactly once in CURRENT C>","new":"<replacement>"}
Choose actual line slots from the CURRENT C table below; L23 is only an example.
Keep the required outer kind/hypothesis/edits JSON object and edit-count limits.
Do not repeat a rejected empty anchor, no-op, or ambiguous substring: use a slot.
No #pragma, #include, assembly, guessed layouts, duplicate typedefs, or changes
to a locked public signature. A valid location does not establish a correct type:
use supplied header/binary evidence, and preserve unknowns when evidence is absent.

'''


def prepend(prompt: str, *, slots_only: bool = False) -> str:
    guidance = GUIDANCE
    if slots_only:
        guidance = guidance.replace(
            'Every edit needs exactly ONE location: a slot, or a nonempty unique old substring.',
            'Every edit must use a source slot. Old-substring edits are forbidden in this mode.')
        guidance = '\n'.join(line for line in guidance.split('\n')
                             if not line.startswith('{"old":'))
    return guidance + prompt
