"""The machinery card classifies each application by what the mechanism did to the compiled object."""
from eval import machinery_card

DIFF = "--- a\n+++ b\n@@ -1,3 +1,3 @@\n-lw    v0,0x280(a0)\n+lw    v0,0(a0)\n jr    ra\n"
OTHER = "--- a\n+++ b\n@@ -1,3 +1,3 @@\n-lw    v0,0x280(a0)\n+lw    v1,0x280(a0)\n jr    ra\n"


def node(id, parent, family, sha, compiled=True, exact=False, score=90.0, diff=DIFF, stderr=""):
    return {"id": id, "parent": parent, "family": family, "source_sha256": sha,
            "verdict": {"compiled": compiled, "exact": exact, "score": score, "diff": diff, "stderr": stderr}}


def world():
    return {"context": {"task": "f"}, "nodes": [
        node("root", None, "baseline", "r"),
        node("root/0", "root", "breaker", "a", compiled=False, diff="", stderr="cfe: Error: candidate.c, line 3: bad"),
        node("root/1", "root", "idle", "b"),                                      # same diff: no-op
        node("root/2", "root", "fixer", "c", exact=True, score=100.0, diff=""),
        node("root/3", "root", "mover", "d", score=91.0, diff=OTHER),
    ]}


def test_each_application_is_classified_by_its_effect_on_the_object():
    table = machinery_card.card([("f", world())])
    assert table["breaker"]["broke"] == 1.0 and table["breaker"]["top_refusals"][0][0].startswith("candidate.c")
    assert table["idle"]["no_op"] == 1.0
    assert table["fixer"]["exact"] == 1
    assert table["mover"]["acted"] == 1.0 and table["mover"]["of_acted_improved"] == 1.0


def test_the_same_edit_seen_in_two_arms_counts_once():
    table = machinery_card.card([("f", world()), ("f", world())])
    assert table["idle"]["edges"] == 1
