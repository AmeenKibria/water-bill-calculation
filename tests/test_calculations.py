import pytest

from utils import compute_split, compute_trueup


def test_compute_split_ignore_mismatch():
    result = compute_split(
        s1_use=30.0,
        s2_use=20.0,
        basic_fees=100.0,
        usage_fees=200.0,
        mismatch_policy="ignore",
        main_use=55.0,
    )
    assert pytest.approx(result["adj_s1_use"], 0.001) == 30.0
    assert pytest.approx(result["adj_s2_use"], 0.001) == 20.0
    assert pytest.approx(result["total_1"] + result["total_2"], 0.001) == 300.0


def test_compute_split_half_mismatch():
    result = compute_split(
        s1_use=30.0,
        s2_use=20.0,
        basic_fees=100.0,
        usage_fees=200.0,
        mismatch_policy="half",
        main_use=60.0,
    )
    assert pytest.approx(result["adj_s1_use"], 0.001) == 35.0
    assert pytest.approx(result["adj_s2_use"], 0.001) == 25.0


def test_compute_split_proportional_mismatch():
    result = compute_split(
        s1_use=30.0,
        s2_use=20.0,
        basic_fees=100.0,
        usage_fees=200.0,
        mismatch_policy="proportional",
        main_use=60.0,
    )
    assert pytest.approx(result["adj_s1_use"], 0.001) == 36.0
    assert pytest.approx(result["adj_s2_use"], 0.001) == 24.0


def test_compute_trueup():
    result = compute_trueup(s1_use=35.0, s2_use=55.0, trueup_amount=60.0)
    assert pytest.approx(result["share_1"], 0.001) == 23.333333
    assert pytest.approx(result["share_2"], 0.001) == 36.666667

