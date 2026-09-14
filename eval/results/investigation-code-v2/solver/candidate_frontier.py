"""Small, deterministic, source-preserving frontier for plateau exploration."""
from dataclasses import dataclass
from typing import Any


@dataclass
class Candidate:
    source: str
    attempt: Any
    results: Any
    assembly: str
    tag: str
    rank: tuple
    shape: str


class Frontier:
    def __init__(self, width: int = 4, representatives_per_object: int | None = None):
        if width < 1:
            raise ValueError("frontier width must be positive")
        self.width = width
        self.pending: list[Candidate] = []
        self.expanded: set[str] = set()
        self.representatives_per_object = representatives_per_object
        self.expanded_objects: dict[str, int] = {}

    def offer(self, candidate: Candidate) -> bool:
        if candidate.source in self.expanded:
            return False
        pool = [c for c in self.pending if c.source != candidate.source] + [candidate]
        pool.sort(key=lambda c: (c.rank, c.source), reverse=True)
        # First preserve different residual/source-action shapes, then fill
        # spare slots with alternate source representatives of the same object.
        selected, shapes = [], set()
        counts = dict(self.expanded_objects)
        if self.representatives_per_object is not None:
            filtered = []
            for c in pool:
                key = c.assembly
                if counts.get(key, 0) >= self.representatives_per_object:
                    continue
                filtered.append(c)
                counts[key] = counts.get(key, 0) + 1
            pool = filtered
        for c in pool:
            if c.shape not in shapes:
                selected.append(c)
                shapes.add(c.shape)
        selected += [c for c in pool if all(c is not s for s in selected)]
        self.pending = selected[:self.width]
        return any(c is candidate for c in self.pending)

    def pop(self) -> Candidate | None:
        if not self.pending:
            return None
        candidate = self.pending.pop(0)
        self.expanded.add(candidate.source)
        self.expanded_objects[candidate.assembly] = self.expanded_objects.get(candidate.assembly, 0) + 1
        return candidate
