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
