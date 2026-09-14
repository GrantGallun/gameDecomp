import math

import pytest

from eval.benchmark_campaign_parallel import fit_amdahl, fit_contention, quantile_cohorts, recommend


def test_amdahl_recovers_known_serial_fraction():
    rows = [{'workers':p,'wall_seconds':20+80/p} for p in range(1,5)]
    fit = fit_amdahl(rows)
    assert fit['effective_serial_fraction'] == pytest.approx(.2)
    assert fit['rmse_seconds'] < 1e-10


def test_contention_predicts_interior_optimum():
    rows = [{'workers':p,'wall_seconds':4+36/p+4*(p-1)} for p in range(1,5)]
    fit = fit_contention(rows,4)
    assert fit['continuous_optimum'] == pytest.approx(3)
    assert fit['predicted_integer_optimum'] == 3
    assert fit['rmse_seconds'] < 1e-10


def test_contention_handles_zero_serial_boundary_without_negative_cost():
    rows = [{'workers':p,'wall_seconds':40/p} for p in range(1,5)]
    fit = fit_contention(rows,4)
    assert fit['serial_seconds'] >= 0
    assert fit['parallel_seconds'] >= 0
    assert fit['contention_seconds_per_extra_worker'] >= 0
    assert fit['predicted_integer_optimum'] == 4


def test_cohorts_disjoint_and_spread_across_duration_range():
    rows = [{'function':f'f{i:03}', 'historical_wall_seconds':i} for i in range(80)]
    groups = quantile_cohorts(rows,8)
    names = [r['function'] for g in groups.values() for r in g]
    assert len(names) == len(set(names)) == 16
    for cohort in groups.values():
        assert cohort[0]['historical_wall_seconds'] < 10
        assert cohort[-1]['historical_wall_seconds'] >= 70
    assert groups == quantile_cohorts(list(reversed(rows)),8)


def test_recommendation_respects_cpu_memory_outcomes_and_noise_margin():
    rows = [{'cohort':'fit','workers':p,'wall_seconds':t,'valid':True,
             'peak_tree_rss_bytes':m} for p,t,m in [(1,100,10),(2,50,20),(3,49,30),(4,30,100)]]
    assert recommend(rows,50,4)['workers'] == 2  # 3 is only 2% faster; 4 exceeds RAM
    assert recommend(rows,200,2)['workers'] == 2
    rows[1]['valid'] = False
    assert recommend(rows,50,4)['workers'] == 3
    assert recommend(rows,1,4) is None


def test_fit_refuses_single_concurrency():
    with pytest.raises(ValueError):
        fit_amdahl([{'workers':1,'wall_seconds':1}]*2)
