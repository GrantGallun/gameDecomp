"""A small, bounded, DETERMINISTIC archive of diverse repair candidates.

WHY IT EXISTS
-------------
Compilation is many-to-one and an intermediate repair can temporarily LOWER instruction similarity
while enabling an eventual exact match. A search that always continues from the single best-scoring
candidate can therefore strand a productive branch: the one edit that unlocks the match may sit on a
line of attack that looked worse at the time. The archive keeps the best candidate AND a few that
differ structurally, so exploration has somewhere other than the leader to go.

WHAT IT DOES NOT CLAIM
----------------------
- **Novelty is not quality.** Keeping a candidate says nothing about whether it is useful; that is
  only ever decided by the certificate.
- **A quality threshold is a heuristic.** `min_score` filters noise, it does not prove anything.
- **This module never computes exactness.** It does not compile and does not import a compiler.
  `exact` is the caller's certificate verdict, recorded verbatim. A module that could infer
  exactness would eventually be trusted to.
- **An archive is inert without a search that CHOOSES SEEDS.** The synthetic evaluator draws
  independent samples, so nothing here changes any result until multi-round attempts exist.

WHAT "THE SAME APPROACH" MEANS
------------------------------
`canonical` decides. It ignores whitespace and identifier NAMES, because `s32 value; return value;`
and `s32 total; return total;` are one approach spelled twice. It does NOT ignore integer literals,
operators, or control-flow keywords, because those change the program. A descriptor that hid a
changed constant would make every downstream duplicate test vacuous, so each of those three is
tested to be significant.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict

# C keywords and type names that must NOT be alpha-renamed: renaming them changes the program (or
# makes it unparseable). Anything else that looks like an identifier is a name and is renamed.
KEYWORDS = frozenset("""
auto break case char const continue default do double else enum extern float for goto if inline int
long register restrict return short signed sizeof static struct switch typedef union unsigned void
volatile while
""".split())

# The project's fixed-width type names. These are NOT interchangeable and must survive canonical
# renaming: `s32 x` and `u32 x` are different programs that can compile differently, so collapsing
# them would make the duplicate test hide a real change. Found by asking what the descriptor would
# wrongly ignore, not by watching a test fail.
FIXED_WIDTH_TYPES = frozenset(
    "s8 s16 s32 s64 u8 u16 u32 u64 f32 f64 s128 u128".split())

PROTECTED = KEYWORDS | FIXED_WIDTH_TYPES

_STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"\b(?:0[xX][0-9a-fA-F]+|\d+)\b")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def canonical(source: str) -> str:
    """Whitespace-insensitive, identifier-insensitive canonical form, for NOVELTY COMPARISON ONLY.

    Deterministic and pure: the same text always yields the same string, in this process and any
    other, so it is safe to use as a dict key across modules.

    Ignored:  runs of whitespace, and identifier NAMES (renamed to positional placeholders, with
              keywords and type names left alone).
    Kept:     integer literals, operators, punctuation and control-flow keywords -- all of which
              change the object and therefore must not collapse two programs into one class.
    """
    if not source:
        return ""
    # COMMENTS FIRST. A comment cannot change the object, so a candidate that differs only by a
    # comment is the same approach. Missing this made `cosmetic_duplicates_collapsed` measure 0 of 82
    # variants on the real dataset -- every comment-only variant counted as a distinct child and
    # inflated both the diversity count and the training weight.
    source = _COMMENT.sub(" ", source)
    # Protect string and character literals before identifier handling, so a name inside a literal is
    # not renamed and two different message strings do not collapse.
    literals: list[str] = []

    def stash(match: re.Match) -> str:
        literals.append(match.group(0))
        return f"\x00{len(literals) - 1}\x00"

    body = _STRING.sub(stash, source)

    names: dict[str, str] = {}

    def rename(match: re.Match) -> str:
        word = match.group(0)
        if word in PROTECTED:
            return word
        if word not in names:
            names[word] = f"v{len(names)}"
        return names[word]

    body = _IDENT.sub(rename, body)
    for index, literal in enumerate(literals):
        body = body.replace(f"\x00{index}\x00", literal)
    # Whitespace AND the space around punctuation. Collapsing runs of whitespace alone leaves
    # `return 1 ;` different from `return 1;`, so a purely cosmetic reflow would have been counted as
    # a second approach -- the exact failure this descriptor exists to prevent. Literals are already
    # stashed above, so this cannot reach inside a string.
    body = re.sub(r"\s+", " ", body)
    body = re.sub(r"\s*([{}()\[\];,.?])\s*", r"\1", body)
    body = re.sub(r"\s*([-+*/%<>=!&|^]+)\s*", r"\1", body)
    return body.strip()


def novelty_key(source: str) -> str:
    """The dedup key: a stable digest of the canonical form, not `hash()`.

    A process-local hash would make two runs disagree about whether a child is a duplicate, which is
    exactly the kind of silent inconsistency the weighting would then inherit.
    """
    return hashlib.sha256(canonical(source).encode("utf-8")).hexdigest()


# Control-flow keywords whose multiset describes the SHAPE of a repair rather than its spelling.
CONTROL_KEYWORDS = ("if", "else", "for", "while", "do", "switch", "case", "default", "return",
                    "break", "continue", "goto")
_STRUCTURAL = re.compile(r"\b(" + "|".join(CONTROL_KEYWORDS) + r")\b")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def structural_features(source: str) -> dict:
    """Cheap deterministic descriptors of the repair STRATEGY, not of similarity to anything.

    Two candidates with different control-flow multisets took different routes even when their
    scores are close. No LLM judges this and nothing here is a quality score.
    """
    body = _COMMENT.sub(" ", source or "")
    counts = {word: len(re.findall(rf"\b{word}\b", body)) for word in CONTROL_KEYWORDS}
    return {
        "control_flow": counts,
        "statements": len(re.findall(r";", body)),
        "cases": counts["case"],
        "has_default": bool(counts["default"]),
        "operators": sorted(set(re.findall(r"[+\-*/%<>=!&|^]{1,2}", body))),
        "literals": sorted(set(_NUMBER.findall(body))),
    }


@dataclass
class ArchiveEntry:
    id: int
    source: str
    score: float
    exact: bool
    certificate_status: str = ""
    novelty_key: str = ""
    features: dict = field(default_factory=dict)
    parent: int | None = None
    strategy: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class RepairArchive:
    """Bounded, deterministic retention of diverse candidates.

    Retention rules, in order, and each one is a test:

      1. an EXACT candidate is always stored and becomes the best known candidate;
      2. a candidate whose `novelty_key` is already present is declined as a duplicate;
      3. the best-scoring candidate is always retained, however unremarkable its novelty;
      4. other candidates are stored only if novel AND `score >= min_score`, up to `capacity`;
      5. when full, the LOWEST-scoring non-exact entry is evicted, ties broken by insertion id, and
         the best known candidate is never evicted;
      6. `exploration_seeds` returns the best first, then structurally distinct entries, capped by
         the exploration budget -- which is spent explicitly, so exploration cost cannot run away.
    """

    def __init__(self, *, capacity: int = 8, exploration_budget: int = 0,
                 min_score: float = 0.0):
        if capacity < 1:
            raise ValueError("capacity must be at least 1, or the best candidate cannot be kept")
        self.capacity = int(capacity)
        self.exploration_budget = int(exploration_budget)
        self.spent = 0
        self.min_score = float(min_score)
        self.entries: list[ArchiveEntry] = []
        self._next_id = 0
        self._keys: set[str] = set()
        self.duplicate_count = 0
        self.declined_low_score = 0
        self.evicted: list[dict] = []

    # -- internals --
    def _best(self) -> ArchiveEntry | None:
        if not self.entries:
            return None
        # Exact beats inexact; then score; then earliest id, so the choice is deterministic.
        return max(self.entries, key=lambda e: (e.exact, e.score, -e.id))

    @property
    def best(self) -> ArchiveEntry | None:
        return self._best()

    def __len__(self) -> int:
        return len(self.entries)

    def _evict_one(self) -> ArchiveEntry | None:
        """Remove the lowest-scoring entry that is neither exact nor the best known candidate."""
        best = self._best()
        candidates = [e for e in self.entries if not e.exact and (best is None or e.id != best.id)]
        if not candidates:
            return None
        victim = min(candidates, key=lambda e: (e.score, e.id))
        self.entries.remove(victim)
        self._keys.discard(victim.novelty_key)
        return victim

    # -- the interface --
    def offer(self, *, source: str, score: float, exact: bool, certificate_status: str = "",
              parent: int | None = None, strategy: str = "") -> ArchiveEntry | None:
        """Consider one candidate. Returns the stored entry, or None if it was declined.

        `exact` MUST be a real certificate verdict supplied by the caller. This module has no way to
        check it and does not pretend to: it records the claim and the status string verbatim.
        """
        source = source or ""
        if not source.strip():
            return None
        key = novelty_key(source)
        if key in self._keys:
            self.duplicate_count += 1
            return None
        if not exact and score < self.min_score:
            self.declined_low_score += 1
            return None

        if len(self.entries) >= self.capacity:
            victim = self._evict_one()
            if victim is None:
                # Everything stored is exact or is the leader; nothing may be dropped for this.
                if not exact:
                    self.declined_low_score += 1
                    return None
            else:
                self.evicted.append({"id": victim.id, "score": victim.score,
                                     "novelty_key": victim.novelty_key[:12]})

        entry = ArchiveEntry(id=self._next_id, source=source, score=float(score), exact=bool(exact),
                             certificate_status=certificate_status, novelty_key=key,
                             features=structural_features(source), parent=parent,
                             strategy=strategy)
        self._next_id += 1
        self.entries.append(entry)
        self._keys.add(key)
        return entry

    def exploration_seeds(self) -> list[ArchiveEntry]:
        """Where to explore FROM: the best first, then structurally distinct entries.

        The best is offered even when the exploration budget is exhausted -- a search with nothing
        left to explore still has to be able to continue from the leader, and silently returning
        nothing there would look like "no candidates" rather than "no budget".
        """
        best = self._best()
        if best is None:
            return []
        seeds = [best]
        if self.exploration_budget <= self.spent:
            return seeds
        remaining = self.exploration_budget - self.spent
        others = [e for e in self.entries if e.id != best.id]
        # Distinct shape first, then score: a candidate that took a different route is worth more
        # exploration than one that scored a little higher on the same route.
        seen_shapes: set[str] = {_shape(best)}
        for entry in sorted(others, key=lambda e: (-e.score, e.id)):
            if len(seeds) >= 1 + remaining:
                break
            shape = _shape(entry)
            if shape in seen_shapes:
                continue
            seen_shapes.add(shape)
            seeds.append(entry)
        return seeds

    def spend(self, n: int = 1) -> int:
        """Consume exploration budget. Floors at the limit; returns what is left."""
        self.spent = min(self.exploration_budget, self.spent + max(0, int(n)))
        return self.exploration_budget - self.spent

    def as_dict(self) -> dict:
        best = self._best()
        return {
            "capacity": self.capacity,
            "size": len(self.entries),
            "best_id": best.id if best else None,
            "best_score": best.score if best else None,
            "best_exact": bool(best.exact) if best else False,
            "duplicate_count": self.duplicate_count,
            "declined_low_score": self.declined_low_score,
            "evicted": self.evicted,
            "exploration_budget": self.exploration_budget,
            "exploration_spent": self.spent,
            "entries": [e.as_dict() for e in self.entries],
            "note": ("novelty is not quality and exactness here is the caller's claim, recorded "
                     "verbatim; this module compiles nothing"),
        }


def _shape(entry: ArchiveEntry) -> str:
    """A coarse shape key: the control-flow multiset, ignoring spelling and literal values."""
    counts = entry.features.get("control_flow") or {}
    return "|".join(f"{name}:{counts.get(name, 0)}" for name in CONTROL_KEYWORDS)
