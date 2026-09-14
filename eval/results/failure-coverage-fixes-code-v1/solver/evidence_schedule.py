"""Dependency-pinned evidence and cycle-aware work ordering; no trust inflation."""
import hashlib
import json


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def evidence_key(row: dict | None) -> str | None:
    if not row:
        return None
    return fingerprint({"attempt": row.get("best_attempt_id"),
                        "certificate": row.get("semantic_certificate"),
                        "verification": row.get("verification")})


def pins(dependencies, evidence: dict[str, dict]) -> dict:
    return {name: evidence_key(evidence.get(name)) for name in sorted(dependencies)}


def stale_nodes(evidence: dict[str, dict]) -> list[str]:
    stale = set()
    for name, row in evidence.items():
        dependencies = row.get("dependencies", [])
        expected = pins(dependencies, evidence)
        if dependencies and row.get("dependency_fingerprints") != expected:
            stale.add(name)
        if dependencies and any(value is None for value in expected.values()):
            stale.add(name)
    while True:
        more = {name for name, row in evidence.items()
                if set(row.get("dependencies", [])) & stale}
        if more <= stale:
            return sorted(stale)
        stale.update(more)


def components(dependencies: dict[str, set[str]]) -> list[list[str]]:
    """Kosaraju SCCs, emitted callee-first without recursion depth limits."""
    graph = {n: sorted(set(ds) & dependencies.keys()) for n, ds in dependencies.items()}
    reverse = {n: [] for n in graph}
    for caller, callees in graph.items():
        for callee in callees:
            reverse[callee].append(caller)
    seen, order = set(), []
    for root in sorted(graph):
        stack = [(root, False)]
        while stack:
            node, finished = stack.pop()
            if finished:
                order.append(node)
            elif node not in seen:
                seen.add(node)
                stack.append((node, True))
                stack.extend((c, False) for c in reversed(graph[node]) if c not in seen)
    assigned, result = set(), []
    for root in reversed(order):
        group, stack = [], [root]
        while stack:
            node = stack.pop()
            if node in assigned:
                continue
            assigned.add(node)
            group.append(node)
            stack.extend(reverse[node])
        if group:
            result.append(sorted(group))
    return list(reversed(result))


def levels(dependencies: dict[str, set[str]]) -> tuple[dict, dict]:
    assigned, groups = {}, {}
    for component in components(dependencies):
        external = {callee for node in component for callee in dependencies[node]
                    if callee not in component and callee in dependencies}
        level = max((assigned[c] + 1 for c in external), default=0)
        for node in component:
            assigned[node], groups[node] = level, component
    return assigned, groups


def render_contracts(dependencies, evidence: dict[str, dict]) -> str:
    rows = []
    stale = set(stale_nodes(evidence))
    for name in sorted(dependencies):
        if name in stale or (evidence.get(name) or {}).get("dependency_stale"):
            continue
        contract = (evidence.get(name) or {}).get("verified_callee_contract")
        if contract:
            rows.append({"callee": name, "evidence_sha256": evidence_key(evidence[name]),
                         "contract": contract})
    if not rows:
        return ""
    return ("\nCURRENT VERIFIED CALLEE ABI CONTRACTS (compatible source, not original names; "
            "not an executable callee side-effect model):\n" + json.dumps(rows, sort_keys=True))
