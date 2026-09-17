"""Derive a compiler rule, and refuse to promote one that only re-predicts what was observed.

The problem this exists to solve. Two rules were derived by hand in the two rounds before this module,
and the second one's refutation only surfaced because someone happened to test it. That is a process
that depends on diligence, and CLAUDE.md's whole history is of diligence failing quietly -- four
separate "silent decline" bugs, each of which looked healthy from outside.

So the acceptance test is mechanical:

    A rule is CONFIRMED only when the oracle closes at least one case OTHER than the case the rule
    was derived from.

A rule that closes only its own motivating residual is a restatement, not a derivation, and
`require_confirmation` refuses to let it through. The distinction is not cosmetic: the store-order rule
in `patterns/catalog.py` closes Fstop (its derivation case) and REGRESSED its first held-out case. It
is confirmed on the derivation case and scoped to stores precisely because this test was run.

The harness does not judge. `compile_fn` is the caller's oracle, so the same code drives the real
compiler and a fake one in tests, and no rule is ever confirmed by a model's opinion.

Usage:
    python3 -m patterns.derive --rule ordering-store-reorder --db ... --repo ... --functions a,b,c
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class Variant:
    """One predicted source. `label` is what the oracle's receipt records."""
    label: str
    source: str


@dataclass(frozen=True)
class Case:
    """One residual to test a rule against."""
    function: str
    source: str
    diff: str
    receipt: int | None = None


class Rule(Protocol):
    """What a rule must expose. `applies` is a claim, not a filter: a rule that claims a residual and
    predicts nothing has declined, and that is counted separately from never applying.

    `criterion` says what this rule is playing for, and it is not cosmetic:

        "exact"     a SOLVER rule. Success is the oracle's exact flag -- byte-identical object code.
        "compiled"  an ADMISSION rule. Success is that the source builds at all.

    The project treats those as different achievements (`admission` vs the match count), so a rule
    confirmed on "compiled" must never be reported as producing matches. Putting the criterion on the
    rule, and printing it in every verdict, is what keeps the two apart.
    """
    id: str
    derivation_case: str | None

    def applies(self, case: Case) -> bool: ...

    def predict(self, case: Case) -> Sequence[Variant]: ...


@dataclass
class Verdict:
    rule: str
    criterion: str = "exact"
    claimed: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)
    predicted: int = 0
    compiled: int = 0
    exact: list[str] = field(default_factory=list)
    met: list[str] = field(default_factory=list)
    derivation_case: str | None = None
    per_case: list[dict] = field(default_factory=list)

    @property
    def beyond_derivation(self) -> list[str]:
        """Cases the rule brought to its criterion that it was NOT derived from. The whole point."""
        return [f for f in self.met if f != self.derivation_case]

    @property
    def is_confirmed(self) -> bool:
        return bool(self.beyond_derivation)

    def summary(self) -> dict:
        return {
            "rule": self.rule,
            "criterion": self.criterion,
            "derivation_case": self.derivation_case,
            "claimed": len(self.claimed),
            "declined": len(self.declined),
            "predicted": self.predicted,
            "compiled": self.compiled,
            "exact": self.exact,
            "met": self.met,
            "beyond_derivation": self.beyond_derivation,
            "confirmed": self.is_confirmed,
            "note": ("confirmed on ADMISSION (compiles), not on matches"
                     if self.criterion == "compiled" else
                     "confirmed on byte-exact objects"),
        }


class Unconfirmed(RuntimeError):
    """Raised when a rule is promoted without a held-out confirmation."""


def evaluate(rule: Rule, cases: Sequence[Case], *,
             compile_fn: Callable[[str, str, str], object]) -> Verdict:
    """Run `rule` over `cases`, compiling every prediction through `compile_fn`.

    `compile_fn(function, source, label)` returns an object with `.compiled` and `.exact`, which is
    exactly what `solver.workspace.score` returns -- so the oracle and a test double are
    interchangeable and the harness never needs to know which it has.
    """
    verdict = Verdict(rule=rule.id, derivation_case=getattr(rule, "derivation_case", None),
                      criterion=getattr(rule, "criterion", "exact"))
    for case in cases:
        if not rule.applies(case):
            verdict.declined.append(case.function)
            verdict.per_case.append({"function": case.function, "status": "declined"})
            continue
        verdict.claimed.append(case.function)
        variants = list(rule.predict(case))
        if not variants:
            # Claimed the residual and predicted nothing. Counted separately: this is the shape of
            # every silent-decline bug in this project, and collapsing it into "declined" is how they
            # stayed hidden.
            verdict.per_case.append({"function": case.function, "status": "claimed-nothing"})
            continue
        verdict.predicted += len(variants)
        row = {"function": case.function, "status": "predicted", "variants": []}
        for variant in variants:
            attempt = compile_fn(case.function, variant.source, variant.label)
            compiled = bool(getattr(attempt, "compiled", False))
            is_exact = bool(getattr(attempt, "exact", False))
            row["variants"].append({"label": variant.label, "compiled": compiled, "exact": is_exact})
            if compiled:
                verdict.compiled += 1
            if is_exact and case.function not in verdict.exact:
                verdict.exact.append(case.function)
            reached = is_exact if verdict.criterion == "exact" else compiled
            if reached:
                if case.function not in verdict.met:
                    verdict.met.append(case.function)
                break
        verdict.per_case.append(row)
    return verdict


def require_confirmation(verdict: Verdict) -> None:
    """Refuse to let a rule change behaviour on the strength of its own motivating case."""
    if verdict.is_confirmed:
        return
    what = "matches" if verdict.criterion == "exact" else "admissions"
    if verdict.met and not verdict.beyond_derivation:
        raise Unconfirmed(
            f"{verdict.rule}: reaches {verdict.met} but ONLY its derivation case "
            f"({verdict.derivation_case}). That is a restatement, not a derivation -- the rule must "
            f"predict a case the observations did not cover before it may change behaviour.")
    raise Unconfirmed(
        f"{verdict.rule}: no case reached {what} (claimed {len(verdict.claimed)}, "
        f"predicted {verdict.predicted}, compiled {verdict.compiled}). Nothing to confirm.")


def cases_from_kb(conn, names: Sequence[str], *, kind: str = "residual") -> list[Case]:
    """Build Cases from the knowledge base.

    `kind` selects the population, because the two rule families are tested against different
    attempts and one function's best attempt is not the other's:

        "residual"   best COMPILING, non-exact attempt -- the attempt a solver rule has to improve.
                     Selecting the best compiling attempt regardless of exactness picked up Fstop's
                     exact 100.0 claim once it had been closed, whose diff is empty; the harness then
                     declined its own derivation case and reported `claimed: 0`.
        "admission"  most recent NON-COMPILING attempt with source -- what an admission rule exists to
                     fix. A rule that repairs a build failure is never tested by compiling an attempt
                     that already builds.

    Source and diff always come from the SAME attempt: a diff from one candidate and a source from
    another describe different programs, and feeding the classifier a mismatched pair is how a
    function ends up classified two ways.
    """
    query = {
        "residual": ("select a.id, a.source_code, a.diff_summary from attempts a "
                     "join functions f on f.addr = a.func_addr "
                     "where f.name = ? and a.compiled = 1 and coalesce(a.exact, 0) = 0 "
                     "order by a.score desc limit 1"),
        "admission": ("select a.id, a.source_code, a.diff_summary from attempts a "
                      "join functions f on f.addr = a.func_addr "
                      "where f.name = ? and a.compiled = 0 and a.source_code is not null "
                      "and length(a.source_code) > 0 order by a.id desc limit 1"),
    }[kind]
    out = []
    for name in names:
        row = conn.execute(query, (name,)).fetchone()
        if row:
            out.append(Case(function=name, receipt=row[0], source=row[1] or "", diff=row[2] or ""))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rule", required=True, help="a rule id registered in patterns.rules")
    ap.add_argument("--db", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--functions", required=True, help="comma-separated; the derivation case first")
    ap.add_argument("--kind", default=None, choices=("residual", "admission"),
                    help="which population to build cases from. Defaults to the rule's own kind.")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    from patterns import rules as rules_mod
    from solver import workspace

    rule = rules_mod.RULES.get(args.rule)
    if rule is None:
        print(f"unknown rule {args.rule!r}; known: {sorted(rules_mod.RULES)}")
        return 2

    repo = Path(args.repo)
    conn = sqlite3.connect(args.db, timeout=120)
    names = [n.strip() for n in args.functions.split(",") if n.strip()]
    kind = args.kind or getattr(rule, "case_kind", "residual")
    cases = cases_from_kb(conn, names, kind=kind)
    print("cases: %d of %d requested (kind=%s)" % (len(cases), len(names), kind), flush=True)

    def compile_fn(function: str, source: str, label: str):
        ws = workspace.bootstrap(repo, function)
        return workspace.score(ws, repo, function, source, conn=conn, func=function,
                               strategy=f"derive:{args.rule}:{label}", run_kind="derive")

    verdict = evaluate(rule, cases, compile_fn=compile_fn)
    result = verdict.summary()
    print(json.dumps(result, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"summary": result, "per_case": verdict.per_case},
                                       indent=2), encoding="utf-8")
    try:
        require_confirmation(verdict)
    except Unconfirmed as exc:
        print("\nNOT CONFIRMED: %s" % exc)
        return 1
    print("\nCONFIRMED on held-out case(s): %s" % ", ".join(verdict.beyond_derivation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
