"""eval/pg_update.py: group-relative advantages; flat groups carry no signal."""
from eval import pg_update as pg


def test_advantages_are_relative_within_a_group_and_flat_groups_skipped():
    rows = [{"group": "a", "reward": r} for r in (1.0, 0.0, 0.0, 0.0)] + \
           [{"group": "b", "reward": 0.5} for _ in range(4)]
    scored, flat = pg.advantages(rows)
    assert flat == 1 and len(scored) == 4
    advs = [adv for _s, adv in scored]
    assert advs[0] > 0 and all(x < 0 for x in advs[1:]) and abs(sum(advs)) < 1e-6
