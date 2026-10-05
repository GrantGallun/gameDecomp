"""Observable descriptors for existing guarded storage generators.

Rules propose C; they do not certify it. Statistical consumers see no function
names, local names, candidate identities, future compiler results or score.
"""
import hashlib
import json
import re

from solver import project_headers, signals, storage_repairs

SCHEMA_VERSION = 1
CATALOG = {
    "register_storage": {"requires": ["candidate-only stack accesses", "eligible ordinary locals", "no address escape"],
                         "hypothesis": "remove local spills; reveal remaining residual"},
    "address_reuse": {"requires": ["changed load base", "adjacent same-field local alias", "unconditional first statement"],
                      "hypothesis": "reuse established address for following load"},
    "parameter_reuse": {"requires": ["candidate-only stack accesses", "leading identical-type parameter copy", "otherwise unused parameter"],
                        "hypothesis": "eliminate redundant local storage"},
}


def action_key(family, parent, child):
    if family not in CATALOG:
        return None
    if family == "register_storage":
        counts = [len(re.findall(r"\bregister\b", project_headers._mask_noncode(s))) for s in (parent, child)]
        arity = counts[1] - counts[0]
        if not 1 <= arity <= 8:
            return None
        return f"register_storage:{arity}"
    return family


def proposals(source, function, verdict):
    if not verdict["compiled"] or verdict["exact"]:
        return []
    result = []
    for family in CATALOG:
        try:
            for label, kind, child in getattr(storage_repairs, family)(source, function, verdict.get("diff", "")):
                result.append({"label": label, "family": kind, "source": child,
                               "action": action_key(kind, source, child)})
        except ValueError:
            continue
    return result


def state_features(source, function, verdict):
    """Lossy abstract state; concrete evidence identity remains in repair_graph."""
    available = sorted({r["action"] for r in proposals(source, function, verdict) if r["action"]})
    diff = verdict.get("diff", "")
    if verdict["exact"]:
        residual = "exact"
    elif not verdict["compiled"]:
        residual = "compile_failed"
    elif storage_repairs._spill_evidence(diff):
        residual = "spills"
    elif "address_reuse" in available:
        residual = "load_base"
    elif not diff.strip():
        residual = "certificate_only"
    else:
        signal = signals.analyse(diff)
        axes = ("structural", "regalloc", "ordering", "reloc", "layout", "immediate")
        residual = max(axes, key=lambda k: getattr(signal, k)) if any(getattr(signal, k) for k in axes) else "other"
    recipe = verdict.get("compiler_recipe") or {}
    domain = {k: recipe.get(k) for k in ("settings", "makefile_sha256", "helper_sha256")}
    return {"schema_version": SCHEMA_VERSION, "domain": hashlib.sha256(json.dumps(domain, sort_keys=True).encode()).hexdigest(),
            "residual": residual, "available": available}
