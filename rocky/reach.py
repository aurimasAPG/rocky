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
"""

from __future__ import annotations

import json
import math
from itertools import combinations
from typing import Any

from rocky.tools import ToolError

ASSUMPTIONS = ("independent", "bounds", "given")
REACH_UNITS = ("people", "pct")
# Above this many channels the Bonferroni search over channel groups is limited
# to pairs; 2^12 subsets is cheap, 2^20 is not.
MAX_SUBSET_CHANNELS = 12
_SPACES = (" ", " ", " ", " ", "'")


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
        if unit == "pct":
            reach = reach / 100 * audience
        if reach > audience:
            warnings.append(f"{name} reach {_fmt(reach)} is above the audience of {_fmt(audience)}: clipped to the audience.")
            reach = audience
        cost = _number(item, "cost", minimum=0.0, label=f"cost of {name}")
        frequency = _number(item, "frequency", minimum=0.0, label=f"frequency of {name}")
        channels.append({"name": name, "reach": reach, "cost": cost, "frequency": frequency})
    return channels


def _parse_overlaps(args: dict, channels: list[dict[str, Any]], audience: float, unit: str) -> dict[tuple[int, int], float]:
    raw = args.get("overlaps")
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
            if name.lower() not in index:
                raise ToolError(f"overlap names an unknown channel {name!r}")
            names.append(name)
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


def reach_overlap(args: dict) -> dict[str, Any]:
    """Combined and incremental reach across channels, as bounds plus a modelled point.

    Inputs: ``audience`` (people), ``channels`` [{name, reach, cost?, frequency?}],
    ``reach_unit`` ``people`` (default) or ``pct`` of the audience, ``overlaps``
    [{a, b, shared}] for the pairs whose duplication is known, ``assumption``
    ``independent`` | ``bounds`` | ``given`` (default: ``given`` when every pair
    is known, otherwise ``bounds``), optional ``budget`` and ``audience_name``.
    Pairs not listed under ``overlaps`` are reported under ``missing`` and the
    result says which one to ask about first.
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

    warnings: list[str] = []
    channels = _parse_channels(args, audience, unit, warnings)
    known = _parse_overlaps(args, channels, audience, unit)
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
    for c in channels:
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
        if c["frequency"] is not None:
            row["impressions"] = _people(c["reach"] * c["frequency"])
        out_channels.append(row)

    missing = [_pair_label(channels[a]["name"], channels[b]["name"]) for a, b in unknown_pairs]
    if len(channels) < 2:
        warnings.append("One channel: there is no overlap to ask about; the combined reach is that channel's reach.")
    if total_reach > audience + 0.5:
        warnings.append(
            f"Channel reaches add up to {_fmt(total_reach)}, above the audience of {_fmt(audience)}: some people are counted "
            f"in two channels, which is expected; the combined reach cannot exceed the audience."
        )

    out: dict[str, Any] = {
        "inputs": {
            "audience": _people(audience),
            "audience_name": audience_name or None,
            "reach_unit": unit,
            "assumption": requested,
            "assumption_used": model,
            "budget": None if budget is None else round(budget, 2),
            "known_overlaps": len(known),
            "unknown_overlaps": len(unknown_pairs),
        },
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
    if len(costs) == len(channels):
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
        out["missing"].append("cost for " + ", ".join(c["name"] for c in channels if c["cost"] is None))
    if budget is not None:
        cost_block["budget"] = round(budget, 2)
        cost_block["budget_left"] = round(budget - total, 2)
        if total > budget + 1e-9:
            warnings.append(f"Channel costs {total:,.2f} exceed the budget {budget:,.2f}.")
    out["cost"] = cost_block


def _frequency(out: dict[str, Any], channels: list[dict[str, Any]], lower: float, upper: float, audience: float) -> None:
    if not all(c["frequency"] is not None for c in channels):
        return
    impressions = sum(c["reach"] * c["frequency"] for c in channels)
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


def reach_overlap_tool(args: dict) -> str:
    """Backs the worker-facing ``reach_overlap`` tool: JSON the worker can quote from."""
    return json.dumps(reach_overlap(args or {}), ensure_ascii=False, indent=1)
