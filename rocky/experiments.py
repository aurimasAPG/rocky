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


MODES = {"evaluate": evaluate_results, "plan_means": plan_means, "plan_rate": plan_rate}


def experiments_tool(args: dict) -> str:
    """Backs the ``experiment_plan`` tool for modes other than the plain rate plan."""
    args = args or {}
    mode = str(args.get("mode") or "").strip().lower()
    if mode not in MODES:
        raise ToolError(f"mode must be one of {', '.join(sorted(MODES))}; got {mode!r}")
    return json.dumps(MODES[mode](args), ensure_ascii=False, indent=1)
