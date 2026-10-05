"""Create the one-off experiment driver from the prior audited harness."""
from pathlib import Path

OUT = Path(__file__).resolve().parent
PRIOR = OUT.parent / "repair-transition-20260922"


def replace(text,old,new):
    assert old in text,old
    return text.replace(old,new)


def main():
    text = (PRIOR/"measure.py").read_text()
    text = text.replace("repair-transition-20260922/paired","repair-theory-20260922/paired").replace("repair-transition-v1:","repair-theory-v1:")
    text = replace(text,'from solver import regalloc_mutations','from solver import regalloc_mutations\nfrom solver.theory_repairs import inspect\nfrom eval.theory_planner import TheoryOnline')
    text = replace(text,'NEGATIVE = ["osCreateViManager","updateRacePlayerAirborneLaunch","updateRacePlayerMode06TerrainFall"]','NEGATIVE = []')
    text = replace(text,'evaluation_compiles=1280','evaluation_compiles=640').replace('compiles=1280','compiles=640')
    text = replace(text,'per_task_actions=32','per_task_actions=16')
    text = replace(text,'"kind":"frozen-repair-transition-comparison"','"kind":"frozen-theory-route-ablation"')
    text = replace(text,'"budget":32','"budget":16')
    text = replace(text,'for arm in ("control","planner"):','for arm in ("baseline","intake","theory"):')
    start = text.index('            cls = ActionOnline if arm == "planner" else Online')
    end = text.index('            assert result["complete"]',start)
    text = text[:start]+'''            def inspector(source,verdict):
                return inspect(source,name,verdict,repo=repo,workspace=ws)
            def factory(*args,**kwargs):
                return ActionOnline(*args,**kwargs) if arm == "baseline" else TheoryOnline(
                    *args,**kwargs,inspect_routes=inspector,guide=arm == "theory")
            env = factory(sources[name],callback,variants,context,checkpoint=lambda w:save_world(w,path))
            args = {"budget":report["budget"],"horizon":report["horizon"],"preview":report["preview"],"fallback":policy}
            result = run_planner(env,model,**args)
            assert result == replay_planner(env.world,model,variants,environment_factory=factory,**args)
''' + text[end:]
    start = text.index('    report["comparison"] = []')
    end = text.index('    check()\n    assert before',start)
    text = text[:start]+'''    report["comparison"] = []
    for case in cases:
        arms = {r["arm"]:r for r in report["runs"] if r["function"] == case["function"]}
        report["comparison"].append({**case,"arms":{a:{k:r[k] for k in ("exact","compiles","best_score")} for a,r in arms.items()}})
    report["known_losses"] = [r["function"] for r in report["runs"] if r["phase"] in {"retention","development"} and not r["exact"]]
    report["followup_gains"] = [r["function"] for r in report["comparison"] if r["arms"]["theory"]["exact"] and not r["arms"]["baseline"]["exact"]]
    report["losses"] = [r["function"] for r in report["comparison"] if r["arms"]["baseline"]["exact"] and not r["arms"]["theory"]["exact"]]
''' + text[end:]
    (OUT/"measure.py").write_text(text)
    tests = (PRIOR/"verify_tests.py").read_text()
    tests = replace(tests,'TESTS = ["repair_graph"','TESTS = ["repair_theory","theory_repairs","theory_planner","repair_graph"')
    tests = replace(tests,'MODULES = ["eval.repair_graph"','MODULES = ["solver.repair_theory","solver.theory_repairs","eval.theory_planner","eval.repair_graph"')
    (OUT/"verify_tests.py").write_text(tests)


if __name__ == "__main__":
    main()
