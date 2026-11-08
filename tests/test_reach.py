import json

import pytest

from rocky.reach import reach_overlap, reach_overlap_tool
from rocky.tools import ToolError

TWO = {"audience": 400_000, "channels": [{"name": "TV", "reach": 200_000}, {"name": "Radio", "reach": 150_000}]}


def test_unknown_overlap_gives_bounds_and_no_point():
    out = reach_overlap(TWO)
    # lower = the bigger channel alone (200k); upper = 200k + 150k with nobody in both (350k)
    assert out["combined"]["lower"] == 200_000 and out["combined"]["upper"] == 350_000
    assert out["combined"]["point"] is None
    assert "overlap unknown" in out["combined"]["note"]
    # 200/400 = 50 %, 350/400 = 87.5 %, width 37.5 points
    assert out["combined_pct"] == {"lower": 50.0, "upper": 87.5, "point": None}
    assert out["bounds_width_pct"] == 37.5
    assert out["missing"] == ["overlap TV×Radio"]
    assert out["inputs"]["assumption_used"] == "bounds"
    narrows = out["narrows_most"]
    assert narrows["a"] == "TV" and narrows["b"] == "Radio"
    assert narrows["possible_shared"] == 150_000  # the whole of the smaller channel could sit inside the bigger one
    assert narrows["question"] == "Ask for the share of TV readers who also use Radio"
    assert "Unknown overlap between TV and Radio" in out["summary"]
    assert "ask what share of TV users also see Radio" in out["summary"]
    assert "50–87.5 % of 400 000" in out["summary"]


def test_incremental_steps_carry_the_bounds():
    out = reach_overlap(TWO)
    steps = out["incremental"]["steps"]
    assert out["incremental"]["order"] == ["TV", "Radio"]
    assert steps[0]["incremental_lower"] == steps[0]["incremental_upper"] == 200_000
    # Radio may add nobody (all inside TV) or all 150k (nobody in both)
    assert steps[1]["incremental_lower"] == 0 and steps[1]["incremental_upper"] == 150_000
    assert steps[1]["incremental_point"] is None and steps[1]["cumulative_point"] is None
    assert steps[1]["cumulative_lower"] == 200_000 and steps[1]["cumulative_upper"] == 350_000
    assert "assumption" in out["incremental"]["note"]


def test_independent_model_gives_a_point_inside_the_bounds():
    out = reach_overlap(TWO | {"assumption": "independent"})
    # 400k × (1 − (1 − 0.5)(1 − 0.375)) = 400k × (1 − 0.5 × 0.625) = 400k × 0.6875 = 275k
    assert out["combined"]["point"] == 275_000
    assert out["combined_pct"]["point"] == 68.8
    assert out["combined"]["lower"] == 200_000 and out["combined"]["upper"] == 350_000
    assert out["combined"]["point_assumption"].startswith("independent")
    steps = out["incremental"]["steps"]
    # Radio adds the 37.5 % share of the 200k TV did not reach: 0.375 × 200k = 75k
    assert steps[1]["incremental_point"] == 75_000 and steps[1]["cumulative_point"] == 275_000
    assert "independent model: 68.8 %" in out["summary"]


def test_a_given_overlap_collapses_the_bounds_to_the_point():
    out = reach_overlap(TWO | {"overlaps": [{"a": "TV", "b": "Radio", "shared": 100_000}]})
    # 200k + 150k − 100k = 250k, and with one pair that is exact
    assert out["combined"] == {
        "lower": 250_000,
        "upper": 250_000,
        "point": 250_000,
        "point_assumption": "bounds coincide: the overlaps given fix the combined reach",
    }
    assert out["combined_pct"]["point"] == 62.5 and out["bounds_width_pct"] == 0.0
    assert out["missing"] == [] and out["narrows_most"] is None
    assert out["inputs"]["assumption_used"] == "given"
    radio = out["incremental"]["steps"][1]
    # Radio's 150k minus the 100k already in TV = 50k new people
    assert radio["incremental_lower"] == radio["incremental_upper"] == radio["incremental_point"] == 50_000
    assert out["summary"].startswith("Combined reach 62.5 % of 400 000 (250 000 people).")


def test_three_channels_with_all_pairs_use_pairwise_inclusion_exclusion_as_a_lower_bound():
    out = reach_overlap(
        {
            "audience": 400_000,
            "channels": [{"name": "TV", "reach": 200_000}, {"name": "Radio", "reach": 150_000}, {"name": "Online", "reach": 100_000}],
            "overlaps": [
                {"a": "TV", "b": "Radio", "shared": 50_000},
                {"a": "TV", "b": "Online", "shared": 30_000},
                {"a": "Radio", "b": "Online", "shared": 20_000},
            ],
        }
    )
    # pairs only: 450k − (50k + 30k + 20k) = 350k; people in all three were subtracted
    # three times and added back never, so the true union is at least 350k
    assert out["combined"]["point"] == 350_000 and out["combined"]["lower"] == 350_000
    # upper: 450k minus the two heaviest overlaps that join all three channels (50k + 30k) = 370k
    assert out["combined"]["upper"] == 370_000
    assert "triple overlaps are ignored" in out["combined"]["point_assumption"]
    assert "lower bound" in out["combined"]["point_assumption"]
    assert out["missing"] == []
    online = out["incremental"]["steps"][2]
    # Online shares at least 30k (its TV overlap) and at most 50k (30k + 20k) with TV ∪ Radio
    assert online["name"] == "Online"
    assert online["incremental_lower"] == 50_000 and online["incremental_upper"] == 70_000
    assert online["incremental_point"] == 50_000


def test_pct_unit_converts_reach_and_shared_to_people():
    out = reach_overlap(
        {
            "audience": "1 000 000",
            "reach_unit": "pct",
            "channels": [{"name": "Facebook", "reach": "30 %"}, {"name": "Instagram", "reach": 20}],
            "overlaps": [{"a": "Facebook", "b": "Instagram", "shared": 5}],
        }
    )
    assert [c["reach"] for c in out["channels"]] == [300_000, 200_000]
    assert out["channels"][0]["reach_pct"] == 30.0
    # 300k + 200k − 50k = 450k = 45 %
    assert out["combined"]["point"] == 450_000 and out["combined_pct"]["point"] == 45.0


def test_cost_gives_cpm_and_greedy_order_favours_cheap_incremental_reach():
    out = reach_overlap(
        {
            "audience": 400_000,
            "channels": [{"name": "TV", "reach": 200_000, "cost": "20 000 €"}, {"name": "Radio", "reach": 100_000, "cost": 2000}],
        }
    )
    tv, radio = out["channels"]
    # 20 000 / 200 000 × 1000 = 100 per thousand reached; 2 000 / 100 000 × 1000 = 20
    assert tv["cpm_reach"] == 100.0 and tv["cost_per_reach"] == 0.1
    assert radio["cpm_reach"] == 20.0
    # Radio: 100k per 2 000 = 50 people per euro; TV: 200k per 20 000 = 10 → Radio first
    assert out["incremental"]["order"] == ["Radio", "TV"]
    steps = out["incremental"]["steps"]
    assert steps[0]["cpm_incremental"] == 20.0
    # TV after Radio adds between 200k − 100k (Radio fully inside TV) and 200k (no one in both)
    assert steps[1]["incremental_lower"] == 100_000 and steps[1]["incremental_upper"] == 200_000
    assert "per cost" in out["incremental"]["note"]
    cost = out["cost"]
    assert cost["total_cost"] == 22_000
    # 22 000 / 300k × 1000 = 73.33 at best, 22 000 / 200k × 1000 = 110 at worst
    assert cost["cpm_combined_lower"] == 73.33 and cost["cpm_combined_upper"] == 110.0


def test_budget_is_checked_against_channel_costs():
    out = reach_overlap(
        {"audience": 400_000, "budget": 10_000, "channels": [{"name": "TV", "reach": 200_000, "cost": 12_000}, {"name": "Radio", "reach": 100_000, "cost": 2000}]}
    )
    assert out["cost"]["budget_left"] == -4_000
    assert any("exceed the budget" in w for w in out["warnings"])


def test_frequency_gives_impressions_and_grps():
    out = reach_overlap(
        {"audience": 400_000, "channels": [{"name": "TV", "reach": 200_000, "frequency": 3}, {"name": "Radio", "reach": 100_000, "frequency": "2,5"}]}
    )
    assert out["channels"][0]["impressions"] == 600_000 and out["channels"][1]["impressions"] == 250_000
    # 850k impressions / 400k people = 212.5 GRPs
    assert out["exposure"]["impressions"] == 850_000 and out["exposure"]["grp"] == 212.5
    # 850k / 300k = 2.83 at the widest reach, 850k / 200k = 4.25 at the narrowest
    assert out["exposure"]["avg_frequency_lower"] == 2.83 and out["exposure"]["avg_frequency_upper"] == 4.25


def test_reach_above_audience_is_clipped_with_a_warning():
    out = reach_overlap({"audience": 100_000, "channels": [{"name": "TV", "reach": 120_000}]})
    assert out["channels"][0]["reach"] == 100_000 and out["channels"][0]["reach_pct"] == 100.0
    assert any("clipped" in w for w in out["warnings"])


def test_reaches_adding_above_the_audience_is_noted_not_refused():
    out = reach_overlap({"audience": 300_000, "channels": [{"name": "TV", "reach": 200_000}, {"name": "Radio", "reach": 150_000}]})
    assert out["sum_of_reaches"] == 350_000 and out["combined"]["upper"] == 300_000
    assert any("add up to 350 000" in w for w in out["warnings"])


def test_one_channel_needs_no_overlap_question():
    out = reach_overlap({"audience": 400_000, "channels": [{"name": "TV", "reach": 200_000, "cost": 5000}]})
    assert out["combined"]["lower"] == out["combined"]["upper"] == out["combined"]["point"] == 200_000
    assert out["missing"] == [] and out["narrows_most"] is None
    assert out["channels"][0]["cpm_reach"] == 25.0  # 5 000 / 200 000 × 1000
    assert any("One channel" in w for w in out["warnings"])
    assert "no overlap to resolve" in out["summary"]


def test_narrows_most_picks_the_pair_with_the_largest_possible_overlap():
    out = reach_overlap(
        {
            "audience": 1_000_000,
            "channels": [{"name": "TV", "reach": 500_000}, {"name": "Radio", "reach": 400_000}, {"name": "Print", "reach": 50_000}],
            "overlaps": [{"a": "TV", "b": "Radio", "shared": 200_000}],
        }
    )
    # unknown: TV×Print (≤ 50k) and Radio×Print (≤ 50k); known pair is excluded; ties go to the bigger channel
    assert out["missing"] == ["overlap TV×Print", "overlap Radio×Print"]
    assert out["narrows_most"]["pair"] == "overlap TV×Print"
    # lower: TV ∪ Radio = 500k + 400k − 200k = 700k; upper: 950k − 200k = 750k
    assert out["combined"]["lower"] == 700_000 and out["combined"]["upper"] == 750_000


def test_lenient_numbers_and_audience_name():
    out = reach_overlap({"audience": "420 000", "audience_name": "Lithuanian adults", "channels": [{"name": "TV", "reach": "250k", "cost": "1 250,50 €"}]})
    assert out["inputs"]["audience"] == 420_000 and out["channels"][0]["reach"] == 250_000
    assert out["channels"][0]["cost"] == 1250.5
    assert "of 420 000 Lithuanian adults" in out["summary"]


@pytest.mark.parametrize(
    "args, message",
    [
        ({"channels": [{"name": "TV", "reach": 1}]}, "audience is required"),
        ({"audience": 0, "channels": [{"name": "TV", "reach": 1}]}, "greater than zero"),
        ({"audience": -5, "channels": [{"name": "TV", "reach": 1}]}, "at least 0"),
        ({"audience": 100}, "at least one channel"),
        ({"audience": 100, "channels": []}, "at least one channel"),
        ({"audience": 100, "channels": ["TV"]}, "must be an object"),
        ({"audience": 100, "channels": [{"reach": 10}]}, "channel 1 needs a name"),
        ({"audience": 100, "channels": [{"name": "TV"}]}, "reach of TV is required"),
        ({"audience": 100, "channels": [{"name": "TV", "reach": -10}]}, "reach of TV must be at least 0"),
        ({"audience": 100, "channels": [{"name": "TV", "reach": 10, "cost": -1}]}, "cost of TV must be at least 0"),
        ({"audience": 100, "channels": [{"name": "TV", "reach": "lots"}]}, "must be a number"),
        ({"audience": 100, "channels": [{"name": "TV", "reach": 10}, {"name": "tv", "reach": 5}]}, "appears twice"),
        ({"audience": 100, "reach_unit": "percent", "channels": [{"name": "TV", "reach": 10}]}, "reach_unit must be people or pct"),
        ({"audience": 100, "assumption": "magic", "channels": [{"name": "TV", "reach": 10}]}, "assumption must be independent, bounds or given"),
        (
            {"audience": 100, "channels": [{"name": "TV", "reach": 50}, {"name": "Radio", "reach": 40}], "overlaps": [{"a": "TV", "b": "Print", "shared": 5}]},
            "unknown channel 'Print'",
        ),
        (
            {"audience": 100, "channels": [{"name": "TV", "reach": 50}, {"name": "Radio", "reach": 40}], "overlaps": [{"a": "TV", "b": "TV", "shared": 5}]},
            "two different channels",
        ),
        (
            {"audience": 100, "channels": [{"name": "TV", "reach": 50}, {"name": "Radio", "reach": 40}], "overlaps": [{"a": "TV", "b": "Radio", "shared": 45}]},
            "larger than Radio's reach 40",
        ),
        (
            {"audience": 100, "channels": [{"name": "TV", "reach": 50}, {"name": "Radio", "reach": 40}], "overlaps": [{"a": "TV", "b": "Radio", "shared": -1}]},
            "at least 0",
        ),
        (
            {"audience": 100, "channels": [{"name": "TV", "reach": 50}, {"name": "Radio", "reach": 40}], "overlaps": [{"a": "TV", "b": "Radio"}]},
            "shared of TV×Radio is required",
        ),
    ],
)
def test_bad_input_is_refused_in_plain_words(args, message):
    with pytest.raises(ToolError, match=message):
        reach_overlap(args)


def test_tool_returns_json():
    text = reach_overlap_tool(TWO)
    data = json.loads(text)
    assert data["combined"]["lower"] == 200_000 and data["combined"]["point"] is None
    assert "overlap TV×Radio" in text  # ensure_ascii=False keeps the × readable


# --- extensions: source / cap / addressable / category / role / measure -----


def test_plain_call_output_shape_is_unchanged_by_the_extensions():
    out = reach_overlap(TWO)
    assert set(out) == {
        "inputs", "channels", "sum_of_reaches", "combined", "combined_pct", "bounds_width_pct",
        "missing", "warnings", "narrows_most", "incremental", "summary",
    }
    assert set(out["channels"][0]) == {"name", "reach", "reach_pct", "cost", "frequency"}
    assert set(out["inputs"]) == {"audience", "audience_name", "reach_unit", "assumption", "assumption_used", "budget", "known_overlaps", "unknown_overlaps"}


VILNIAUS_DUONA = {
    "audience": 1_200_000,
    "required_reach": 500_000,
    "channels": [
        {"name": "Meta paid", "reach": 320_000, "cost": 4200, "frequency": 2.4, "source": "paid"},
        {"name": "FB/IG organic", "reach": 45_000, "source": "owned"},
        {"name": "Newsletter", "reach": 18_000, "source": "owned"},
        {"name": "Shares", "reach": 61_000, "source": "earned"},
    ],
}


def test_paid_owned_earned_split_and_whether_unpaid_can_carry_the_target():
    out = reach_overlap(VILNIAUS_DUONA)
    paid, owned, earned = out["by_source"]["paid"], out["by_source"]["owned"], out["by_source"]["earned"]
    # 4 200 / 320 000 × 1000 = 13.125 per thousand reached
    assert paid["reach_lower"] == paid["reach_upper"] == 320_000 and paid["cost"] == 4200
    assert paid["cpm_reached_lower"] == pytest.approx(13.125, abs=0.01)
    # owned: 45k alone up to 45k + 18k with nobody in both
    assert owned["reach_lower"] == 45_000 and owned["reach_upper"] == 63_000 and "cost" not in owned
    assert earned["reach_lower"] == 61_000 and "at least" in earned["note"]
    # impressions 320k × 2.4 = 768k + 45k + 18k + 61k (one exposure each) = 892k; 61k / 892k = 6.8 %
    assert out["earned_share_pct"] == 6.8
    assert "one exposure" in out["earned_share_note"]
    # owned + earned at most 63k + 61k = 124k < 500k required → no; paid must bring 376k
    assert out["unpaid_reach_upper"] == 124_000 and out["unpaid_sustains_required"] == "no"
    assert out["paid_must_deliver_at_least"] == 376_000
    assert out["combined"]["lower"] == 320_000 and out["combined"]["upper"] == 444_000
    assert "at most 124 000 of the 500 000 required" in out["summary"]
    assert out["inputs"]["required_reach"] == 500_000
    # owned and earned channels have no media cost by nature: not reported as missing
    assert not any(m.startswith("cost for") for m in out["missing"])
    assert out["cost"]["total_cost"] == 4200 and out["cost"]["cpm_combined_lower"] == pytest.approx(4200 / 444_000 * 1000, abs=0.01)


def test_sources_without_required_reach_or_required_reach_without_sources_say_unknown():
    args = {k: v for k, v in VILNIAUS_DUONA.items() if k != "required_reach"}
    args["channels"] = [dict(c) for c in args["channels"]]
    del args["channels"][2]["source"]
    out = reach_overlap(args)
    assert out["unpaid_sustains_required"] == "unknown"
    assert "required_reach" in out["missing"] and "source for Newsletter" in out["missing"]
    assert out["by_source"]["owned"]["channels"] == ["FB/IG organic"]  # the untagged channel is left out of by_source
    out = reach_overlap({"audience": 1_000_000, "required_reach": 500_000, "channels": [{"name": "TV", "reach": 200_000}]})
    assert out["unpaid_sustains_required"] == "unknown" and "by_source" not in out
    assert any(m.startswith("source (paid, owned or earned) per channel") for m in out["missing"])
    # unpaid at least the required → yes
    out = reach_overlap({"audience": 1_000_000, "required_reach": 50_000, "channels": [{"name": "Newsletter", "reach": 60_000, "source": "owned"}]})
    assert out["unpaid_sustains_required"] == "yes"


PIGU = {
    "audience": 1_500_000,
    "channels": [
        {
            "name": "Meta",
            "reach": 410_000,
            "cost": 9000,
            "impressions": 2_100_000,
            "cap": 3,
            "frequency_distribution": [{"exposures": "1-2", "people": 180_000}, {"exposures": "3–5", "people": 130_000}, {"exposures": "6+", "people": 100_000}],
        }
    ],
}


def test_frequency_cap_scenario_from_a_distribution():
    out = reach_overlap(PIGU)
    cap = out["cap_scenario"]["Meta"]
    # 1–2 (midpoint 1.5) releases nothing; 3–5 (midpoint 4) releases 1 × 130k; 6+ counts as 6 → 3 × 100k: 430k
    assert cap["released_impressions"] == 430_000
    # 430k of 2.1M = 20.5 %; 9 000 × 430/2100 = 1 842.86
    assert cap["released_share_pct"] == 20.5 and cap["released_budget"] == 1842.86
    assert cap["unexposed_pool"] == 1_090_000  # 1.5M − 410k
    # 430k / 3 exposures each = 143 333 new people → reach 553 333; 2.1M / 553 333 = 3.8 average frequency
    assert cap["extra_reach_upper"] == 143_333 and cap["new_reach_upper"] == 553_333 and cap["new_avg_frequency"] == 3.8
    assert cap["extra_reach_lower"] is None
    assert any(m.startswith("cpm_unexposed_factor for Meta") for m in out["missing"])
    assert "midpoint" in cap["note"] and "capped vs uncapped" in cap["note"]
    # the given impressions win over reach × frequency; the derived frequency is 2.1M / 410k = 5.12
    assert out["channels"][0]["impressions"] == 2_100_000 and out["channels"][0]["frequency"] == 5.12
    assert out["exposure"]["impressions"] == 2_100_000
    # new people cost 1.5× per impression → 143 333 / 1.5 = 95 556 at worst
    args = {"audience": 1_500_000, "channels": [PIGU["channels"][0] | {"cpm_unexposed_factor": 1.5}]}
    cap = reach_overlap(args)["cap_scenario"]["Meta"]
    assert cap["extra_reach_lower"] == 95_556 and cap["new_reach_lower"] == 505_556


def test_cap_without_a_distribution_or_without_unexposed_people_gives_no_extra_reach():
    out = reach_overlap({"audience": 1_500_000, "channels": [{"name": "Meta", "reach": 410_000, "frequency": 6.8, "cap": 3}]})
    assert "cap_scenario" not in out
    assert any(m.startswith("frequency_distribution for Meta") for m in out["missing"])
    out = reach_overlap(
        {"audience": 100_000, "channels": [{"name": "Meta", "reach": 100_000, "cap": 2, "frequency_distribution": [{"exposures": 5, "people": 100_000}]}]}
    )
    cap = out["cap_scenario"]["Meta"]
    assert cap["released_impressions"] == 300_000 and cap["extra_reach_upper"] == 0 and "no unexposed people" in cap["note"]
    # distribution that does not add up to the reach is flagged, not fixed
    out = reach_overlap(
        {"audience": 1_000_000, "channels": [{"name": "Meta", "reach": 400_000, "cap": 2, "frequency_distribution": [{"exposures": 3, "people": 100_000}]}]}
    )
    assert any("sums to 100 000 people but reach is 400 000" in w for w in out["warnings"])


DEHUMIDIFIER = {
    "audience": 2_100_000,
    "category_buyers": 6000,
    "category_source": "Sodra occupation counts",
    "channels": [
        {"name": "Delfi verslas", "reach": 600_000, "cost": 4200, "addressable_pct": 0.4, "addressable_source": "media kit"},
        {"name": "Verslo žinios", "reach": 90_000, "cost": 2800, "addressable_pct": 2.5},
        {"name": "Logistics newsletter", "reach": 12_000, "cost": 900, "addressable_pct": 30},
    ],
}


def test_addressable_reach_reranks_channels_by_cost_per_buyer():
    out = reach_overlap(DEHUMIDIFIER)
    delfi, vz, news = out["channels"]
    # 600k × 0.4 % = 2 400 buyers, 4 200 / 2.4 = 1 750 per thousand; 90k × 2.5 % = 2 250, 2 800 / 2.25 = 1 244.44; 12k × 30 % = 3 600, 900 / 3.6 = 250
    assert delfi["addressable_reach"] == 2400 and delfi["cpm_addressable"] == 1750.0 and delfi["cpm_reach"] == 7.0
    assert vz["addressable_reach"] == 2250 and vz["cpm_addressable"] == 1244.44
    assert news["addressable_reach"] == 3600 and news["cpm_addressable"] == 250.0
    block = out["addressable"]
    assert block["basis"] == "buyers" and block["missing"] == []
    assert [r["name"] for r in block["ranking"]] == ["Logistics newsletter", "Verslo žinios", "Delfi verslas"]
    assert block["ranking"][0]["rank"] == 1
    # addressable bounds: at least the biggest (3 600), at most all 6 000 buyers (the 8 250 sum is capped at the category)
    assert block["combined"]["reach_lower"] == 3600 and block["combined"]["reach_upper"] == 6000
    assert block["combined"]["pct_of_category"] == {"lower": 60.0, "upper": 100.0}
    # 7 900 total / 6 000 × 1000 = 1 316.67 at best, / 3 600 = 2 194.44 at worst
    assert block["cpm_addressable_combined_lower"] == 1316.67 and block["cpm_addressable_combined_upper"] == 2194.44
    assert "Cheapest per thousand addressable buyers: Logistics newsletter (250.00); Delfi verslas is cheapest per reader but not per buyer." in out["summary"]
    assert out["category_reach"]["source"] == "Sodra occupation counts"


def test_addressable_pct_missing_for_one_channel_keeps_the_ranking_by_readers():
    args = {"audience": 2_100_000, "channels": [dict(c) for c in DEHUMIDIFIER["channels"]]}
    del args["channels"][0]["addressable_pct"]
    out = reach_overlap(args)
    assert out["addressable"]["basis"] == "readers" and out["addressable"]["missing"] == ["Delfi verslas"]
    assert "combined" not in out["addressable"]
    assert "addressable_pct for Delfi verslas" in out["missing"]
    assert "addressable_reach" not in out["channels"][0]
    with pytest.raises(ToolError, match="addressable_pct of Delfi verslas must be between 0 and 100"):
        reach_overlap({"audience": 100, "channels": [{"name": "Delfi verslas", "reach": 50, "addressable_pct": 150}]})


AVIZE = {
    "audience": 1_200_000,
    "category_buyers": 1_200_000,
    "category_source": "plant-milk buyer panel",
    "channels": [
        {"name": "newsletter", "reach": 8750, "existing_customer_share": 0.9},
        {"name": "Facebook organic", "reach": 12_000, "existing_customer_share": 0.6},
        {"name": "Meta paid", "reach": 300_000, "cost": 15_000, "existing_customer_share": 0.1},
    ],
}


def test_category_reach_and_future_buyer_reach():
    out = reach_overlap(AVIZE)
    # combined 300k–320 750 over 1.2M category buyers = 25.0–26.7 %
    assert out["combined"]["lower"] == 300_000 and out["combined"]["upper"] == 320_750
    assert out["category_reach"] == {"denominator": 1_200_000, "source": "plant-milk buyer panel", "lower_pct": 25.0, "upper_pct": 26.7, "point_pct": None}
    future = out["future_buyer_reach"]
    # non-customers: 8 750 × 0.1 = 875; 12 000 × 0.4 = 4 800; 300k × 0.9 = 270k → 270k alone up to 275 675 added up
    assert future["lower"] == 270_000 and future["upper"] == 275_675
    assert future["pct_of_category"] == {"lower": 22.5, "upper": 23.0}
    assert future["channels_mostly_customers"] == ["newsletter", "Facebook organic"]
    assert "That is 25–26.7 % of 1 200 000 category buyers." in out["summary"]
    # 8 750 + 12 000 = 20 750 / 1.2M = 1.7 %
    assert "newsletter, Facebook organic reach mostly current customers (1.7 % of category buyers at most)." in out["summary"]
    assert out["channels"][0]["future_buyer_reach"] == 875


def test_customer_audiences_and_partial_customer_shares_are_flagged():
    out = reach_overlap({"audience": 40_000, "audience_name": "Facebook followers", "category_buyers": 30_000, "channels": [{"name": "FB", "reach": 12_000}]})
    assert any("customer or follower base" in w for w in out["warnings"])
    assert any("category_buyers 30 000 is below the audience 40 000" in w for w in out["warnings"])
    assert any(m.startswith("category_source") for m in out["missing"])
    out = reach_overlap({"audience": 100_000, "channels": [{"name": "A", "reach": 50_000, "existing_customer_share": 0.5}, {"name": "B", "reach": 20_000}]})
    future = out["future_buyer_reach"]
    # lower: B all customers → 25k alone; upper: B no customers → 25k + 20k = 45k
    assert future["lower"] == 25_000 and future["upper"] == 45_000
    assert "No share for B" in future["note"] and "existing_customer_share for B" in out["missing"]
    with pytest.raises(ToolError, match="existing_customer_share of A must be between 0 and 1"):
        reach_overlap({"audience": 100, "channels": [{"name": "A", "reach": 50, "existing_customer_share": 1.5}]})


def test_brand_activation_split_against_the_60_40_comparison_point():
    out = reach_overlap(
        {
            "audience": 420_000,
            "budget": 120_000,
            "channels": [
                {"name": "Meta conversion", "reach": 200_000, "cost": 48_000, "role": "activation"},
                {"name": "Google Search", "reach": 100_000, "cost": 30_000, "role": "activation"},
                {"name": "TV3 sponsorship", "reach": 250_000, "cost": 30_000, "role": "brand"},
                {"name": "YouTube bumper", "reach": 150_000, "cost": 12_000, "role": "brand"},
            ],
        }
    )
    split = out["split"]
    # brand 30k + 12k = 42k, activation 48k + 30k = 78k → 35 / 65, 25 points under the 60 reference
    assert split["brand"] == 42_000 and split["activation"] == 78_000
    assert split["brand_pct"] == 35.0 and split["activation_pct"] == 65.0 and split["deviation_pts"] == -25.0
    assert split["reference"]["brand_pct"] == 60 and "Binet & Field" in split["reference"]["source"]
    assert split["unassigned"] == [] and split["partial"] is False
    assert "60/40" in split["note"]
    # role both splits 50/50 unless role_brand_pct says otherwise; missing roles and costs make it partial
    out = reach_overlap(
        {
            "audience": 420_000,
            "channels": [
                {"name": "Radio", "reach": 100_000, "cost": 10_000, "role": "both", "role_brand_pct": 25},
                {"name": "Print", "reach": 50_000, "cost": 6000},
                {"name": "PR", "reach": 20_000, "role": "brand"},
            ],
        }
    )
    split = out["split"]
    # 25 % of 10 000 = 2 500 brand, 7 500 activation
    assert split["brand"] == 2500 and split["activation"] == 7500 and split["brand_pct"] == 25.0
    assert split["unassigned"] == ["Print"] and split["uncosted"] == ["PR"] and split["partial"] is True
    assert "role for Print" in out["missing"]
    with pytest.raises(ToolError, match="role of Radio must be one of brand, activation, both"):
        reach_overlap({"audience": 100, "channels": [{"name": "Radio", "reach": 10, "role": "awareness"}]})


YT_VS_TV = {
    "audience": 2_300_000,
    "channels": [
        {"name": "TV3", "reach": 480_000, "cost": 40_000, "measure": {"metric": "people", "base": "LT 4+", "period": "month", "devices": "TV sets", "source": "Kantar TAM"}},
        {"name": "YouTube", "reach": 1_200_000, "cost": 20_000, "measure": {"metric": "views", "base": "all countries", "period": "month", "devices": "all", "source": "YouTube Analytics"}},
    ],
}


def test_views_are_not_added_to_people():
    out = reach_overlap(YT_VS_TV)
    comp = out["comparability"]
    assert comp["comparable"] is False
    assert comp["pairs"][0]["a"] == "TV3" and comp["pairs"][0]["b"] == "YouTube"
    assert {"metric", "base"} <= set(comp["pairs"][0]["differs"])
    assert comp["not_comparable"] == ["YouTube"]
    # only TV3 is combined
    assert out["combined"]["lower"] == out["combined"]["upper"] == 480_000
    yt = out["channels"][1]
    assert yt["reach"] is None and yt["raw_figure"] == 1_200_000 and "not people" in yt["excluded_reason"]
    assert "cpm_reach" not in yt
    assert "YouTube is excluded from the combined reach" in out["summary"]
    assert out["incremental"]["order"] == ["TV3"]


def test_measurement_bases_that_differ_or_are_missing_are_named():
    args = {
        "audience": 2_300_000,
        "channels": [
            {"name": "TV3", "reach": 480_000, "measure": {"metric": "people", "base": "LT 4+", "period": "month"}},
            {"name": "LNK", "reach": 400_000, "measure": {"metric": "people", "base": "LT 18+", "period": "month"}},
            {"name": "Delfi", "reach": 300_000},
        ],
    }
    out = reach_overlap(args)
    assert out["comparability"]["comparable"] is False
    assert out["comparability"]["pairs"][0]["differs"] == ["base"]
    assert sorted(out["comparability"]["not_comparable"]) == ["LNK", "TV3"]
    assert any("mixes measurement bases" in w for w in out["warnings"])
    assert any(m.startswith("measurement basis for Delfi") for m in out["missing"])
    # the combined figure is still computed over all three people-based channels
    assert out["combined"]["upper"] == 1_180_000
    # an overlap naming an excluded channel is dropped with a warning, not an error
    out = reach_overlap(YT_VS_TV | {"overlaps": [{"a": "TV3", "b": "YouTube", "shared": 100_000}]})
    assert any("excluded as not comparable and is ignored" in w for w in out["warnings"])
    with pytest.raises(ToolError, match="no channel is measured in people"):
        reach_overlap({"audience": 100, "channels": [{"name": "YT", "reach": 50, "measure": {"metric": "views"}}]})
    with pytest.raises(ToolError, match="measure.metric of YT must be one of people, views, impressions, gross_contacts"):
        reach_overlap({"audience": 100, "channels": [{"name": "YT", "reach": 50, "measure": {"metric": "clicks"}}]})


@pytest.mark.parametrize(
    "channel, message",
    [
        ({"name": "Meta", "reach": 10, "source": "viral"}, "source of Meta must be one of paid, owned, earned"),
        ({"name": "Meta", "reach": 10, "cap": 2.5}, "cap of Meta must be a whole number"),
        ({"name": "Meta", "reach": 10, "cap": 0}, "cap of Meta must be at least 1"),
        ({"name": "Meta", "reach": 10, "cap": 2, "frequency_distribution": [{"exposures": "lots", "people": 5}]}, "must be a whole number, a range like 3-5, or 6\\+"),
        ({"name": "Meta", "reach": 10, "cap": 2, "frequency_distribution": [{"people": 5}]}, "bucket 1 of Meta's frequency_distribution must be an object with exposures and people"),
        ({"name": "Meta", "reach": 10, "cap": 2, "frequency_distribution": "many"}, "frequency_distribution of Meta must be a list"),
        ({"name": "Meta", "reach": 10, "cpm_unexposed_factor": 0}, "cpm_unexposed_factor of Meta must be above 0"),
        ({"name": "Meta", "reach": 10, "measure": "people"}, "measure of Meta must be an object"),
        ({"name": "Meta", "reach": 10, "role_brand_pct": 120}, "role_brand_pct of Meta must be between 0 and 100"),
    ],
)
def test_bad_extension_input_is_refused_in_plain_words(channel, message):
    with pytest.raises(ToolError, match=message):
        reach_overlap({"audience": 100, "channels": [channel]})


def test_category_buyers_must_be_positive():
    with pytest.raises(ToolError, match="category_buyers must be greater than zero"):
        reach_overlap({"audience": 100, "category_buyers": 0, "channels": [{"name": "TV", "reach": 10}]})
