"""Combined and incremental reach of a media plan, with honest bounds.

Channel reaches must not be added up as if nobody saw two channels. Given the
audience, each channel's reach and whatever pairwise overlaps are known,
``reach_overlap`` returns the combined reach as a lower and an upper bound, a
point estimate only under a stated model (independent duplication, or the
overlaps the planner supplied), the incremental reach of each channel in a
greedy order, and the single overlap question that would narrow the bounds
most. Every function is pure, works in people, and names what is missing.

Bounds used (all exact for any family of sets, so they hold whatever the real
duplication is):

* lower: the largest single channel, and for every group of channels whose
  pairwise overlaps are all known the Bonferroni bound ``sum(reach) − sum(shared)``;
* upper: the audience, and Hunter's bound ``sum(reach) − weight of the maximum
  spanning forest of known overlaps`` (for two channels that is exact).

Optional per-channel fields extend the same arithmetic without changing the
plain call: ``source`` (paid / owned / earned) with a plan-level
``required_reach`` gives the paid-owned-earned split and whether unpaid reach
can carry the target; ``role`` (brand / activation) gives the budget split
against the 60/40 comparison point; ``addressable_pct`` ranks channels by cost
per thousand people who can actually buy; ``category_buyers`` and
``existing_customer_share`` show reach against the category rather than the
follower base; ``frequency_distribution`` with ``cap`` sizes a frequency-cap
scenario; ``measure`` refuses to add views to people.
"""

from __future__ import annotations

import json
import math
from itertools import combinations
from typing import Any

from rocky.tools import ToolError

ASSUMPTIONS = ("independent", "bounds", "given")
REACH_UNITS = ("people", "pct")
SOURCES = ("paid", "owned", "earned")
ROLES = ("brand", "activation", "both")
METRICS = ("people", "views", "impressions", "gross_contacts")
PERIODS = ("day", "week", "month", "campaign")
# Above this many channels the Bonferroni search over channel groups is limited
# to pairs; 2^12 subsets is cheap, 2^20 is not.
MAX_SUBSET_CHANNELS = 12
_SPACES = (" ", " ", " ", " ", "'")
# A comparison point, not a target: the planner adjusts it for the category and says why.
ROLE_REFERENCE = {
    "brand_pct": 60,
    "activation_pct": 40,
    "source": "Binet & Field, Effectiveness in Context (IPA, 2018), pp. 9–10 — a cross-category average to compare against, not a recommendation",
}
# Audience names that point at a follower or customer base rather than the people who could buy.
_CUSTOMER_WORDS = ("customer", "klient", "follower", "sekėj", "sekej", "prenumerator", "subscriber", "newsletter", "naujienlaišk")
_MEASURE_FIELDS = ("metric", "base", "period", "devices")


def _parse(raw: Any, label: str, *, minimum: float | None = None) -> float:
    """Lenient number: accepts 12 400, 1,5, 350k, 12 400 €, 35 %."""
    text = str(raw)
    for space in _SPACES:
        text = text.replace(space, "")
    text = text.replace("€", "").replace("EUR", "").replace("%", "").replace(",", ".").strip()
    factor = 1.0
    if text and text[-1] in "kKmM":
        factor = 1_000.0 if text[-1] in "kK" else 1_000_000.0
        text = text[:-1]
    try:
        value = float(text) * factor
    except ValueError as exc:
        raise ToolError(f"{label} must be a number, got {raw!r}") from exc
    if math.isnan(value) or math.isinf(value):
        raise ToolError(f"{label} must be a finite number")
    if minimum is not None and value < minimum:
        raise ToolError(f"{label} must be at least {minimum:g}")
    return value


def _number(args: dict, key: str, *, required: bool = False, minimum: float | None = None, label: str | None = None) -> float | None:
    raw = args.get(key)
    if raw is None or raw == "":
        if required:
            raise ToolError(f"{label or key} is required")
        return None
    return _parse(raw, label or key, minimum=minimum)


def _enum(item: dict, key: str, allowed: tuple[str, ...], label: str) -> str | None:
    raw = item.get(key)
    if raw is None or raw == "":
        return None
    value = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
    if value not in allowed:
        raise ToolError(f"{label} must be one of {', '.join(allowed)}, got {raw!r}")
    return value


def _people(value: float) -> int:
    return int(round(value))


def _pct_of(value: float, audience: float) -> float:
    return round(value / audience * 100, 1)


def _fmt(value: float) -> str:
    return f"{_people(value):,}".replace(",", " ")


def _pair_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def _pair_label(name_a: str, name_b: str) -> str:
    return f"overlap {name_a}×{name_b}"


class _Plan:
    """Parsed inputs plus the set arithmetic shared by the combined and incremental views."""

    def __init__(self, audience: float, reaches: list[float], known: dict[tuple[int, int], float]):
        self.audience = audience
        self.reaches = reaches
        self.known = known
        # Channels with at least one known overlap: only they can sit in a fully known group.
        self.linked = {i for pair in known for i in pair}
        self._lower: dict[frozenset[int], float] = {}
        self._upper: dict[frozenset[int], float] = {}

    def all_known(self, ids: list[int]) -> bool:
        return all(_pair_key(a, b) in self.known for a, b in combinations(ids, 2))

    def lower(self, ids: list[int]) -> float:
        if not ids:
            return 0.0
        key = frozenset(ids)
        if key in self._lower:
            return self._lower[key]
        best = max(self.reaches[i] for i in ids)
        # Bonferroni: for a group whose pairwise overlaps are all known, the union is at
        # least sum(reach) − sum(shared). Triple and higher overlaps only push the true
        # union up from there, which is why this is a lower bound.
        linked = [i for i in ids if i in self.linked]
        max_size = len(linked) if len(linked) <= MAX_SUBSET_CHANNELS else 2
        for size in range(2, max_size + 1):
            for group in combinations(linked, size):
                pairs = [_pair_key(a, b) for a, b in combinations(group, 2)]
                if all(p in self.known for p in pairs):
                    best = max(best, sum(self.reaches[i] for i in group) - sum(self.known[p] for p in pairs))
        self._lower[key] = min(best, self.audience)
        return self._lower[key]

    def upper(self, ids: list[int]) -> float:
        if not ids:
            return 0.0
        key = frozenset(ids)
        if key in self._upper:
            return self._upper[key]
        total = sum(self.reaches[i] for i in ids)
        # Hunter's bound: subtract the heaviest spanning forest of known overlaps
        # (Kruskal). Subtracting every known pair would not be an upper bound once
        # three channels share the same people.
        parent = {i: i for i in ids}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        edges = sorted(((s, p) for p, s in self.known.items() if p[0] in parent and p[1] in parent), reverse=True)
        forest = 0.0
        for shared, (a, b) in edges:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
                forest += shared
        self._upper[key] = max(min(self.audience, total - forest), self.lower(ids))
        return self._upper[key]

    def point(self, ids: list[int], model: str) -> float | None:
        if not ids:
            return 0.0
        lo, hi = self.lower(ids), self.upper(ids)
        if hi - lo < 0.5:
            return lo
        if model == "independent":
            survive = 1.0
            for i in ids:
                survive *= 1 - self.reaches[i] / self.audience
            raw = self.audience * (1 - survive)
        elif model == "given" and self.all_known(ids):
            raw = sum(self.reaches[i] for i in ids) - sum(self.known[_pair_key(a, b)] for a, b in combinations(ids, 2))
        else:
            return None
        return min(max(raw, lo), hi)

    def incremental(self, chosen: list[int], c: int, model: str) -> tuple[float, float, float | None]:
        """Bounds and model point for the new people channel ``c`` adds to ``chosen``."""
        r = self.reaches[c]
        if not chosen:
            return r, r, r
        lo_s, hi_s = self.lower(chosen), self.upper(chosen)
        lo_sc, hi_sc = self.lower(chosen + [c]), self.upper(chosen + [c])
        known_with = [self.known[_pair_key(c, s)] for s in chosen if _pair_key(c, s) in self.known]
        # c's overlap with the union of the chosen channels is at least its largest known
        # overlap with any one of them, and at most the sum of its overlaps with each
        # (unknown ones capped at the smaller channel) or the union itself.
        overlap_min = max(known_with) if known_with else 0.0
        overlap_max = min(
            hi_s,
            sum(self.known.get(_pair_key(c, s), min(r, self.reaches[s])) for s in chosen),
        )
        inc_upper = min(r - overlap_min, self.audience - lo_s, hi_sc - lo_s)
        inc_lower = max(0.0, r - overlap_max, lo_sc - hi_s)
        inc_upper = max(inc_upper, inc_lower)
        p_s, p_sc = self.point(chosen, model), self.point(chosen + [c], model)
        inc_point = None if p_s is None or p_sc is None else max(0.0, p_sc - p_s)
        return inc_lower, inc_upper, inc_point

    def scaled(self, factors: list[float], audience: float | None = None) -> "_Plan":
        """The same plan with every channel's reach scaled by its factor (0–1) and each known
        overlap scaled by the smaller of the two factors — exact when the scaled people are a
        uniform slice of each channel, approximate otherwise, which the caller must say.
        ``audience`` replaces the universe (e.g. category buyers instead of all readers)."""
        universe = self.audience if audience is None else audience
        reaches = [min(r * f, universe) for r, f in zip(self.reaches, factors)]
        known = {pair: min(shared * min(factors[pair[0]], factors[pair[1]]), reaches[pair[0]], reaches[pair[1]]) for pair, shared in self.known.items()}
        return _Plan(universe, reaches, known)


# --- parsing ---------------------------------------------------------------


def _parse_exposures(raw: Any, name: str) -> tuple[int, int | None]:
    text = str(raw).strip().replace("–", "-").replace("—", "-").replace(" ", "")
    try:
        if text.endswith("+"):
            return int(text[:-1]), None
        if "-" in text:
            a, b = text.split("-", 1)
            lo, hi = int(a), int(b)
            if hi < lo:
                raise ValueError
            return lo, hi
        value = int(text)
        return value, value
    except ValueError as exc:
        raise ToolError(f"exposures {raw!r} in {name}'s frequency_distribution must be a whole number, a range like 3-5, or 6+") from exc


def _parse_distribution(raw: Any, name: str) -> list[dict[str, Any]] | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, list) or not raw:
        raise ToolError(f"frequency_distribution of {name} must be a list of {{exposures, people}}")
    buckets = []
    for n, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or "exposures" not in item:
            raise ToolError(f"bucket {n} of {name}'s frequency_distribution must be an object with exposures and people")
        lo, hi = _parse_exposures(item.get("exposures"), name)
        people = _number(item, "people", required=True, minimum=0.0, label=f"people in bucket {item.get('exposures')!r} of {name}")
        buckets.append({"label": str(item.get("exposures")), "min": lo, "max": hi, "people": people})
    return buckets


def _parse_measure(raw: Any, name: str) -> dict[str, Any] | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict):
        raise ToolError(f"measure of {name} must be an object with metric, base, period, devices, source")
    measure: dict[str, Any] = {
        "metric": _enum(raw, "metric", METRICS, f"measure.metric of {name}"),
        "period": _enum(raw, "period", PERIODS, f"measure.period of {name}"),
    }
    for key in ("base", "devices", "source", "viewability"):
        value = str(raw.get(key) or "").strip()
        measure[key] = value or None
    return measure


def _parse_channels(args: dict, audience: float, unit: str, warnings: list[str]) -> list[dict[str, Any]]:
    raw = args.get("channels")
    if not isinstance(raw, list) or not raw:
        raise ToolError("channels must be a list with at least one channel")
    channels: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ToolError(f"channel {n} must be an object with name and reach")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ToolError(f"channel {n} needs a name")
        if name.lower() in seen:
            raise ToolError(f"channel name {name!r} appears twice")
        seen.add(name.lower())
        reach = _number(item, "reach", required=True, minimum=0.0, label=f"reach of {name}")
        measure = _parse_measure(item.get("measure"), name)
        excluded = None
        if measure is not None and measure["metric"] is not None and measure["metric"] != "people":
            where = f" ({measure['base']})" if measure["base"] else ""
            excluded = (
                f"measured in {measure['metric'].replace('_', ' ')}{where}, not people: cannot be combined with people-based reach; "
                f"supply unique people on the same base and period"
            )
        if unit == "pct" and excluded is None:
            reach = reach / 100 * audience
        if excluded is None and reach > audience:
            warnings.append(f"{name} reach {_fmt(reach)} is above the audience of {_fmt(audience)}: clipped to the audience.")
            reach = audience
        cost = _number(item, "cost", minimum=0.0, label=f"cost of {name}")
        frequency = _number(item, "frequency", minimum=0.0, label=f"frequency of {name}")
        impressions = _number(item, "impressions", minimum=0.0, label=f"impressions of {name}")
        if impressions is not None and frequency is None and reach > 0:
            frequency = round(impressions / reach, 2)
        elif impressions is None and frequency is not None:
            impressions = reach * frequency
        addressable = _number(item, "addressable_pct", minimum=0.0, label=f"addressable_pct of {name}")
        if addressable is not None and addressable > 100:
            raise ToolError(f"addressable_pct of {name} must be between 0 and 100 (percent of its reach who can buy), got {addressable:g}")
        customer_share = _number(item, "existing_customer_share", minimum=0.0, label=f"existing_customer_share of {name}")
        if customer_share is not None and customer_share > 1:
            raise ToolError(f"existing_customer_share of {name} must be between 0 and 1 (a share, not a percent), got {customer_share:g}")
        cap = _number(item, "cap", minimum=1.0, label=f"cap of {name}")
        if cap is not None and cap != int(cap):
            raise ToolError(f"cap of {name} must be a whole number of exposures")
        factor = _number(item, "cpm_unexposed_factor", minimum=0.0, label=f"cpm_unexposed_factor of {name}")
        if factor is not None and factor <= 0:
            raise ToolError(f"cpm_unexposed_factor of {name} must be above 0 (ratio of the CPM for new people to the current CPM)")
        role_brand_pct = _number(item, "role_brand_pct", minimum=0.0, label=f"role_brand_pct of {name}")
        if role_brand_pct is not None and role_brand_pct > 100:
            raise ToolError(f"role_brand_pct of {name} must be between 0 and 100")
        channels.append(
            {
                "name": name,
                "reach": reach,
                "cost": cost,
                "frequency": frequency,
                "impressions": impressions,
                "source": _enum(item, "source", SOURCES, f"source of {name}"),
                "role": _enum(item, "role", ROLES, f"role of {name}"),
                "role_brand_pct": role_brand_pct,
                "addressable_pct": addressable,
                "addressable_source": str(item.get("addressable_source") or "").strip() or None,
                "existing_customer_share": customer_share,
                "frequency_distribution": _parse_distribution(item.get("frequency_distribution"), name),
                "cap": None if cap is None else int(cap),
                "cpm_unexposed_factor": factor,
                "measure": measure,
                "excluded_reason": excluded,
            }
        )
    return channels


def _parse_overlaps(
    raw: Any, channels: list[dict[str, Any]], audience: float, unit: str, excluded_names: set[str], warnings: list[str]
) -> dict[tuple[int, int], float]:
    if raw in (None, ""):
        return {}
    if not isinstance(raw, list):
        raise ToolError("overlaps must be a list of {a, b, shared}")
    index = {c["name"].lower(): i for i, c in enumerate(channels)}
    known: dict[tuple[int, int], float] = {}
    for n, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ToolError(f"overlap {n} must be an object with a, b and shared")
        names = []
        for side in ("a", "b"):
            name = str(item.get(side) or "").strip()
            if not name:
                raise ToolError(f"overlap {n} needs both a and b")
            if name.lower() in excluded_names:
                break
            if name.lower() not in index:
                raise ToolError(f"overlap names an unknown channel {name!r}")
            names.append(name)
        if len(names) < 2:
            warnings.append(f"overlap {n} names a channel excluded as not comparable and is ignored.")
            continue
        ia, ib = index[names[0].lower()], index[names[1].lower()]
        if ia == ib:
            raise ToolError(f"overlap {n} must name two different channels")
        shared = _number(item, "shared", required=True, minimum=0.0, label=f"shared of {names[0]}×{names[1]}")
        if unit == "pct":
            shared = shared / 100 * audience
        smaller = min(channels[ia], channels[ib], key=lambda c: c["reach"])
        if shared > smaller["reach"] + 1e-9:
            raise ToolError(
                f"shared {_fmt(shared)} for {names[0]}×{names[1]} is larger than {smaller['name']}'s reach {_fmt(smaller['reach'])}"
            )
        key = _pair_key(ia, ib)
        if key in known:
            raise ToolError(f"overlap {names[0]}×{names[1]} is given twice")
        known[key] = shared
    return known


# --- the tool --------------------------------------------------------------


def reach_overlap(args: dict) -> dict[str, Any]:
    """Combined and incremental reach across channels, as bounds plus a modelled point.

    Inputs: ``audience`` (people), ``channels`` [{name, reach, cost?, frequency?}],
    ``reach_unit`` ``people`` (default) or ``pct`` of the audience, ``overlaps``
    [{a, b, shared}] for the pairs whose duplication is known, ``assumption``
    ``independent`` | ``bounds`` | ``given`` (default: ``given`` when every pair
    is known, otherwise ``bounds``), optional ``budget`` and ``audience_name``.
    Pairs not listed under ``overlaps`` are reported under ``missing`` and the
    result says which one to ask about first.

    Optional extensions, each present in the output only when its inputs are:
    per-channel ``source`` + ``required_reach`` (paid/owned/earned), ``role``
    (brand/activation split), ``addressable_pct`` (+ ``addressable_source``),
    ``category_buyers`` (+ ``category_source``) and ``existing_customer_share``,
    ``frequency_distribution`` + ``cap`` (+ ``cpm_unexposed_factor``),
    ``impressions`` and ``measure`` {metric, base, period, devices, source}.
    """
    audience = _number(args, "audience", required=True, minimum=0.0)
    if audience <= 0:
        raise ToolError("audience must be greater than zero")
    audience_name = str(args.get("audience_name") or "").strip()
    unit = str(args.get("reach_unit") or "people").strip().lower()
    if unit not in REACH_UNITS:
        raise ToolError("reach_unit must be people or pct")
    requested = str(args.get("assumption") or "").strip().lower() or None
    if requested is not None and requested not in ASSUMPTIONS:
        raise ToolError("assumption must be independent, bounds or given")
    budget = _number(args, "budget", minimum=0.0)
    required_reach = _number(args, "required_reach", minimum=0.0)
    category_buyers = _number(args, "category_buyers", minimum=0.0)
    if category_buyers is not None and category_buyers <= 0:
        raise ToolError("category_buyers must be greater than zero")

    warnings: list[str] = []
    all_channels = _parse_channels(args, audience, unit, warnings)
    excluded = [c for c in all_channels if c["excluded_reason"]]
    channels = [c for c in all_channels if not c["excluded_reason"]]
    if not channels:
        raise ToolError(
            "no channel is measured in people: " + "; ".join(f"{c['name']} is {c['excluded_reason']}" for c in excluded)
        )
    excluded_names = {c["name"].lower() for c in excluded}
    known = _parse_overlaps(args.get("overlaps"), channels, audience, unit, excluded_names, warnings)
    plan = _Plan(audience, [c["reach"] for c in channels], known)
    ids = list(range(len(channels)))
    unknown_pairs = [(a, b) for a, b in combinations(ids, 2) if _pair_key(a, b) not in known]
    model = requested or ("given" if not unknown_pairs else "bounds")

    total_reach = sum(plan.reaches)
    lower, upper = plan.lower(ids), plan.upper(ids)
    point = plan.point(ids, model)
    if model == "independent":
        point_assumption = "independent: each channel reaches people at random, duplication = product of reach shares"
    elif point is not None and upper - lower < 0.5:
        point_assumption = "bounds coincide: the overlaps given fix the combined reach"
    elif model == "given" and not unknown_pairs:
        point_assumption = (
            "pairwise inclusion–exclusion from the overlaps given; triple overlaps are ignored, so the point is a lower bound for the union"
            if len(channels) > 2
            else "inclusion–exclusion from the overlap given"
        )
    else:
        point_assumption = None

    out_channels = []
    for c in all_channels:
        if c["excluded_reason"]:
            out_channels.append(
                {
                    "name": c["name"],
                    "reach": None,
                    "raw_figure": _people(c["reach"]),
                    "measure": c["measure"],
                    "cost": None if c["cost"] is None else round(c["cost"], 2),
                    "excluded_reason": c["excluded_reason"],
                }
            )
            continue
        row: dict[str, Any] = {
            "name": c["name"],
            "reach": _people(c["reach"]),
            "reach_pct": _pct_of(c["reach"], audience),
            "cost": None if c["cost"] is None else round(c["cost"], 2),
            "frequency": c["frequency"],
        }
        if c["cost"] is not None and c["reach"] > 0:
            row["cost_per_reach"] = round(c["cost"] / c["reach"], 4)
            row["cpm_reach"] = round(c["cost"] / c["reach"] * 1000, 2)
        if c["impressions"] is not None:
            row["impressions"] = _people(c["impressions"])
        if c["source"] is not None:
            row["source"] = c["source"]
        if c["role"] is not None:
            row["role"] = c["role"]
        if c["measure"] is not None:
            row["measure"] = c["measure"]
        if c["addressable_pct"] is not None:
            row["addressable_pct"] = c["addressable_pct"]
            row["addressable_reach"] = _people(c["reach"] * c["addressable_pct"] / 100)
            row["addressable_source"] = c["addressable_source"]
            if c["cost"] is not None and c["reach"] * c["addressable_pct"] > 0:
                row["cpm_addressable"] = round(c["cost"] / (c["reach"] * c["addressable_pct"] / 100) * 1000, 2)
        if c["existing_customer_share"] is not None:
            row["existing_customer_share"] = c["existing_customer_share"]
            row["future_buyer_reach"] = _people(c["reach"] * (1 - c["existing_customer_share"]))
        out_channels.append(row)

    missing = [_pair_label(channels[a]["name"], channels[b]["name"]) for a, b in unknown_pairs]
    if len(channels) < 2:
        warnings.append("One channel: there is no overlap to ask about; the combined reach is that channel's reach.")
    if total_reach > audience + 0.5:
        warnings.append(
            f"Channel reaches add up to {_fmt(total_reach)}, above the audience of {_fmt(audience)}: some people are counted "
            f"in two channels, which is expected; the combined reach cannot exceed the audience."
        )

    inputs: dict[str, Any] = {
        "audience": _people(audience),
        "audience_name": audience_name or None,
        "reach_unit": unit,
        "assumption": requested,
        "assumption_used": model,
        "budget": None if budget is None else round(budget, 2),
        "known_overlaps": len(known),
        "unknown_overlaps": len(unknown_pairs),
    }
    if required_reach is not None:
        inputs["required_reach"] = _people(required_reach)
    if category_buyers is not None:
        inputs["category_buyers"] = _people(category_buyers)
    out: dict[str, Any] = {
        "inputs": inputs,
        "channels": out_channels,
        "sum_of_reaches": _people(total_reach),
        "combined": {
            "lower": _people(lower),
            "upper": _people(upper),
            "point": None if point is None else _people(point),
            "point_assumption": point_assumption,
        },
        "combined_pct": {
            "lower": _pct_of(lower, audience),
            "upper": _pct_of(upper, audience),
            "point": None if point is None else _pct_of(point, audience),
        },
        "bounds_width_pct": _pct_of(upper - lower, audience),
        "missing": missing,
        "warnings": warnings,
    }
    if point is None:
        out["combined"]["note"] = "overlap unknown — no point estimate; use the bounds or ask for the overlap named in narrows_most."
    if model == "independent" and not known and len(channels) > 1:
        out["combined"]["note"] = "independent duplication is an assumption, not a measurement; the true figure lies anywhere in the bounds."

    out["narrows_most"] = _narrows_most(channels, unknown_pairs, audience)
    out["incremental"] = _incremental(plan, channels, model)
    _costs(out, channels, plan, budget, lower, upper, point, audience, warnings)
    _frequency(out, channels, lower, upper, audience)
    out["summary"] = _summary(out, channels, audience, audience_name, model)
    extra_sentences: list[str] = []
    _sources(out, channels, plan, model, required_reach, extra_sentences)
    _roles(out, channels)
    _addressable(out, channels, plan, model, category_buyers, extra_sentences)
    _category(out, channels, plan, model, audience, audience_name, category_buyers, args, lower, upper, point, extra_sentences)
    _caps(out, channels, audience)
    _comparability(out, all_channels, excluded, extra_sentences)
    if extra_sentences:
        out["summary"] = out["summary"] + " " + " ".join(extra_sentences)
    return out


def _narrows_most(channels: list[dict[str, Any]], unknown_pairs: list[tuple[int, int]], audience: float) -> dict[str, Any] | None:
    if not unknown_pairs:
        return None
    a, b = max(unknown_pairs, key=lambda p: min(channels[p[0]]["reach"], channels[p[1]]["reach"]))
    # The bigger channel is the one to ask about: "what share of its users also use the other".
    big, small = (a, b) if channels[a]["reach"] >= channels[b]["reach"] else (b, a)
    possible = min(channels[a]["reach"], channels[b]["reach"])
    return {
        "a": channels[big]["name"],
        "b": channels[small]["name"],
        "pair": _pair_label(channels[big]["name"], channels[small]["name"]),
        "possible_shared": _people(possible),
        "possible_shared_pct": _pct_of(possible, audience),
        "question": f"Ask for the share of {channels[big]['name']} readers who also use {channels[small]['name']}",
    }


def _incremental(plan: _Plan, channels: list[dict[str, Any]], model: str) -> dict[str, Any]:
    costed = all(c["cost"] is not None and c["cost"] > 0 for c in channels)
    chosen: list[int] = []
    remaining = list(range(len(channels)))
    steps = []
    while remaining:
        scored = []
        for c in remaining:
            lo, hi, pt = plan.incremental(chosen, c, model)
            score = pt if pt is not None else (lo + hi) / 2
            if costed:
                score = score / channels[c]["cost"]
            scored.append((score, -channels[c]["reach"], c, lo, hi, pt))
        scored.sort(reverse=True)
        score, _, c, lo, hi, pt = scored[0]
        chosen.append(c)
        remaining.remove(c)
        cum_point = plan.point(chosen, model)
        step: dict[str, Any] = {
            "step": len(chosen),
            "name": channels[c]["name"],
            "incremental_lower": _people(lo),
            "incremental_upper": _people(hi),
            "incremental_point": None if pt is None else _people(pt),
            "cumulative_lower": _people(plan.lower(chosen)),
            "cumulative_upper": _people(plan.upper(chosen)),
            "cumulative_point": None if cum_point is None else _people(cum_point),
        }
        if costed:
            step["cost"] = round(channels[c]["cost"], 2)
            if pt is not None and pt > 0:
                step["cpm_incremental"] = round(channels[c]["cost"] / pt * 1000, 2)
            elif hi > 0:
                step["cpm_incremental_at_best"] = round(channels[c]["cost"] / hi * 1000, 2)
        steps.append(step)
    basis = "incremental reach per cost" if costed else "incremental reach"
    ranked_by = "the model point" if model != "bounds" else "the midpoint of the incremental bounds"
    return {
        "order": [s["name"] for s in steps],
        "steps": steps,
        "note": (
            f"Greedy order by {basis}, ranked on {ranked_by} under the {model} assumption; "
            "a different assumption or a measured overlap can change the order."
        ),
    }


def _costs(
    out: dict[str, Any],
    channels: list[dict[str, Any]],
    plan: _Plan,
    budget: float | None,
    lower: float,
    upper: float,
    point: float | None,
    audience: float,
    warnings: list[str],
) -> None:
    costs = [c["cost"] for c in channels if c["cost"] is not None]
    if not costs:
        if budget is not None:
            out["missing"].append("cost per channel")
        return
    total = sum(costs)
    cost_block: dict[str, Any] = {"total_cost": round(total, 2), "channels_costed": len(costs), "channels": len(channels)}
    # Owned and earned channels carry no media cost by nature: they count as costed at 0.
    uncosted = [c["name"] for c in channels if c["cost"] is None and c["source"] not in ("owned", "earned")]
    if not uncosted:
        if upper > 0:
            cost_block["cpm_combined_lower"] = round(total / upper * 1000, 2)
        if lower > 0:
            cost_block["cpm_combined_upper"] = round(total / lower * 1000, 2)
        if point:
            cost_block["cpm_combined_point"] = round(total / point * 1000, 2)
        if total_reach := sum(plan.reaches):
            cost_block["cpm_naive_sum"] = round(total / total_reach * 1000, 2)
            cost_block["note"] = "cpm_naive_sum divides by the added-up reaches and understates the real cost per person reached."
    else:
        out["missing"].append("cost for " + ", ".join(uncosted))
    if budget is not None:
        cost_block["budget"] = round(budget, 2)
        cost_block["budget_left"] = round(budget - total, 2)
        if total > budget + 1e-9:
            warnings.append(f"Channel costs {total:,.2f} exceed the budget {budget:,.2f}.")
    out["cost"] = cost_block


def _frequency(out: dict[str, Any], channels: list[dict[str, Any]], lower: float, upper: float, audience: float) -> None:
    if not all(c["impressions"] is not None for c in channels):
        return
    impressions = sum(c["impressions"] for c in channels)
    block: dict[str, Any] = {"impressions": _people(impressions), "grp": round(impressions / audience * 100, 1)}
    if lower > 0:
        block["avg_frequency_lower"] = round(impressions / upper, 2)
        block["avg_frequency_upper"] = round(impressions / lower, 2)
    out["exposure"] = block


def _summary(out: dict[str, Any], channels: list[dict[str, Any]], audience: float, audience_name: str, model: str) -> str:
    pct = out["combined_pct"]
    who = f"{_fmt(audience)}" + (f" {audience_name}" if audience_name else "")
    if pct["lower"] == pct["upper"]:
        head = f"Combined reach {pct['lower']:g} % of {who} ({_fmt(out['combined']['lower'])} people)."
    else:
        head = f"Combined reach {pct['lower']:g}–{pct['upper']:g} % of {who}"
        if pct["point"] is not None:
            label = "independent model" if model == "independent" else "pairwise overlaps, a lower bound"
            head += f" ({label}: {pct['point']:g} %)"
        head += "."
    narrows = out.get("narrows_most")
    if narrows:
        head += (
            f" Unknown overlap between {narrows['a']} and {narrows['b']} is the widest gap: "
            f"ask what share of {narrows['a']} users also see {narrows['b']}."
        )
    elif len(channels) == 1:
        head += " One channel, so there is no overlap to resolve."
    return head


# --- extensions ------------------------------------------------------------


def _bounds_block(plan: _Plan, ids: list[int], model: str) -> dict[str, Any]:
    lo, hi = plan.lower(ids), plan.upper(ids)
    pt = plan.point(ids, model)
    return {"reach_lower": _people(lo), "reach_upper": _people(hi), "reach_point": None if pt is None else _people(pt)}


def _sources(
    out: dict[str, Any],
    channels: list[dict[str, Any]],
    plan: _Plan,
    model: str,
    required_reach: float | None,
    sentences: list[str],
) -> None:
    """Paid / owned / earned split (#401): bounds per source group, earned share of impressions,
    and whether owned + earned could carry the required reach on their own."""
    tagged = [i for i, c in enumerate(channels) if c["source"] is not None]
    untagged = [c["name"] for c in channels if c["source"] is None]
    if not tagged and required_reach is None:
        return
    if not tagged:
        out["missing"].append("source (paid, owned or earned) per channel — required_reach cannot be checked against unpaid reach without it")
        out["unpaid_sustains_required"] = "unknown"
        return
    for name in untagged:
        out["missing"].append(f"source for {name}")
    by_source: dict[str, Any] = {}
    impressions_total = 0.0
    impressions_by: dict[str, float] = {}
    one_exposure: list[str] = []
    for source in SOURCES:
        ids = [i for i in tagged if channels[i]["source"] == source]
        if not ids:
            continue
        block = _bounds_block(plan, ids, model)
        block["channels"] = [channels[i]["name"] for i in ids]
        costs = [channels[i]["cost"] for i in ids if channels[i]["cost"] is not None]
        if costs:
            block["cost"] = round(sum(costs), 2)
            if block["reach_upper"] > 0:
                block["cpm_reached_lower"] = round(sum(costs) / plan.upper(ids) * 1000, 2)
            if block["reach_lower"] > 0:
                block["cpm_reached_upper"] = round(sum(costs) / plan.lower(ids) * 1000, 2)
        elif source == "paid":
            out["missing"].append("cost for paid channels")
        if source == "earned":
            block["note"] = "earned reach counts only what was measured (shares, views from shares, mentions); the true figure is at least this."
        group_impressions = 0.0
        for i in ids:
            c = channels[i]
            if c["impressions"] is not None:
                group_impressions += c["impressions"]
            else:
                group_impressions += c["reach"]
                one_exposure.append(c["name"])
        impressions_by[source] = group_impressions
        impressions_total += group_impressions
        by_source[source] = block
    out["by_source"] = by_source
    if "earned" in by_source and impressions_total > 0:
        out["earned_share_pct"] = round(impressions_by["earned"] / impressions_total * 100, 1)
        if one_exposure:
            out["earned_share_note"] = (
                "impressions = reach × frequency; " + ", ".join(one_exposure) + " had no frequency and count one exposure per person, so the share is approximate."
            )
    unpaid_ids = [i for i in tagged if channels[i]["source"] != "paid"]
    if unpaid_ids:
        unpaid_lo, unpaid_hi = plan.lower(unpaid_ids), plan.upper(unpaid_ids)
        out["unpaid_reach_lower"] = _people(unpaid_lo)
        out["unpaid_reach_upper"] = _people(unpaid_hi)
    if required_reach is None:
        out["unpaid_sustains_required"] = "unknown"
        out["unpaid_note"] = "give required_reach (people the next flight must reach) to check whether owned + earned could carry it."
        out["missing"].append("required_reach")
        return
    if not unpaid_ids:
        out["unpaid_sustains_required"] = "unknown"
        out["unpaid_note"] = "no owned or earned channel was given, so unpaid reach is unknown, not zero."
        return
    covers = round(unpaid_hi / required_reach * 100, 1) if required_reach > 0 else None
    if unpaid_hi < required_reach - 0.5:
        verdict = "no"
        paid_needed = required_reach - unpaid_hi
        note = (
            f"Owned and earned reach at most {_fmt(unpaid_hi)} of the {_fmt(required_reach)} required; unpaid distribution covers at most "
            f"{covers:g} %, so paid must deliver at least {_fmt(paid_needed)} new people."
        )
        out["paid_must_deliver_at_least"] = _people(paid_needed)
    elif unpaid_lo >= required_reach - 0.5:
        verdict = "yes"
        note = f"Owned and earned reach at least {_fmt(unpaid_lo)}, which already meets the {_fmt(required_reach)} required."
    else:
        verdict = "unknown"
        note = (
            f"Owned and earned reach {_fmt(unpaid_lo)}–{_fmt(unpaid_hi)} against {_fmt(required_reach)} required: the overlap between "
            f"them decides it; measure it before cutting paid."
        )
    out["unpaid_sustains_required"] = verdict
    out["unpaid_note"] = note
    sentences.append(note)


def _roles(out: dict[str, Any], channels: list[dict[str, Any]]) -> None:
    """Brand / activation split of channel costs against the 60/40 comparison point (#399)."""
    if not any(c["role"] is not None for c in channels):
        return
    brand = activation = 0.0
    unassigned = [c["name"] for c in channels if c["role"] is None and c["cost"] is not None]
    uncosted = [c["name"] for c in channels if c["role"] is not None and c["cost"] is None]
    for c in channels:
        if c["role"] is None or c["cost"] is None:
            continue
        if c["role"] == "brand":
            brand += c["cost"]
        elif c["role"] == "activation":
            activation += c["cost"]
        else:
            share = 50.0 if c["role_brand_pct"] is None else c["role_brand_pct"]
            brand += c["cost"] * share / 100
            activation += c["cost"] * (100 - share) / 100
    total = brand + activation
    split: dict[str, Any] = {"reference": dict(ROLE_REFERENCE), "unassigned": unassigned, "uncosted": uncosted, "partial": bool(unassigned or uncosted)}
    if total <= 0:
        split["note"] = "no channel has both a role and a cost, so the split cannot be computed."
        out["split"] = split
        out["missing"].append("cost for channels with a role")
        return
    brand_pct = round(brand / total * 100, 1)
    split.update(
        {
            "brand": round(brand, 2),
            "activation": round(activation, 2),
            "brand_pct": brand_pct,
            "activation_pct": round(100 - brand_pct, 1),
            "deviation_pts": round(brand_pct - ROLE_REFERENCE["brand_pct"], 1),
        }
    )
    gaps = []
    if unassigned:
        gaps.append("no role for " + ", ".join(unassigned))
        out["missing"].append("role for " + ", ".join(unassigned))
    if uncosted:
        gaps.append("no cost for " + ", ".join(uncosted))
    split["note"] = (
        f"Brand {brand_pct:g} % / activation {split['activation_pct']:g} % of the costs that carry a role, against the 60/40 comparison point "
        f"({split['deviation_pts']:+g} points on brand); adjust the comparison for the category and say why."
        + (f" Partial: {'; '.join(gaps)}." if gaps else "")
    )
    out["split"] = split


def _addressable(
    out: dict[str, Any], channels: list[dict[str, Any]], plan: _Plan, model: str, category_buyers: float | None, sentences: list[str]
) -> None:
    """Cost per thousand people who can actually buy (#415), ranked, with the combined bounds
    recomputed over addressable people when every channel carries the share. ``category_buyers``
    is the universe of addressable people, so the bounds cannot exceed it."""
    with_pct = [c for c in channels if c["addressable_pct"] is not None]
    if not with_pct:
        return
    missing = [c["name"] for c in channels if c["addressable_pct"] is None]
    rows = []
    for c in with_pct:
        addressable = c["reach"] * c["addressable_pct"] / 100
        row: dict[str, Any] = {"name": c["name"], "addressable_reach": _people(addressable), "addressable_pct": c["addressable_pct"]}
        if c["cost"] is not None and addressable > 0:
            row["cpm_addressable"] = round(c["cost"] / addressable * 1000, 2)
        rows.append(row)
    # Costed channels first by price per addressable thousand; uncosted after, biggest first.
    rows.sort(key=lambda r: (r.get("cpm_addressable") is None, r.get("cpm_addressable", 0.0), -r["addressable_reach"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    block: dict[str, Any] = {"basis": "buyers" if not missing else "readers", "ranking": rows, "missing": missing}
    if missing:
        out["missing"].append("addressable_pct for " + ", ".join(missing))
        block["note"] = "ranking of the whole plan is still by readers, not buyers, until every channel has addressable_pct."
    else:
        factors = [c["addressable_pct"] / 100 for c in channels]
        scaled = plan.scaled(factors, category_buyers)
        ids = list(range(len(channels)))
        block["combined"] = _bounds_block(scaled, ids, model)
        block["combined"]["note"] = (
            "bounds over addressable people; known overlaps were scaled by the smaller addressable share, an approximation that assumes "
            "buyers duplicate like readers."
        )
        if category_buyers is not None:
            block["combined"]["pct_of_category"] = {
                "lower": round(scaled.lower(ids) / category_buyers * 100, 1),
                "upper": round(scaled.upper(ids) / category_buyers * 100, 1),
            }
        else:
            block["combined"]["note"] += " Give category_buyers to cap the bounds at the number of people who can buy."
        costs = [c["cost"] for c in channels if c["cost"] is not None]
        if len(costs) == len(channels) and scaled.upper(ids) > 0:
            total = sum(costs)
            block["cpm_addressable_combined_lower"] = round(total / scaled.upper(ids) * 1000, 2)
            if scaled.lower(ids) > 0:
                block["cpm_addressable_combined_upper"] = round(total / scaled.lower(ids) * 1000, 2)
    costed = [r for r in rows if "cpm_addressable" in r]
    if costed:
        cheapest = costed[0]
        by_reader = min(
            (c for c in channels if c["cost"] is not None and c["reach"] > 0),
            key=lambda c: c["cost"] / c["reach"],
            default=None,
        )
        sentence = f"Cheapest per thousand addressable buyers: {cheapest['name']} ({cheapest['cpm_addressable']:,.2f})"
        if by_reader is not None and by_reader["name"] != cheapest["name"]:
            sentence += f"; {by_reader['name']} is cheapest per reader but not per buyer"
        sentences.append(sentence + ".")
    out["addressable"] = block


def _category(
    out: dict[str, Any],
    channels: list[dict[str, Any]],
    plan: _Plan,
    model: str,
    audience: float,
    audience_name: str,
    category_buyers: float | None,
    args: dict,
    lower: float,
    upper: float,
    point: float | None,
    sentences: list[str],
) -> None:
    """Reach against the category (#400): the share of all potential buyers reached, and the reach
    among people who are not already customers."""
    lowered = audience_name.lower()
    if any(word in lowered for word in _CUSTOMER_WORDS):
        out["warnings"].append(
            f"The audience {audience_name!r} looks like a customer or follower base: reach of people who already buy is not reach of the category; "
            f"give category_buyers for the real denominator."
        )
    if category_buyers is not None:
        source = str(args.get("category_source") or "").strip() or None
        block: dict[str, Any] = {
            "denominator": _people(category_buyers),
            "source": source,
            "lower_pct": round(lower / category_buyers * 100, 1),
            "upper_pct": round(upper / category_buyers * 100, 1),
            "point_pct": None if point is None else round(point / category_buyers * 100, 1),
        }
        if category_buyers < audience - 0.5:
            out["warnings"].append(
                f"category_buyers {_fmt(category_buyers)} is below the audience {_fmt(audience)}: the audience includes people who cannot buy, "
                f"so category_reach is the figure to quote."
            )
        if source is None:
            out["missing"].append("category_source (where the category-buyer count comes from)")
        out["category_reach"] = block
        sentences.append(
            f"That is {block['lower_pct']:g}–{block['upper_pct']:g} % of {_fmt(category_buyers)} category buyers."
            if block["lower_pct"] != block["upper_pct"]
            else f"That is {block['lower_pct']:g} % of {_fmt(category_buyers)} category buyers."
        )
    with_share = [c for c in channels if c["existing_customer_share"] is not None]
    if not with_share:
        return
    without = [c["name"] for c in channels if c["existing_customer_share"] is None]
    ids = list(range(len(channels)))
    # Channels without a share: all customers in the lower bound, no customers in the upper.
    low_factors = [0.0 if c["existing_customer_share"] is None else 1 - c["existing_customer_share"] for c in channels]
    high_factors = [1.0 if c["existing_customer_share"] is None else 1 - c["existing_customer_share"] for c in channels]
    fb_lower = plan.scaled(low_factors).lower(ids)
    fb_upper = plan.scaled(high_factors).upper(ids)
    block = {
        "lower": _people(fb_lower),
        "upper": _people(fb_upper),
        "pct_of_audience": {"lower": _pct_of(fb_lower, audience), "upper": _pct_of(fb_upper, audience)},
        "channels_mostly_customers": [c["name"] for c in with_share if c["existing_customer_share"] >= 0.5],
        "note": "channel reach × (1 − existing_customer_share), then the same bounds; known overlaps scaled by the smaller factor (approximate).",
    }
    if category_buyers is not None:
        block["pct_of_category"] = {
            "lower": round(fb_lower / category_buyers * 100, 1),
            "upper": round(fb_upper / category_buyers * 100, 1),
        }
    if without:
        block["note"] += f" No share for {', '.join(without)}: counted as all customers in the lower bound and no customers in the upper."
        out["missing"].append("existing_customer_share for " + ", ".join(without))
    out["future_buyer_reach"] = block
    if block["channels_mostly_customers"]:
        names = ", ".join(block["channels_mostly_customers"])
        mostly_ids = [i for i, c in enumerate(channels) if c["existing_customer_share"] is not None and c["existing_customer_share"] >= 0.5]
        mostly_hi = plan.upper(mostly_ids)
        tail = f" ({round(mostly_hi / category_buyers * 100, 1):g} % of category buyers at most)" if category_buyers else ""
        sentences.append(f"{names} reach mostly current customers{tail}.")


def _caps(out: dict[str, Any], channels: list[dict[str, Any]], audience: float) -> None:
    """Frequency-cap scenario (#402) per channel that has a distribution and a cap."""
    scenarios: dict[str, Any] = {}
    for c in channels:
        dist, cap, name = c["frequency_distribution"], c["cap"], c["name"]
        if dist is None and cap is None:
            continue
        if dist is None:
            out["missing"].append(
                f"frequency_distribution for {name} (an average frequency alone cannot say how many impressions a cap at {cap} releases)"
            )
            continue
        if cap is None:
            out["missing"].append(f"cap for {name}")
            continue
        released = estimated = dist_people = 0.0
        for bucket in dist:
            est = bucket["min"] if bucket["max"] is None else (bucket["min"] + bucket["max"]) / 2
            estimated += est * bucket["people"]
            if bucket["min"] > 0:
                dist_people += bucket["people"]
            released += bucket["people"] * max(0.0, est - cap)
        impressions = c["impressions"] if c["impressions"] is not None else estimated
        if abs(dist_people - c["reach"]) > 0.02 * max(c["reach"], 1.0):
            out["warnings"].append(
                f"frequency_distribution of {name} sums to {_fmt(dist_people)} people but reach is {_fmt(c['reach'])}: check the breakdown."
            )
        block: dict[str, Any] = {
            "cap": cap,
            "released_impressions": _people(released),
            "released_share_pct": round(released / impressions * 100, 1) if impressions > 0 else None,
            "impressions_basis": "impressions given (or reach × frequency)" if c["impressions"] is not None else "estimated from the distribution",
        }
        if c["cost"] is not None and impressions > 0:
            block["released_budget"] = round(c["cost"] * released / impressions, 2)
        unexposed = max(0.0, audience - c["reach"])
        block["unexposed_pool"] = _people(unexposed)
        if unexposed <= 0:
            block.update({"extra_reach_lower": 0, "extra_reach_upper": 0, "new_reach_lower": _people(c["reach"]), "new_reach_upper": _people(c["reach"])})
            block["note"] = "no unexposed people left in this audience: a cap cannot add reach; cut the budget or widen the audience."
        else:
            extra_upper = min(released / cap, unexposed)
            block["extra_reach_upper"] = _people(extra_upper)
            block["new_reach_upper"] = _people(c["reach"] + extra_upper)
            if c["cpm_unexposed_factor"] is not None:
                extra_lower = min(released / cap / c["cpm_unexposed_factor"], unexposed)
                block["extra_reach_lower"] = _people(extra_lower)
                block["new_reach_lower"] = _people(c["reach"] + extra_lower)
            else:
                block["extra_reach_lower"] = None
                block["new_reach_lower"] = None
                out["missing"].append(f"cpm_unexposed_factor for {name} (how much more an impression on a new person costs; sets the lower bound of extra reach)")
            if impressions > 0:
                block["new_avg_frequency"] = round(impressions / (c["reach"] + extra_upper), 2)
            block["note"] = (
                "Closed ranges count at their midpoint and open buckets (e.g. 6+) at their minimum, so released impressions are conservative. "
                f"Extra reach assumes each new person is shown the capped {cap} exposures: at the current CPM that is the upper bound"
                + (", scaled down by cpm_unexposed_factor for the lower." if c["cpm_unexposed_factor"] is not None else "; the lower bound needs cpm_unexposed_factor.")
                + " Test it as capped vs uncapped cells on the business outcome, not on CPM."
            )
        scenarios[name] = block
    if scenarios:
        out["cap_scenario"] = scenarios


def _comparability(out: dict[str, Any], all_channels: list[dict[str, Any]], excluded: list[dict[str, Any]], sentences: list[str]) -> None:
    """Same audience, same ruler (#471): which channel figures can be put side by side."""
    if not any(c["measure"] is not None for c in all_channels):
        return
    for c in all_channels:
        if c["measure"] is None:
            out["missing"].append(f"measurement basis for {c['name']}: supply unique people, the base (e.g. LT 18+) and the period")
    pairs = []
    flagged: set[str] = set()
    with_measure = [c for c in all_channels if c["measure"] is not None]
    for a, b in combinations(with_measure, 2):
        differs = [f for f in _MEASURE_FIELDS if a["measure"][f] and b["measure"][f] and a["measure"][f].lower() != b["measure"][f].lower()]
        if not differs:
            continue
        # A views-based channel is the one that cannot be compared; the people-based side is fine.
        flagged.update({c["name"] for c in (a, b) if c["excluded_reason"] or not (a["excluded_reason"] or b["excluded_reason"])})
        pairs.append(
            {
                "a": a["name"],
                "b": b["name"],
                "differs": differs,
                "note": f"{a['name']} and {b['name']} are measured differently ({', '.join(differs)}): convert to unique people on one base and period before comparing.",
            }
        )
    excluded_names = [c["name"] for c in excluded]
    not_comparable = excluded_names + [n for n in sorted(flagged) if n not in excluded_names]
    comparable = not pairs and not excluded and all(c["measure"] is not None for c in all_channels)
    out["comparability"] = {"comparable": comparable, "pairs": pairs, "not_comparable": not_comparable}
    for c in excluded:
        sentences.append(f"{c['name']} is excluded from the combined reach — {c['excluded_reason']}.")
    people_pairs = [p for p in pairs if p["a"] not in excluded_names and p["b"] not in excluded_names]
    if people_pairs:
        out["warnings"].append(
            "Combined reach mixes measurement bases ("
            + "; ".join(f"{p['a']} vs {p['b']}: {', '.join(p['differs'])}" for p in people_pairs)
            + "): quote it only after converting to one base and period."
        )


def reach_overlap_tool(args: dict) -> str:
    """Backs the worker-facing ``reach_overlap`` tool: JSON the worker can quote from."""
    return json.dumps(reach_overlap(args or {}), ensure_ascii=False, indent=1)
