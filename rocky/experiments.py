"""Reading out a finished test and sizing tests on a mean.

``calc.experiment_plan`` sizes a test on a rate. This module adds the other
half of the job: ``evaluate_results`` turns the counts (or means) of a finished
test into a verdict a worker can quote, with the interval and the sample
context next to every number; ``plan_means`` sizes a test on a measured
quantity (prep time, order value, score), paired or not; ``plan_rate`` adds
guardrails and a segment to the rate plan without a second sizing formula.
Every function is pure, stdlib only, and says what is missing instead of
guessing.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from statistics import NormalDist
from typing import Any

from rocky.calc import experiment_plan
from rocky.tools import ToolError

DEFAULT_ALPHA = 0.05
DEFAULT_POWER = 0.8
DEFAULT_CORRELATION = 0.5
# Without a measured spread, the plan is shown for these coefficients of variation.
CONDITIONAL_CV_PCT = (10.0, 20.0, 30.0, 50.0)
_Z = NormalDist()

VERDICT_WINNER = "winner"
VERDICT_LOSER = "loser"
VERDICT_NO_DIFFERENCE = "no difference detected"
VERDICT_UNDERPOWERED = "underpowered: keep running"
VERDICT_BELOW_PLANNED = "significant but below the planned effect"


# --- parsing -------------------------------------------------------------


def _num(value: Any, label: str, *, required: bool = False, minimum: float | None = None) -> float | None:
    """Lenient number parsing: '3,5', '12 %', '€ 40' and plain numbers all pass."""
    if value is None or value == "":
        if required:
            raise ToolError(f"{label} is required")
        return None
    try:
        text = str(value).replace("€", "").replace("%", "").replace("\u202f", "").replace("\xa0", "").replace(" ", "")
        parsed = float(text.replace(",", "."))
    except ValueError as exc:
        raise ToolError(f"{label} must be a number, got {value!r}") from exc
    if math.isnan(parsed) or math.isinf(parsed):
        raise ToolError(f"{label} must be a finite number")
    if minimum is not None and parsed < minimum:
        raise ToolError(f"{label} must be at least {minimum:g}")
    return parsed


def _whole(value: float) -> int | float:
    """Counts print as 1000, not 1000.0."""
    return int(value) if float(value).is_integer() else value


def _flag(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() not in {"false", "0", "no", "off"}


def _pct(value: float) -> float:
    return round(float(value) * 100, 1)


def _rate_pct(value: float) -> float:
    # Rates keep three decimals, as calc does for target_rate_pct: 0.35 % must not become 0.4 %.
    return round(float(value) * 100, 3)


def _alpha_power(args: dict) -> tuple[float, float]:
    alpha = _num(args.get("alpha"), "alpha")
    power = _num(args.get("power"), "power")
    alpha = DEFAULT_ALPHA if alpha is None else alpha
    power = DEFAULT_POWER if power is None else power
    if not 0 < alpha < 0.5 or not 0.5 <= power < 1:
        raise ToolError("alpha must be between 0 and 0.5, power between 0.5 and 1")
    return alpha, power


# --- Student t without scipy ----------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = tiny if abs(d) < tiny else d
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = tiny if abs(d) < tiny else d
        c = 1.0 + aa / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularised incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _t_two_sided_p(t: float, df: float) -> float:
    """P(|T| >= |t|) for Student t with df degrees of freedom."""
    if df <= 0:
        return 1.0
    if math.isinf(t):
        return 0.0
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def _t_crit(df: float, two_sided_alpha: float) -> float:
    """The t with P(|T| >= t) = alpha, by bisection; falls back to the normal for huge df."""
    if df > 1e6:
        return _Z.inv_cdf(1 - two_sided_alpha / 2)
    lo, hi = 0.0, 1000.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if _t_two_sided_p(mid, df) > two_sided_alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --- arms -----------------------------------------------------------------


def _parse_arms(raw: Any, *, label: str) -> tuple[list[dict[str, Any]], str]:
    """Returns the arms with numbers parsed and the kind: 'rates' or 'means'."""
    if not isinstance(raw, list):
        raise ToolError(f"{label} must be a list of arms, each with name and n")
    if len(raw) < 2:
        raise ToolError(f"{label} needs at least two arms (a control and a challenger); got {len(raw)}")
    arms: list[dict[str, Any]] = []
    kinds: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ToolError(f"{label}[{i}] must be an object with name and n")
        name = str(item.get("name") or item.get("label") or item.get("variant") or f"arm {i + 1}").strip()
        where = f"{label} '{name}'"
        n = _num(item.get("n", item.get("units", item.get("exposed"))), f"{where} n", required=True)
        if n < 1:
            raise ToolError(f"{where}: n must be at least 1 (units exposed); it is {n:g}")
        conversions = item.get("conversions", item.get("successes", item.get("converted")))
        if conversions is not None and conversions != "":
            c = _num(conversions, f"{where} conversions", required=True, minimum=0.0)
            if c > n:
                raise ToolError(f"{where}: conversions ({c:g}) exceed n ({n:g})")
            arms.append({"name": name, "n": _whole(n), "conversions": _whole(c)})
            kinds.add("rates")
            continue
        mean = _num(item.get("mean"), f"{where} mean")
        if mean is None:
            raise ToolError(f"{where}: give conversions (for a rate) or mean and sd (for a measured quantity)")
        sd = _num(item.get("sd", item.get("std", item.get("stdev"))), f"{where} sd", minimum=0.0)
        if sd is None:
            raise ToolError(
                f"{where}: sd is required for a mean. Compute the standard deviation from the raw values of that arm; "
                "without it no interval or p-value is possible."
            )
        if n < 2:
            raise ToolError(f"{where}: a mean needs at least 2 units")
        arms.append({"name": name, "n": _whole(n), "mean": mean, "sd": sd})
        kinds.add("means")
    if len(kinds) > 1:
        raise ToolError(f"{label}: all arms must be the same kind, either counts (conversions) or means with sd")
    return arms, kinds.pop()


def _pick_control(arms: list[dict[str, Any]], wanted: Any) -> int:
    if wanted not in (None, ""):
        name = str(wanted).strip().lower()
        for i, arm in enumerate(arms):
            if arm["name"].lower() == name:
                return i
        raise ToolError(f"control '{wanted}' is not one of the arms: {', '.join(a['name'] for a in arms)}")
    for i, arm in enumerate(arms):
        if arm["name"].lower() in {"control", "a", "baseline", "holdout", "current"}:
            return i
    return 0


def _compare(
    control: dict[str, Any],
    arm: dict[str, Any],
    kind: str,
    *,
    alpha_each: float,
    two_sided: bool,
    lower_is_better: bool,
    power: float,
) -> dict[str, Any]:
    """One challenger against the control: effect, interval, test statistic, p-value."""
    row: dict[str, Any] = {"name": arm["name"], "n": arm["n"], "control_n": control["n"]}
    if kind == "rates":
        n1, n2 = control["n"], arm["n"]
        p1, p2 = control["conversions"] / n1, arm["conversions"] / n2
        diff = p2 - p1
        se_ci = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
        pooled = (control["conversions"] + arm["conversions"]) / (n1 + n2)
        se_test = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
        stat = diff / se_test if se_test > 0 else (0.0 if diff == 0 else math.copysign(math.inf, diff))
        p_two = 2 * (1 - _Z.cdf(abs(stat))) if math.isfinite(stat) else 0.0
        half = _Z.inv_cdf(0.975) * se_ci
        row.update(
            {
                "control_rate_pct": _rate_pct(p1),
                "rate_pct": _rate_pct(p2),
                "absolute_diff_pp": _rate_pct(diff),
                "ci95_pp": [_rate_pct(diff - half), _rate_pct(diff + half)],
                "z": round(stat, 3) if math.isfinite(stat) else None,
            }
        )
        base = p1
        # Smallest absolute difference this many units could have detected at the stated power.
        detectable = (_Z.inv_cdf(1 - alpha_each / (2 if two_sided else 1)) + _Z.inv_cdf(power)) * math.sqrt(
            p1 * (1 - p1) * (1 / n1 + 1 / n2)
        )
        row["detectable_at_this_sample"] = {
            "absolute_pp": _rate_pct(detectable),
            "relative_pct": _pct(detectable / p1) if p1 > 0 else None,
        }
        lo, hi = diff - half, diff + half
    else:
        n1, n2 = control["n"], arm["n"]
        m1, m2, s1, s2 = control["mean"], arm["mean"], control["sd"], arm["sd"]
        diff = m2 - m1
        v1, v2 = s1 * s1 / n1, s2 * s2 / n2
        se = math.sqrt(v1 + v2)
        if se > 0:
            df = (v1 + v2) ** 2 / ((v1 * v1 / (n1 - 1)) + (v2 * v2 / (n2 - 1)))
            stat = diff / se
            p_two = _t_two_sided_p(stat, df)
            half = _t_crit(df, 0.05) * se
        else:
            df = float(n1 + n2 - 2)
            stat = 0.0 if diff == 0 else math.copysign(math.inf, diff)
            p_two = 1.0 if diff == 0 else 0.0
            half = 0.0
        row.update(
            {
                "control_mean": round(m1, 4),
                "mean": round(m2, 4),
                "absolute_diff": round(diff, 4),
                "ci95": [round(diff - half, 4), round(diff + half, 4)],
                "t": round(stat, 3) if math.isfinite(stat) else None,
                "df": round(df, 1),
            }
        )
        base = m1
        detectable = (_Z.inv_cdf(1 - alpha_each / (2 if two_sided else 1)) + _Z.inv_cdf(power)) * se
        row["detectable_at_this_sample"] = {
            "absolute": round(detectable, 4),
            "relative_pct": _pct(detectable / abs(m1)) if m1 else None,
        }
        lo, hi = diff - half, diff + half

    if base:
        row["relative_lift_pct"] = _pct(diff / abs(base))
        row["ci95_relative_lift_pct"] = [_pct(lo / abs(base)), _pct(hi / abs(base))]
    else:
        row["relative_lift_pct"] = None
        row["ci95_relative_lift_pct"] = None
    better = diff < 0 if lower_is_better else diff > 0
    if two_sided:
        p_value = p_two
    else:
        p_value = p_two / 2 if better else 1 - p_two / 2
    row["p_value"] = round(min(max(p_value, 0.0), 1.0), 4)
    row["alpha_used"] = round(alpha_each, 4)
    row["significant"] = p_value < alpha_each
    row["_better"] = better
    row["_diff"] = diff
    return row


def _planned_checks(row: dict[str, Any], planned: dict[str, Any] | None, kind: str) -> None:
    row["planned_sample_reached"] = None
    row["met_planned_effect"] = None
    if not planned:
        return
    target_n = planned.get("sample_per_variant")
    if target_n is not None:
        smallest = min(row["n"], row["control_n"])
        row["planned_sample_reached"] = smallest >= target_n
        row["planned_progress_pct"] = _pct(min(smallest / target_n, 1.0))
    effect = planned.get("minimum_effect")
    if effect is not None:
        if planned.get("effect_kind") == "absolute":
            observed = abs(row["absolute_diff_pp"] if kind == "rates" else row["absolute_diff"])
        else:
            observed = abs(row["relative_lift_pct"]) if row.get("relative_lift_pct") is not None else None
        if observed is not None:
            row["met_planned_effect"] = bool(row["_better"]) and observed >= effect - 1e-9


def _verdict(row: dict[str, Any], planned: dict[str, Any] | None) -> str:
    if row["significant"]:
        if not row["_better"]:
            return VERDICT_LOSER
        if row.get("met_planned_effect") is False:
            return VERDICT_BELOW_PLANNED
        return VERDICT_WINNER
    if planned and row.get("planned_sample_reached") is False:
        return VERDICT_UNDERPOWERED
    return VERDICT_NO_DIFFERENCE


def _guardrails(
    raw: Any,
    main_arms: list[dict[str, Any]],
    control_name: str,
    *,
    alpha_each: float,
    two_sided: bool,
    power: float,
) -> list[dict[str, Any]]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ToolError("guardrails must be a list of {name, arms, max_drop_pct}")
    out: list[dict[str, Any]] = []
    main_names = {a["name"].lower() for a in main_arms}
    for i, g in enumerate(raw):
        if not isinstance(g, dict):
            raise ToolError(f"guardrails[{i}] must be an object with name, arms and max_drop_pct")
        name = str(g.get("name") or f"guardrail {i + 1}").strip()
        arms, kind = _parse_arms(g.get("arms"), label=f"guardrail '{name}' arms")
        for arm in arms:
            if arm["name"].lower() not in main_names:
                raise ToolError(f"guardrail '{name}': arm '{arm['name']}' is not one of the test arms")
        max_drop = _num(g.get("max_drop_pct"), f"guardrail '{name}' max_drop_pct", minimum=0.0)
        lower_better = _flag(g.get("lower_is_better"), False)
        ctrl_idx = next((k for k, a in enumerate(arms) if a["name"].lower() == control_name.lower()), None)
        if ctrl_idx is None:
            raise ToolError(f"guardrail '{name}' has no arm named '{control_name}' (the control)")
        control = arms[ctrl_idx]
        rows = []
        for k, arm in enumerate(arms):
            if k == ctrl_idx:
                continue
            cmp = _compare(
                control, arm, kind, alpha_each=alpha_each, two_sided=two_sided, lower_is_better=lower_better, power=power
            )
            rel = cmp.get("relative_lift_pct")
            rel_ci = cmp.get("ci95_relative_lift_pct")
            # Worsening is positive when the metric moved the wrong way.
            if rel is None:
                worsening, worst_case = None, None
            elif lower_better:
                worsening, worst_case = rel, rel_ci[1]
            else:
                worsening, worst_case = -rel, -rel_ci[0]
            breached = max_drop is not None and worsening is not None and worsening > max_drop + 1e-9
            at_risk = max_drop is not None and worst_case is not None and worst_case > max_drop + 1e-9
            rows.append(
                {
                    "arm": arm["name"],
                    "control_value": cmp.get("control_rate_pct", cmp.get("control_mean")),
                    "value": cmp.get("rate_pct", cmp.get("mean")),
                    "relative_change_pct": rel,
                    "ci95_relative_change_pct": rel_ci,
                    "worsening_pct": None if worsening is None else round(worsening, 1),
                    "p_value": cmp["p_value"],
                    "breached": breached,
                    "could_breach": at_risk and not breached,
                }
            )
        out.append(
            {
                "name": name,
                "kind": kind,
                "lower_is_better": lower_better,
                "max_drop_pct": max_drop,
                "arms": rows,
                "note": None if max_drop is not None else "max_drop_pct missing: the change is reported but no breach can be called",
            }
        )
    return out


def _fmt_pp(value: float) -> str:
    return f"{value:+.3g}"


def _sentence(row: dict[str, Any], kind: str, metric: str, control_name: str, planned: dict[str, Any] | None) -> str:
    if kind == "rates":
        head = (
            f"{row['name']}: {row['rate_pct']:g} % vs {row['control_rate_pct']:g} % for {control_name} "
            f"({_fmt_pp(row['absolute_diff_pp'])} pp, {row['relative_lift_pct']:+g} %, 95 % CI "
            f"{row['ci95_pp'][0]:g} to {row['ci95_pp'][1]:g} pp, p = {row['p_value']:.3g})"
        )
    else:
        rel = "" if row.get("relative_lift_pct") is None else f", {row['relative_lift_pct']:+g} %"
        head = (
            f"{row['name']}: {metric} {row['mean']:g} vs {row['control_mean']:g} for {control_name} "
            f"({row['absolute_diff']:+g}{rel}, 95 % CI {row['ci95'][0]:g} to {row['ci95'][1]:g}, p = {row['p_value']:.3g})"
        )
    verdict = row["verdict"]
    tail = f": {verdict}"
    if verdict == VERDICT_UNDERPOWERED and planned:
        tail += (
            f" — {min(row['n'], row['control_n']):,.0f} of {planned['sample_per_variant']:,.0f} planned per arm "
            f"({row['planned_progress_pct']:g} %)"
        )
    elif verdict == VERDICT_NO_DIFFERENCE:
        det = row["detectable_at_this_sample"]
        if kind == "rates":
            tail += f" — at this sample only a lift of {det['absolute_pp']:g} pp ({det['relative_pct']:g} % relative) or more could have been seen"
        else:
            tail += f" — at this sample only a difference of {det['absolute']:g} or more could have been seen"
    elif verdict == VERDICT_BELOW_PLANNED and planned:
        tail += f" — the plan asked for {planned['minimum_effect']:g}{' pp' if planned.get('effect_kind') == 'absolute' else ' %'} at least"
    if row.get("guardrail_breaches"):
        tail += f"; guardrail breached: {', '.join(row['guardrail_breaches'])} — do not ship on this result alone"
    return head + tail + "."


def evaluate_results(args: dict) -> dict[str, Any]:
    """Read out a finished A/B test: effect, interval, p-value and a verdict per challenger.

    Inputs: ``arms`` ``[{name, n, conversions}]`` for rates or ``[{name, n, mean,
    sd}]`` for means (two or more; the arm named control, or ``control``, or the
    first one is the control); ``metric``; ``alpha`` (0.05); ``two_sided``
    (true); ``lower_is_better`` (false); ``guardrails`` ``[{name, arms,
    max_drop_pct, lower_is_better}]``; ``planned`` ``{sample_per_variant,
    minimum_effect, effect_kind}`` from the plan written before the test.
    """
    arms, kind = _parse_arms(args.get("arms"), label="arms")
    control_idx = _pick_control(arms, args.get("control"))
    control = arms[control_idx]
    metric = str(args.get("metric") or ("conversion rate" if kind == "rates" else "mean")).strip()
    alpha, power = _alpha_power(args)
    two_sided = _flag(args.get("two_sided"), True)
    lower_is_better = _flag(args.get("lower_is_better"), False)
    comparisons = len(arms) - 1
    alpha_each = alpha / comparisons

    planned_raw = args.get("planned")
    planned: dict[str, Any] | None = None
    if planned_raw:
        if not isinstance(planned_raw, dict):
            raise ToolError("planned must be an object: {sample_per_variant, minimum_effect, effect_kind}")
        planned = {
            "sample_per_variant": _num(planned_raw.get("sample_per_variant"), "planned.sample_per_variant", minimum=1.0),
            "minimum_effect": _num(planned_raw.get("minimum_effect"), "planned.minimum_effect", minimum=0.0),
            "effect_kind": str(planned_raw.get("effect_kind") or "relative").strip().lower(),
        }
        if planned["effect_kind"] not in {"relative", "absolute"}:
            raise ToolError("planned.effect_kind must be relative or absolute")

    out: dict[str, Any] = {
        "kind": kind,
        "metric": metric,
        "control": control["name"],
        "inputs": {
            "alpha": alpha,
            "alpha_per_comparison": round(alpha_each, 4),
            "two_sided": two_sided,
            "lower_is_better": lower_is_better,
            "comparisons": comparisons,
            "power_for_detectable": power,
        },
        "arms": [],
        "comparisons": [],
        "guardrails": [],
        "planned": planned,
        "missing": [],
        "warnings": [],
    }
    for arm in arms:
        if kind == "rates":
            p = arm["conversions"] / arm["n"]
            half = _Z.inv_cdf(0.975) * math.sqrt(p * (1 - p) / arm["n"])
            out["arms"].append(
                {
                    "name": arm["name"],
                    "n": arm["n"],
                    "conversions": arm["conversions"],
                    "rate_pct": _rate_pct(p),
                    "ci95_pct": [_rate_pct(max(p - half, 0.0)), _rate_pct(min(p + half, 1.0))],
                }
            )
            if arm["conversions"] < 10 or arm["n"] - arm["conversions"] < 10:
                out["warnings"].append(
                    f"{arm['name']} has fewer than 10 conversions or non-conversions: the Wald interval and z-test are rough here; treat the p-value as indicative."
                )
        else:
            se = arm["sd"] / math.sqrt(arm["n"])
            out["arms"].append(
                {
                    "name": arm["name"],
                    "n": arm["n"],
                    "mean": round(arm["mean"], 4),
                    "sd": round(arm["sd"], 4),
                    "se": round(se, 4),
                    "ci95": [round(arm["mean"] - _t_crit(arm["n"] - 1, 0.05) * se, 4), round(arm["mean"] + _t_crit(arm["n"] - 1, 0.05) * se, 4)],
                }
            )
            if arm["n"] < 30:
                out["warnings"].append(f"{arm['name']} has fewer than 30 units: the t-test assumes roughly normal values at this size.")

    guardrails = _guardrails(
        args.get("guardrails"), arms, control["name"], alpha_each=alpha_each, two_sided=two_sided, power=power
    )
    out["guardrails"] = guardrails
    breaches_by_arm: dict[str, list[str]] = {}
    for g in guardrails:
        for r in g["arms"]:
            if r["breached"]:
                breaches_by_arm.setdefault(r["arm"].lower(), []).append(g["name"])

    for i, arm in enumerate(arms):
        if i == control_idx:
            continue
        row = _compare(
            control, arm, kind, alpha_each=alpha_each, two_sided=two_sided, lower_is_better=lower_is_better, power=power
        )
        _planned_checks(row, planned, kind)
        row["verdict"] = _verdict(row, planned)
        row["guardrail_breaches"] = breaches_by_arm.get(arm["name"].lower(), [])
        row["summary"] = _sentence(row, kind, metric, control["name"], planned)
        row.pop("_better", None)
        row.pop("_diff", None)
        out["comparisons"].append(row)

    if comparisons > 1:
        out["warnings"].append(
            f"{comparisons} challengers against one control: alpha is split to {alpha_each:.4g} per comparison (Bonferroni)."
        )
    if not two_sided:
        out["warnings"].append("One-sided test: only a better variant can be called; a worse one shows as no difference, not as a loser.")
    if planned is None:
        out["missing"].append("planned")
        out["warnings"].append(
            "No planned sample was given, so 'no difference detected' cannot be told apart from 'not enough data'. "
            "Compare n per arm with the sample the plan asked for, or read detectable_at_this_sample."
        )
    if kind == "rates":
        out["method"] = (
            "two-proportion z-test with pooled standard error; Wald 95 % interval for the difference without continuity "
            "correction (rough below 10 events per arm); Bonferroni split of alpha across challengers"
        )
    else:
        out["method"] = (
            "Welch t-test for two means with Welch–Satterthwaite degrees of freedom; t-based 95 % interval; "
            "Bonferroni split of alpha across challengers"
        )
    out["summary"] = " ".join(r["summary"] for r in out["comparisons"])
    return out


# --- sizing a test on a mean ------------------------------------------------


def _means_per_arm(sd_eff: float, diff: float, alpha_each: float, power: float, two_sided: bool, *, arms_sharing: int) -> int:
    """Units per arm for a difference in means; refined once with the t quantiles for small samples."""
    z_a = _Z.inv_cdf(1 - alpha_each / 2) if two_sided else _Z.inv_cdf(1 - alpha_each)
    z_b = _Z.inv_cdf(power)
    n = math.ceil(arms_sharing * (sd_eff * (z_a + z_b) / diff) ** 2 - 1e-9)
    n = max(n, 2)
    for _ in range(2):
        df = max(arms_sharing * (n - 1), 1)
        t_a = _t_crit(df, alpha_each if two_sided else 2 * alpha_each)
        t_b = _t_crit(df, 2 * (1 - power))
        refined = math.ceil(arms_sharing * (sd_eff * (t_a + t_b) / diff) ** 2 - 1e-9)
        if refined == n:
            break
        n = max(refined, 2)
    return n


def plan_means(args: dict) -> dict[str, Any]:
    """Sample size for a difference in means, between subjects or paired.

    Inputs: ``sd`` (spread of the metric in one arm, same units as the metric)
    or ``baseline_mean`` with ``cv_pct`` (coefficient of variation);
    ``minimum_difference`` (same units); ``alpha`` (0.05); ``power`` (0.8);
    ``two_sided`` (true); ``variants`` (arms incl. control, 2); ``weekly_units``;
    ``paired`` (false) with ``correlation`` between the two measurements of one
    subject (0.5); ``metric`` and ``units`` labels.
    """
    metric = str(args.get("metric") or "the metric").strip()
    units = str(args.get("units") or "").strip()
    diff = _num(args.get("minimum_difference", args.get("min_detectable_diff")), "minimum_difference", required=True)
    if diff <= 0:
        raise ToolError("minimum_difference must be greater than zero (same units as the metric)")
    alpha, power = _alpha_power(args)
    two_sided = _flag(args.get("two_sided"), True)
    variants = int(_num(args.get("variants"), "variants", minimum=2) or 2)
    paired = _flag(args.get("paired"), False)
    correlation = _num(args.get("correlation"), "correlation")
    correlation = DEFAULT_CORRELATION if correlation is None else correlation
    if not -1 <= correlation <= 1:
        raise ToolError("correlation must be between -1 and 1")
    weekly = _num(args.get("weekly_units", args.get("weekly_traffic")), "weekly_units", minimum=0.0)
    comparisons = variants - 1
    alpha_each = alpha / comparisons

    sd = _num(args.get("sd"), "sd", minimum=0.0)
    baseline_mean = _num(args.get("baseline_mean"), "baseline_mean")
    cv = _num(args.get("cv_pct"), "cv_pct", minimum=0.0)
    if sd is None and baseline_mean is not None and cv is not None:
        sd = abs(baseline_mean) * cv / 100
    unit_label = f" {units}" if units else ""

    out: dict[str, Any] = {
        "inputs": {
            "sd": sd,
            "baseline_mean": baseline_mean,
            "cv_pct": cv,
            "minimum_difference": diff,
            "alpha": alpha,
            "alpha_per_comparison": round(alpha_each, 4),
            "power": power,
            "two_sided": two_sided,
            "variants": variants,
            "paired": paired,
            "correlation": correlation if paired else None,
            "weekly_units": weekly,
        },
        "missing": [],
        "warnings": [],
    }
    if sd is None or sd <= 0:
        out["missing"].append("sd")
        out["per_variant"] = None
        out["total"] = None
        out["weeks"] = None
        note = (
            "The spread of the metric is missing. Measure 20 units (a pilot or last month's records) and compute the standard "
            "deviation, or give baseline_mean with cv_pct (typical spread as a percent of the mean)."
        )
        if baseline_mean:
            out["conditional"] = []
            for cv_guess in CONDITIONAL_CV_PCT:
                sd_guess = abs(baseline_mean) * cv_guess / 100
                sd_eff = sd_guess * math.sqrt(2 * (1 - correlation)) if paired else sd_guess
                n = _means_per_arm(sd_eff, diff, alpha_each, power, two_sided, arms_sharing=1 if paired else 2)
                out["conditional"].append({"assumed_cv_pct": cv_guess, "assumed_sd": round(sd_guess, 4), "per_variant": n, "total": n if paired else n * variants})
            note += " Until then the table shows the sample for typical spreads."
        out["method"] = "no sizing without a standard deviation"
        out["summary"] = note
        return out

    if paired:
        sd_eff = sd * math.sqrt(2 * (1 - correlation))
        per_variant = _means_per_arm(sd_eff, diff, alpha_each, power, two_sided, arms_sharing=1)
        total = per_variant  # every subject is measured under every variant
        if correlation <= 0:
            out["warnings"].append("correlation is zero or negative: pairing brings no saving over separate groups here.")
        out["method"] = (
            "paired t-test on within-subject differences: sd of the difference = sd × √(2(1 − r)); "
            "normal approximation refined with t quantiles; Bonferroni split of alpha across challengers"
        )
    else:
        per_variant = _means_per_arm(sd, diff, alpha_each, power, two_sided, arms_sharing=2)
        total = per_variant * variants
        out["method"] = (
            "two-sample t-test for a difference in means, equal spread assumed: n per arm = 2·(sd·(z_α + z_β)/Δ)², "
            "refined with t quantiles; Bonferroni split of alpha across challengers"
        )
    out["per_variant"] = per_variant
    out["total"] = total
    out["standardised_effect"] = round(diff / sd, 3)
    if comparisons > 1:
        out["warnings"].append(f"{comparisons} challengers against one control: alpha is split to {alpha_each:.4g} per comparison, which raises the sample.")
    if per_variant < 30:
        out["warnings"].append("Fewer than 30 per arm: the result will lean on the values being roughly normal; check for outliers before reading it.")
    if diff / sd > 1.5:
        out["warnings"].append("The minimum difference is more than 1.5 standard deviations: a change that large is rare; check the units.")

    what = f"{per_variant:,} {'participants, each measured under every variant' if paired else 'per variant'} ({total:,} in all)"
    if weekly is None:
        out["missing"].append("weekly_units")
        out["weeks"] = None
        out["summary"] = f"{what} to detect {metric} moving by {diff:g}{unit_label} (sd {sd:g}). Duration is unknown until weekly_units is given; ask for it."
    elif weekly <= 0:
        out["missing"].append("weekly_units")
        out["weeks"] = None
        out["summary"] = "weekly_units is zero: the test cannot run."
    else:
        weeks = math.ceil(total / weekly - 1e-9)
        out["weeks"] = weeks
        out["days"] = weeks * 7
        if weeks < 2:
            out["warnings"].append("Run at least two full weeks anyway so every weekday is covered twice.")
        out["summary"] = f"{what}: about {weeks} week{'s' if weeks != 1 else ''} at {weekly:,.0f} units per week, to detect {metric} moving by {diff:g}{unit_label} (sd {sd:g})."
    tail = "two-sided" if two_sided else "one-sided"
    design = "Each participant uses every variant in random order" if paired else "Assign units to variants at random"
    out["decision_rule"] = (
        f"Fix the sample in advance: {what}. {design}; run whole weeks and do not stop early. Call a variant the winner only "
        f"if its {metric} differs from the control by at least {diff:g}{unit_label} with a {tail} p-value below {alpha_each:.4g}"
        f"{' (per comparison)' if comparisons > 1 else ''}. Otherwise keep the control and record the result."
    )
    return out


# --- rate plan with guardrails and a segment ------------------------------


def plan_rate(args: dict) -> dict[str, Any]:
    """``calc.experiment_plan`` plus guardrail names and a segment written into the decision rule."""
    out = experiment_plan(args or {})
    raw_guardrails = args.get("guardrails") or []
    if not isinstance(raw_guardrails, list):
        raise ToolError("guardrails must be a list of names or {name, max_drop_pct}")
    guardrails: list[dict[str, Any]] = []
    for i, g in enumerate(raw_guardrails):
        if isinstance(g, dict):
            name = str(g.get("name") or f"guardrail {i + 1}").strip()
            max_drop = _num(g.get("max_drop_pct"), f"guardrail '{name}' max_drop_pct", minimum=0.0)
        else:
            name, max_drop = str(g).strip(), None
        guardrails.append({"name": name, "max_drop_pct": max_drop})
    segment = str(args.get("segment") or "").strip() or None
    out["guardrails"] = guardrails
    out["segment"] = segment
    extra = []
    if segment:
        extra.append(f"Population: {segment} only — randomise and read the result inside that segment; do not carry it over to others.")
    if guardrails:
        parts = [
            f"{g['name']} may not worsen by more than {g['max_drop_pct']:g} %" if g["max_drop_pct"] is not None else f"{g['name']} must not worsen"
            for g in guardrails
        ]
        extra.append("Guardrails, checked with the same counts: " + "; ".join(parts) + ". A breach blocks the launch even with a winner.")
        if any(g["max_drop_pct"] is None for g in guardrails):
            out.setdefault("warnings", []).append("Some guardrails have no max_drop_pct: set the tolerated drop before the test starts.")
    if extra:
        out["decision_rule"] = out["decision_rule"] + " " + " ".join(extra)
    return out



# --- shared helpers for the research modes ---------------------------------

LT_LETTERS = set("ąčęėįšųūžĄČĘĖĮŠŲŪŽ")
WTP_MIN_N = 30
INCOME_BAND = (85.0, 115.0)  # income index vs national that still counts as representative
MIN_SHARE_PCT = 1.0


def _wilson(k: float, n: float, *, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials, as fractions."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z / denom * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def _lang(args: dict, *texts: Any) -> str:
    explicit = str(args.get("language") or "").strip().lower()
    if explicit in {"lt", "en"}:
        return explicit
    joined = " ".join(str(t) for t in texts if t)
    return "lt" if any(ch in LT_LETTERS for ch in joined) else "en"


def _rows(raw: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ToolError(f"{label} must be a list of objects")
    out = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ToolError(f"{label}[{i}] must be an object")
        out.append(row)
    return out


def _count(value: Any, label: str, *, minimum: float = 0.0) -> int:
    n = _num(value, label, required=True, minimum=minimum)
    if not float(n).is_integer():
        raise ToolError(f"{label} must be a whole number, got {n:g}")
    return int(n)


def _share_block(k: int, n: int) -> dict[str, Any]:
    lo, hi = _wilson(k, n)
    return {"k": k, "n": n, "share_pct": _pct(k / n) if n else None, "ci95_pct": [_pct(lo), _pct(hi)] if n else None}


def _natural_days(days: int) -> str:
    if days < 14:
        return f"{days} day{'s' if days != 1 else ''}"
    if days < 60:
        weeks = round(days / 7)
        return f"{weeks} week{'s' if weeks != 1 else ''}"
    months = max(2, round(days / 30.4375))
    if months >= 24 and months % 12 == 0:
        return f"{months // 12} years"
    return f"{months} months"


def _natural_days_lt(days: int) -> str:
    phrase = _natural_days(days)
    number, unit = phrase.split(" ", 1)
    n = int(number)
    forms = {
        "day": ("dieną", "dienas", "dienų"),
        "week": ("savaitę", "savaites", "savaičių"),
        "month": ("mėnesį", "mėnesius", "mėnesių"),
        "year": ("metus", "metus", "metų"),
    }
    key = unit.rstrip("s")
    last, last_two = n % 10, n % 100
    if last == 1 and last_two != 11:
        form = forms[key][0]
    elif 2 <= last <= 9 and not 11 <= last_two <= 19:
        form = forms[key][1]
    else:
        form = forms[key][2]
    return f"{n} {form}"


# --- wtp: Van Westendorp and the fake door --------------------------------

WTP_KEYS = ("too_cheap", "cheap", "expensive", "too_expensive")
WTP_ALIASES = {
    "too_cheap": ("too_cheap", "toocheap", "too cheap", "per_pigu"),
    "cheap": ("cheap", "bargain", "pigu"),
    "expensive": ("expensive", "brangu"),
    "too_expensive": ("too_expensive", "tooexpensive", "too expensive", "per_brangu"),
}


def _wtp_row(row: dict[str, Any], i: int) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in WTP_KEYS:
        value = None
        for alias in WTP_ALIASES[key]:
            if alias in row and row[alias] not in (None, ""):
                value = row[alias]
                break
        out[key] = _num(value, f"responses[{i}].{key}", required=True, minimum=0.0)
    return out


def _curve_share(values: list[float], price: float, *, at_least: bool) -> float:
    if at_least:
        return sum(1 for v in values if v >= price) / len(values)
    return sum(1 for v in values if v <= price) / len(values)


def _crossing(prices: list[float], a: list[float], b: list[float]) -> float | None:
    """Price where the two piecewise-linear curves a and b cross; linear interpolation inside the segment."""
    for i in range(len(prices)):
        d = a[i] - b[i]
        if abs(d) < 1e-12:
            return prices[i]
        if i + 1 < len(prices):
            d_next = a[i + 1] - b[i + 1]
            if d * d_next < 0:
                frac = d / (d - d_next)
                return prices[i] + frac * (prices[i + 1] - prices[i])
    return None


def _van_westendorp(args: dict, out: dict[str, Any]) -> dict[str, Any] | None:
    responses_raw = args.get("responses")
    cumulative = args.get("cumulative")
    if not responses_raw and not cumulative:
        return None
    n: int | None
    if responses_raw:
        rows = _rows(responses_raw, "responses")
        kept: list[dict[str, float]] = []
        dropped = 0
        for i, row in enumerate(rows):
            parsed = _wtp_row(row, i)
            if not parsed["too_cheap"] <= parsed["cheap"] <= parsed["expensive"] <= parsed["too_expensive"]:
                dropped += 1
                continue
            kept.append(parsed)
        if dropped:
            out["warnings"].append(f"{dropped} response(s) dropped: the four prices were not in the order too cheap ≤ cheap ≤ expensive ≤ too expensive.")
        if len(kept) < 2:
            raise ToolError("Van Westendorp needs at least two consistent responses with too_cheap, cheap, expensive and too_expensive")
        n = len(kept)
        prices = sorted({v for r in kept for v in r.values()})
        curves = {
            "too_cheap": [_curve_share([r["too_cheap"] for r in kept], p, at_least=True) for p in prices],
            "cheap": [_curve_share([r["cheap"] for r in kept], p, at_least=True) for p in prices],
            "expensive": [_curve_share([r["expensive"] for r in kept], p, at_least=False) for p in prices],
            "too_expensive": [_curve_share([r["too_expensive"] for r in kept], p, at_least=False) for p in prices],
        }
    else:
        if not isinstance(cumulative, dict):
            raise ToolError("cumulative must be an object: {prices, too_cheap, cheap, expensive, too_expensive} with shares in percent")
        prices = [_num(p, "cumulative.prices", required=True) for p in cumulative.get("prices") or []]
        if len(prices) < 2 or prices != sorted(prices):
            raise ToolError("cumulative.prices must be at least two prices in rising order")
        curves = {}
        for key in WTP_KEYS:
            series = cumulative.get(key)
            if not isinstance(series, list) or len(series) != len(prices):
                raise ToolError(f"cumulative.{key} must list one share (percent) per price")
            vals = [_num(v, f"cumulative.{key}", required=True, minimum=0.0) for v in series]
            curves[key] = [v / 100 if max(vals) > 1 else v for v in vals]
        n_raw = _num(cumulative.get("n"), "cumulative.n", minimum=1.0)
        n = int(n_raw) if n_raw else None
        if n is None:
            out["missing"].append("cumulative.n")
    not_cheap = [1 - v for v in curves["cheap"]]
    not_expensive = [1 - v for v in curves["expensive"]]
    points: dict[str, float | None] = {
        "pmc": _crossing(prices, curves["too_cheap"], not_cheap),
        "opp": _crossing(prices, curves["too_cheap"], curves["too_expensive"]),
        "ipp": _crossing(prices, curves["cheap"], curves["expensive"]),
        "pme": _crossing(prices, curves["too_expensive"], not_expensive),
    }
    for key, value in list(points.items()):
        if value is None:
            out["warnings"].append(f"{key.upper()} could not be found: the curves do not cross inside the price range asked about.")
        else:
            points[key] = round(value, 2)
    if n is not None and n < WTP_MIN_N:
        out["warnings"].append(f"Only {n} respondents: Van Westendorp points move a lot below {WTP_MIN_N}; treat the range as a first hint, not a price.")
    if points["pmc"] is not None and points["pme"] is not None and points["pmc"] > points["pme"]:
        out["warnings"].append("PMC lies above PME: the answers are inconsistent as a group; the acceptable range is not usable.")
    return {
        "n": n,
        "points": {
            "pmc": points["pmc"],
            "opp": points["opp"],
            "ipp": points["ipp"],
            "pme": points["pme"],
            "labels": {
                "pmc": "point of marginal cheapness (too cheap × not cheap)",
                "opp": "optimal price point (too cheap × too expensive)",
                "ipp": "indifference price point (cheap × expensive)",
                "pme": "point of marginal expensiveness (too expensive × not expensive)",
            },
        },
        "acceptable_range": [points["pmc"], points["pme"]] if points["pmc"] is not None and points["pme"] is not None else None,
        "curves": {"prices": prices, **{k: [_pct(v) for v in vals] for k, vals in curves.items()}},
    }


def _wtp_questions(language: str, offer: str) -> list[dict[str, str]]:
    if language == "lt":
        qs = [
            ("too_cheap", f"Kokia {offer} kaina būtų tokia maža, kad suabejotumėte kokybe?"),
            ("cheap", f"Kokia {offer} kaina jums atrodytų geras pirkinys?"),
            ("expensive", f"Nuo kokios kainos {offer} atrodytų brangu, bet dar svarstytumėte?"),
            ("too_expensive", f"Nuo kokios kainos {offer} būtų per brangu, kad apskritai svarstytumėte?"),
        ]
    else:
        qs = [
            ("too_cheap", f"At what price would {offer} be so cheap that you would doubt its quality?"),
            ("cheap", f"At what price would {offer} be a bargain?"),
            ("expensive", f"At what price would {offer} start to seem expensive, though you would still consider it?"),
            ("too_expensive", f"At what price would {offer} be too expensive to consider at all?"),
        ]
    return [{"id": k, "text": t, "type": "number"} for k, t in qs]


def _fake_door(args: dict, out: dict[str, Any], language: str, offer: str) -> dict[str, Any] | None:
    block = args.get("fake_door")
    price_points = args.get("price_points") or (block.get("price_points") if isinstance(block, dict) else None)
    if block is None and not price_points and args.get("baseline_rate") in (None, ""):
        return None
    block = block if isinstance(block, dict) else {}
    points = [_num(p, "price_points", required=True, minimum=0.0) for p in (price_points or [])]
    baseline = _num(block.get("baseline_rate", args.get("baseline_rate")), "fake_door.baseline_rate")
    weekly = _num(block.get("weekly_traffic", args.get("weekly_traffic")), "fake_door.weekly_traffic", minimum=0.0)
    effect = _num(block.get("minimum_effect", args.get("minimum_effect")), "fake_door.minimum_effect")
    effect = 25.0 if effect is None else effect
    cost = _num(block.get("delivery_cost", args.get("delivery_cost")), "fake_door.delivery_cost", minimum=0.0)
    disclosure = (
        "Ši paslauga dar nesiūloma – matuojame susidomėjimą. Mokėti nereikės; palikite el. paštą ir pranešime, kai ji atsiras."
        if language == "lt"
        else "This offer is not available yet; we are measuring interest. You will not be charged; leave your e-mail and we will tell you when it launches."
    )
    plan: dict[str, Any] = {"price_points": points, "disclosure": disclosure, "cells": [], "sample_per_cell": None, "weeks": None, "decision_rule": None}
    if cost is not None:
        below = [p for p in points if p < cost]
        if below:
            out["warnings"].append(f"below_cost: price point(s) {', '.join(f'{p:g}' for p in below)} sit under the delivery cost {cost:g}.")
    if baseline is None:
        out["missing"].append("fake_door.baseline_rate")
        plan["status"] = "insufficient_inputs: baseline_rate missing (expected click-to-waitlist rate in percent)"
        return plan
    variants = max(2, len(points))
    sized = experiment_plan(
        {
            "baseline_rate": baseline,
            "minimum_effect": effect,
            "effect_kind": "relative",
            "weekly_traffic": weekly,
            "variants": variants,
            "alpha": args.get("alpha"),
            "power": args.get("power"),
            "metric": "waitlist rate",
        }
    )
    plan["sample_per_cell"] = sized["sample_per_variant"]
    plan["total_sample"] = sized["total_sample"]
    plan["weeks"] = sized.get("weeks")
    plan["cells"] = [{"price": p, "n_required": sized["sample_per_variant"]} for p in points] or [{"price": None, "n_required": sized["sample_per_variant"]} for _ in range(variants)]
    out["warnings"].extend(sized.get("warnings", []))
    if weekly is None:
        out["missing"].append("fake_door.weekly_traffic")
    target = sized["inputs"]["target_rate_pct"]
    anchor = f"{points[len(points) // 2]:g}" if points else "the tested price"
    plan["decision_rule"] = (
        f"Fix the sample at {sized['sample_per_variant']:,} visitors per price cell before the page goes live. Proceed with {offer} if the cell at {anchor} "
        f"joins the waitlist at {target:g} % or more ({baseline:g} % expected, +{effect:g} % relative); otherwise record the cell rates and do not quote a price as proven. "
        "Every cell carries the disclosure sentence; no money is taken."
    )
    plan["status"] = "ok" if weekly else "duration unknown: weekly_traffic missing"
    return plan


def wtp(args: dict) -> dict[str, Any]:
    """Willingness to pay: Van Westendorp crossing points from responses, and a fake-door plan.

    Inputs: ``responses`` ``[{too_cheap, cheap, expensive, too_expensive}]`` or ``cumulative``
    ``{prices, too_cheap, cheap, expensive, too_expensive, n}``; ``offer``; ``fake_door``
    ``{baseline_rate, minimum_effect (25 % relative), weekly_traffic, price_points, delivery_cost}``;
    ``language`` lt|en.
    """
    offer = str(args.get("offer") or args.get("service") or ("paslauga" if _lang(args) == "lt" else "the offer")).strip()
    language = _lang(args, offer)
    out: dict[str, Any] = {"mode": "wtp", "offer": offer, "language": language, "missing": [], "warnings": []}
    vw = _van_westendorp(args, out)
    out["van_westendorp"] = vw
    out["survey_questions"] = _wtp_questions(language, offer) if vw is None else None
    out["fake_door"] = _fake_door(args, out, language, offer)
    bits = []
    if vw:
        pts = vw["points"]
        if vw["acceptable_range"]:
            n_text = vw["n"] if vw["n"] is not None else "?"
            bits.append(f"Van Westendorp on n = {n_text}: acceptable range {pts['pmc']:g}–{pts['pme']:g}, indifference {pts['ipp']:g}, optimal {pts['opp']:g}.")
        else:
            bits.append("Van Westendorp: the curves do not all cross inside the prices asked; widen the price range.")
    fd = out["fake_door"]
    if fd:
        if fd.get("sample_per_cell"):
            cells = f"{len(fd['price_points'])} price cells" if fd["price_points"] else "2 cells"
            bits.append(f"Fake door: {fd['sample_per_cell']:,} visitors per cell, {cells}" + (f", about {fd['weeks']} weeks." if fd.get("weeks") else "; duration unknown until weekly_traffic is given."))
        else:
            bits.append(f"Fake door: {fd['status']}.")
    if vw is None and fd is None:
        out["missing"].append("responses or fake_door")
        bits.append("No responses and no fake-door inputs: the four Van Westendorp questions are returned to field first.")
    out["method"] = (
        "Van Westendorp price sensitivity meter: four cumulative curves on the respondents' own prices (too cheap and cheap read downward, expensive and too expensive upward), "
        "crossings by linear interpolation between observed prices; PMC = too cheap × not cheap, OPP = too cheap × too expensive, IPP = cheap × expensive, PME = too expensive × not expensive. "
        "Fake door sized with calc.experiment_plan (normal approximation, Bonferroni across price cells)."
    )
    out["summary"] = " ".join(bits)
    return out


# --- concept: reading a fielded concept test --------------------------------

DEFAULT_MIN_COMPREHENSION = 0.7
DEFAULT_TOP_TWO_BOX_MIN = 0.4
CONCEPT_MIN_N = 30
YES_WORDS = {"1", "true", "yes", "y", "taip", "understood", "correct", "suprato", "teisingai", "ok"}
NO_WORDS = {"0", "false", "no", "n", "ne", "wrong", "incorrect", "nesuprato", "klaidingai", ""}


def _understood(value: Any, keywords: list[str]) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in YES_WORDS:
        return True
    if text in NO_WORDS:
        return False
    if keywords:
        return any(k in text for k in keywords)
    return None


def _empty_share() -> dict[str, Any]:
    return {"k": 0, "n": 0, "share_pct": None, "ci95_pct": None}


def concept(args: dict) -> dict[str, Any]:
    """Read out a concept test: comprehension first, then top-two-box intent among those who understood.

    Inputs: ``responses`` ``[{concept?, understood|comprehension (bool, 0/1, yes/no or the open benefit
    answer), intent 1–5}]``; ``benefit_keywords`` to code open answers; ``min_comprehension`` (0.7);
    ``top_two_box_min`` (0.4). The instrument itself comes from research_plan kind concept_test.
    """
    rows = _rows(args.get("responses"), "responses")
    if not rows:
        raise ToolError("responses is empty: give one row per respondent with understood and intent")
    keywords = [str(k).strip().lower() for k in (args.get("benefit_keywords") or []) if str(k).strip()]
    min_comp = _num(args.get("min_comprehension"), "min_comprehension", minimum=0.0)
    t2b_min = _num(args.get("top_two_box_min"), "top_two_box_min", minimum=0.0)
    defaults_used = [k for k, v in (("min_comprehension", min_comp), ("top_two_box_min", t2b_min)) if v is None]
    min_comp = DEFAULT_MIN_COMPREHENSION if min_comp is None else min_comp
    t2b_min = DEFAULT_TOP_TWO_BOX_MIN if t2b_min is None else t2b_min
    if min_comp > 1 or t2b_min > 1:
        raise ToolError("min_comprehension and top_two_box_min are fractions between 0 and 1")

    has_understood = any(any(k in row for k in ("understood", "comprehension", "benefit_answer")) for row in rows)
    if not has_understood:
        raise ToolError(
            "unsuitable: comprehension not recorded (no understood column), so intent is not reported. Code each open benefit answer as understood yes/no first."
        )
    out: dict[str, Any] = {
        "mode": "concept",
        "missing": [],
        "warnings": [],
        "decision_rule": {"min_comprehension": min_comp, "top_two_box_min": t2b_min, "defaults_used": defaults_used, "min_n": CONCEPT_MIN_N},
    }
    by_concept: dict[str, dict[str, Any]] = {}
    uncoded = 0
    bad_intent = 0
    for i, row in enumerate(rows):
        name = str(row.get("concept") or row.get("name") or "concept").strip()
        cell = by_concept.setdefault(name, {"n": 0, "understood": 0, "t2b_all": 0, "t2b_understood": 0, "intent_sum": 0.0, "intent_n": 0})
        cell["n"] += 1
        raw_u = row.get("understood", row.get("comprehension", row.get("benefit_answer")))
        u = _understood(raw_u, keywords) if raw_u is not None else None
        if u is None:
            uncoded += 1
        else:
            cell["understood"] += int(u)
        intent = _num(row.get("intent"), f"responses[{i}].intent")
        if intent is None or not 1 <= intent <= 5:
            bad_intent += 1
            continue
        cell["intent_n"] += 1
        cell["intent_sum"] += intent
        if intent >= 4:
            cell["t2b_all"] += 1
            if u:
                cell["t2b_understood"] += 1
    if uncoded:
        out["warnings"].append(f"{uncoded} answer(s) could not be coded as understood or not; give benefit_keywords or code them by hand. They count as not understood for the shares.")
    if bad_intent:
        out["warnings"].append(f"{bad_intent} row(s) without an intent of 1–5 are left out of the intent shares.")
    concepts = []
    for name, c in by_concept.items():
        comp = _share_block(c["understood"], c["n"])
        t2b_u = _share_block(c["t2b_understood"], c["understood"]) if c["understood"] else _empty_share()
        t2b_all = _share_block(c["t2b_all"], c["intent_n"]) if c["intent_n"] else _empty_share()
        comp_ok = c["understood"] / c["n"] >= min_comp - 1e-9
        t2b_ok = c["understood"] > 0 and c["t2b_understood"] / c["understood"] >= t2b_min - 1e-9
        if c["n"] < CONCEPT_MIN_N:
            verdict = "insufficient data"
            out["warnings"].append(f"{name}: {c['n']} respondents is below {CONCEPT_MIN_N}; the verdict is withheld and the shares are indicative.")
        elif not comp_ok:
            verdict = "rework message"
        elif not t2b_ok:
            verdict = "rework concept"
        else:
            verdict = "proceed"
        if c["understood"] and t2b_all["share_pct"] is not None:
            intent_text = (
                f"would buy (4–5) {t2b_u['share_pct']:g} % among those who understood ({c['t2b_understood']}/{c['understood']}, "
                f"95 % CI {t2b_u['ci95_pct'][0]:g}–{t2b_u['ci95_pct'][1]:g} %), {t2b_all['share_pct']:g} % of all"
            )
        else:
            intent_text = "intent not reported: nobody understood or no intent answers"
        concepts.append(
            {
                "concept": name,
                "n": c["n"],
                "comprehension": comp,
                "comprehension_passes": comp_ok,
                "top_two_box_understood": t2b_u,
                "top_two_box_all": t2b_all,
                "top_two_box_passes": t2b_ok,
                "mean_intent": round(c["intent_sum"] / c["intent_n"], 2) if c["intent_n"] else None,
                "verdict": verdict,
                "sentence": f"{name}: understood the benefit {comp['share_pct']:g} % ({c['understood']}/{c['n']}); {intent_text} — {verdict}.",
            }
        )
    out["concepts"] = concepts
    out["method"] = (
        "Comprehension share = respondents coded as restating the intended benefit / all; top-two-box = intent 4 or 5, reported first among those who understood "
        "(the decision figure) and then among all; Wilson 95 % intervals; verdict against min_comprehension and top_two_box_min"
        + (f" (defaults: {', '.join(defaults_used)})" if defaults_used else "")
        + f"; withheld below {CONCEPT_MIN_N} respondents."
    )
    out["summary"] = " ".join(c["sentence"] for c in concepts)
    return out


# --- test_market: rehearsing a rollout in one city -------------------------

MIX_ELEMENTS = ("product", "price", "place", "promotion")
MIN_TEST_MARKET_WEEKS = 4


def _parse_date(value: Any, label: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ToolError(f"{label} must be a date like 2026-03-01, got {value!r}") from exc


def _maybe_num(value: Any) -> float | None:
    """A number when the value is one; text such as 'high' or 'nerasta' stays text."""
    if isinstance(value, (list, dict)):
        return None
    try:
        return _num(value, "value")
    except ToolError:
        return None


def test_market(args: dict) -> dict[str, Any]:
    """A structured test-market plan with representativeness flags; it never predicts the national result.

    Inputs: ``market {name, population, population_share_pct, income_index}`` (income vs national, 100 = equal);
    ``mix {product, price, place[], promotion[]}``; ``duration_weeks``; ``start_date``; ``kpis [{name, target, unit}]``;
    ``local_conditions [{factor, local_value, national_value, source_url}]``; ``language``.
    """
    market = args.get("market") if isinstance(args.get("market"), dict) else {}
    mix = args.get("mix") if isinstance(args.get("mix"), dict) else {}
    name = str(market.get("name") or args.get("city") or "").strip()
    out: dict[str, Any] = {"mode": "test_market", "missing": [], "warnings": [], "flags": []}
    if not name:
        out["missing"].append("market.name")
        name = "the test market"
    share = _num(market.get("population_share_pct"), "market.population_share_pct", minimum=0.0)
    income = _num(market.get("income_index", market.get("income_index_vs_national")), "market.income_index")
    population = _num(market.get("population"), "market.population", minimum=0.0)
    duration = _num(args.get("duration_weeks"), "duration_weeks", minimum=0.0)
    start = _parse_date(args.get("start_date"), "start_date")

    present: dict[str, Any] = {}
    missing_elements = []
    for element in MIX_ELEMENTS:
        value = mix.get(element, args.get(element))
        if isinstance(value, list):
            value = [str(v).strip() for v in value if str(v).strip()]
        empty = value in (None, "", [], {})
        present[element] = None if empty else value
        if empty:
            missing_elements.append(element)
    mix_complete = not missing_elements
    if not mix_complete:
        out["flags"].append("mix_incomplete")
        out["warnings"].append(f"Marketing mix incomplete: missing {', '.join(missing_elements)}. A test market must run every element as it would nationally.")

    representativeness = []
    if share is None:
        out["missing"].append("market.population_share_pct")
    elif share < MIN_SHARE_PCT:
        out["flags"].append("share_below_1pct")
        representativeness.append(f"{name} holds {share:g} % of the population: below {MIN_SHARE_PCT:g} %, too small for a national read.")
    if income is None:
        out["missing"].append("market.income_index")
    elif not INCOME_BAND[0] <= income <= INCOME_BAND[1]:
        out["flags"].append("income_outside_band")
        direction = "richer" if income > INCOME_BAND[1] else "poorer"
        representativeness.append(f"Income index {income:g} vs national 100: {name} is {direction} than the country ({INCOME_BAND[0]:g}–{INCOME_BAND[1]:g} counts as representative); price acceptance will not carry over.")
    if duration is None:
        out["missing"].append("duration_weeks")
    elif duration < MIN_TEST_MARKET_WEEKS:
        out["flags"].append("short_duration")
        out["warnings"].append(f"{duration:g} weeks is under {MIN_TEST_MARKET_WEEKS}: too short to see a repeat purchase.")

    kpis_raw = _rows(args.get("kpis") or [], "kpis")
    kpis = []
    for i, k in enumerate(kpis_raw):
        kname = str(k.get("name") or f"KPI {i + 1}").strip()
        target = _num(k.get("target"), f"kpis '{kname}' target")
        if target is None:
            out["missing"].append(f"kpis.{kname}.target")
        kpis.append({"name": kname, "target": target, "unit": str(k.get("unit") or "").strip() or None, "readout": None})
    if not kpis:
        out["missing"].append("kpis")
        out["warnings"].append("No KPIs with targets: fix trial rate, repeat rate and CAC targets before launch or the pilot cannot fail.")

    conditions_raw = _rows(args.get("local_conditions") or [], "local_conditions")
    conditions = []
    unsourced = 0
    for c in conditions_raw:
        row = {
            "factor": str(c.get("factor") or "").strip(),
            "local_value": c.get("local_value"),
            "national_value": c.get("national_value"),
            "source_url": str(c.get("source_url") or "").strip() or None,
        }
        if not row["source_url"]:
            unsourced += 1
        local_n, nat_n = _maybe_num(row["local_value"]), _maybe_num(row["national_value"])
        if local_n is not None and nat_n not in (None, 0):
            row["ratio_vs_national"] = round(local_n / nat_n, 2)
        conditions.append(row)
    if not conditions:
        out["flags"].append("no_local_conditions")
        out["warnings"].append("local_conditions is empty: list what makes this market unlike the country (population share, income, competitors, media cost, seasonality, pickup coverage).")
    if unsourced:
        out["flags"].append("unsourced_condition")
        out["warnings"].append(f"{unsourced} local condition(s) without source_url: write 'nerasta' / 'not found' rather than a guess.")

    readouts: dict[str, Any] = {}
    if duration:
        weeks = int(math.ceil(duration))
        mid = max(1, weeks // 2)
        points = {"baseline": 0, "midpoint": mid, "end": weeks, "repeat_check": weeks + 4}
        for key, offset in points.items():
            readouts[key] = {"week": offset, "date": (start + timedelta(weeks=offset)).isoformat() if start else None}
        for k in kpis:
            k["readout"] = "end" if "repeat" not in k["name"].lower() and "pakart" not in k["name"].lower() else "repeat_check"
    else:
        readouts = {"note": "duration_weeks missing: no readout dates"}

    limits = [f"{c['factor']}: {c['local_value']} locally vs {c['national_value']} nationally" + ("" if c["source_url"] else " (unsourced)") for c in conditions if c["factor"]]
    limits.extend(representativeness)
    plan = {
        "market": {"name": name, "population": population, "population_share_pct": share, "income_index": income},
        "mix": present,
        "duration_weeks": duration,
        "start_date": start.isoformat() if start else None,
        "readouts": readouts,
    }
    out.update({"plan": plan, "mix_complete": mix_complete, "missing_elements": missing_elements, "kpis": kpis, "local_conditions": conditions, "generalisation_limits": limits})
    out["method"] = (
        "Structural check: all four mix elements present; population share and income index against fixed bands "
        f"(share ≥ {MIN_SHARE_PCT:g} %, income index {INCOME_BAND[0]:g}–{INCOME_BAND[1]:g}); readout weeks at 0, midpoint, end and end + 4 for repeat; no national projection is computed."
    )
    head = f"Test market {name}" + (f", {duration:g} weeks" if duration else "")
    head += ": mix complete" if mix_complete else f": mix incomplete ({', '.join(missing_elements)} missing)"
    head += f"; {len(kpis)} KPI(s)" + (f"; flags {', '.join(out['flags'])}" if out["flags"] else "; no flags")
    head += f". What {name} will not predict: {len(limits)} listed condition(s)."
    out["summary"] = head
    return out


# --- conjoint: a balanced choice design and a count-based readout -------------

DEFAULT_TASKS = 10
DEFAULT_ALTERNATIVES = 2
DESIGN_ROW_CAP = 600
LOGIT_ROWS_PER_PARAMETER = 10
CONJOINT_MIN_RESPONDENTS = 50


def _attributes(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict) or not raw:
        raise ToolError("attributes must be an object {name: [levels]} with at least two attributes")
    attrs: dict[str, list[str]] = {}
    for name, levels in raw.items():
        if not isinstance(levels, list):
            raise ToolError(f"attribute '{name}' must list its levels")
        clean = [str(v).strip() for v in levels if str(v).strip()]
        if len(clean) < 2:
            raise ToolError(f"attribute '{name}' needs at least 2 levels, got {len(clean)}")
        if len(set(clean)) != len(clean):
            raise ToolError(f"attribute '{name}' repeats a level")
        attrs[str(name).strip()] = clean
    if len(attrs) < 2:
        raise ToolError("a conjoint needs at least two attributes")
    return attrs


def conjoint_design(args: dict) -> dict[str, Any]:
    """Balanced choice tasks, returned inline as rows.

    Inputs: ``attributes {name: [levels]}``, ``n_respondents``, ``tasks_per_respondent`` (10), ``alternatives`` (2),
    ``seed`` (7), ``max_rows`` (600, the rest is regenerable from the seed).
    """
    attrs = _attributes(args.get("attributes"))
    n_resp = _count(args.get("n_respondents"), "n_respondents", minimum=1)
    tasks = _count(args.get("tasks_per_respondent", DEFAULT_TASKS), "tasks_per_respondent", minimum=1)
    alts = _count(args.get("alternatives", DEFAULT_ALTERNATIVES), "alternatives", minimum=2)
    seed = _count(args.get("seed", 7), "seed")
    cap = _count(args.get("max_rows", DESIGN_ROW_CAP), "max_rows", minimum=1)
    names = list(attrs)
    profiles: list[tuple[str, ...]] = [()]
    for name in names:
        profiles = [p + (level,) for p in profiles for level in attrs[name]]
    n_profiles = len(profiles)
    if alts > n_profiles:
        raise ToolError(f"alternatives ({alts}) exceed the {n_profiles} distinct profiles the attributes allow")
    rng = random.Random(seed)
    order = list(range(n_profiles))
    rng.shuffle(order)
    cursor = 0
    rows: list[dict[str, Any]] = []
    balance: dict[str, dict[str, int]] = {name: {level: 0 for level in attrs[name]} for name in names}
    for r in range(1, n_resp + 1):
        for t in range(1, tasks + 1):
            chosen_idx: list[int] = []
            while len(chosen_idx) < alts:
                if cursor >= n_profiles:
                    rng.shuffle(order)
                    cursor = 0
                idx = order[cursor]
                cursor += 1
                if idx in chosen_idx:
                    continue
                chosen_idx.append(idx)
            for a, idx in enumerate(chosen_idx, start=1):
                levels = dict(zip(names, profiles[idx]))
                for name, level in levels.items():
                    balance[name][level] += 1
                rows.append({"respondent": r, "task": t, "alternative": a, "levels": levels})
    total_rows = len(rows)
    n_tasks = n_resp * tasks
    out: dict[str, Any] = {"mode": "conjoint_design", "missing": [], "warnings": []}
    if n_profiles > n_tasks / 3:
        out["warnings"].append(f"{n_profiles} distinct profiles against {n_tasks} tasks: each profile is shown fewer than three times; add respondents or tasks or drop a level.")
    if n_resp < CONJOINT_MIN_RESPONDENTS:
        out["warnings"].append(f"{n_resp} respondents is under {CONJOINT_MIN_RESPONDENTS}: the analysis will return counts only, not estimated weights.")
    parameters = sum(len(v) - 1 for v in attrs.values())
    balance_table = []
    for name in names:
        counts = balance[name]
        lo, hi = min(counts.values()), max(counts.values())
        balance_table.append({"attribute": name, "levels": counts, "min": lo, "max": hi, "imbalance_pct": _pct((hi - lo) / hi) if hi else 0.0})
    truncated = total_rows > cap
    if truncated:
        out["warnings"].append(f"rows cut to the first {cap} of {total_rows}; the full design regenerates from seed {seed} with the same inputs.")
    out.update(
        {
            "attributes": attrs,
            "n_respondents": n_resp,
            "tasks_per_respondent": tasks,
            "alternatives": alts,
            "n_tasks": n_tasks,
            "n_profiles": n_profiles,
            "parameters": parameters,
            "seed": seed,
            "n_rows": total_rows,
            "rows_truncated": truncated,
            "rows": rows[:cap],
            "level_balance": balance_table,
            "columns": ["respondent", "task", "alternative", *names, "chosen"],
        }
    )
    out["method"] = (
        "Full-factorial profiles dealt in a seeded shuffled cycle so every level appears equally often (±1 per cycle); "
        "no two alternatives in a task are identical; balance is reported from the rows actually generated."
    )
    worst = max(balance_table, key=lambda b: b["imbalance_pct"])
    out["summary"] = (
        f"{n_tasks} choice tasks ({n_resp} × {tasks}) with {alts} alternatives each, {total_rows} rows, {n_profiles} distinct profiles, {parameters} parameters to estimate; "
        f"worst level imbalance {worst['imbalance_pct']:g} % ({worst['attribute']}). Add a 'chosen' column (1 for the picked alternative) and return the rows to conjoint_analyze."
    )
    return out


def conjoint_analyze(args: dict) -> dict[str, Any]:
    """Count-based part-worths from choice rows ``[{respondent, task, alternative, chosen, levels {attr: level}}]``."""
    rows = _rows(args.get("choices", args.get("rows", args.get("responses"))), "choices")
    if not rows:
        raise ToolError("choices is empty: give one row per alternative shown, with chosen 0/1 and levels")
    out: dict[str, Any] = {"mode": "conjoint_analyze", "missing": [], "warnings": []}
    shown: dict[str, dict[str, int]] = {}
    picked: dict[str, dict[str, int]] = {}
    respondents: set[str] = set()
    tasks: set[tuple[str, str]] = set()
    n_chosen = 0
    for i, row in enumerate(rows):
        levels = row.get("levels")
        if not isinstance(levels, dict) or not levels:
            flat = {k: v for k, v in row.items() if k not in {"respondent", "task", "alternative", "chosen"}}
            if not flat:
                raise ToolError(f"choices[{i}] has no levels: give levels {{attribute: level}}")
            levels = flat
        chosen = _flag(row.get("chosen"), False)
        n_chosen += int(chosen)
        resp = str(row.get("respondent", ""))
        respondents.add(resp)
        tasks.add((resp, str(row.get("task", i))))
        for attr, level in levels.items():
            shown.setdefault(attr, {}).setdefault(str(level), 0)
            picked.setdefault(attr, {}).setdefault(str(level), 0)
            shown[attr][str(level)] += 1
            picked[attr][str(level)] += int(chosen)
    n_tasks = len(tasks)
    if n_chosen == 0:
        raise ToolError("no row has chosen = 1: mark the picked alternative in every task")
    if n_chosen != n_tasks:
        out["warnings"].append(f"{n_chosen} chosen rows against {n_tasks} tasks: every task should have exactly one chosen alternative.")
    parameters = sum(len(v) - 1 for v in shown.values())
    part_worths: dict[str, dict[str, Any]] = {}
    ranges: dict[str, float] = {}
    for attr, levels in shown.items():
        shares = {level: picked[attr][level] / n for level, n in levels.items() if n}
        if len(shares) < 2:
            out["warnings"].append(f"attribute '{attr}' has one level in the data: no weight can be read for it.")
            continue
        mean_share = sum(shares.values()) / len(shares)
        worths = {level: round(share - mean_share, 4) for level, share in shares.items()}
        part_worths[attr] = {
            "levels": {level: {"shown": levels[level], "chosen": picked[attr][level], "choice_share_pct": _pct(shares[level]), "part_worth": worths[level]} for level in levels},
            "range": round(max(worths.values()) - min(worths.values()), 4),
        }
        ranges[attr] = part_worths[attr]["range"]
    total_range = sum(ranges.values())
    importance = {attr: _pct(r / total_range) if total_range else None for attr, r in ranges.items()}
    status = "ok"
    if len(respondents) < CONJOINT_MIN_RESPONDENTS:
        status = "unsuitable"
        out["warnings"].append(f"unsuitable: {len(respondents)} respondents is under {CONJOINT_MIN_RESPONDENTS}; the counts are shown but must not be quoted as attribute weights.")
    if n_tasks < LOGIT_ROWS_PER_PARAMETER * parameters:
        out["warnings"].append(
            f"A logit fit needs about {LOGIT_ROWS_PER_PARAMETER} choices per parameter ({LOGIT_ROWS_PER_PARAMETER * parameters} for {parameters}); {n_tasks} are available, so only the count-based estimate is given."
        )
    out.update(
        {
            "status": status,
            "n_respondents": len(respondents),
            "n_choices": n_tasks,
            "n_rows": len(rows),
            "parameters": parameters,
            "estimate": "count-based choice shares, centred per attribute (not a logit fit)",
            "part_worths": part_worths,
            "importance_pct": importance,
        }
    )
    out["method"] = (
        "For each level: choice share = times chosen / times shown; part-worth = share minus the mean share of its attribute (centred, so a level's part-worth is in share points); "
        "attribute importance = range of part-worths / sum of ranges. This is a count estimate, labelled as such: no utilities, standard errors or log-likelihood are claimed."
    )
    ranked = sorted(importance.items(), key=lambda kv: -(kv[1] or 0))
    out["summary"] = (
        f"{n_tasks} choices from {len(respondents)} respondents, {parameters} parameters: count-based importance "
        + ", ".join(f"{a} {v:g} %" for a, v in ranked if v is not None)
        + (" — unsuitable for quoting weights (too few respondents)." if status == "unsuitable" else ".")
    )
    return out


# --- screener: who to recruit and how many to invite -------------------------


def screener(args: dict) -> dict[str, Any]:
    """Purchase-window screener: ``category``, ``purchase_cycle_days``, ``target_n``, ``incidence_pct``,
    ``window_days`` (defaults to the cycle), ``response_rate_pct`` (panel completes per invite), ``language``."""
    category = str(args.get("category") or "").strip()
    if not category:
        raise ToolError("category is required (what is bought, e.g. 'a new car' / 'naują automobilį' in the accusative)")
    cycle = _count(args.get("purchase_cycle_days"), "purchase_cycle_days", minimum=1)
    target_n = _count(args.get("target_n"), "target_n", minimum=1)
    incidence = _num(args.get("incidence_pct"), "incidence_pct", minimum=0.0)
    response = _num(args.get("response_rate_pct"), "response_rate_pct", minimum=0.0)
    window_raw = _num(args.get("window_days"), "window_days", minimum=1.0)
    window = int(window_raw) if window_raw is not None else cycle
    language = _lang(args, category)
    out: dict[str, Any] = {"mode": "screener", "missing": [], "warnings": [], "assumptions": []}
    if window < cycle:
        out["warnings"].append(f"window shorter than one purchase cycle: {window} days against a {cycle}-day cycle; most real buyers fall outside it.")
    phrase_en = _natural_days(window)
    phrase = _natural_days_lt(window) if language == "lt" else phrase_en
    if language == "lt":
        question = f"Ar planuojate įsigyti {category} per artimiausius {phrase}?" if not phrase.endswith(("dieną", "dienas", "dienų", "savaitę", "savaites", "savaičių")) else f"Ar planuojate įsigyti {category} per artimiausias {phrase}?"
        options = ["Taip", "Ne", "Nežinau"]
        qualify = ["Taip"]
        follow_up = f"Kada paskutinį kartą pirkote {category}? (per pastaruosius {_natural_days_lt(cycle)} / anksčiau / niekada)"
        out["assumptions"].append("Kategorijos pavadinimas įrašomas galininku (ką? – naują automobilį).")
    else:
        question = f"Do you plan to buy {category} in the next {phrase}?"
        options = ["Yes", "No", "Don't know"]
        qualify = ["Yes"]
        follow_up = f"When did you last buy {category}? (within the last {_natural_days(cycle)} / earlier / never)"
    if incidence is None:
        out["missing"].append("incidence_pct")
        out["warnings"].append("incidence unknown: gross invites cannot be estimated; give the share of the panel that qualifies.")
        gross_qualifying = None
    elif incidence <= 0:
        raise ToolError("incidence_pct must be above 0")
    else:
        gross_qualifying = math.ceil(target_n / (incidence / 100) - 1e-9)
        out["assumptions"].append(f"incidence {incidence:g} % as given; recruit {gross_qualifying:,} screened contacts to reach {target_n} qualified.")
    gross_invites = None
    if gross_qualifying is not None:
        if response is None:
            gross_invites = gross_qualifying
            out["assumptions"].append("response_rate_pct not given: invites equal screened contacts (every invitee answers the screener).")
        elif response <= 0:
            raise ToolError("response_rate_pct must be above 0")
        else:
            gross_invites = math.ceil(gross_qualifying / (response / 100) - 1e-9)
    out.update(
        {
            "category": category,
            "language": language,
            "purchase_cycle_days": cycle,
            "window_days": window,
            "window_phrase": phrase,
            "screener_question": question,
            "options": options,
            "qualify_answers": qualify,
            "follow_up_question": follow_up,
            "target_n": target_n,
            "incidence_pct": incidence,
            "gross_contacts": gross_qualifying,
            "gross_invites": gross_invites,
        }
    )
    out["method"] = "window = max(purchase cycle, requested window) rounded to a natural phrase; gross contacts = target_n / incidence; invites = contacts / response rate; no incidence is assumed when it is missing."
    out["summary"] = (
        f"Screen for a purchase window of {phrase_en} ({window} days, cycle {cycle}); "
        + (f"invite about {gross_invites:,} to reach {target_n} qualified at {incidence:g} % incidence." if gross_invites else f"gross invites not estimated: incidence unknown (target {target_n}).")
    )
    return out


# --- choice_study: second-choice presence test and the invented-brand control --

CHOICE_MIN_N = 30
DISCLOSURE = {"lt": "Tyrimo tikslais – prekės ženklas išgalvotas", "en": "Research only – this brand is fictional"}
LABEL_INVENTED = "simulated response (invented brand)"
LABEL_REAL = "stated preference (real brand)"


def _stimulus(block: Any, label: str, out: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(block, dict):
        raise ToolError(f"{label} must be an object {{name, price, size, attributes[]}}")
    name = str(block.get("name") or block.get("brand_name") or "").strip()
    if not name:
        raise ToolError(f"{label}.name is required")
    price = _num(block.get("price", block.get("price_eur")), f"{label}.price", minimum=0.0)
    attrs = [str(a).strip() for a in (block.get("attributes") or []) if str(a).strip()]
    if price is None:
        out["missing"].append(f"{label}.price")
    if not attrs:
        out["missing"].append(f"{label}.attributes")
    return {"name": name, "price": price, "size": str(block.get("size") or "").strip() or None, "attributes": attrs, "invented": _flag(block.get("invented"), False)}


def _card(stim: dict[str, Any], language: str) -> str:
    lines = [stim["name"]]
    if stim["size"]:
        lines.append(stim["size"])
    if stim["price"] is not None:
        lines.append(f"{stim['price']:.2f} €")
    lines.extend(f"• {a}" for a in stim["attributes"])
    if stim["invented"]:
        lines.append(DISCLOSURE[language])
    return "\n".join(lines)


def _choice_design(args: dict, out: dict[str, Any]) -> dict[str, Any]:
    favourites = [str(b).strip() for b in (args.get("favourite_brands") or []) if str(b).strip()]
    alternative = _stimulus(args.get("alternative"), "alternative", out)
    incumbent_raw = args.get("incumbent") or args.get("control_brand")
    incumbent = _stimulus(incumbent_raw, "incumbent", out) if incumbent_raw is not None else None
    arms_raw = args.get("arms")
    extra_arms = [_stimulus(a, f"arms[{i}]", out) for i, a in enumerate(arms_raw)] if isinstance(arms_raw, list) and arms_raw and all("shown" not in a for a in arms_raw if isinstance(a, dict)) else []
    language = _lang(args, alternative["name"], *alternative["attributes"], *favourites)
    n_target = _num(args.get("n_target"), "n_target", minimum=1.0)
    stimuli = [alternative] + ([incumbent] if incumbent else []) + extra_arms
    if incumbent:
        if alternative["price"] is not None and incumbent["price"] is not None and abs(alternative["price"] - incumbent["price"]) > 1e-9:
            raise ToolError(f"alternative is not equally described: price {alternative['price']:g} vs incumbent {incumbent['price']:g}; the cards must carry the same price")
        if alternative["attributes"] and incumbent["attributes"] and len(alternative["attributes"]) != len(incumbent["attributes"]):
            raise ToolError(f"alternative is not equally described: {len(alternative['attributes'])} attributes vs {len(incumbent['attributes'])} for the incumbent")
        if alternative["size"] and incumbent["size"] and alternative["size"].lower() != incumbent["size"].lower():
            out["warnings"].append("Sizes differ between the cards; the comparison is no longer like for like.")
    else:
        out["missing"].append("incumbent")
        out["warnings"].append("No incumbent card: the switch share has nothing to be read against.")
    if not favourites:
        out["missing"].append("favourite_brands")
    if n_target is None:
        out["missing"].append("n_target")
    elif n_target < CHOICE_MIN_N:
        out["warnings"].append(f"n_target {n_target:g} is under {CHOICE_MIN_N}: the interval will be too wide to act on.")
    cards = []
    for s in stimuli:
        text = _card(s, language)
        if s["invented"] and DISCLOSURE[language] not in text:
            raise ToolError("an invented brand card must carry the disclosure sentence")
        cards.append({"name": s["name"], "invented": s["invented"], "label": LABEL_INVENTED if s["invented"] else LABEL_REAL, "text": text})
    if language == "lt":
        steps = [
            "1. Pirmiausia užfiksuokite mėgstamiausią prekės ženklą kategorijoje (atviras klausimas, tada sąrašas).",
            "2. Parodykite dvi vienodos struktūros korteles: mėgstamiausią ir alternatyvą su ta pačia kaina, dydžiu ir tiek pat savybių; kortelių tvarką keiskite atsitiktinai.",
            "3. Klauskite: „Jei lentynoje būtų tik šie du, kurį pasirinktumėte?“ – vienas atsakymas, be „abu“.",
            "4. Išgalvoto prekės ženklo kortelė visada turi užrašą „" + DISCLOSURE["lt"] + "“.",
            "5. Rezultatus skaitykite kaip simuliaciją: „X % iš Y pasirinko alternatyvą“, ne „X % pereis“.",
        ]
    else:
        steps = [
            "1. Record the favourite brand in the category first (open answer, then a list).",
            "2. Show two cards of identical structure: the favourite and the alternative at the same price, size and attribute count; rotate card order at random.",
            "3. Ask: 'If only these two were on the shelf, which would you take?' — one answer, no 'both'.",
            "4. An invented brand's card always carries '" + DISCLOSURE["en"] + "'.",
            "5. Read results as a simulation: 'X % of Y chose the alternative', never 'X % will switch'.",
        ]
    out["method"] = "Design check only: equal price, equal attribute count and a disclosure line on invented brands are enforced; no share is estimated before results arrive."
    out["summary"] = f"{len(cards)} stimulus cards" + (f", target n = {n_target:g}" if n_target else "") + (f"; {len(out['missing'])} input(s) missing" if out["missing"] else "; cards are equally described") + "."
    return {"action": "design", "language": language, "favourite_brands": favourites, "stimulus_cards": cards, "design_steps": steps, "n_target": n_target}


def _choice_analyze(args: dict, out: dict[str, Any]) -> dict[str, Any]:
    arms_raw = args.get("arms")
    if not arms_raw and args.get("n_shown") is not None:
        arms_raw = [{"arm_id": "alternative", "brand_name": str(args.get("alternative_name") or "alternative"), "invented": _flag(args.get("invented"), False), "shown": args.get("n_shown"), "chosen": args.get("n_switched", args.get("n_chosen"))}]
    rows = _rows(arms_raw, "arms")
    if not rows:
        raise ToolError("arms is empty: give {arm_id, brand_name, invented, shown, chosen} per arm or n_shown with n_switched")
    language = _lang(args, *(r.get("brand_name", "") for r in rows))
    arms = []
    for i, r in enumerate(rows):
        name = str(r.get("brand_name") or r.get("name") or r.get("arm_id") or f"arm {i + 1}").strip()
        shown = _count(r.get("shown"), f"arm '{name}' shown", minimum=1)
        chosen = _count(r.get("chosen", r.get("switched")), f"arm '{name}' chosen")
        if chosen > shown:
            raise ToolError(f"arm '{name}': chosen ({chosen}) exceeds shown ({shown})")
        invented = _flag(r.get("invented"), False)
        block = _share_block(chosen, shown)
        if shown < CHOICE_MIN_N:
            out["warnings"].append(f"arm '{name}': n below {CHOICE_MIN_N}; interval too wide to act on.")
        if language == "lt":
            sentence = f"Šioje simuliacijoje {block['share_pct']:g} % iš {shown} pasirinko {name} (95 % PI {block['ci95_pct'][0]:g}–{block['ci95_pct'][1]:g} %)."
        else:
            sentence = f"In this simulation {block['share_pct']:g} % of {shown} buyers chose {name} (95 % CI {block['ci95_pct'][0]:g}–{block['ci95_pct'][1]:g} %)."
        if invented:
            sentence += " Simulated preference for the offer; not a sales forecast." if language == "en" else " Simuliuotas atsakas į pasiūlymą; ne pardavimų prognozė."
        arms.append({"arm_id": str(r.get("arm_id") or name), "brand_name": name, "invented": invented, "shown": shown, "chosen": chosen, "share_pct": block["share_pct"], "ci95_pct": block["ci95_pct"], "label": LABEL_INVENTED if invented else LABEL_REAL, "sentence": sentence})
    real = [a for a in arms if not a["invented"]]
    fake = [a for a in arms if a["invented"]]
    brand_lift = None
    if real and fake:
        brand_lift = round(real[0]["share_pct"] - fake[0]["share_pct"], 1)
        if len(real) > 1 or len(fake) > 1:
            out["warnings"].append("brand_lift_pp uses the first real and the first invented arm.")
    elif fake and not real:
        out["warnings"].append("no real-brand arm to compare against: the invented arm's share stands alone.")
    by_fav = args.get("by_favourite")
    per_brand = {}
    if isinstance(by_fav, dict):
        for brand, cell in by_fav.items():
            if not isinstance(cell, dict):
                raise ToolError(f"by_favourite['{brand}'] must be {{shown, switched}}")
            s = _count(cell.get("shown"), f"by_favourite '{brand}' shown", minimum=1)
            c = _count(cell.get("switched", cell.get("chosen")), f"by_favourite '{brand}' switched")
            if c > s:
                raise ToolError(f"by_favourite '{brand}': switched exceeds shown")
            per_brand[str(brand)] = _share_block(c, s)
            if s < CHOICE_MIN_N:
                out["warnings"].append(f"by_favourite '{brand}': n below {CHOICE_MIN_N}; interval too wide to act on.")
    out["method"] = "Share = chosen / shown per arm with a Wilson 95 % interval; invented arms are labelled as a simulated response to the offer; brand_lift_pp = real-brand share − invented-brand share (no test of the gap)."
    bits = [a["sentence"] for a in arms]
    if brand_lift is not None:
        bits.append(f"Brand lift ≈ {brand_lift:+g} pp (real brand over the invented one)." if language == "en" else f"Prekės ženklo priedas ≈ {brand_lift:+g} p. p.")
    out["summary"] = " ".join(bits)
    return {"action": "analyze", "language": language, "arms": arms, "per_favourite_brand": per_brand or None, "brand_lift_pp": brand_lift}


def choice_study(args: dict) -> dict[str, Any]:
    """Design (``alternative``, ``incumbent``, ``favourite_brands``, ``n_target``) or analyze
    (``arms [{arm_id, brand_name, invented, shown, chosen}]`` or ``n_shown`` + ``n_switched``); ``action`` forces one."""
    out: dict[str, Any] = {"mode": "choice_study", "missing": [], "warnings": []}
    action = str(args.get("action") or "").strip().lower()
    arms = args.get("arms")
    has_results = args.get("n_shown") is not None or (isinstance(arms, list) and arms and all(isinstance(a, dict) and "shown" in a for a in arms))
    if action == "analyze" or (not action and has_results):
        out.update(_choice_analyze(args, out))
    else:
        out.update(_choice_design(args, out))
    return out


# --- creative_test: holdouts, readouts, crossover, portfolio, factorial ---------

CARRYOVER_TOLERANCE_PCT = 10.0
DEFAULT_RESERVE_SHARE = 0.4
SCREENING_ALPHA = 0.1
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_TOO_SMALL = "too_small"
VERDICT_NOT_COMPARABLE = "not_comparable"


def _two_rates(c_n: int, c_k: int, t_n: int, t_k: int, *, alpha: float) -> dict[str, Any]:
    p1, p2 = c_k / c_n, t_k / t_n
    diff = p2 - p1
    se_ci = math.sqrt(p1 * (1 - p1) / c_n + p2 * (1 - p2) / t_n)
    pooled = (c_k + t_k) / (c_n + t_n)
    se_test = math.sqrt(pooled * (1 - pooled) * (1 / c_n + 1 / t_n))
    z = diff / se_test if se_test > 0 else (0.0 if diff == 0 else math.copysign(math.inf, diff))
    p = 2 * (1 - _Z.cdf(abs(z))) if math.isfinite(z) else 0.0
    half = _Z.inv_cdf(0.975) * se_ci
    return {
        "control_rate_pct": _rate_pct(p1),
        "treatment_rate_pct": _rate_pct(p2),
        "lift_abs_pp": _rate_pct(diff),
        "lift_rel_pct": _pct(diff / p1) if p1 else None,
        "ci95_abs_pp": [_rate_pct(diff - half), _rate_pct(diff + half)],
        "z": round(z, 3) if math.isfinite(z) else None,
        "p_value": round(p, 4),
        "significant": p < alpha,
        "_diff": diff,
        "_p1": p1,
    }


def _group(raw: Any, label: str) -> tuple[int, int]:
    if not isinstance(raw, dict):
        raise ToolError(f"{label} must be {{exposed, converted}}")
    n = _count(raw.get("exposed", raw.get("n")), f"{label}.exposed", minimum=1)
    k = _count(raw.get("converted", raw.get("conversions")), f"{label}.converted")
    if k > n:
        raise ToolError(f"{label}: converted ({k}) exceeds exposed ({n})")
    return n, k


def _holdout(args: dict, out: dict[str, Any]) -> dict[str, Any]:
    """Geo or audience split with a holdout: lift, incremental conversions, verdict (#451, #168)."""
    alpha, power = _alpha_power(args)
    metric = str(args.get("metric") or "conversion rate").strip()
    c_n, c_k = _group(args.get("control") or args.get("holdout"), "control")
    t_n, t_k = _group(args.get("treatment") or args.get("exposed_group"), "treatment")
    comparable = args.get("comparable")
    split = str(args.get("split") or "audience").strip().lower()
    res = _two_rates(c_n, c_k, t_n, t_k, alpha=alpha)
    inc = t_k - res["_p1"] * t_n
    inc_ci = [t_n * (res["ci95_abs_pp"][0] / 100), t_n * (res["ci95_abs_pp"][1] / 100)]
    if comparable is None:
        out["missing"].append("comparable")
        verdict = VERDICT_NOT_COMPARABLE
        reason = "comparable not stated: say whether the holdout was drawn from the same population by random split"
    elif not _flag(comparable, False):
        verdict = VERDICT_NOT_COMPARABLE
        reason = str(args.get("comparable_reason") or "groups are not comparable; no lift reported. Hold out a matched group next time.")
    elif min(c_k, t_k, c_n - c_k, t_n - t_k) < 5:
        verdict = VERDICT_TOO_SMALL
        reason = "fewer than 5 events in a cell: the z-test is unreliable"
    elif res["significant"]:
        verdict = VERDICT_WINNER if res["_diff"] > 0 else VERDICT_LOSER
        reason = None
    else:
        verdict = VERDICT_INCONCLUSIVE
        reason = None
    min_sample = None
    if res["_p1"] > 0 and res["_diff"] != 0:
        try:
            plan = experiment_plan({"baseline_rate": res["_p1"] * 100, "minimum_effect": abs(res["_diff"]) * 100, "effect_kind": "absolute", "alpha": alpha, "power": power})
            min_sample = plan["sample_per_variant"]
        except ToolError:
            min_sample = None
    if verdict == VERDICT_NOT_COMPARABLE:
        for key in ("lift_abs_pp", "lift_rel_pct", "ci95_abs_pp", "p_value"):
            res[key] = None
        inc, inc_ci = None, None
    public = {k: v for k, v in res.items() if not k.startswith("_")}
    public.update(
        {
            "design": "holdout",
            "split": split,
            "metric": metric,
            "control": {"exposed": c_n, "converted": c_k},
            "treatment": {"exposed": t_n, "converted": t_k},
            "incremental_conversions": None if inc is None else round(inc, 1),
            "incremental_ci95": None if inc_ci is None else [round(inc_ci[0], 1), round(inc_ci[1], 1)],
            "verdict": verdict,
            "reason": reason,
            "min_sample_for_this_lift": min_sample,
        }
    )
    out["method"] = "Two-proportion z-test with pooled SE and a Wald 95 % interval; incremental conversions = treatment conversions − control rate × treatment exposed; min_sample_for_this_lift from calc.experiment_plan at the observed gap."
    if verdict == VERDICT_NOT_COMPARABLE:
        out["summary"] = f"Groups are not comparable ({reason}); no lift reported."
    else:
        out["summary"] = (
            f"{metric}: {res['treatment_rate_pct']:g} % vs {res['control_rate_pct']:g} % in the {split} holdout, lift {res['lift_abs_pp']:+g} pp ({res['lift_rel_pct']:+g} %), "
            f"p = {res['p_value']:.3g}, 95 % CI {res['ci95_abs_pp'][0]:+g} to {res['ci95_abs_pp'][1]:+g} pp; about {public['incremental_conversions']:g} incremental conversions — {verdict}"
            + (f" ({reason})" if reason else "")
            + (f"; {min_sample:,} per group would be needed to confirm a gap this size." if min_sample and verdict == VERDICT_INCONCLUSIVE else ".")
        )
    return public


def _qualified_readout(args: dict, out: dict[str, Any]) -> dict[str, Any]:
    """Per-arm spend, responses and qualified responses: cost per qualified and a test against the first arm (#218)."""
    alpha, power = _alpha_power(args)
    rows = _rows(args.get("results") or args.get("arms"), "results")
    if len(rows) < 2:
        raise ToolError("results needs at least two arms {name, spend, responses, qualified}")
    arms = []
    for i, r in enumerate(rows):
        name = str(r.get("name") or f"arm {i + 1}").strip()
        spend = _num(r.get("spend", r.get("spend_eur")), f"results '{name}' spend", minimum=0.0)
        responses = _count(r.get("responses", r.get("leads")), f"results '{name}' responses")
        qualified = r.get("qualified")
        qualified = None if qualified in (None, "") else _count(qualified, f"results '{name}' qualified")
        if qualified is not None and qualified > responses:
            raise ToolError(f"results '{name}': qualified ({qualified}) exceeds responses ({responses})")
        impressions = r.get("impressions")
        impressions = None if impressions in (None, "") else _count(impressions, f"results '{name}' impressions")
        arm: dict[str, Any] = {"name": name, "spend": spend, "responses": responses, "qualified": qualified, "impressions": impressions}
        if spend is None:
            out["missing"].append(f"results.{name}.spend")
        if responses == 0 or not spend:
            arm["status"] = "insufficient data"
            arm["cost_per_response"] = None
            arm["cost_per_qualified"] = None
            arm["qualified_rate_pct"] = None
        else:
            arm["status"] = "ok"
            arm["cost_per_response"] = round(spend / responses, 2)
            arm["qualified_rate_pct"] = None if qualified is None else _pct(qualified / responses)
            arm["cost_per_qualified"] = None if not qualified else round(spend / qualified, 2)
        if impressions:
            arm["response_rate_pct"] = _rate_pct(responses / impressions)
        arms.append(arm)
    control = arms[0]
    comparisons = []
    any_qualified = all(a["qualified"] is not None for a in arms)
    for arm in arms[1:]:
        cmp: dict[str, Any] = {"arm": arm["name"], "vs": control["name"]}
        if any_qualified and control["responses"] and arm["responses"]:
            res = _two_rates(control["responses"], control["qualified"], arm["responses"], arm["qualified"], alpha=alpha)
            cmp.update({k: v for k, v in res.items() if not k.startswith("_")})
            more = None
            if res["_p1"] > 0 and res["_diff"] != 0:
                try:
                    plan = experiment_plan({"baseline_rate": res["_p1"] * 100, "minimum_effect": abs(res["_diff"]) * 100, "effect_kind": "absolute", "alpha": alpha, "power": power})
                    more = max(0, plan["sample_per_variant"] - min(control["responses"], arm["responses"]))
                except ToolError:
                    more = None
            cmp["more_responses_per_arm_needed"] = more
            cheaper = arm["cost_per_qualified"] is not None and control["cost_per_qualified"] is not None and arm["cost_per_qualified"] < control["cost_per_qualified"]
            if res["significant"] and res["_diff"] > 0 and cheaper:
                cmp["verdict"] = f"winner: {arm['name']}"
            elif res["significant"] and res["_diff"] < 0:
                cmp["verdict"] = f"winner: {control['name']}"
            else:
                cmp["verdict"] = f"no difference yet — need ~{more:,} more responses per arm" if more else "no difference yet"
                if cheaper:
                    cmp["verdict"] += f"; on cost per qualified response {arm['name']} is ahead"
        else:
            cmp["verdict"] = "unjudgeable: qualified counts missing" if not any_qualified else "insufficient data"
        comparisons.append(cmp)
    if not any_qualified:
        out["warnings"].append("qualified responses not provided — cannot judge which arm brings real customers; cost per response only.")
    out["method"] = "Cost per response = spend / responses; cost per qualified = spend / qualified; qualified rate compared with a two-proportion z-test against the first arm; extra responses needed from calc.experiment_plan at the observed gap."
    bits = []
    for a in arms:
        if a["status"] == "ok":
            bits.append(f"{a['name']}: €{a['cost_per_response']:g} per response" + (f", €{a['cost_per_qualified']:g} per qualified ({a['qualified_rate_pct']:g} % qualified)" if a["cost_per_qualified"] else ""))
        else:
            bits.append(f"{a['name']}: insufficient data")
    bits.extend(f"{c['arm']} vs {c['vs']}: " + (f"{c['lift_abs_pp']:+g} pp, p = {c['p_value']:.3g} — " if "p_value" in c else "") + c["verdict"] for c in comparisons)
    out["summary"] = "; ".join(bits) + "."
    return {"design": "qualified_readout", "arms": arms, "comparisons": comparisons}


def _copy_test(args: dict, out: dict[str, Any]) -> dict[str, Any]:
    """A pair (or set) of ads differing in one element, sized with a fixed stopping rule (#224)."""
    variants_raw = _rows(args.get("variants") or [], "variants")
    baseline = _num(args.get("baseline_rate"), "baseline_rate")
    effect = _num(args.get("minimum_effect"), "minimum_effect")
    effect = 20.0 if effect is None else effect
    weekly = _num(args.get("weekly_traffic"), "weekly_traffic", minimum=0.0)
    element = str(args.get("element") or "headline lead").strip()
    metric = str(args.get("metric") or "click-through rate").strip()
    arms = max(2, len(variants_raw)) if variants_raw else 2
    leads = [str(v.get("offer_lead") or "").strip().lower() for v in variants_raw]
    if "free" in leads and "price" not in leads:
        out["warnings"].append("free_lead_untested: a free-led headline has no price-led twin with the same angle; add one so prominence, not the giveaway, is tested.")
    angles = {str(v.get("angle") or "").strip().lower() for v in variants_raw if v.get("angle")}
    if len(angles) > 1:
        out["warnings"].append("variants carry different angles: the pair must differ only in " + element + ".")
    plan = None
    assumed = False
    if baseline is None:
        baseline, assumed = 2.0, True
        out["missing"].append("baseline_rate")
    plan = experiment_plan({"baseline_rate": baseline, "minimum_effect": effect, "effect_kind": "relative", "weekly_traffic": weekly, "variants": arms, "alpha": args.get("alpha"), "power": args.get("power"), "metric": metric})
    out["warnings"].extend(plan.get("warnings", []))
    if weekly is None:
        out["missing"].append("weekly_traffic")
    weeks = plan.get("weeks")
    stopping = (
        f"Fixed stopping rule: run until every arm has {plan['sample_per_variant']:,} units"
        + (f" (about {weeks} week{'s' if weeks != 1 else ''}, whole weeks only)" if weeks else " (duration unknown: weekly_traffic missing)")
        + f"; do not look at the p-value before then and do not stop early on a hunch. Then call the arm whose {metric} "
        f"beats the control with p below {plan['inputs']['alpha_per_comparison']:g}; otherwise keep the control."
    )
    out["method"] = "Sample per arm from calc.experiment_plan (normal approximation, Bonferroni across arms) plus a pre-registered stopping rule; the variant check is structural (same angle, one element changed)."
    out["summary"] = (
        f"Copy test on {element}, {arms} arms: {plan['sample_per_variant']:,} per arm"
        + (f" (baseline unknown; {baseline:g} % assumed)" if assumed else f" from a {baseline:g} % baseline")
        + (f", about {weeks} weeks at {weekly:,.0f} a week." if weeks else "; duration unknown until weekly_traffic is given.")
    )
    return {"design": "copy_test", "element": element, "variants": variants_raw, "baseline_assumed": assumed, "sample_per_variant": plan["sample_per_variant"], "total_sample": plan["total_sample"], "weeks": weeks, "stopping_rule": stopping}


def _series(raw: Any, label: str) -> list[float]:
    if not isinstance(raw, list) or len(raw) < 2:
        raise ToolError(f"{label} must list at least two values")
    return [_num(v, label, required=True) for v in raw]


def _diff_in_diff(args: dict, out: dict[str, Any]) -> dict[str, Any]:
    """Pre/post with a control series: difference in differences on 2 × 2 (parallel trends stated)."""
    t = args.get("treatment")
    c = args.get("control")
    if not isinstance(t, dict) or not isinstance(c, dict):
        raise ToolError("treatment and control must each be {pre, post} (one value per period or a list per period)")

    def _cell(group: dict, key: str, label: str) -> tuple[float, int]:
        value = group.get(key)
        if isinstance(value, list):
            vals = _series(value, f"{label}.{key}")
            return sum(vals) / len(vals), len(vals)
        return _num(value, f"{label}.{key}", required=True), 1

    t_pre, t_pre_n = _cell(t, "pre", "treatment")
    t_post, t_post_n = _cell(t, "post", "treatment")
    c_pre, c_pre_n = _cell(c, "pre", "control")
    c_post, c_post_n = _cell(c, "post", "control")
    t_change = t_post - t_pre
    c_change = c_post - c_pre
    did = t_change - c_change
    counterfactual = t_pre + c_change
    rel = did / abs(counterfactual) if counterfactual else None
    trends = None
    t_hist = t.get("pre_series")
    c_hist = c.get("pre_series")
    if isinstance(t_hist, list) and isinstance(c_hist, list):
        th, ch = _series(t_hist, "treatment.pre_series"), _series(c_hist, "control.pre_series")
        if len(th) != len(ch):
            raise ToolError("pre_series must have the same length for treatment and control")
        t_slope = (th[-1] - th[0]) / (len(th) - 1)
        c_slope = (ch[-1] - ch[0]) / (len(ch) - 1)
        scale = max(abs(t_slope), abs(c_slope), 1e-12)
        parallel = abs(t_slope - c_slope) / scale <= 0.25 if scale > 1e-12 else True
        trends = {"treatment_slope_per_period": round(t_slope, 4), "control_slope_per_period": round(c_slope, 4), "roughly_parallel": parallel}
        if not parallel:
            out["warnings"].append("Pre-period trends differ by more than 25 %: the parallel-trends assumption is doubtful; read the effect as a bound.")
    else:
        out["missing"].append("pre_series")
        out["warnings"].append("No pre-period series given: the parallel-trends assumption is stated, not checked.")
    if min(t_pre_n, t_post_n, c_pre_n, c_post_n) == 1:
        out["warnings"].append("Each cell is a single value, so no interval can be formed; pass a list of weekly values per cell for one.")
    metric = str(args.get("metric") or "the metric").strip()
    out["method"] = "Difference in differences on two periods × two groups: (treatment post − pre) − (control post − pre); counterfactual = treatment pre + control change. Assumes parallel trends: without the intervention the treated group would have moved like the control."
    out["summary"] = (
        f"{metric}: treatment {t_pre:g} → {t_post:g} ({t_change:+g}), control {c_pre:g} → {c_post:g} ({c_change:+g}); "
        f"difference in differences {did:+g}" + (f" ({rel * 100:+.1f} % against the counterfactual {counterfactual:g})" if rel is not None else "") + ". Assumes parallel trends."
    )
    return {
        "design": "diff_in_diff",
        "metric": metric,
        "cells": {"treatment": {"pre": t_pre, "post": t_post}, "control": {"pre": c_pre, "post": c_post}},
        "treatment_change": round(t_change, 4),
        "control_change": round(c_change, 4),
        "effect": round(did, 4),
        "counterfactual": round(counterfactual, 4),
        "effect_relative_pct": None if rel is None else round(rel * 100, 1),
        "assumption": "parallel trends",
        "pre_trends": trends,
    }


def _crossover(args: dict, out: dict[str, Any]) -> dict[str, Any]:
    """Geographic crossover: schedule, and once the swap has run, creative vs market effect (#457)."""
    markets = [str(m).strip() for m in (args.get("markets") or []) if str(m).strip()]
    creatives = [str(c).strip() for c in (args.get("creatives") or []) if str(c).strip()]
    cells_raw = _rows(args.get("cells") or [], "cells")
    if cells_raw and (not markets or not creatives):
        markets = markets or list(dict.fromkeys(str(c.get("market")).strip() for c in cells_raw))
        creatives = creatives or list(dict.fromkeys(str(c.get("creative")).strip() for c in cells_raw))
    if len(markets) < 2 or len(creatives) != 2:
        raise ToolError("a crossover needs at least two markets and exactly two creatives")
    period_days = _num(args.get("period_days"), "period_days", minimum=1.0)
    cycle = _num(args.get("purchase_cycle_days"), "purchase_cycle_days", minimum=0.0)
    washout = int(cycle) if cycle is not None else None
    if cycle is None:
        out["missing"].append("purchase_cycle_days")
        out["warnings"].append("purchase_cycle_days missing: the washout between periods cannot be set.")
    schedule = []
    for p in (1, 2):
        for i, m in enumerate(markets):
            creative = creatives[(i + p - 1) % 2]
            offset = 0 if p == 1 else ((period_days or 0) + (washout or 0))
            schedule.append({"period": f"P{p}", "market": m, "creative": creative, "start_offset_days": int(offset) if period_days else None})
    tolerance = _num(args.get("carryover_tolerance_pct"), "carryover_tolerance_pct", minimum=0.0)
    tolerance = CARRYOVER_TOLERANCE_PCT if tolerance is None else tolerance
    rule = (
        f"Pre-registered: creative effect = mean over markets of (value under {creatives[0]} − value under {creatives[1]}) within the same market; "
        f"market effect = mean over periods per market; swap only after a {washout if washout is not None else '?'}-day washout; "
        f"if period totals for the same creative shift by more than {tolerance:g} %, treat the effect as bounds, not a point."
    )
    result: dict[str, Any] = {"design": "crossover", "markets": markets, "creatives": creatives, "schedule": schedule, "washout_days": washout, "decision_rule": rule}
    if not cells_raw:
        out["method"] = "Two-period, two-creative crossover schedule with a washout of one purchase cycle; analysis waits for the period-2 cells."
        out["summary"] = f"Swap schedule for {', '.join(markets)} over two periods" + (f" with a {washout}-day washout" if washout is not None else "") + "; cannot separate creative from market: the swap has not run."
        result["analysis"] = None
        return result
    values: dict[tuple[str, str], dict[str, float]] = {}
    for i, c in enumerate(cells_raw):
        m, p, cr = str(c.get("market")).strip(), str(c.get("period")).strip(), str(c.get("creative")).strip()
        v = _num(c.get("value", c.get("units")), f"cells[{i}].value", required=True)
        values.setdefault((m, cr), {})[p] = v
    per_market = []
    diffs = []
    missing_cells = []
    for m in markets:
        a = values.get((m, creatives[0]), {})
        b = values.get((m, creatives[1]), {})
        if not a or not b:
            missing_cells.append(m)
            continue
        va, vb = list(a.values())[0], list(b.values())[0]
        diffs.append(va - vb)
        per_market.append({"market": m, creatives[0]: va, creatives[1]: vb, "difference": round(va - vb, 4)})
    if missing_cells or len(diffs) < 2:
        out["missing"].extend(f"cells for {m}" for m in missing_cells)
        out["method"] = "Crossover analysis needs both creatives in every market."
        out["summary"] = "cannot separate creative from market: the swap has not run in " + (", ".join(missing_cells) or "enough markets") + "."
        result["analysis"] = None
        return result
    effect = sum(diffs) / len(diffs)
    base_b = sum(pm[creatives[1]] for pm in per_market) / len(per_market)
    market_means = {pm["market"]: round((pm[creatives[0]] + pm[creatives[1]]) / 2, 2) for pm in per_market}
    grand = sum(market_means.values()) / len(market_means)
    market_effect = {m: round(v - grand, 2) for m, v in market_means.items()}
    ordered = sorted(market_means.items(), key=lambda kv: -kv[1])
    market_gap = {"markets": f"{ordered[0][0]} over {ordered[-1][0]}", "abs": round(ordered[0][1] - ordered[-1][1], 2)}
    periods: dict[str, float] = {}
    for (m, cr), by_p in values.items():
        for p, v in by_p.items():
            periods[p] = periods.get(p, 0.0) + v
    p_keys = sorted(periods)
    carry = None
    if len(p_keys) == 2 and periods[p_keys[0]]:
        shift = (periods[p_keys[1]] - periods[p_keys[0]]) / periods[p_keys[0]] * 100
        detected = abs(shift) > tolerance
        carry = {"detected": detected, "period_shift_pct": round(shift, 1), "period_totals": {k: periods[k] for k in p_keys}, "note": "carryover present — treat the effect as bounds" if detected else f"period totals moved {shift:+.1f} %, within the {tolerance:g} % tolerance"}
        if detected:
            out["warnings"].append(carry["note"] + ".")
    result["analysis"] = {
        "creative_effect": {"abs": round(effect, 2), "pct": round(effect / base_b * 100, 1) if base_b else None, "direction": f"{creatives[0]} over {creatives[1]}" if effect > 0 else f"{creatives[1]} over {creatives[0]}", "range_of_within_market_differences": [round(min(diffs), 2), round(max(diffs), 2)]},
        "market_effect": market_effect,
        "market_gap": market_gap,
        "market_means": market_means,
        "per_market": per_market,
        "carryover": carry,
    }
    out["method"] = "Within-market difference between creatives averaged over markets (the crossover estimate); market effect = market mean minus grand mean; carryover checked as the shift in period totals against the tolerance. With two markets the range of within-market differences stands in for an interval."
    better = creatives[0] if effect > 0 else creatives[1]
    out["summary"] = (
        f"Creative {better} beats the other by {abs(effect):g} on average across the crossover (within-market differences {min(diffs):g} to {max(diffs):g}"
        + (f", about {abs(effect) / base_b * 100:+.1f} %" if base_b else "")
        + f"); market effect {market_gap['markets']} {market_gap['abs']:+g} independent of creative"
        + (f"; carryover: {carry['note']}" if carry else "")
        + ". The market gap is not attributed to the creative."
    )
    return result


def _portfolio(args: dict, out: dict[str, Any]) -> dict[str, Any]:
    """Seed/reserve split across creative ideas and the reallocation rule (#254)."""
    budget = _num(args.get("total_budget"), "total_budget", required=True, minimum=0.0)
    ideas = _count(args.get("ideas"), "ideas", minimum=2)
    reserve = _num(args.get("reserve_share"), "reserve_share", minimum=0.0)
    reserve = DEFAULT_RESERVE_SHARE if reserve is None else reserve
    if reserve > 0.9:
        raise ToolError("reserve_share must be between 0 and 0.9")
    cpm = _num(args.get("cpm"), "cpm", minimum=0.0)
    cpc = _num(args.get("cpc"), "cpc", minimum=0.0)
    metric = str(args.get("metric") or "CTR").strip()
    baseline = _num(args.get("baseline_rate"), "baseline_rate", minimum=0.0)
    alpha = _num(args.get("alpha"), "alpha")
    alpha = SCREENING_ALPHA if alpha is None else alpha
    seed_total = budget * (1 - reserve)
    seed_per_idea = round(seed_total / ideas, 2)
    reserve_eur = round(budget * reserve, 2)
    notes: list[str] = []
    impressions = None
    if cpm:
        impressions = int(seed_per_idea / cpm * 1000)
    elif cpc and baseline:
        impressions = int(seed_per_idea / cpc / (baseline / 100))
    else:
        notes.append("cpm (or cpc with baseline_rate) missing — ask the marketer; impressions per idea unknown")
        out["missing"].append("cpm")
    needed = None
    min_events = None
    expected_events = None
    sufficient = None
    if baseline:
        plan = experiment_plan({"baseline_rate": baseline, "minimum_effect": 50, "effect_kind": "relative", "alpha": alpha, "power": args.get("power") or 0.8, "metric": metric})
        needed = plan["sample_per_variant"]
        min_events = math.ceil(needed * baseline / 100)
        if impressions is not None:
            expected_events = round(impressions * baseline / 100)
            sufficient = impressions >= needed
    else:
        notes.append("baseline_rate missing — the separation threshold cannot be computed")
        out["missing"].append("baseline_rate")
    top = max(1, round(ideas / 3))
    rule = (
        f"After {impressions:,} impressions per idea" if impressions else "After the seed stage"
    ) + f" (about {expected_events} {metric} events each at {baseline:g} %)" * bool(expected_events) + (
        f", move the €{reserve_eur:g} reserve to the top {top} idea(s) by {metric} (top third); stop ideas below half the median {metric}."
    )
    out["method"] = f"Seed = budget × (1 − reserve) / ideas; impressions = seed / CPM × 1000; the threshold to separate a 1.5× {metric} from the median comes from calc.experiment_plan at alpha {alpha:g} (screening); events = impressions × baseline."
    out["summary"] = (
        f"€{seed_per_idea:g} seed per idea × {ideas}, €{reserve_eur:g} in reserve"
        + (f"; {impressions:,} impressions per idea, " + ("enough" if sufficient else "not enough") + f" to separate the best from the pack (needs {needed:,})." if sufficient is not None else "; sufficiency unknown: " + "; ".join(notes) + ".")
    )
    return {
        "design": "portfolio",
        "total_budget": budget,
        "ideas": ideas,
        "reserve_share": reserve,
        "seed_per_idea": seed_per_idea,
        "reserve": reserve_eur,
        "impressions_per_idea": impressions,
        "impressions_needed_per_idea": needed,
        "min_events_per_idea": min_events,
        "expected_events_per_idea": expected_events,
        "sufficient": sufficient,
        "alpha": alpha,
        "reallocation_rule": rule,
        "notes": notes,
    }


def _factorial(args: dict, out: dict[str, Any]) -> dict[str, Any]:
    """Several elements at once: cells, weeks for the full grid and reduction options (#487)."""
    elements_raw = _rows(args.get("elements"), "elements")
    if len(elements_raw) < 1:
        raise ToolError("elements must list at least one {name, levels}")
    elements = []
    for i, e in enumerate(elements_raw):
        name = str(e.get("name") or f"element {i + 1}").strip()
        levels = _count(e.get("levels"), f"elements '{name}' levels")
        if levels < 2:
            raise ToolError(f"each element needs at least 2 levels; '{name}' has {levels}")
        elements.append({"name": name, "levels": levels})
    baseline = _num(args.get("baseline_rate"), "baseline_rate", required=True)
    effect = _num(args.get("minimum_effect"), "minimum_effect", required=True)
    effect_kind = str(args.get("effect_kind") or "relative").strip().lower()
    weekly = _num(args.get("weekly_traffic"), "weekly_traffic", minimum=0.0)
    max_weeks = _num(args.get("max_weeks"), "max_weeks", minimum=0.0)
    base_args = {"baseline_rate": baseline, "minimum_effect": effect, "effect_kind": effect_kind, "alpha": args.get("alpha"), "power": args.get("power"), "two_sided": args.get("two_sided", True), "metric": args.get("metric") or "conversion rate"}

    def _size(cells: int) -> tuple[int, int | None]:
        plan = experiment_plan({**base_args, "variants": cells, "weekly_traffic": weekly})
        return plan["sample_per_variant"], plan.get("weeks")

    cells = math.prod(e["levels"] for e in elements)
    per_cell, weeks_full = _size(cells)
    options = []
    for i, e in enumerate(elements):
        if e["levels"] > 2:
            reduced = [dict(x) for x in elements]
            reduced[i]["levels"] = 2
            c = math.prod(x["levels"] for x in reduced)
            pc, w = _size(c)
            options.append({"drop": f"{e['name']} {e['levels']}->2 levels", "cells": c, "per_cell": pc, "weeks": w, "fits_max_weeks": None if w is None or max_weeks is None else w <= max_weeks})
        if len(elements) > 1:
            reduced = [dict(x) for j, x in enumerate(elements) if j != i]
            c = math.prod(x["levels"] for x in reduced)
            pc, w = _size(c)
            options.append({"drop": f"{e['name']} to 1 level (removed)", "cells": c, "per_cell": pc, "weeks": w, "fits_max_weeks": None if w is None or max_weeks is None else w <= max_weeks})
    pc2, w2 = _size(2)
    options.append({"drop": f"all elements as sequential A/B tests: {len(elements)} tests × {w2 if w2 is not None else '?'} weeks", "cells": 2, "per_cell": pc2, "weeks": None if w2 is None else w2 * len(elements), "fits_max_weeks": None if w2 is None or max_weeks is None else w2 * len(elements) <= max_weeks, "sequential": True})
    first_single = {"drop": f"one element with two levels ({elements[0]['name']} first)", "cells": 2, "per_cell": pc2, "weeks": w2, "fits_max_weeks": None if w2 is None or max_weeks is None else w2 <= max_weeks}
    options.append(first_single)
    options.sort(key=lambda o: (o["weeks"] is None, o["weeks"] if o["weeks"] is not None else 0, o["cells"]))
    fits = None if weeks_full is None or max_weeks is None else weeks_full <= max_weeks
    if weekly is None:
        out["missing"].append("weekly_traffic")
    alpha_each = (_num(args.get("alpha"), "alpha") or DEFAULT_ALPHA) / (cells - 1)
    out["method"] = "cells = product of levels; alpha split by Bonferroni across cells − 1 comparisons; per-cell sample from calc.experiment_plan; reductions re-sized the same way and sorted by weeks."
    if weeks_full is None:
        summary = f"{cells} cells, {per_cell:,} per cell; duration unknown until weekly_traffic is given."
    elif fits is False:
        best = next((o for o in options if o["fits_max_weeks"]), None)
        summary = f"The full {cells}-cell grid needs about {weeks_full} weeks at {weekly:,.0f} visitors a week; " + (
            f"in {max_weeks:g} weeks you can run {best['drop']} ({best['cells']} cells, about {best['weeks']} weeks). Test the {elements[0]['name']} first." if best else f"nothing fits in {max_weeks:g} weeks; even one two-arm test needs {w2} weeks."
        )
    else:
        summary = f"{cells} cells, {per_cell:,} per cell, about {weeks_full} weeks for the full grid" + (f" (fits in {max_weeks:g})" if fits else "") + "."
    out["summary"] = summary
    return {
        "design": "factorial",
        "elements": elements,
        "cells": cells,
        "alpha_per_comparison": round(alpha_each, 4),
        "per_cell": per_cell,
        "total_sample": per_cell * cells,
        "visitors_per_cell_per_week": None if not weekly else round(weekly / cells, 1),
        "weeks_full_factorial": weeks_full,
        "feasible_in_max_weeks": fits,
        "reduction_options": options,
    }


def _stated_vs_observed(args: dict, out: dict[str, Any]) -> dict[str, Any]:
    """A survey share against the live purchase share at the real price (#492)."""
    stated = _num(args.get("stated_rate"), "stated_rate", required=True)
    if not 0 < stated < 100:
        raise ToolError("stated_rate is a percent between 0 and 100")
    stated_n = args.get("stated_n")
    stated_n = None if stated_n in (None, "") else _count(stated_n, "stated_n", minimum=1)
    observed = args.get("observed")
    alpha, power = _alpha_power(args)
    weekly = _num(args.get("weekly_traffic"), "weekly_traffic", minimum=0.0)
    stated_ci = None
    if stated_n:
        lo, hi = _wilson(stated * stated_n / 100, stated_n)
        stated_ci = [_pct(lo), _pct(hi)]
    else:
        out["missing"].append("stated_n")
        out["warnings"].append("stated_n missing: no interval on the survey share.")
    plan = experiment_plan({"baseline_rate": stated, "minimum_effect": 50, "effect_kind": "relative", "weekly_traffic": weekly, "alpha": alpha, "power": power, "metric": "purchase share"})
    live_plan = {"sample_per_arm": plan["sample_per_variant"], "weeks": plan.get("weeks"), "detects": f"{stated:g} % against {stated / 2:g} % (stated × 0.5)"}
    result: dict[str, Any] = {"design": "stated_vs_observed", "stated_rate_pct": stated, "stated_n": stated_n, "stated_ci95_pct": stated_ci, "price": args.get("price"), "live_test_plan": live_plan}
    if not isinstance(observed, dict):
        out["missing"].append("observed")
        result.update({"observed_rate_pct": None, "observed_ci95_pct": None, "conversion_ratio": None, "stated_overstates": None, "forecast_rate_pct": None, "forecast_reason": "no live data", "forecast_use": "not yet — validate live"})
        out["method"] = "Live test sized with calc.experiment_plan to tell the stated share from half of it; no forecast rate until purchases at the real price are observed."
        out["summary"] = f"Stated {stated:g} %" + (f" (n = {stated_n})" if stated_n else "") + f": forecast use not yet — validate live with {plan['sample_per_variant']:,} visitors per arm" + (f" (about {plan['weeks']} weeks)" if plan.get("weeks") else "") + "."
        return result
    exposures = _count(observed.get("exposures", observed.get("n")), "observed.exposures")
    purchases = _count(observed.get("purchases", observed.get("conversions")), "observed.purchases")
    if exposures == 0 or purchases > exposures:
        raise ToolError("unsuitable data: observed.exposures must be above zero and purchases may not exceed exposures")
    block = _share_block(purchases, exposures)
    ratio = round(block["share_pct"] / stated, 2)
    overstates = stated > block["ci95_pct"][1]
    understates = stated < block["ci95_pct"][0]
    result.update(
        {
            "observed": {"exposures": exposures, "purchases": purchases},
            "observed_rate_pct": block["share_pct"],
            "observed_ci95_pct": block["ci95_pct"],
            "conversion_ratio": ratio,
            "stated_overstates": overstates,
            "stated_understates": understates,
            "forecast_rate_pct": block["share_pct"],
            "forecast_use": "ok",
        }
    )
    if exposures < 100:
        out["warnings"].append("Fewer than 100 exposures: the observed share is rough; keep the live test running.")
    out["method"] = "Observed share = purchases / exposures with a Wilson 95 % interval; conversion ratio = observed / stated; stated overstates when it lies above the interval. The forecast rate is the observed share, never the survey share."
    out["summary"] = (
        f"Stated {stated:g} %" + (f" (n = {stated_n})" if stated_n else "") + f"; observed {block['share_pct']:g} % ({purchases}/{exposures:,}, 95 % CI {block['ci95_pct'][0]:g}–{block['ci95_pct'][1]:g} %); "
        f"conversion ratio {ratio:g}; " + ("stated choice overstates purchase. " if overstates else "stated choice understates purchase. " if understates else "stated and observed agree. ")
        + f"Forecast rate: {block['share_pct']:g} %, not {stated:g} %."
    )
    return result


CREATIVE_DESIGNS = {
    "holdout": _holdout,
    "readout": _holdout,
    "qualified_readout": _qualified_readout,
    "copy_test": _copy_test,
    "diff_in_diff": _diff_in_diff,
    "crossover": _crossover,
    "portfolio": _portfolio,
    "factorial": _factorial,
    "stated_vs_observed": _stated_vs_observed,
}
DECLINED_DESIGNS = {
    "speed_ab": "measuring page load time needs a browser; this tool only sizes the test (use design copy_test or plan) and cannot verify that the variant is faster or that the content matches",
}


def creative_test(args: dict) -> dict[str, Any]:
    """Sequential and holdout designs for ads; ``design`` picks one of
    holdout | qualified_readout | copy_test | diff_in_diff | crossover | portfolio | factorial | stated_vs_observed."""
    design = str(args.get("design") or "").strip().lower()
    if design in DECLINED_DESIGNS:
        raise ToolError(f"design {design} is not available: {DECLINED_DESIGNS[design]}")
    if design not in CREATIVE_DESIGNS:
        raise ToolError(f"design must be one of {', '.join(sorted(CREATIVE_DESIGNS))}; got {design!r}")
    out: dict[str, Any] = {"mode": "creative_test", "missing": [], "warnings": []}
    out.update(CREATIVE_DESIGNS[design](args, out))
    return out


MODES = {
    "evaluate": evaluate_results,
    "plan_means": plan_means,
    "plan_rate": plan_rate,
    "wtp": wtp,
    "concept": concept,
    "test_market": test_market,
    "conjoint_design": conjoint_design,
    "conjoint_analyze": conjoint_analyze,
    "screener": screener,
    "choice_study": choice_study,
    "creative_test": creative_test,
}
RESEARCH_MODES = frozenset(MODES) - {"evaluate", "plan_means", "plan_rate"}


def experiments_tool(args: dict) -> str:
    """Backs the ``experiment_plan`` tool for modes other than the plain rate plan."""
    args = args or {}
    mode = str(args.get("mode") or "").strip().lower()
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(sorted(MODES))}; got {mode!r}")
    return json.dumps(MODES[mode](args), ensure_ascii=False, indent=1)
