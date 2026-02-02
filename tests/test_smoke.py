import pytest

from utils import compute_split, format_eur, format_m3, parse_number


def test_smoke_split_from_text_inputs():
    basic_fees = parse_number("84,03€")
    usage_fees = parse_number("222,13€")
    s1_use = parse_number("12,500m3")
    s2_use = parse_number("17,500m3")

    assert basic_fees == 84.03
    assert usage_fees == 222.13
    assert s1_use == 12.5
    assert s2_use == 17.5

    result = compute_split(
        s1_use=s1_use,
        s2_use=s2_use,
        basic_fees=basic_fees,
        usage_fees=usage_fees,
        mismatch_policy="ignore",
        main_use=31.0,
    )

    total_ameen = result["basic_share"] + result["usage_share_1"]
    total_jussi = result["basic_share"] + result["usage_share_2"]

    assert pytest.approx(total_ameen + total_jussi, 0.001) == 306.16
    assert format_eur(total_ameen) == "134,57€"
    assert format_eur(total_jussi) == "171,59€"
    assert format_m3(result["adj_s1_use"]) == "12,500m3"
    assert format_m3(result["adj_s2_use"]) == "17,500m3"
