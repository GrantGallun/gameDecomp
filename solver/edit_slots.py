"""Source-bound edit locations. Headers and assembly never acquire edit slots."""
import hashlib
import re


def slots(source: str) -> dict[str, tuple[int, int]]:
    from solver import project_headers
    prefix = hashlib.sha256(source.encode()).hexdigest()[:12]
    masked = project_headers._mask_noncode(source)
    out, offset = {}, 0
    for index, line in enumerate(source.splitlines(keepends=True), 1):
        # Whole physical lines are unambiguous even if their text repeats. A line is comment-only when
        # masking comments leaves nothing; a leading `*` alone is not a comment: m2c's pointer stores
        # (`*(s16 *)(p + 4) = x;`) start with one, and their slots were rejected as unknown (2026-09-15).
        # Inside-comment lines such as `   therefore hypotheses. */` are not code either; a string-only
        # continuation line masks blank too, so a quote keeps its slot.
        code = masked[offset:offset + len(line)]
        comment_only = not code.strip() and '"' not in line
        if line.strip() and not comment_only and not line.lstrip().startswith('#'):
            out[f'{prefix}:L{index}'] = (offset, offset + len(line.rstrip('\r\n')))
        offset += len(line)
    includes = list(re.finditer(r'(?m)^[ \t]*#\s*include[^\n]*(?:\n|$)', source))
    at = includes[-1].end() if includes else 0
    out[f'{prefix}:DECLARATIONS'] = (at, at)
    return out


def render(source: str) -> str:
    rows = []
    for key, (start, end) in slots(source).items():
        short = key.split(':', 1)[1]
        rows.append(f'{short}: {source[start:end]}' if start != end else
                    f'{short}: INSERT new declarations here (add trailing newline).')
    return ('\nEDITABLE SOURCE SLOTS (bound to CURRENT C only):\n'
            'Prefer {"slot":"L23","new":"replacement"} or {"slot":"DECLARATIONS","new":"declarations\\n"}. '
            'Short slot names are bound by the controller to this exact parent source. '
            'A line slot replaces that complete line; DECLARATIONS inserts before local source declarations. '
            'To complete an included forward typedef, insert the named struct definition, not another typedef. '
            'Offset comments do not create padding. No header or assembly edits.\n' + '\n'.join(rows) + '\n')


def normalize_newlines(text):
    """Decode mistakenly double-escaped newlines only outside C literals."""
    out, quote, index = [], '', 0
    while index < len(text):
        char = text[index]
        if quote:
            out.append(char)
            index += 1
            if char == '\\' and index < len(text):
                out.append(text[index])
                index += 1
            elif char == quote:
                quote = ''
        elif char in ('"', "'"):
            quote = char
            out.append(char)
            index += 1
        elif text[index:index+2] in ('\\n', '\\t'):
            out.append('\n' if text[index+1] == 'n' else '\t')
            index += 2
        else:
            out.append(char)
            index += 1
    return ''.join(out)


def apply(source, edits, max_chars=12000):
    available = slots(source)
    changes = []
    for edit in edits:
        if edit.slot:
            if edit.slot not in available:
                raise ValueError('unknown or stale source slot')
            start, end = available[edit.slot]
        else:
            if source.count(edit.old) != 1:
                raise ValueError(f'old substring occurs {source.count(edit.old)} times')
            start, end = source.index(edit.old), source.index(edit.old) + len(edit.old)
        if source[start:end].strip() == source.strip():
            raise ValueError('whole-file replacement is not allowed')
        changes.append((start, end, edit.new))
    if sum(end-start+len(new) for start,end,new in changes) > max_chars:
        raise ValueError('edit budget exceeded')
    ordered = sorted(changes)
    for left, right in zip(ordered, ordered[1:]):
        if left[1] > right[0] or left[0] == right[0]:
            raise ValueError('edits overlap or invalidate one another')
    for start, end, new in reversed(ordered):
        source = source[:start] + new + source[end:]
    return source
