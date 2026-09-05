from rocky.engine import _maybe_lift_budget
from rocky.usage import estimate_run_eur, needs_budget_lift, parse_budget_reply, suggest_cap_eur


def test_moderate_writing_stays_under_one_euro():
    estimate = estimate_run_eur(
        {"complexity": "moderate", "domain": "writing", "needs_web": False},
        "Write a short article",
    )
    assert estimate < 1.15
    assert not needs_budget_lift(estimate, 1.0)


def test_hard_marketing_trips_the_default_cap():
    estimate = estimate_run_eur(
        {"complexity": "hard", "domain": "marketing", "needs_web": True},
        "write Meta Ads copy and a deck",
    )
    assert estimate > 1.0
    assert needs_budget_lift(estimate, 1.0)
    assert suggest_cap_eur(estimate, 1.0) >= 3.0


def test_already_high_cap_does_not_nag():
    estimate = estimate_run_eur(
        {"complexity": "hard", "domain": "code", "needs_web": True},
        "refactor the package",
    )
    assert not needs_budget_lift(estimate, 10.0)


def test_parse_budget_reply():
    assert parse_budget_reply("yes", 5.0, 1.0) == 5.0
    assert parse_budget_reply("taip", 5.0, 1.0) == 5.0
    assert parse_budget_reply("1", 5.0, 1.0) == 5.0
    assert parse_budget_reply("keep", 5.0, 1.0) == 1.0
    assert parse_budget_reply("2", 5.0, 1.0) == 1.0
    assert parse_budget_reply("3", 5.0, 1.0) == 3.0
    assert parse_budget_reply("€8", 5.0, 1.0) == 8.0
    assert parse_budget_reply("nope", 5.0, 1.0) == 1.0


def test_maybe_lift_raises_this_run_only():
    events = []

    usd, eur = _maybe_lift_budget(
        assessment={"complexity": "hard", "domain": "marketing"},
        estimate_eur=7.25,
        budget_eur=1.0,
        usd_cap=1.09,
        rate=0.92,
        ask_user=lambda q, o: "yes",
        fire=lambda t, **p: events.append((t, p)),
    )
    assert eur == 10.0
    assert abs(usd - (10.0 / 0.92)) < 0.01
    kinds = [k for k, _ in events]
    assert kinds == ["budget_offer", "budget_set"]
    assert events[1][1]["lifted"] is True
    assert events[1][1]["budget_eur"] == 10.0


def test_maybe_lift_keep_leaves_default():
    events = []
    usd, eur = _maybe_lift_budget(
        assessment={"complexity": "hard", "domain": "research"},
        estimate_eur=4.0,
        budget_eur=1.0,
        usd_cap=1.09,
        rate=0.92,
        ask_user=lambda q, o: "keep",
        fire=lambda t, **p: events.append((t, p)),
    )
    assert eur == 1.0
    assert events[-1][1]["lifted"] is False


def test_maybe_lift_without_human_keeps_cap():
    events = []
    usd, eur = _maybe_lift_budget(
        assessment={"complexity": "hard", "domain": "code"},
        estimate_eur=4.0,
        budget_eur=1.0,
        usd_cap=1.09,
        rate=0.92,
        ask_user=None,
        fire=lambda t, **p: events.append((t, p)),
    )
    assert eur == 1.0
    assert usd == 1.09
    assert events[0][0] == "budget_offer"
    assert events[1][0] == "budget_warn"


def test_maybe_lift_asks_in_lithuanian_for_lithuanian_goals():
    asked = {}

    def ask(question, options):
        asked["q"], asked["o"] = question, options
        return "taip"

    usd, eur = _maybe_lift_budget(
        assessment={"complexity": "hard", "domain": "marketing", "language": "lt"},
        estimate_eur=7.25,
        budget_eur=1.0,
        usd_cap=1.09,
        rate=0.92,
        ask_user=ask,
        fire=lambda t, **p: None,
    )
    assert eur == 10.0
    assert "Pakelti" in asked["q"] and asked["o"][0].startswith("taip")
