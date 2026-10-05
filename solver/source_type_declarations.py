"""Type declarations the sibling translation unit already carries, gated on binary corroboration.

THE GAP. When m2c loses a struct's body it writes the access anyway:

    void renderPickupShardParticle(PickupShardParticleActor *arg0) {
        arg0->transformDirty = 1;
    }

and the type is forward-declared or absent, so the draft fails with `incomplete definition of type
'PickupShardParticleActor'` (clang) or `'transformDirty' undefined` (cfe). Both say one thing: the type has
no visible members.

`header_variant` scans `include/**`. The definition is not there -- it is in the function's OWN translation
unit:

    src/race/course/race_course_props_and_pickups.c:
        struct PickupShardParticleActor {
            char pad0[0x10];
            /* 0x10 */ u16 spawnOffsetIndex;
            ...
            /* 0x44 */ s8 transformDirty;
        };

WHAT MAKES THIS DEFENSIBLE, and the two conditions are both mechanical rather than editorial:

  1. DECLARATIONS ONLY. The recovered text is a struct/union/enum body with no function bodies in it, so it
     is vocabulary rather than an answer. `include/**` is already admitted on exactly this basis and matches
     that lean on it are tiered `header-assisted`, never `SOLVED`. The src-side file is the same kind of
     object; it only lives in a different directory.
  2. THE BINARY HAS TO AGREE. Every member the draft actually USES must be annotated with an offset, and
     that offset must be one the target assembly touches through the relevant parameter. Measured on
     `renderPickupShardParticle`: the binary accesses param0 at 0x10, 0x18, 0x1c, 0x20, 0x30, 0x32, 0x34,
     0x40, 0x44, 0x48, 0x4c and every one of those is an annotated offset in the file's declaration.
     Members the binary cannot see (inside a nested `Vec3i`, say) are carried on the project's authority and
     are NAMED AS SUCH in the receipt, so "the binary agrees" and "the project says so" stay distinguishable.

WHAT IT DECLINES, and every decline names its reason: a type defined only as a forward declaration, a type
whose declaration is not in the function's own TU, a member the declaration does not have, and a member
whose annotated offset the binary does not touch. The last of those is the one that keeps this from being an
invention: it is exactly the check that rejected `ShopMenuWidgetActor` (`state` annotated 0x1c against
binary offsets 24, 26, 44..56).
"""
from __future__ import annotations

import re
from pathlib import Path

# `/* 0x44 */ s8 transformDirty;` -- the project's own offset annotations.
ANNOTATED = re.compile(r"/\*\s*(?P<offset>0x[0-9A-Fa-f]+)\s*\*/\s*(?P<decl>[^;]+);")
# `struct T {` / `union T {` / `enum T {` / `typedef struct T {` at a line start. The BODY is scanned
# separately and depth-aware (see `_body_span`); the first version tried to match the body in one pattern and
# could not cross the nested braces these structs are made of.
TYPEDEF_HEAD = re.compile(r"(?m)^[ \t]*(?P<typedef>typedef\s+)?(?P<kind>struct|union|enum)\s+"
                          r"(?P<tag>[A-Za-z_]\w*)\s*\{")
DECLARED_IN = re.compile(r"(?m)^\s*(?:typedef\s+)?(?:struct|union|enum)\s+(?P<tag>[A-Za-z_]\w*)\s*\{")
FORWARD_ONLY = re.compile(r"(?m)^\s*(?:typedef\s+)?struct\s+(?P<tag>[A-Za-z_]\w*)\s*;")
REFERENCE = re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s*\*")


def target_source_file(repo: Path, target: str) -> Path | None:
    """`build/src/race/ui/race_ui_effects.o` -> `<repo>/src/race/ui/race_ui_effects.c`.

    The mapping is the build's own: the compiler recipe names the object, and the object is built from the
    source at the same relative path. A target that is not `build/src/...o` is declined rather than guessed,
    because guessing it would attach another function's types to this draft.
    """
    if not target or not re.fullmatch(r"build/src/[\w./-]+\.o", target):
        return None
    candidate = (Path(repo) / (target[len("build/"):-len(".o")] + ".c")).resolve()
    root = (Path(repo) / "src").resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _body_span(text: str, opening: int) -> int:
    """Index of the `}` matching the `{` at `opening`, or -1.

    A DEPTH-AWARE SCAN, AND IT HAS TO BE. The first version matched a body with `\\{([^{}]*)\\}`, which
    cannot cross a nested brace -- and these structs are FULL of them:

        struct EndingCreditsEffectActor {
            union {
                struct {
                    union { /* 0x1C */ s16 offsetX; /* 0x1C */ u16 animFrame; };
                    ...
                };
            };
        };

    So the pass declined with `no body in ending_credits_effects.c` on the six actor types it was written
    for, while the body sat in the file. Same shape as every other silent decline this session: a pattern
    that quietly matches nothing is indistinguishable from a pass with nothing to do.
    """
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def type_bodies(text: str) -> dict[str, dict]:
    """{name: {text, tag, kind, members, annotated}} for every struct/union/enum body in `text`.

    `members` carries the OFFSET-ANNOTATED fields anywhere in the body, nested ones included, because a
    member the draft reaches through a nested union still has a real offset the binary can corroborate.
    """
    out: dict[str, dict] = {}
    for head in TYPEDEF_HEAD.finditer(text):
        opening = text.index("{", head.end() - 1)
        closing = _body_span(text, opening)
        if closing < 0:
            continue
        body = text[opening + 1:closing]
        tail = re.match(r"\s*(?P<alias>[A-Za-z_]\w*)?\s*;", text[closing + 1:closing + 80])
        alias = (tail.group("alias") if tail else None) or None
        end = closing + 1 + (tail.end() if tail else 0)
        members = {}
        for field in ANNOTATED.finditer(body):
            words = re.findall(r"[A-Za-z_]\w*", field.group("decl"))
            if words:
                members[words[-1]] = int(field.group("offset"), 16)
        record = {"text": text[head.start():end], "tag": head.group("tag"),
                  "kind": head.group("kind"), "members": members, "alias": alias, "body": body}
        for name in (head.group("tag"), alias):
            if name:
                out[name] = record
    return out


# Types that are NOT aggregates to be recovered: C's own, m2c's base spellings, and the SDK's opaque
# handles. Measured on the wide frame, the first version returned `u8`, `s32`, `s16`, `u16`, `Gfx`, `Vec3i`,
# `OSPfs` and more of the same kind, so every state declined with `no body in <file>: u8` and the action
# never reached a single real aggregate: 0 fired, 197 declined.
BASE_TYPES = frozenset({
    "void", "char", "short", "int", "long", "float", "double", "signed", "unsigned", "_Bool",
    "s8", "u8", "s16", "u16", "s32", "u32", "s64", "u64", "f32", "f64",
    "Gfx", "Mtx", "Vec3i", "Vec3s", "Vec3f", "Mtxf", "ALParam", "Acmd", "OSPfs", "OSMesg",
    "OSMesgQueue", "OSThread", "OSContPad", "OSIoMesg", "OSPiHandle", "OSViMode", "ALFilter",
    "ALSynth", "ALVoice", "Transform3D", "M2C_UNK",
})


def referenced_types(source: str, function: str) -> list[str]:
    """Aggregate type names the draft uses in a pointer position -- parameters first, then locals.

    A pointer position is where a name stands as a type, which `T *name` marks. Base types are skipped: a
    nested `Vec3i pos;` inside a recovered body is a MEMBER whose declaration the project carries
    elsewhere, not a type this draft needs declaring.
    """
    names: list[str] = []
    for match in re.finditer(r"\b(?P<type>[A-Za-z_]\w*)\s*\*+\s*(?P<var>[A-Za-z_]\w*)", source):
        name = match.group("type")
        if name in BASE_TYPES or name in names:
            continue
        names.append(name)
    return names


def parameter_index(source: str, name: str) -> int | None:
    """Which parameter position `name` is the type of, or None.

    THE FIRST VERSION WAS A NESTED QUANTIFIER that could not match: `[A-Za-z_][\\w \\t*]*\\bName\\s*\\*+`
    requires a word boundary between the type name and the `*`, and `Name *` has none. It returned no
    matches on every input, so `parameter` was always None and every recovery fell to
    `on_project_authority` -- the corroboration gate never ran. The tests caught it; this replaces the
    guess with an explicit parse of the signature's parameter list, which is also the only honest way to
    get `param<i>`, because that key is defined by the parameter's POSITION.
    """
    match = re.search(r"(?P<name>[A-Za-z_]\w*)\s*\(\s*(?P<params>[^)]*)\)\s*\{", source)
    if not match:
        return None
    for index, parameter in enumerate(match.group("params").split(",")):
        if re.search(r"\b" + re.escape(name) + r"\b", parameter):
            return index
    return None


def parameter_names(source: str) -> list[str]:
    """The parameter variable names, so a member access can be attributed to the right base."""
    match = re.search(r"(?m)^[A-Za-z_][^;\n]*\((?P<params>[^)]*)\)\s*\{", source)
    if not match:
        return []
    names = []
    for part in match.group("params").split(","):
        words = re.findall(r"[A-Za-z_]\w*", part)
        if words:
            names.append(words[-1])
    return names


def members_through(source: str, bases: set[str]) -> set[str]:
    """Members reached as `base->member` for one of `bases`.

    THE SCOPE MATTERS AND THE FIRST VERSION GOT IT WRONG. It collected every `->member` in the draft,
    including accesses through LOCALS (`var_v0->words`), and then required the recovered declaration to have
    them -- so `renderPickupShardParticle` declined with "the draft uses words, which
    race_course_props_and_pickups.c does not declare". The declaration belongs to the PARAMETER's type; a
    local's members are a different type's business and demanding them here rejected the very states this
    pass exists for.

    THE SECOND VERSION WAS STILL WRONG in the other direction: it pooled the members of ALL parameters, so a
    draft with two pointer parameters demanded each type declare the other's members. The unit is one
    parameter.
    """
    return {match.group("member") for match in
            re.finditer(r"\b(?P<base>[A-Za-z_]\w*)\s*->\s*(?P<member>[A-Za-z_]\w*)", source)
            if match.group("base") in bases}


def type_names_with_bodies(source: str) -> set[str]:
    """Names the candidate DEFINES (a `{` follows the tag) -- not names it merely forward-declares.

    THE DISTINCTION IS THE WHOLE FUNCTION OF THIS CHECK, and getting it wrong made the pass skip the types
    it exists for. `updateEndingCreditsCharacterLoopingSparkle`'s candidate carries
    `struct EndingCreditsEffectActor;` -- a FORWARD declaration, which is exactly the "incomplete definition"
    the compiler reports -- and the old check used `DECLARED_IN`, whose pattern allows `;` OR `{`, so it
    counted that as "already declared" and never emitted the body. Verified: `body found: True`, the draft's
    members all present in it, and `recovered: []`.
    """
    return {head.group("tag") for head in TYPEDEF_HEAD.finditer(source)}


def recover(source: str, *, function: str, repo: Path, target: str, assembly: str,
            binary_offsets: dict[str, set[int]] | None = None) -> tuple[str, dict]:
    """Return (declaration block to insert, receipt). The block is empty when it declines.

    `binary_offsets` maps a parameter name (`param0`) to the offsets the target assembly touches through it;
    `solver.compile_obligations.analyse` produces it. The check uses it for the parameter the referenced
    type is attached to, which is the one whose member accesses the draft performs.
    """
    receipt: dict = {"stage": "source-type-declarations", "declined": [], "recovered": [],
                     "corroborated": {}, "carried": {}}
    path = target_source_file(repo, target)
    if path is None:
        receipt["declined"].append("the compiler recipe target does not name a source file under src/")
        return "", receipt
    text = path.read_text(encoding="utf-8", errors="replace")
    bodies = type_bodies(text)
    if not bodies:
        # A file with no aggregate BODY at all is the forward-declaration case, and saying so per type is
        # more useful than a file-level message: it is the difference between "the type has no members
        # anywhere" and "the type's members live somewhere this pass does not look".
        for name in referenced_types(source, function):
            receipt["declined"].append(
                f"{name}: no body in {path.name} -- it is forward-declared or defined elsewhere")
        if not receipt["declined"]:
            receipt["declined"].append(f"{path.name} carries no annotated struct/union/enum body")
        return "", receipt

    already = type_names_with_bodies(source)
    offsets = binary_offsets or {}
    parameters = parameter_names(source)
    blocks: list[str] = []
    for name in referenced_types(source, function):
        if name in already:
            continue
        record = bodies.get(name)
        if record is None:
            receipt["declined"].append(f"{name}: no body in {path.name}")
            continue
        index = parameter_index(source, name)
        parameter = None if index is None else f"param{index}"
        touched = offsets.get(parameter or "", set())
        # ONE PARAMETER AT A TIME. `via_parameters` pools every parameter's member accesses, which is
        # wrong when a draft has two pointer parameters: each type would be asked to declare the other's
        # members. The base for this type is the parameter whose -- and whose only -- type it is.
        base = parameters[index] if index is not None and index < len(parameters) else None
        via_this_parameter = members_through(source, {base}) if base else set()
        used = sorted(member for member in via_this_parameter if member in record["members"])
        # THE CORROBORATION GATE. It only means something when the caller supplied binary offsets at all;
        # with none, the recovery is carried on the project's authority and the receipt says so rather than
        # pretending the check passed.
        bad = [member for member in used
               if record["members"][member] not in touched] if touched else []
        if touched and bad:
            receipt["declined"].append(
                f"{name}: the binary does not touch {', '.join(bad)} at the offset "
                f"{path.name} annotates, so the declaration is not corroborated")
            continue
        missing = [member for member in sorted(via_this_parameter)
                   if member not in record["members"]]
        if missing:
            receipt["declined"].append(
                f"{name}: the draft uses {', '.join(missing[:4])}, which {path.name} does not declare")
            continue
        blocks.append(record["text"].strip())
        receipt["recovered"].append({"type": name, "from": path.name, "parameter": parameter,
                                     "members_used": used})
        # WHAT SUPPORTS THE RECOVERY IS PART OF THE RESULT, not a footnote. A type on a PARAMETER has binary
        # evidence -- `param<i>`'s access offsets -- and is checked. A type on a LOCAL has none: the
        # assembly's access to a local is through a register m2c named, not through a `param` slot, and this
        # pass has no mapping for that. Those recoveries are CARRIED, not corroborated, and a summary that
        # counts them together makes an unverified recovery look like a verified one.
        if touched:
            receipt["corroborated"][name] = {m: hex(record["members"][m]) for m in used}
        else:
            receipt["carried"][name] = {
                "members": {m: hex(record["members"][m]) for m in used},
                "reason": ("the type is a local, not a parameter, so there is no `param<i>` evidence to "
                           "corroborate it" if parameter is None else
                           f"the target assembly touches no offset through {parameter}")}
    if not blocks:
        return "", receipt
    # A PARTIAL IS STILL A RECOVERY, and returning nothing when one type of several is unavailable was a
    # defect: `updateEndingCreditsCharacterLoopingSparkle` needs `EndingCreditsEffectActor` (which the file
    # declares) and `MainMenuSceneModel` (which it does not), and the first version emitted NEITHER, so the
    # state gained nothing from a type it could have been given. Each declaration is independently
    # corroborated, so the ones that pass stand on their own; the ones that failed are in `declined` and
    # `unrecoverable` names them for the caller.
    receipt["partial"] = bool(receipt["declined"])
    receipt["recovered_total"] = len(receipt["recovered"])
    receipt["corroborated_total"] = len(receipt["corroborated"])
    receipt["carried_total"] = len(receipt["carried"])
    block = ("\n/* Type declarations recovered from this function's own translation unit.\n"
             "   Declarations only: no function body is copied. Members the target assembly corroborates\n"
             "   are listed in the action's receipt; the rest are the project's own layout. */\n"
             + "\n".join(blocks) + "\n")
    return block, receipt
