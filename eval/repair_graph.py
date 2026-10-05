"""Concrete, source-bound repair evidence; predictions have no place in this graph."""
from copy import deepcopy
import re

from eval.search_replay import digest, validate_world


def _diff(text):
    return "\n".join(line.split("\t", 1)[0] if line.startswith(("--- ", "+++ ")) else line
                     for line in (text or "").splitlines())


def _bindings(node, context):
    """Validate present bindings for failures too, including the build wrapper.

    The frontend sees project C_DEFINES prepended by workspace.score; its hash
    is intentionally distinct from the original candidate's hash.
    """
    verdict, source = node["verdict"], node["source"]
    frontend = verdict.get("frontend") or {}
    defines = ((frontend.get("recipe") or {}).get("settings") or {}).get("C_DEFINES", "")
    existing = set(re.findall(r"(?m)^\s*#\s*define\s+([A-Za-z_]\w*)\b", source))
    lines = ["/* project C_DEFINES mirrored from Makefile */"]
    for name, value in dict.fromkeys(re.findall(r"(?:^|\s)-D([A-Za-z_]\w*)(?:=([^\s]+))?", defines)):
        if name not in existing and not value.startswith(('"', "'")):
            lines.extend((f"#ifndef {name}", f"#define {name} {value or '1'}", "#endif"))
    compiled_source = "\n".join(lines + ['#line 1 "candidate.c"']) + "\n" + source if len(lines) > 1 else source
    compiled_hash = digest(compiled_source)
    if frontend and frontend.get("source_sha256") != compiled_hash:
        raise ValueError("frontend source binding mismatch")
    certificate = verdict.get("verification") or {}
    if certificate and (certificate.get("candidate_source_sha256", certificate.get("source_sha256")) != node["source_sha256"]
            or certificate.get("target_sha256") != context["target_sha256"]
            or certificate.get("source_sha256") != compiled_hash):
        raise ValueError("certificate source/target binding mismatch")
    attribution = verdict.get("source_attribution") or {}
    if attribution and (attribution.get("source_sha256") != node["source_sha256"]
            or (attribution.get("compile_source_sha256") and attribution["compile_source_sha256"] != compiled_hash)):
        raise ValueError("attribution source binding mismatch")


def build_graph(worlds):
    """Merge identical concrete states, retaining each observation's provenance.

    Generator versions are edge provenance rather than compiler-state identity.
    Repeated imports of the same world do not create extra evidence.
    """
    graph = {"schema_version": 1, "kind": "observed-repair-graph", "nodes": {},
             "edges": [], "worlds": {}, "training_eligible": False}
    edges = {}
    for world in sorted(worlds, key=digest):
        validate_world(world)
        wid = digest(world)
        if wid in graph["worlds"]:
            continue
        context = world["context"]
        # Retain the originals so reload can reconstruct every derived node and
        # edge, rather than trusting a checksum of arbitrary derived claims.
        graph["worlds"][wid] = deepcopy(world)
        local, receipts = {}, {}
        for node in world["nodes"]:
            _bindings(node, context)
            verdict = node["verdict"]
            receipt = verdict.get("receipt_id")
            if type(receipt) is not int or receipt <= 0 or receipt in receipts.values():
                raise ValueError("concrete graph requires a durable receipt")
            if verdict.get("error"):
                raise ValueError("infrastructure error is not repair evidence")
            parent = node["parent"]
            expected = receipts[parent] if parent is not None else None
            if node.get("parent_receipt_id") != expected:
                raise ValueError("parent receipt does not match observed edge")
            identity = {k: context.get(k) for k in ("target_sha256", "compiler_sha256", "assistance")}
            identity["source_sha256"] = node["source_sha256"]
            sid = digest(identity)
            observed = {k: verdict[k] for k in ("compiled", "exact", "score")}
            observed["diff"] = _diff(verdict.get("diff"))
            observed["frontend_passed"] = (verdict.get("frontend") or {}).get("passed")
            ref = {"world": wid, "node": node["id"], "receipt_id": receipt,
                   "parent_receipt_id": expected}
            if sid in graph["nodes"]:
                if graph["nodes"][sid]["observed"] != observed:
                    raise ValueError("contradictory verdict for identical concrete state")
                graph["nodes"][sid]["observations"].append(ref)
            else:
                graph["nodes"][sid] = {"id": sid, "identity": identity, "source": node["source"],
                    "function": context["task"], "observed": observed, "verdict": deepcopy(verdict),
                    "observations": [ref]}
            if parent is not None:
                transition = {"parent": local[parent], "child": sid,
                              "family": node["family"], "label": node["label"]}
                eid = digest(transition)
                if eid not in edges:
                    edges[eid] = {"id": eid, **transition, "observations": []}
                edges[eid]["observations"].append(ref)
            local[node["id"]], receipts[node["id"]] = sid, receipt
    graph["edges"] = list(edges.values())
    graph["sha256"] = digest(graph)
    return graph


def validate_graph(graph):
    payload = {k: v for k, v in graph.items() if k != "sha256"}
    if graph.get("sha256") != digest(payload) or graph.get("kind") != "observed-repair-graph":
        raise ValueError("graph checksum or kind mismatch")
    try:
        reconstructed = build_graph(graph["worlds"].values())
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("invalid graph evidence structure") from exc
    if reconstructed != graph:
        raise ValueError("graph differs from its original evidence")
    return graph
