"""Bounded memoization of identical interpreter inputs within one search run.

This reuses a deterministic test execution, not an object-exactness claim.
Compilation and byte certificates remain independent and are never cached here.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
import json


class ReplayCache:
    def __init__(self, evaluator, maximum=2):
        if maximum < 1:
            raise ValueError('maximum must be positive')
        self.evaluator = evaluator
        self.maximum = maximum
        self.entries = OrderedDict()
        self.hits = self.misses = 0

    def evaluate(self, target, candidate, *, cases, call_arities, return_registers):
        # Include every argument the interpreter consumes. String equality
        # avoids treating an assembly hash collision as reusable evidence.
        context = json.dumps(([asdict(case) for case in cases], call_arities,
                              return_registers), sort_keys=True)
        key = (target, candidate, context)
        if key in self.entries:
            self.hits += 1
            self.entries.move_to_end(key)
            return deepcopy(self.entries[key])
        self.misses += 1
        results = self.evaluator(target, candidate, cases=cases,
            call_arities=call_arities, return_registers=return_registers)
        self.entries[key] = deepcopy(results)
        while len(self.entries) > self.maximum:
            self.entries.popitem(last=False)
        return results

    def summary(self):
        return dict(hits=self.hits, misses=self.misses, maximum_entries=self.maximum,
                    authority='identical interpreter inputs only; no byte certificate reuse')
