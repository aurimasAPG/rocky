from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path

from rocky.config import extra_bash_deny, sandbox_image, sandbox_mode
from rocky.jsonutil import parse_json_object
from rocky.sandbox import check_command, sandbox_available, wrap_command

MAX_OUTPUT = 50_000


class ToolError(RuntimeError):
    pass


class WorkspaceTools:
    def __init__(self, workspace: str | Path):
        self.root = Path(workspace).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.written: list[str] = []
        self._written_lock = threading.Lock()

    def resolve(self, path: str) -> Path:
        raw = Path(path).expanduser()
        target = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ToolError(f"path escapes workspace: {path}") from exc
        return target

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._note_written(target)
        return f"wrote {target.relative_to(self.root)} ({len(content)} chars)"

    def append_file(self, path: str, content: str) -> str:
        """Add text to the end of a file (created if absent). Long documents go in parts:
        a single write_file with a very long content gets cut off by the model API."""
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(content)
        self._note_written(target)
        return f"appended {len(content)} chars to {target.relative_to(self.root)} ({target.stat().st_size} bytes now)"

    def _note_written(self, target: Path) -> None:
        rel = str(target.relative_to(self.root))
        with self._written_lock:
            if rel not in self.written:
                self.written.append(rel)

    def files_on_disk(self) -> list[str]:
        """Files this run wrote that still exist and are not empty, in write order."""
        out = []
        with self._written_lock:
            for rel in self.written:
                path = self.root / rel
                if path.is_file() and path.stat().st_size > 0:
                    out.append(rel)
        return out

    def read_file(self, path: str) -> str:
        target = self.resolve(path)
        if not target.exists():
            raise ToolError(f"not found: {path}")
        if target.is_dir():
            raise ToolError(f"is a directory: {path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        return _clip(text)

    def list_dir(self, path: str = ".") -> str:
        target = self.resolve(path)
        if not target.exists():
            raise ToolError(f"not found: {path}")
        if not target.is_dir():
            raise ToolError(f"not a directory: {path}")
        names = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            names.append(child.name + ("/" if child.is_dir() else ""))
        return "\n".join(names) if names else "(empty)"

    def grep(self, pattern: str, path: str = ".") -> str:
        target = self.resolve(path)
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"invalid pattern: {exc}") from exc
        hits: list[str] = []
        files = [target] if target.is_file() else target.rglob("*")
        for file in files:
            if not file.is_file():
                continue
            if any(part.startswith(".") for part in file.relative_to(self.root).parts):
                continue
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = file.relative_to(self.root)
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}:{line[:240]}")
                    if len(hits) >= 80:
                        return _clip("\n".join(hits))
        return "\n".join(hits) if hits else "no matches"

    def patch(self, path: str, old_string: str, new_string: str) -> str:
        if not old_string:
            raise ToolError("old_string is empty")
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError(f"not found: {path}")
        # The raw text, never the clipped view read_file returns: patching through
        # the 50 KB clip silently threw away the rest of a long file.
        current = target.read_text(encoding="utf-8", errors="replace")
        count = current.count(old_string)
        if count == 0:
            raise ToolError("old_string not found in file")
        if count > 1:
            raise ToolError(f"old_string found {count} times — make it unique")
        updated = current.replace(old_string, new_string, 1)
        self.write_file(path, updated)
        return f"patched {path} ({len(old_string)} → {len(new_string)} chars)"

    def todo_list(self) -> str:
        items = self._todo_load()
        if not items:
            return "(todo empty)"
        lines = []
        for item in items:
            lines.append(f"- [{item.get('status')}] {item.get('id')}: {item.get('content')}")
        return "\n".join(lines)

    def todo_write(self, items: list) -> str:
        cleaned = []
        for raw in items or []:
            if not isinstance(raw, dict):
                continue
            ident = str(raw.get("id") or len(cleaned) + 1)
            content = str(raw.get("content") or "").strip()
            status = str(raw.get("status") or "pending").strip() or "pending"
            if not content:
                continue
            cleaned.append({"id": ident, "content": content, "status": status})
        path = self.root / ".rocky" / "todo.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.todo_list()

    def _todo_load(self) -> list:
        path = self.root / ".rocky" / "todo.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def bash(self, command: str, timeout: int = 60, sandbox: str | None = None) -> str:
        if not (command or "").strip():
            raise ToolError("bash needs a command")
        reason = check_command(command, extra_bash_deny())
        if reason:
            raise ToolError(f"refused ({reason}): {command[:200]}")
        mode = sandbox_mode() if sandbox is None else sandbox
        blocker = sandbox_available(mode)
        if blocker:
            raise ToolError(f"ROCKY_SANDBOX={mode} but {blocker}")
        argv = wrap_command(command, self.root, mode, sandbox_image())
        try:
            proc = subprocess.run(
                argv,
                shell=isinstance(argv, str),
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=bash_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"timed out after {timeout}s: {command}") from exc
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        if proc.returncode != 0:
            out = f"exit {proc.returncode}\n{out}"
        return _clip(out.strip() or "(no output)")


_RAW_PATH = re.compile(r'"path"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"')


def cut_off_message(name: str, raw: str) -> str:
    """What to tell a worker whose tool-call arguments did not parse: almost always a
    write_file whose content was too long for one call and got truncated mid-JSON."""
    match = _RAW_PATH.search(raw or "")
    where = f" for {match.group(1)}" if match else ""
    return (
        f"{name} arguments were not valid JSON — the call was cut off{where} ({len(raw or '')} characters received). "
        "Nothing was written. Write the file in parts: write_file with the first part (under ~8000 characters), "
        "then append_file for each further part."
    )


def bash_env() -> dict[str, str]:
    """Environment for worker shell commands: Rocky's own Python first, no inherited PYTHONPATH.

    The job process is started with PYTHONPATH pointing at Rocky's site-packages so
    `python -m rocky.job` imports. A worker's `python3` is whatever the shell finds
    (Homebrew 3.14 on this Mac); with that PYTHONPATH it loads Rocky's 3.13-compiled
    lxml and crashes on `import pptx`. Putting Rocky's interpreter directory first
    makes `python3` the one that has python-pptx, openpyxl, python-docx, reportlab.
    """
    import sys

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    bindir = str(Path(sys.executable).parent)  # not resolve(): a venv python is a symlink out of the venv
    path = env.get("PATH", "")
    if bindir not in path.split(os.pathsep):
        env["PATH"] = bindir + os.pathsep + path
    return env


def _clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... truncated ({len(text)} chars)"


CLIENT_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the workspace directory. Use for tests, git, builds, listing, scripts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or overwrite a UTF-8 text file. Keep one call under ~8000 characters: a longer content gets "
                "cut off and the call fails — write the first part here, then append_file the rest. "
                "Do NOT use for .xlsx .docx .pptx .pdf — use bash + the matching skill instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "Add text to the end of a text file (created if missing). Use it to write long documents in parts after write_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 file relative to the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and folders in a workspace path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents in the workspace with a regex.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web. Returns titles, URLs, and snippets.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and return extracted text.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch",
            "description": "Replace one unique snippet in a workspace file. Prefer this over rewriting the whole file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "Update or list the session todo list. Pass items to replace the list; omit items to only read it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clarify",
            "description": "Ask the human a blocking question when the brief is missing a decision you cannot safely assume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vision_analyze",
            "description": "Analyze an image from a workspace path or http(s) URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "url": {"type": "string"},
                    "prompt": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "image_generate",
            "description": (
                "Generate an image (ad visual, hero, illustration, social post) with an image model and save it "
                "as PNG under deliverables/. Describe subject, composition, style, colours (hex), text overlay, "
                "format. reference_path anchors style or edits an existing image. Costs a few cents per image."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "What to draw, in detail"},
                    "path": {"type": "string", "description": "Where to save, e.g. deliverables/visual-1.png"},
                    "reference_path": {"type": "string", "description": "Optional workspace image to edit or match"},
                    "aspect_ratio": {
                        "type": "string",
                        "description": "1:1 (feed), 9:16 (story), 16:9 (banner), 4:5, 3:2 …",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unit_economics",
            "description": (
                "Calculate contribution, margin and markup, break-even and what a price change must win back. "
                "Call it for every pricing, discount, promotion, bundle, tier, channel-price or acquisition-cost question "
                "instead of doing the arithmetic yourself; give per-unit numbers in one currency. Optional blocks on the "
                "same call: several plans ranked by net contribution (plans), realized price after markdowns (sales_mix "
                "or markdown_pct), a bundle against separate purchases (bundle), tiers blended against the baseline with "
                "the cannibalization break-even (variants), the price buildup through a distribution chain with a "
                "back-solve to a shelf target (chain), and fixed/per-unit obligations with payback (obligations). If "
                "variable_cost is unknown the result lists it under missing with a conditional table: ask the user "
                "(clarify) or state the assumption."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "price": {"type": "number", "description": "current price per unit"},
                    "variable_cost": {"type": "number", "description": "cost per unit that scales with sales"},
                    "units": {"type": "number", "description": "current sales per period"},
                    "fixed_costs": {"type": "number", "description": "costs per period that do not scale, for break-even"},
                    "new_price": {"type": "number", "description": "the price being considered"},
                    "discount_pct": {"type": "number", "description": "alternative to new_price: percent off"},
                    "new_variable_cost": {"type": "number", "description": "cost per unit after the change, if it moves"},
                    "expected_uplift_pct": {"type": "number", "description": "the volume gain the marketer expects, to get a decision"},
                                        "margin_pct": {"type": "number", "description": "alternative to variable_cost: margin in percent of price; the cost is derived and stated under assumptions"},
                    "markup_pct": {"type": "number", "description": "alternative to variable_cost: markup in percent on cost; the cost is derived and stated under assumptions"},
                    "sales_mix": {"type": "array", "description": "units actually sold at each price point, for the realized price and markup after markdowns", "items": {"type": "object", "properties": {"price": {"type": "number"}, "units": {"type": "number"}}, "required": ["price", "units"]}},
                    "markdown_pct": {"type": "number", "description": "alternative to sales_mix: depth of the markdown in percent; needs markdown_share_pct"},
                    "markdown_share_pct": {"type": "number", "description": "share of units (percent) sold at the markdown"},
                    "target_contribution": {"type": "number", "description": "contribution per period to reach; returns units_for_target today and per plan"},
                    "plans": {"type": "array", "description": "several price plans evaluated against price/variable_cost/units and ranked by net contribution", "items": {"type": "object", "properties": {"name": {"type": "string"}, "new_price": {"type": "number"}, "discount_pct": {"type": "number", "description": "alternative to new_price"}, "expected_volume_change_pct": {"type": "number", "description": "volume change the marketer expects, for a decision"}, "expected_units": {"type": "number", "description": "alternative to expected_volume_change_pct"}, "promo_cost": {"type": "number", "description": "one-off cost of the plan per period"}, "added_variable_cost": {"type": "number", "description": "extra cost per unit the plan brings (gift, shipping)"}}}},
                    "bundle": {"type": "object", "description": "a bundle judged against the items bought separately", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "price": {"type": "number"}, "variable_cost": {"type": "number"}, "would_buy_anyway_pct": {"type": "number", "description": "share of baseline buyers who would buy this item at full price anyway; default 100 for the baseline item, 0 for others"}}, "required": ["name", "price"]}}, "bundle_price": {"type": "number"}, "expected_attach_rate_pct": {"type": "number", "description": "share of baseline buyers expected to take the bundle"}, "baseline_item": {"type": "string", "description": "the item sold alone today; default the first item"}}, "required": ["items", "bundle_price"]},
                    "variants": {"type": "array", "description": "tiers or variants blended against the base price/cost; share_pct is each one's share of all buyers, the rest stay at the base price", "items": {"type": "object", "properties": {"name": {"type": "string"}, "price": {"type": "number"}, "share_pct": {"type": "number"}, "extra_variable_cost": {"type": "number", "description": "cost added on top of the base variable_cost"}, "variable_cost": {"type": "number", "description": "full cost per unit if it differs from base + extra"}, "effort_note": {"type": "string", "description": "what the buyer gives up or does, e.g. '25-min round trip'; carried into the output"}}, "required": ["name", "price", "share_pct"]}},
                    "chain": {"type": "object", "description": "price buildup from production cost through distribution stages to the shelf", "properties": {"production_cost": {"type": "number"}, "stages": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "added_cost": {"type": "number"}, "margin_pct": {"type": "number"}, "basis": {"type": "string", "enum": ["of_price", "on_cost"], "description": "of_price = margin as share of selling price (default); on_cost = markup on cost"}}, "required": ["name", "margin_pct"]}}, "vat_pct": {"type": "number"}, "target_shelf_price": {"type": "number", "description": "consumer price (gross when vat_pct is given) to back-solve the production cost for"}}, "required": ["production_cost", "stages"]},
                    "obligations": {"type": "array", "description": "fixed, per-period and per-unit commitments added to the base economics; price may be 0 (free tier) when these or variants carry the economics", "items": {"type": "object", "properties": {"name": {"type": "string"}, "one_off": {"type": "number"}, "per_period": {"type": "number"}, "per_unit": {"type": "number"}}, "required": ["name"]}},
                    "currency": {"type": "string", "description": "EUR by default"},
                },
                "required": ["price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "experiment_plan",
            "description": "Plan or read out an A/B test. mode plan (default) or plan_rate: sample per variant, duration from weekly_traffic and a decision rule for a rate given in percent; plan_rate also writes guardrails and a segment into the rule. mode plan_means: the same for a measured quantity (order value, time, score) from sd or baseline_mean + cv_pct and minimum_difference; paired designs with correlation need fewer people. mode evaluate: give arms with n and conversions (or mean and sd) and get per challenger the difference, 95 % interval, p-value and a verdict — winner, loser, no difference detected, or underpowered: keep running when the planned sample was not reached; guardrail breaches are named. Use it instead of guessing how long to run or whether a result is real. Research modes read fielded data or lay out a study without running it: wtp (Van Westendorp PMC/OPP/IPP/PME and a fake-door price page with its disclosure), concept (comprehension share before top-two-box intent, Wilson intervals, proceed / rework message / rework concept), test_market (mix completeness, income and share flags, KPI readout dates), conjoint_design / conjoint_analyze (balanced tasks inline; count-based part-worths labelled as such), screener (purchase window, question in lt/en, gross invites), choice_study (equally described cards, invented-brand disclosure, switch share with Wilson CI, brand lift). creative_test takes design holdout (lift, incremental conversions, not_comparable when the holdout is not matched), qualified_readout (cost per qualified response and a z-test), copy_test (fixed stopping rule), diff_in_diff (parallel trends stated), crossover (creative vs market effect, carryover check), portfolio (seed/reserve split and the reallocation rule), factorial (cells, weeks, reduction options) and stated_vs_observed (forecast rate = observed, never the survey share); page-speed tests are declined because they need a browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": [
                            "plan",
                            "plan_rate",
                            "plan_means",
                            "evaluate",
                            "wtp",
                            "concept",
                            "test_market",
                            "conjoint_design",
                            "conjoint_analyze",
                            "screener",
                            "choice_study",
                            "creative_test"
                        ],
                        "description": "plan (default) sizes a test on a rate; plan_rate = plan plus guardrails and segment; plan_means sizes a test on a measured quantity; evaluate reads out a finished test; wtp = Van Westendorp price points from responses and/or a fake-door plan; concept = read out a fielded concept test (comprehension before intent); test_market = structured pilot plan with representativeness flags; conjoint_design / conjoint_analyze = balanced choice tasks and count-based part-worths; screener = purchase-window screener and gross invites; choice_study = second-choice presence test (design or analyze); creative_test = holdout, qualified_readout, copy_test, diff_in_diff, crossover, portfolio, factorial or stated_vs_observed (pick with design)"
                    },
                    "arms": {
                        "type": "array",
                        "description": "evaluate: 2+ arms; the arm named control (or the first) is the control. Rates: {name, n, conversions}; means: {name, n, mean, sd}",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "n": {
                                    "type": "number",
                                    "description": "units exposed"
                                },
                                "conversions": {
                                    "type": "number"
                                },
                                "mean": {
                                    "type": "number"
                                },
                                "sd": {
                                    "type": "number",
                                    "description": "standard deviation of that arm, required with mean"
                                }
                            },
                            "required": [
                                "name",
                                "n"
                            ]
                        }
                    },
                    "control": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "object"
                            }
                        ],
                        "description": "evaluate: name of the control arm if it is not called control or first; creative_test holdout: {exposed, converted}; diff_in_diff: {pre, post, pre_series?}"
                    },
                    "lower_is_better": {
                        "type": "boolean",
                        "description": "evaluate: True for bounce, refund or cost metrics"
                    },
                    "guardrails": {
                        "type": "array",
                        "description": "plan_rate: names or {name, max_drop_pct}; evaluate: {name, arms (same shape as arms), max_drop_pct, lower_is_better} — a breach is named against the winner",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "max_drop_pct": {
                                    "type": "number",
                                    "description": "largest tolerated worsening, percent of the control value"
                                },
                                "lower_is_better": {
                                    "type": "boolean"
                                },
                                "arms": {
                                    "type": "array",
                                    "items": {
                                        "type": "object"
                                    }
                                }
                            },
                            "required": [
                                "name"
                            ]
                        }
                    },
                    "planned": {
                        "type": "object",
                        "description": "evaluate: the plan written before the test; without it 'no difference' cannot be told from 'too little data'",
                        "properties": {
                            "sample_per_variant": {
                                "type": "number"
                            },
                            "minimum_effect": {
                                "type": "number"
                            },
                            "effect_kind": {
                                "type": "string",
                                "enum": [
                                    "relative",
                                    "absolute"
                                ]
                            }
                        }
                    },
                    "sd": {
                        "type": "number",
                        "description": "plan_means: standard deviation of the metric in one arm, same units as the metric (or give baseline_mean and cv_pct)"
                    },
                    "baseline_mean": {
                        "type": "number",
                        "description": "plan_means: current mean, used with cv_pct to derive sd"
                    },
                    "cv_pct": {
                        "type": "number",
                        "description": "plan_means: spread as percent of the mean"
                    },
                    "minimum_difference": {
                        "type": "number",
                        "description": "plan_means: smallest difference in means worth detecting, same units"
                    },
                    "weekly_units": {
                        "type": "number",
                        "description": "plan_means: measured units per week across all variants"
                    },
                    "units": {
                        "type": "string",
                        "description": "plan_means: unit label, e.g. EUR or seconds"
                    },
                    "paired": {
                        "type": "boolean",
                        "description": "plan_means: True when every participant is measured under every variant (within-subject)"
                    },
                    "correlation": {
                        "type": "number",
                        "description": "plan_means, paired: correlation between a participant's two measurements, default 0.5"
                    },
                    "segment": {
                        "type": "string",
                        "description": "plan_rate: the population the test runs in, e.g. returning visitors; written into the decision rule"
                    },
                    "baseline_rate": {
                        "type": "number",
                        "description": "current rate in percent, e.g. 3.2"
                    },
                    "minimum_effect": {
                        "type": "number",
                        "description": "smallest lift worth detecting"
                    },
                    "effect_kind": {
                        "type": "string",
                        "enum": [
                            "relative",
                            "absolute"
                        ],
                        "description": "relative = percent of baseline (default); absolute = percentage points"
                    },
                    "weekly_traffic": {
                        "type": "number",
                        "description": "eligible visitors or orders per week across all variants"
                    },
                    "variants": {
                        "anyOf": [
                            {
                                "type": "integer"
                            },
                            {
                                "type": "array",
                                "items": {
                                    "type": "object"
                                }
                            }
                        ],
                        "description": "arms including control, default 2; creative_test copy_test: the ads as [{name, headline, offer_lead free|price|none, angle}] — a free-led ad without a price-led twin warns free_lead_untested"
                    },
                    "alpha": {
                        "type": "number",
                        "description": "significance level, default 0.05"
                    },
                    "power": {
                        "type": "number",
                        "description": "default 0.8"
                    },
                    "two_sided": {
                        "type": "boolean"
                    },
                    "max_weeks": {
                        "type": "number",
                        "description": "how long the marketer can wait; returns what is detectable in that time"
                    },
                    "metric": {
                        "type": "string",
                        "description": "what the rate measures, e.g. checkout conversion"
                    },
                    "language": {
                        "type": "string",
                        "enum": [
                            "lt",
                            "en"
                        ],
                        "description": "wtp, screener, choice_study: language of the generated questions and cards; detected from the text when absent"
                    },
                    "responses": {
                        "type": "array",
                        "description": "wtp: [{too_cheap, cheap, expensive, too_expensive}] one row per respondent (prices); concept: [{concept?, understood (bool, 0/1, yes/no/taip/ne or the open benefit answer), intent 1-5}]",
                        "items": {
                            "type": "object"
                        }
                    },
                    "cumulative": {
                        "type": "object",
                        "description": "wtp alternative to responses: {prices[], too_cheap[], cheap[], expensive[], too_expensive[], n} with shares in percent per price"
                    },
                    "offer": {
                        "type": "string",
                        "description": "wtp: the service or product being priced, used in the questions and decision rule"
                    },
                    "fake_door": {
                        "type": "object",
                        "description": "wtp: plan a fake-door price page; sized with the rate plan across price cells and returned with the disclosure sentence the page must carry",
                        "properties": {
                            "baseline_rate": {
                                "type": "number",
                                "description": "expected click-to-waitlist rate in percent"
                            },
                            "minimum_effect": {
                                "type": "number",
                                "description": "relative lift worth detecting, default 25"
                            },
                            "weekly_traffic": {
                                "type": "number"
                            },
                            "price_points": {
                                "type": "array",
                                "items": {
                                    "type": "number"
                                }
                            },
                            "delivery_cost": {
                                "type": "number",
                                "description": "cost to deliver; price points below it get a below_cost warning"
                            }
                        }
                    },
                    "benefit_keywords": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "concept: words that code an open benefit answer as understood"
                    },
                    "min_comprehension": {
                        "type": "number",
                        "description": "concept: fraction who must restate the benefit, default 0.7"
                    },
                    "top_two_box_min": {
                        "type": "number",
                        "description": "concept: fraction of those who understood answering 4-5, default 0.4"
                    },
                    "market": {
                        "type": "object",
                        "description": "test_market: {name, population, population_share_pct, income_index (national = 100)}"
                    },
                    "mix": {
                        "type": "object",
                        "description": "test_market: {product, price, place[], promotion[]}; any empty element flags mix_incomplete"
                    },
                    "duration_weeks": {
                        "type": "number",
                        "description": "test_market: length of the pilot; under 4 flags short_duration"
                    },
                    "start_date": {
                        "type": "string",
                        "description": "test_market: ISO date; readout dates at week 0, midpoint, end and end + 4"
                    },
                    "kpis": {
                        "type": "array",
                        "description": "test_market: [{name, target, unit}] fixed before launch",
                        "items": {
                            "type": "object"
                        }
                    },
                    "local_conditions": {
                        "type": "array",
                        "description": "test_market: [{factor, local_value, national_value, source_url}]; rows without source_url flag unsourced_condition",
                        "items": {
                            "type": "object"
                        }
                    },
                    "attributes": {
                        "type": "object",
                        "description": "conjoint_design: {attribute: [levels]} with 2+ levels each, 2+ attributes"
                    },
                    "n_respondents": {
                        "type": "integer",
                        "description": "conjoint_design"
                    },
                    "tasks_per_respondent": {
                        "type": "integer",
                        "description": "conjoint_design, default 10"
                    },
                    "alternatives": {
                        "type": "integer",
                        "description": "conjoint_design: alternatives per task, default 2"
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "conjoint_design: rows returned inline, default 600; the full design regenerates from seed"
                    },
                    "choices": {
                        "type": "array",
                        "description": "conjoint_analyze: [{respondent, task, alternative, chosen 0/1, levels {attribute: level}}]",
                        "items": {
                            "type": "object"
                        }
                    },
                    "category": {
                        "type": "string",
                        "description": "screener: what is bought, in the accusative for Lithuanian (naują automobilį)"
                    },
                    "purchase_cycle_days": {
                        "type": "integer",
                        "description": "screener and crossover: typical days between purchases"
                    },
                    "target_n": {
                        "type": "integer",
                        "description": "screener: completed qualified interviews wanted"
                    },
                    "incidence_pct": {
                        "type": "number",
                        "description": "screener: share of contacts that qualify; missing = gross invites not estimated"
                    },
                    "window_days": {
                        "type": "integer",
                        "description": "screener: purchase window asked about; defaults to the cycle, shorter warns"
                    },
                    "response_rate_pct": {
                        "type": "number",
                        "description": "screener: panel completes per invite"
                    },
                    "action": {
                        "type": "string",
                        "enum": [
                            "design",
                            "analyze"
                        ],
                        "description": "choice_study: forced when results are not obvious"
                    },
                    "favourite_brands": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "choice_study design"
                    },
                    "alternative": {
                        "type": "object",
                        "description": "choice_study design: {name, price, size, attributes[], invented}; must match the incumbent's price and attribute count"
                    },
                    "incumbent": {
                        "type": "object",
                        "description": "choice_study design: the favourite brand's card {name, price, size, attributes[]}"
                    },
                    "n_target": {
                        "type": "integer",
                        "description": "choice_study design: respondents planned; under 30 warns"
                    },
                    "n_shown": {
                        "type": "integer",
                        "description": "choice_study analyze shorthand with n_switched"
                    },
                    "n_switched": {
                        "type": "integer"
                    },
                    "by_favourite": {
                        "type": "object",
                        "description": "choice_study analyze: {brand: {shown, switched}}"
                    },
                    "design": {
                        "type": "string",
                        "enum": [
                            "holdout",
                            "qualified_readout",
                            "copy_test",
                            "diff_in_diff",
                            "crossover",
                            "portfolio",
                            "factorial",
                            "stated_vs_observed"
                        ],
                        "description": "creative_test: which design to plan or read"
                    },
                    "treatment": {
                        "type": "object",
                        "description": "creative_test holdout: {exposed, converted}; diff_in_diff: {pre, post, pre_series?}"
                    },
                    "comparable": {
                        "type": "boolean",
                        "description": "creative_test holdout: True only if the holdout was a random split of the same population; false or absent = not_comparable, no lift"
                    },
                    "split": {
                        "type": "string",
                        "enum": [
                            "geo",
                            "audience"
                        ],
                        "description": "creative_test holdout"
                    },
                    "results": {
                        "type": "array",
                        "description": "creative_test qualified_readout: [{name, spend, responses, qualified?, impressions?}]; first arm is the control",
                        "items": {
                            "type": "object"
                        }
                    },
                    "element": {
                        "type": "string",
                        "description": "creative_test copy_test: the one element that differs, default 'headline lead'"
                    },
                    "cells": {
                        "type": "array",
                        "description": "creative_test crossover: [{market, period, creative, value}]; without them only the swap schedule is returned",
                        "items": {
                            "type": "object"
                        }
                    },
                    "markets": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "creative_test crossover"
                    },
                    "creatives": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "creative_test crossover: exactly two"
                    },
                    "period_days": {
                        "type": "number",
                        "description": "creative_test crossover: length of each period"
                    },
                    "carryover_tolerance_pct": {
                        "type": "number",
                        "description": "creative_test crossover: period-total shift tolerated, default 10"
                    },
                    "total_budget": {
                        "type": "number",
                        "description": "creative_test portfolio"
                    },
                    "ideas": {
                        "type": "integer",
                        "description": "creative_test portfolio: creative ideas, 2+"
                    },
                    "reserve_share": {
                        "type": "number",
                        "description": "creative_test portfolio: 0-0.9, default 0.4"
                    },
                    "cpm": {
                        "type": "number",
                        "description": "creative_test portfolio: expected cost per 1 000 impressions; never estimated"
                    },
                    "cpc": {
                        "type": "number",
                        "description": "creative_test portfolio: alternative to cpm with baseline_rate"
                    },
                    "elements": {
                        "type": "array",
                        "description": "creative_test factorial: [{name, levels >= 2}]; cells = product of levels",
                        "items": {
                            "type": "object"
                        }
                    },
                    "stated_rate": {
                        "type": "number",
                        "description": "creative_test stated_vs_observed: survey share in percent"
                    },
                    "stated_n": {
                        "type": "integer",
                        "description": "creative_test stated_vs_observed: survey sample"
                    },
                    "observed": {
                        "type": "object",
                        "description": "creative_test stated_vs_observed: {exposures, purchases} at the real price"
                    },
                    "price": {
                        "type": "number",
                        "description": "creative_test stated_vs_observed: label only"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "funnel_analyze",
            "description": "Read the user's own campaign, order or event data (CSV or XLSX in the workspace) and compute the funnel: stage counts, conversion from the previous and first stage, the largest loss, the window covered, per-group funnels and the worst group. With group_by it also runs two-proportion z-tests between groups (compare_groups; cost_per_group for cost per converted), lag tables between two dates, time slots (bucket_by), stages derived from column rules and a paid-stage gap. Modes buyer_classes, offers, trend, recruits and categories answer promotion substitution, timed-offer dependence, cost of a new buyer over a launch, new-to-brand share and category shares from the same files. Call it whenever a data file is in the workspace; never compute its percentages yourself. Blank cells, bad numbers, unreadable dates and empty windows are listed under missing: report them, do not fill them in.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "CSV or XLSX file relative to the workspace"
                    },
                    "sheet": {
                        "type": "string",
                        "description": "XLSX sheet name; first sheet by default"
                    },
                    "layout": {
                        "type": "string",
                        "enum": [
                            "wide",
                            "long"
                        ],
                        "description": "wide = one column per stage (default when stages is given); long = one row per event or stage (default when stage_column is given)"
                    },
                    "stages": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "wide layout: ordered column names holding counts, first stage first; summed over rows in the window"
                    },
                    "stage_column": {
                        "type": "string",
                        "description": "long layout: column naming the stage of each row"
                    },
                    "count_column": {
                        "type": "string",
                        "description": "long layout: column with a count per row to sum; omit to count rows"
                    },
                    "id_column": {
                        "type": "string",
                        "description": "long layout: user/order id column; each stage then counts unique ids instead of rows"
                    },
                    "stage_order": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "long layout: stage names in funnel order; default is order of first appearance"
                    },
                    "group_by": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                },
                                "maxItems": 2
                            }
                        ],
                        "description": "column such as channel, campaign or cohort: one funnel per value, the worst group, and (up to 6 groups) a two-proportion comparison; two columns give a teller × listener matrix"
                    },
                    "date_column": {
                        "type": "string",
                        "description": "date column; rows with unreadable dates are left out and reported"
                    },
                    "date_from": {
                        "type": "string",
                        "description": "ISO date YYYY-MM-DD, inclusive"
                    },
                    "date_to": {
                        "type": "string",
                        "description": "ISO date YYYY-MM-DD, inclusive"
                    },
                    "mode": {
                        "type": "string",
                        "enum": [
                            "funnel",
                            "buyer_classes",
                            "offers",
                            "trend",
                            "recruits",
                            "categories"
                        ],
                        "description": "funnel (default) or a separate reading of the same file: buyer_classes (promotion units by prior buying frequency), offers (timed discounts per period), trend (cost of a new buyer per launch period), recruits (buyers of a new product who are new to the brand), categories (counts and shares of a category column)"
                    },
                    "compare_groups": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "2–6 group_by values to test against the first one; default: all groups when there are at most 6"
                    },
                    "compare_stage": {
                        "type": "string",
                        "description": "stage whose rate is compared and priced; default the last stage"
                    },
                    "min_n": {
                        "type": "integer",
                        "description": "first-stage count below which a group's verdict is insufficient_data (default 100)"
                    },
                    "cost_per_group": {
                        "type": "object",
                        "description": "{group: amount} or {group: {media, fixed, fulfilment_per_unit, fee_per_unit}}; net cost = media + fixed + units×fulfilment − units×fee, units = first-stage count",
                        "additionalProperties": True
                    },
                    "cost_column": {
                        "type": "string",
                        "description": "numeric column summed per group as media cost (alternative to cost_per_group)"
                    },
                    "attention_column": {
                        "type": "string",
                        "description": "impressions/views/mentions column: adds attention share per group and flags publicity_without_sales / quiet_contributor"
                    },
                    "currency": {
                        "type": "string",
                        "description": "label for cost figures, default EUR"
                    },
                    "metrics": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "numeric columns to summarise per group (mean, median, n); groups whose median differs by more than 50 % from the rest are listed under unequal"
                    },
                    "share_of": {
                        "type": "object",
                        "properties": {
                            "numerator": {
                                "type": "string"
                            },
                            "denominator": {
                                "type": "string"
                            }
                        },
                        "description": "e.g. brand spend over category spend: share of wallet per group and a selection_bias verdict"
                    },
                    "lag_from": {
                        "type": "string",
                        "description": "date column the lag starts from (e.g. sent_date); use with lag_to"
                    },
                    "lag_to": {
                        "type": "string",
                        "description": "date column the lag ends at (e.g. redeemed_date): buckets 0 / 1–3 / 4–7 / 8+ days, median, share after day 0, per group when group_by is set"
                    },
                    "maturity_days": {
                        "type": "integer",
                        "description": "with lag_from/lag_to: count rows at equal age; groups younger than this are excluded from the comparison verdict"
                    },
                    "as_of": {
                        "type": "string",
                        "description": "ISO date for age and maturity calculations; default the latest date in the file"
                    },
                    "bucket_by": {
                        "type": "object",
                        "properties": {
                            "column": {
                                "type": "string"
                            },
                            "unit": {
                                "type": "string",
                                "enum": [
                                    "hour",
                                    "weekday",
                                    "hour_weekday"
                                ]
                            }
                        },
                        "required": [
                            "column"
                        ],
                        "description": "derive time slots from a timestamp column: stage counts, fulfilled share and the fewest slots covering cover_share of the last stage"
                    },
                    "cover_share": {
                        "type": "number",
                        "description": "share of the last stage the best_slots must cover (default 0.9)"
                    },
                    "derived_stages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "column": {
                                    "type": "string"
                                },
                                "rule": {
                                    "type": "string"
                                }
                            },
                            "required": [
                                "name",
                                "column"
                            ]
                        },
                        "description": "wide layout from a per-user table: each stage counts rows where the rule holds — not_blank (default), blank, gt:N, gte:N, lt:N, lte:N, eq:X, in:a|b, after:<column>+<N>d, within:<column>+<N>d"
                    },
                    "filter": {
                        "type": "object",
                        "properties": {
                            "column": {
                                "type": "string"
                            },
                            "values": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                }
                            },
                            "exclude_values": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                }
                            }
                        },
                        "required": [
                            "column"
                        ],
                        "description": "keep only rows whose column is in values and not in exclude_values"
                    },
                    "cohort_by": {
                        "type": "object",
                        "properties": {
                            "column": {
                                "type": "string"
                            },
                            "boundaries": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                }
                            }
                        },
                        "required": [
                            "column",
                            "boundaries"
                        ],
                        "description": "derive the group from a date column split at ISO boundaries; replaces group_by"
                    },
                    "periods": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string"
                                },
                                "from": {
                                    "type": "string"
                                },
                                "to": {
                                    "type": "string"
                                }
                            },
                            "required": [
                                "from",
                                "to"
                            ]
                        },
                        "description": "labelled, non-overlapping date spans on date_column used as groups; replaces group_by"
                    },
                    "target_segment": {
                        "type": "string",
                        "description": "with a two-column group_by: value of the second column whose share of all first-stage units is reported as share_to_target_pct"
                    },
                    "paid_at_stage": {
                        "type": "string",
                        "description": "stage a commission or payment is based on: reports units paid for that never reached the last stage and incentive_gap when the paid stage comes before the largest loss"
                    },
                    "returns_column": {
                        "type": "string",
                        "description": "with paid_at_stage: returned units, summed and shown as a share of paid units"
                    },
                    "promo_start": {
                        "type": "string",
                        "description": "buyer_classes: ISO start of the promotion"
                    },
                    "promo_end": {
                        "type": "string",
                        "description": "buyer_classes: ISO end of the promotion"
                    },
                    "pre_start": {
                        "type": "string",
                        "description": "buyer_classes: ISO start of the pre-period"
                    },
                    "pre_end": {
                        "type": "string",
                        "description": "buyer_classes: ISO end of the pre-period"
                    },
                    "pre_weeks": {
                        "type": "integer",
                        "description": "buyer_classes: pre-period length in weeks ending the day before promo_start"
                    },
                    "quantity_column": {
                        "type": "string",
                        "description": "buyer_classes/recruits: units per row; rows count 1 when absent"
                    },
                    "promo_flag_column": {
                        "type": "string",
                        "description": "buyer_classes: only rows flagged in this column count as promo purchases inside the window"
                    },
                    "class_cutoffs": {
                        "type": "object",
                        "properties": {
                            "light_max": {
                                "type": "integer"
                            },
                            "heavy_min": {
                                "type": "integer"
                            }
                        },
                        "description": "buyer_classes: pre-period purchases per class (default light ≤ 1, heavy ≥ 3)"
                    },
                    "revenue_column": {
                        "type": "string",
                        "description": "offers: revenue or price paid per order"
                    },
                    "discount_column": {
                        "type": "string",
                        "description": "offers: yes/no flag or discount percent per order"
                    },
                    "customer_column": {
                        "type": "string",
                        "description": "offers: customer id, for the share who only ever buy on discount"
                    },
                    "period": {
                        "type": "string",
                        "enum": [
                            "week",
                            "month",
                            "quarter"
                        ],
                        "description": "offers/trend: calendar period to aggregate date_column into (default month)"
                    },
                    "period_column": {
                        "type": "string",
                        "description": "trend: column naming the period when the file has one row per period"
                    },
                    "spend_column": {
                        "type": "string",
                        "description": "trend: activation spend per row"
                    },
                    "new_column": {
                        "type": "string",
                        "description": "trend: new buyers (or inquiries) per row"
                    },
                    "repeat_column": {
                        "type": "string",
                        "description": "trend: repeat buyers per row"
                    },
                    "brand_spend_column": {
                        "type": "string",
                        "description": "trend: brand spend per row, for brand share of spend"
                    },
                    "baseline_periods": {
                        "type": "integer",
                        "description": "trend: number of first periods pooled as the baseline (default 3)"
                    },
                    "product_column": {
                        "type": "string",
                        "description": "recruits: SKU or product name per row"
                    },
                    "new_products": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "recruits: the new product(s) whose buyers are classified"
                    },
                    "launch_date": {
                        "type": "string",
                        "description": "recruits: ISO launch date; default the first new-product purchase"
                    },
                    "min_history_days": {
                        "type": "integer",
                        "description": "recruits: days of data needed before launch_date, else the split is refused (default 90)"
                    },
                    "price_column": {
                        "type": "string",
                        "description": "recruits: price per row, for average order value per group"
                    },
                    "cycle_days": {
                        "type": "integer",
                        "description": "recruits: replenishment interval; adds buyers past one cycle and their matured repeat rate"
                    },
                    "column": {
                        "type": "string",
                        "description": "categories: the category column to count"
                    },
                    "categories": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "categories: allowed values; others are listed under unmapped_values"
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "categories: groups to list (default 10)"
                    },
                    "pareto_share": {
                        "type": "number",
                        "description": "categories: share the pareto_groups must cover (default 0.8)"
                    },
                    "min_rows": {
                        "type": "integer",
                        "description": "categories: below this many rows a sample-too-small warning is added (default 20)"
                    },
                    "weight_column": {
                        "type": "string",
                        "description": "categories: numeric column to sum instead of counting rows"
                    }
                },
                "required": [
                    "path"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reach_overlap",
            "description": "Combined and incremental reach of a media plan across channels, with bounds instead of reaches added up as if nobody saw two channels. Use it whenever a brief compares channels, allocates a media budget, or quotes a combined reach or cost per person reached. Give the audience and each channel's reach in people (or reach_unit=pct); list the pairwise overlaps you know under overlaps. Unknown pairs are returned under missing with lower and upper bounds, no point estimate unless assumption=independent, and narrows_most names the one overlap to ask for (use clarify or state the assumption). Tag channels with source (paid / owned / earned) and give required_reach to see whether unpaid reach can carry the target; role for the brand/activation split; addressable_pct for cost per thousand buyers, not readers; category_buyers for reach against the category; frequency_distribution + cap for a frequency-cap scenario; measure to keep views and people apart.",
            "parameters": {
                "type": "object",
                "properties": {
                    "audience": {
                        "type": "number",
                        "description": "target population in people"
                    },
                    "audience_name": {
                        "type": "string",
                        "description": "who the audience is, e.g. Lithuanian adults 25–54"
                    },
                    "channels": {
                        "type": "array",
                        "description": "one object per channel",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "reach": {
                                    "type": "number",
                                    "description": "people reached, or percent of the audience when reach_unit is pct"
                                },
                                "cost": {
                                    "type": "number",
                                    "description": "cost per period in one currency, for cost per reach"
                                },
                                "frequency": {
                                    "type": "number",
                                    "description": "average exposures per reached person, for impressions and GRPs"
                                },
                                "source": {
                                    "type": "string",
                                    "enum": [
                                        "paid",
                                        "owned",
                                        "earned"
                                    ],
                                    "description": "who delivered this reach: paid media, own channels (pages, newsletter) or earned (shares, press); with required_reach gives by_source and whether unpaid reach can carry the target"
                                },
                                "role": {
                                    "type": "string",
                                    "enum": [
                                        "brand",
                                        "activation",
                                        "both"
                                    ],
                                    "description": "job of the channel's spend; with cost gives the brand/activation split against the 60/40 comparison point"
                                },
                                "role_brand_pct": {
                                    "type": "number",
                                    "description": "for role=both: share of this channel's cost that is brand (default 50)"
                                },
                                "impressions": {
                                    "type": "number",
                                    "description": "total impressions in the period; alternative to frequency"
                                },
                                "addressable_pct": {
                                    "type": "number",
                                    "description": "percent (0–100) of this channel's reached people who can actually buy; gives addressable_reach and cpm_addressable and re-ranks channels by cost per buyer"
                                },
                                "addressable_source": {
                                    "type": "string",
                                    "description": "where addressable_pct comes from"
                                },
                                "existing_customer_share": {
                                    "type": "number",
                                    "description": "share 0–1 of this channel's reach who already buy from you; gives future_buyer_reach"
                                },
                                "frequency_distribution": {
                                    "type": "array",
                                    "description": "platform frequency breakdown; with cap gives cap_scenario (an average frequency alone is refused)",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "exposures": {
                                                "type": "string",
                                                "description": "a number, a range like 3-5, or an open bucket like 6+"
                                            },
                                            "people": {
                                                "type": "number"
                                            }
                                        },
                                        "required": [
                                            "exposures",
                                            "people"
                                        ]
                                    }
                                },
                                "cap": {
                                    "type": "integer",
                                    "description": "frequency cap to test, e.g. 3"
                                },
                                "cpm_unexposed_factor": {
                                    "type": "number",
                                    "description": "ratio of the CPM for not-yet-reached people to the current CPM; sets the lower bound of extra reach from a cap"
                                },
                                "measure": {
                                    "type": "object",
                                    "description": "what this channel's figure measures; a channel not measured in people is excluded from the combined reach and named under comparability.not_comparable",
                                    "properties": {
                                        "metric": {
                                            "type": "string",
                                            "enum": [
                                                "people",
                                                "views",
                                                "impressions",
                                                "gross_contacts"
                                            ]
                                        },
                                        "base": {
                                            "type": "string"
                                        },
                                        "period": {
                                            "type": "string",
                                            "enum": [
                                                "day",
                                                "week",
                                                "month",
                                                "campaign"
                                            ]
                                        },
                                        "devices": {
                                            "type": "string"
                                        },
                                        "source": {
                                            "type": "string"
                                        },
                                        "viewability": {
                                            "type": "string"
                                        }
                                    }
                                }
                            },
                            "required": [
                                "name",
                                "reach"
                            ]
                        }
                    },
                    "reach_unit": {
                        "type": "string",
                        "enum": [
                            "people",
                            "pct"
                        ],
                        "description": "unit of reach and shared; people by default"
                    },
                    "overlaps": {
                        "type": "array",
                        "description": "pairs whose duplication is known; pairs not listed are reported as missing",
                        "items": {
                            "type": "object",
                            "properties": {
                                "a": {
                                    "type": "string"
                                },
                                "b": {
                                    "type": "string"
                                },
                                "shared": {
                                    "type": "number",
                                    "description": "people (or percent of the audience) reached by both"
                                }
                            },
                            "required": [
                                "a",
                                "b",
                                "shared"
                            ]
                        }
                    },
                    "assumption": {
                        "type": "string",
                        "enum": [
                            "independent",
                            "bounds",
                            "given"
                        ],
                        "description": "independent = random duplication point estimate; bounds = no point (default when a pair is unknown); given = use the overlaps supplied (default when all pairs are known)"
                    },
                    "budget": {
                        "type": "number",
                        "description": "media budget per period, checked against channel costs"
                    },
                    "required_reach": {
                        "type": "number",
                        "description": "people the next flight must reach; compared with the owned + earned upper bound → unpaid_sustains_required yes / no / unknown"
                    },
                    "category_buyers": {
                        "type": "number",
                        "description": "all potential buyers of the category (the real denominator); gives category_reach as a share of them"
                    },
                    "category_source": {
                        "type": "string",
                        "description": "where the category-buyer count comes from"
                    }
                },
                "required": [
                    "audience",
                    "channels"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "page_inspect",
            "description": "Compare an ad, email or post with the page it links to, element by element: promise (H1, title, first screen), price, call to action, audience, offer and language; add query and intent to check that the page answers the search. Run it for every ad or email that links to a page, before handing over. Optional checks on the same page: credibility (who runs it, contact and privacy links, unsourced claims), links (broken internal links, redirects), trust (timers, scarcity, pre-ticked add-ons), price_ladder, crawler (facts missing from raw HTML), data_use (trackers before consent), schema, navigation, promises, touchpoint, tracked_links (UTM), and kind=receipt|shipping|welcome for transactional emails. Give one page source: html, path or url. Every finding quotes the exact copy and page element; fix blocks first. Anything not fetchable or not given lands under missing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ad": {
                        "type": "object",
                        "description": "the ad or email as written; headline or primary_text is required",
                        "properties": {
                            "headline": {
                                "type": "string"
                            },
                            "primary_text": {
                                "type": "string"
                            },
                            "description": {
                                "type": "string"
                            },
                            "cta": {
                                "type": "string",
                                "description": "button text, e.g. Registruotis, Sign up"
                            },
                            "audience": {
                                "type": "string",
                                "description": "who the ad speaks to, in words the page should also use"
                            },
                            "price": {
                                "type": "string",
                                "description": "the price the ad names, e.g. €49 or 1 200,50 €; found in the copy if omitted"
                            },
                            "offer": {
                                "type": "string",
                                "description": "the offer the ad makes, e.g. nemokama konsultacija, free audit"
                            },
                            "language": {
                                "type": "string",
                                "description": "lt or en; detected from the copy if omitted"
                            }
                        }
                    },
                    "url": {
                        "type": "string",
                        "description": "the destination page address; fetched by Rocky"
                    },
                    "path": {
                        "type": "string",
                        "description": "a saved copy of the page in the workspace, e.g. pages/landing.html"
                    },
                    "html": {
                        "type": "string",
                        "description": "the page HTML itself"
                    },
                    "query": {
                        "type": "string",
                        "description": "the search term the visitor typed; checked word by word against H1, title and first screen"
                    },
                    "intent": {
                        "type": "string",
                        "enum": [
                            "informational",
                            "commercial",
                            "transactional",
                            "navigational",
                            "local"
                        ],
                        "description": "what the searcher wants; inferred from query if omitted"
                    },
                    "brand": {
                        "type": "string",
                        "description": "brand or product name, for navigational intent, welcome emails and schema checks"
                    },
                    "checks": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "credibility",
                                "links",
                                "trust",
                                "price_ladder",
                                "crawler",
                                "data_use",
                                "images_off",
                                "schema",
                                "navigation",
                                "promises",
                                "trust_signals",
                                "touchpoint",
                                "listing",
                                "top_tasks",
                                "mobile",
                                "all"
                            ]
                        },
                        "description": "extra checks to run on the page; each adds its own block to the output"
                    },
                    "tiers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "price": {
                                    "type": "string"
                                },
                                "period": {
                                    "type": "string"
                                },
                                "features": {
                                    "type": "array",
                                    "items": {
                                        "type": "string"
                                    }
                                }
                            }
                        },
                        "description": "price ladder tiers when the page's pricing cards cannot be parsed"
                    },
                    "required_facts": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "facts a crawler must see; each comes back html | rendered_only | missing"
                    },
                    "rendered_text": {
                        "type": "string",
                        "description": "the page text from browser_snapshot, to find facts visible only after rendering"
                    },
                    "required_content": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "items a shelf-QR or product page must carry (adds the mobile weight block)"
                    },
                    "kind": {
                        "type": "string",
                        "enum": [
                            "receipt",
                            "shipping",
                            "welcome"
                        ],
                        "description": "check a transactional email for its required fields instead of a landing page"
                    },
                    "expected": {
                        "type": "object",
                        "properties": {
                            "amount": {
                                "type": "number"
                            },
                            "order_ref": {
                                "type": "string"
                            },
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                }
                            }
                        },
                        "description": "the test order's figures to cross-check against the email"
                    },
                    "policy": {
                        "type": "string",
                        "description": "privacy policy as url, workspace path or text; vendors and fields not named in it become findings"
                    },
                    "schema": {
                        "type": "string",
                        "description": "JSON-LD to evaluate as if it were on the page"
                    },
                    "product_name": {
                        "type": "string",
                        "description": "touchpoint check: the product a recipient-facing artefact should name and link"
                    },
                    "tracked_links": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "campaign URLs to check for utm keys, case, consistency and survival through redirects"
                    },
                    "expected_tags": {
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string"
                            },
                            "medium": {
                                "type": "string"
                            },
                            "campaign": {
                                "type": "string"
                            }
                        }
                    },
                    "base_url": {
                        "type": "string",
                        "description": "site address to resolve relative links when the page is given as html or path"
                    },
                    "max_links": {
                        "type": "integer",
                        "description": "internal links to check through the link checker (default 30)"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "claim_check",
            "description": "Check every measurable claim in a draft against the sources: numbers, prices, dates, rankings, credentials. Each claim returns the best passage, its location and a status: supported, number_differs or unsupported. runs/notes/facts.md is read automatically; add files or URLs under sources (HTML table rows and image alt text count as passages). Options: check_scope flags a passage whose qualifier (only, period, place, test condition) the draft dropped (supported_scope_narrower, misleading_inference); quote_sources verifies „quotes“ verbatim in interview notes and the name beside them; own_domains labels each source own/third_party and flags attribution_missing; mode reply checks a thread answer point by point plus spokesperson and thread URL; mode price_anchor checks a struck-through price against evidence; rumor, self_consistency, max_minutes. Always returns fact_sheet, corrections and findings. Quote the result; never call a figure supported yourself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["check", "reply", "price_anchor"], "description": "check (default); reply = a thread answer checked point by point; price_anchor = a struck-through reference price checked against evidence"},
                    "check_scope": {"type": "boolean", "description": "true: a supported claim whose passage holds a qualifier the draft dropped (tik/only, a period, a place, a population, a test condition) becomes supported_scope_narrower with conditions; a draft that generalises (visi, every, always) gets the finding misleading_inference. Default false"},
                    "quote_sources": {"type": "array", "items": {"type": "string"}, "description": "interview notes/transcripts (workspace files or URLs). Sentences inside „…“ \"…\" «…» of 6+ words become claims of kind quote: supported (verbatim), altered or unsupported; a name beside the quote must appear near the passage"},
                    "own_domains": {"type": "array", "items": {"type": "string"}, "description": "the brand's own domains. Each supported claim gets source_domain and party (own | third_party | unknown) and the finding attribution_missing when the sentence does not name the source"},
                    "thread_url": {"type": "string", "description": "mode reply: the thread the reply must stay in"},
                    "criticism_points": {"type": "array", "items": {"type": "string"}, "description": "mode reply: the accusations quoted from the thread; each needs a fact present in reply_text sharing 2 content words (else point_unanswered)"},
                    "facts": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "source": {"type": "string"}}}, "description": "mode reply: the facts the reply rests on; each source (file or URL) is added to sources"},
                    "spokesperson": {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"}}, "description": "mode reply: a named person with a role; empty, generic (komanda, team, support) or a placeholder gives no_spokesperson"},
                    "reply_text": {"type": "string", "description": "mode reply: the reply to post (used as the draft). A URL other than thread_url or a cited source gives thread_mismatch"},
                    "offer_price": {"type": "string", "description": "mode price_anchor: the price charged, e.g. '149 €'"},
                    "reference_price": {"type": "string", "description": "mode price_anchor: the struck-through/compared price; must appear in the evidence (else anchor_unsupported) and exceed the offer"},
                    "reference_kind": {"type": "string", "enum": ["list_price", "competitor", "previous_price", "rrp"], "description": "mode price_anchor: what the reference is; the draft's words (buvo/was, RRP, pas konkurentus, įprasta kaina) must agree (else anchor_misdescribed)"},
                    "evidence": {"type": "string", "description": "mode price_anchor: workspace file or URL that shows the reference price"},
                    "charged_identical": {"type": "boolean", "description": "mode price_anchor: was the reference price actually charged for the identical offer; false gives anchor_never_charged"},
                    "rumor": {"type": "string", "description": "the rumour text; its figures are paired with the draft's correcting figure and its source"},
                    "self_consistency": {"type": "boolean", "description": "true: figures in the draft that disagree about the same subject are listed under conflicts. Default false"},
                    "max_minutes": {"type": "number", "description": "speaking length target; returns length {words, minutes, over_by_words} and the finding too_long"},
                    "wpm": {"type": "number", "description": "words per minute for max_minutes, default 130"},
                                        "draft": {
                        "type": "string",
                        "description": "the draft text; or give draft_path"
                    },
                    "draft_path": {
                        "type": "string",
                        "description": "workspace file (.md, .txt, .html) holding the draft"
                    },
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "workspace files (.md .txt .html .json .csv) and/or http(s) URLs to check against; runs/notes/facts.md is added automatically"
                    },
                    "facts_path": {
                        "type": "string",
                        "description": "facts file to add, default runs/notes/facts.md"
                    },
                    "include_facts": {
                        "type": "boolean",
                        "description": "false to check only the given sources, default true"
                    },
                    "language": {
                        "type": "string",
                        "enum": [
                            "lt",
                            "en"
                        ],
                        "description": "how an ambiguous separator like 12,400 is read; detected from the draft when omitted"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "customer_economics",
            "description": "The economics of one customer over time and of the money spent to win them. Call it instead of unit_economics whenever the question is per customer, over several periods, or about acquisition spend: CLV against CAC (contribution per period, expected lifetime, CLV over a horizon with churn and optional discounting, payback period, acquire / marginal / do not acquire against a target ratio, the maximum CAC and the maximum churn that still pay back), returns-adjusted contribution, a category spend ceiling, a price cut as acquisition funding, a subsidised entry offer with paid follow-ons (break-even attach rate), a subscription rollout table to a target, acquisition channels ranked with a 'profitable but secondary' flag, and the value of research before a decision. Give amounts in one currency, per purchase or per period. Only the blocks whose inputs are present are computed; a block with inputs missing says so under missing, and when the cost or retention is unknown the result gives a conditional table: ask the user (clarify) or state the assumption.",
            "parameters": {
                "type": "object",
                "properties": {
                    "price": {
                        "type": "number",
                        "description": "price per purchase (use with variable_cost or margin_pct)"
                    },
                    "variable_cost": {
                        "type": "number",
                        "description": "cost per purchase that scales with sales"
                    },
                    "margin_pct": {
                        "type": "number",
                        "description": "alternative to variable_cost: contribution margin in percent"
                    },
                    "arpu": {
                        "type": "number",
                        "description": "alternative to price: revenue per customer per period (subscriptions); use with gross_margin_pct"
                    },
                    "gross_margin_pct": {
                        "type": "number",
                        "description": "gross margin on arpu in percent"
                    },
                    "purchases_per_period": {
                        "type": "number",
                        "description": "purchases per period per customer, default 1"
                    },
                    "period": {
                        "type": "string",
                        "enum": [
                            "month",
                            "year"
                        ],
                        "description": "unit of every per-period figure, default month"
                    },
                    "churn_pct_per_period": {
                        "type": "number",
                        "description": "share of customers lost each period"
                    },
                    "periods_retained": {
                        "type": "number",
                        "description": "alternative to churn: average periods a customer stays"
                    },
                    "annual_churn_pct": {
                        "type": "number",
                        "description": "alternative to churn: yearly churn, converted to the period"
                    },
                    "service_cost_per_customer_per_period": {
                        "type": "number",
                        "description": "cost of serving a customer each period"
                    },
                    "cac": {
                        "type": "number",
                        "description": "acquisition cost per new customer"
                    },
                    "acquisition_spend": {
                        "type": "number",
                        "description": "alternative to cac: spend per period, with new_customers"
                    },
                    "new_customers": {
                        "type": "number",
                        "description": "customers won per period for acquisition_spend"
                    },
                    "target_clv_cac_ratio": {
                        "type": "number",
                        "description": "ratio needed to call it 'acquire', default 3"
                    },
                    "horizon_periods": {
                        "type": "integer",
                        "description": "periods counted in CLV, default 36 months or 3 years"
                    },
                    "discount_rate_pct_per_year": {
                        "type": "number",
                        "description": "optional yearly discount rate for future periods"
                    },
                    "payback_target_periods": {
                        "type": "integer",
                        "description": "back-solves the maximum churn that still pays back within this many periods"
                    },
                    "return_rate_pct": {
                        "type": "number",
                        "description": "returns block: share of orders returned"
                    },
                    "return_cost_per_return": {
                        "type": "number",
                        "description": "reverse shipping and handling per return"
                    },
                    "refund_share_pct": {
                        "type": "number",
                        "description": "share of returns refunded (rest exchanged), default 100"
                    },
                    "exchange_cost": {
                        "type": "number",
                        "description": "re-shipping cost per exchange"
                    },
                    "category_spend_per_buyer_per_period": {
                        "type": "number",
                        "description": "ceiling block: what a buyer spends on the whole category per period"
                    },
                    "share_of_wallet_pct": {
                        "type": "number",
                        "description": "share of that spend obtainable"
                    },
                    "customers": {
                        "type": "number",
                        "description": "funding block: current customers, for the contribution pool"
                    },
                    "new_price": {
                        "type": "number",
                        "description": "price being considered as acquisition funding"
                    },
                    "acquisition_price_cut_pct": {
                        "type": "number",
                        "description": "alternative to new_price: percent off"
                    },
                    "entry_price": {
                        "type": "number",
                        "description": "follow-on block: entry offer price, 0 allowed"
                    },
                    "entry_variable_cost": {
                        "type": "number",
                        "description": "cost of the entry offer per entrant"
                    },
                    "variable_cost_items": {
                        "type": "object",
                        "description": "{name: cost per user per period}; summed to marginal_cost_per_user and used as entry_variable_cost when that is absent"
                    },
                    "users": {
                        "type": "number",
                        "description": "users served, for the serving cost per period"
                    },
                    "follow_on": {
                        "type": "object",
                        "description": "{price, variable_cost, units_per_period (1), attach_rate_pct, periods (horizon), churn_pct_per_period}"
                    },
                    "subscribers_now": {
                        "type": "number",
                        "description": "rollout block: subscribers today"
                    },
                    "target_subscribers": {
                        "type": "number",
                        "description": "subscribers to reach"
                    },
                    "new_per_period": {
                        "type": "number",
                        "description": "subscribers added per period; back-solved from the target when absent"
                    },
                    "channels": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "channels block: [{name, spend, conversions, revenue?}]; revenue derived from price when absent; margin from margin_pct or channel_margin_pct"
                    },
                    "channel_margin_pct": {
                        "type": "number",
                        "description": "margin to apply to channel revenue when it differs from the customer margin"
                    },
                    "decision_amount": {
                        "type": "number",
                        "description": "research block: money at stake in the decision"
                    },
                    "p_wrong": {
                        "type": "number",
                        "description": "probability (0-1) the decision is wrong without research"
                    },
                    "loss_if_wrong_share": {
                        "type": "number",
                        "description": "share (0-1) of decision_amount lost if wrong, default 1"
                    },
                    "research_options": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "[{name, cost, p_wrong_after}]"
                    },
                    "currency": {
                        "type": "string",
                        "description": "EUR by default"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "repeat_buying",
            "description": "Brand-growth and repeat-buying arithmetic from the user's own transaction file (CSV or XLSX in the workspace, one row per order: customer id, date, optional amount, order_id, sku, brand, units) or from given numbers. Call it for any question about loyalty, repeat rate, penetration, cohorts and retention, light versus heavy buyers, buyers entering or leaving, brand duplication and repertoire, whether a brand's loyalty fits its size (double jeopardy), market share versus sales growth, or sales spikes versus a baseline — never count these by eye or quote a repeat rate without saying which definition. Pick analysis: summary, frequency, repeat (definition A = 2+ purchases in a window vs definition B = bought in period 1 and again in period 2), skip_return, cohorts (optional baseline vs test window with new vs returning buyers), entrants_leavers (cumulative distinct buyers, penetration when universe is given, double count from summing periods), duplication (needs brand column), spikes (periods above a rolling-median baseline). No file needed for double_jeopardy and share_vs_growth. Results carry window, rows_used, missing, warnings (small_base below min_buyers), method and a one-sentence summary to quote; report missing and warnings, do not fill them in.",
            "parameters": {
                "type": "object",
                "properties": {
                    "analysis": {
                        "type": "string",
                        "enum": [
                            "summary",
                            "frequency",
                            "repeat",
                            "skip_return",
                            "cohorts",
                            "entrants_leavers",
                            "duplication",
                            "spikes",
                            "double_jeopardy",
                            "share_vs_growth"
                        ],
                        "description": "which question to answer; default summary"
                    },
                    "path": {
                        "type": "string",
                        "description": "CSV or XLSX transaction file relative to the workspace (all analyses except double_jeopardy and share_vs_growth)"
                    },
                    "sheet": {
                        "type": "string",
                        "description": "XLSX sheet name; first sheet by default"
                    },
                    "columns": {
                        "type": "object",
                        "description": "header names, case-insensitive: {customer, date, amount?, order_id?, sku?, brand?, units?}; common names are found automatically",
                        "properties": {
                            "customer": {
                                "type": "string"
                            },
                            "date": {
                                "type": "string"
                            },
                            "amount": {
                                "type": "string"
                            },
                            "order_id": {
                                "type": "string"
                            },
                            "sku": {
                                "type": "string"
                            },
                            "brand": {
                                "type": "string"
                            },
                            "units": {
                                "type": "string"
                            }
                        }
                    },
                    "period": {
                        "type": "string",
                        "enum": [
                            "week",
                            "month",
                            "quarter",
                            "year"
                        ],
                        "description": "period for per-period tables; default month"
                    },
                    "date_from": {
                        "type": "string",
                        "description": "ISO date; rows before it are left out"
                    },
                    "date_to": {
                        "type": "string",
                        "description": "ISO date; rows after it are left out"
                    },
                    "sku": {
                        "type": "string",
                        "description": "keep only rows with this sku (needs columns.sku)"
                    },
                    "focus_brand": {
                        "type": "string",
                        "description": "brand to report share of requirements for (duplication) or to give a verdict on (double_jeopardy)"
                    },
                    "min_buyers": {
                        "type": "integer",
                        "description": "bases below this are flagged small_base; default 30"
                    },
                    "universe": {
                        "type": "integer",
                        "description": "number of households or customers in the market, for penetration"
                    },
                    "universe_source": {
                        "type": "string",
                        "description": "where the universe count comes from; required with universe"
                    },
                    "bins": {
                        "type": "array",
                        "items": {},
                        "description": "frequency: purchases-per-buyer bins, default [1, 2, 3, 4, '5+']"
                    },
                    "window_a": {
                        "type": "object",
                        "description": "repeat: {from, to} for definition A and period 1 of definition B"
                    },
                    "window_b": {
                        "type": "object",
                        "description": "repeat: {from, to} for period 2 of definition B"
                    },
                    "pack_life_days": {
                        "type": "number",
                        "description": "repeat: product pack life; recommended_window_days = max(median days between purchases, pack_life_days)"
                    },
                    "intermittent_threshold_pct": {
                        "type": "number",
                        "description": "skip_return: mean skip-then-return share at or above which buying is called intermittent; default 10"
                    },
                    "baseline_from": {
                        "type": "string",
                        "description": "cohorts: ISO start of the baseline window"
                    },
                    "baseline_to": {
                        "type": "string",
                        "description": "cohorts: ISO end of the baseline window"
                    },
                    "test_from": {
                        "type": "string",
                        "description": "cohorts: ISO start of the test (launch, campaign) window"
                    },
                    "test_to": {
                        "type": "string",
                        "description": "cohorts: ISO end of the test window"
                    },
                    "min_baseline_periods": {
                        "type": "integer",
                        "description": "cohorts: baselines shorter than this are flagged short_baseline; default 4"
                    },
                    "value_column": {
                        "type": "string",
                        "description": "spikes: column with units or revenue per row, summed per period"
                    },
                    "measure": {
                        "type": "string",
                        "description": "spikes: name of what value_column measures"
                    },
                    "baseline_window": {
                        "type": "integer",
                        "description": "spikes: number of previous periods whose median is the baseline; default 8"
                    },
                    "spike_factor": {
                        "type": "number",
                        "description": "spikes: a period above baseline × this factor is a spike; default 1.5"
                    },
                    "change_date": {
                        "type": "string",
                        "description": "spikes: ISO date; reports the non-spike level before and after it"
                    },
                    "brands": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "double_jeopardy: [{name, penetration_pct (or buyers with universe), loyalty (or repeat_pct / retention_pct / frequency)}], at least 2, better 4+"
                    },
                    "loyalty_kind": {
                        "type": "string",
                        "enum": [
                            "repeat",
                            "frequency",
                            "retention"
                        ],
                        "description": "double_jeopardy: what the loyalty figure is; default repeat"
                    },
                    "tolerance_pct": {
                        "type": "number",
                        "description": "double_jeopardy: residual within this is 'in line with size'; default 5"
                    },
                    "brand": {
                        "type": "object",
                        "description": "share_vs_growth: {values: [one sales figure per period], label?}"
                    },
                    "category": {
                        "type": "object",
                        "description": "share_vs_growth: {values: [category sales per period, same length], label?}"
                    },
                    "labels": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "share_vs_growth: one period label per value"
                    }
                },
                "required": [
                    "analysis"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "table_analyze",
            "description": "Read the user's own spreadsheet (CSV or XLSX in the workspace) as data and answer one question about it with real numbers: survey and respondent audits, service-quality gaps by branch or channel, complaint themes, search-term brand splits, press benchmarks across periods, before/after checks, lead-time distributions, sampling coverage. Use it for any spreadsheet question that is not a funnel (funnels go to funnel_analyze) or a transaction panel (repeat_buying), and never compute a count, share, mean, difference or percentage from a file by eye or with your own arithmetic. Pick op: describe, group (count/sum/mean/median/share/distinct by columns or week:/month: of a date column, with gap best−worst), crosstab (cells, row %, chi-square when every expected cell ≥ 5), before_after (means around split_date, Welch t, optional normalize_by), period_compare (latest vs previous and vs year ago), proportion_diff (two groups: difference in pp, Wald 95 % CI, z-test; without group_column: one proportion with Wilson CI), intervals (days between two date columns), coverage (list shares vs population_shares), text_themes (regex themes with samples), search_terms_split (branded vs non-branded spend, CPA, ROAS from a Google Ads export). Every result reports the dates actually covered, rows used, groups below min_rows as thin (never ranked), blanks and bad cells under missing, the method behind any statistic, and a one-sentence summary to quote as is.",
            "parameters": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": [
                            "describe",
                            "group",
                            "crosstab",
                            "before_after",
                            "period_compare",
                            "proportion_diff",
                            "intervals",
                            "coverage",
                            "text_themes",
                            "search_terms_split"
                        ],
                        "description": "what to compute"
                    },
                    "path": {
                        "type": "string",
                        "description": "CSV or XLSX file relative to the workspace"
                    },
                    "sheet": {
                        "type": "string",
                        "description": "XLSX sheet name; first sheet by default"
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "row number (from 1) holding the column names when the file has lines above the header; detected automatically otherwise"
                    },
                    "date_column": {
                        "type": "string",
                        "description": "date column for the window, before_after and week:/month: buckets; rows with unreadable dates are left out and reported"
                    },
                    "date_from": {
                        "type": "string",
                        "description": "YYYY-MM-DD, inclusive"
                    },
                    "date_to": {
                        "type": "string",
                        "description": "YYYY-MM-DD, inclusive"
                    },
                    "filter": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {
                                    "type": "string"
                                },
                                "op": {
                                    "type": "string",
                                    "enum": [
                                        "==",
                                        "!=",
                                        "contains",
                                        ">",
                                        "<",
                                        ">=",
                                        "<=",
                                        "in"
                                    ]
                                },
                                "value": {}
                            },
                            "required": [
                                "column",
                                "value"
                            ]
                        },
                        "description": "row filters applied after the date window; text compares case-insensitively, numbers numerically; 'in' takes a list"
                    },
                    "min_rows": {
                        "type": "integer",
                        "description": "groups and cells with fewer rows are reported as thin and never ranked (default 30)"
                    },
                    "by": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "group/coverage/text_themes/before_after: grouping column(s), or week:<date column> / month:<date column> / year:<date column> / weekday:<date column>"
                    },
                    "metric": {
                        "type": "string",
                        "description": "group/before_after: numeric column to aggregate"
                    },
                    "agg": {
                        "type": "string",
                        "enum": [
                            "count",
                            "sum",
                            "mean",
                            "median",
                            "share",
                            "distinct"
                        ],
                        "description": "group: aggregation (default count, or sum when metric is given); period_compare: sum or mean"
                    },
                    "distinct_by": {
                        "type": "string",
                        "description": "group with agg distinct: id column whose unique values are counted"
                    },
                    "top": {
                        "type": "integer",
                        "description": "group: how many groups to list (default 20)"
                    },
                    "population_shares": {
                        "type": "object",
                        "description": "coverage, or group with agg mean: {group: fraction} of the real population; gives ratio/status per group or a weighted mean"
                    },
                    "rows": {
                        "type": "string",
                        "description": "crosstab: row column (≤ 20 levels)"
                    },
                    "cols": {
                        "type": "string",
                        "description": "crosstab: column column (≤ 20 levels)"
                    },
                    "split_date": {
                        "type": "string",
                        "description": "before_after: YYYY-MM-DD; rows before it are before, on or after it are after"
                    },
                    "normalize_by": {
                        "type": "string",
                        "description": "before_after: divide metric by this column per row, e.g. m2 or shipments"
                    },
                    "period_column": {
                        "type": "string",
                        "description": "period_compare: quarter/month/date column (2025-Q1, Q1 2025, 2026-03, 2026-03-01) or month:<date column>"
                    },
                    "metrics": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "period_compare: numeric columns to compare"
                    },
                    "sample_column": {
                        "type": "string",
                        "description": "period_compare: column with the sample size behind each period, e.g. merchants"
                    },
                    "group_column": {
                        "type": "string",
                        "description": "proportion_diff: column with exactly two groups (or name them with group_a/group_b); omit for a single proportion"
                    },
                    "group_a": {
                        "type": "string",
                        "description": "proportion_diff: reference group (control)"
                    },
                    "group_b": {
                        "type": "string",
                        "description": "proportion_diff: compared group; diff = p_b − p_a"
                    },
                    "outcome_column": {
                        "type": "string",
                        "description": "proportion_diff: column holding the outcome"
                    },
                    "positive_values": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "proportion_diff: values counted as positive (default yes/true/1/taip)"
                    },
                    "invited": {
                        "type": "integer",
                        "description": "proportion_diff without group_column: how many were invited, for the response rate"
                    },
                    "start_column": {
                        "type": "string",
                        "description": "intervals: start date column"
                    },
                    "end_column": {
                        "type": "string",
                        "description": "intervals: end date column"
                    },
                    "text_column": {
                        "type": "string",
                        "description": "text_themes: free-text column"
                    },
                    "patterns": {
                        "type": "object",
                        "description": "text_themes: {theme: regex}, matched case-insensitively"
                    },
                    "term_column": {
                        "type": "string",
                        "description": "search_terms_split: search term column; detected from Google Ads headers by default"
                    },
                    "brand_terms": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "search_terms_split: brand name and spellings, matched as case-insensitive substrings"
                    },
                    "cost_column": {
                        "type": "string",
                        "description": "search_terms_split: override for the cost column"
                    },
                    "conversions_column": {
                        "type": "string",
                        "description": "search_terms_split: override for the conversions column"
                    },
                    "value_column": {
                        "type": "string",
                        "description": "search_terms_split: override for the conversion value column"
                    }
                },
                "required": [
                    "op",
                    "path"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "seo_inspect",
            "description": "The search-channel checks to run before paying for traffic, done by code instead of eyeballing titles, URLs, robots.txt and exports. Pick the mode for the question: 'snippet' (are titles and descriptions descriptive, unique, within 60/160 characters, does the snippet's promise and price appear in the H1 and first screen; add query to read competing titles), 'urls' (are addresses readable, do canonical tags, noindex and redirects point one way), 'crawl_rules' (does robots.txt block an important page for Googlebot, which important pages are missing from the sitemaps, which sitemap urls robots blocks), 'structured_data' (does the JSON-LD price, availability and rating match what the page shows), 'crawler_content' (which required facts exist only in rendered_text from browser_snapshot and not in the HTML; images without alt), 'templates' (3-10 pages from one template: unique text share, decision fields present), 'serp' (rank of owned domain and competitors per query or per generated ladder, rule-based intent per result, ambiguous_modifier when no intent reaches 60%), 'keywords' (a Google Ads search-terms CSV/XLSX: term clusters, ad groups that mix needs, negative-keyword candidates with estimated saving). Pages are given as html, a workspace path or a url; a url or query that could not be fetched or searched lands under missing, never guessed. Findings are ordered block / warn / info; fix blocks before buying traffic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": [
                            "snippet",
                            "urls",
                            "crawl_rules",
                            "structured_data",
                            "crawler_content",
                            "templates",
                            "serp",
                            "keywords"
                        ],
                        "description": "which check to run"
                    },
                    "pages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {
                                    "type": "string"
                                },
                                "path": {
                                    "type": "string"
                                },
                                "html": {
                                    "type": "string"
                                }
                            }
                        },
                        "description": "snippet, templates: the pages, one source each"
                    },
                    "url": {
                        "type": "string",
                        "description": "structured_data, crawler_content: the page address"
                    },
                    "path": {
                        "type": "string",
                        "description": "structured_data, crawler_content: workspace HTML file; urls: text file with one url per line; keywords: the search-terms export (.csv/.xlsx)"
                    },
                    "html": {
                        "type": "string",
                        "description": "structured_data, crawler_content: the page source"
                    },
                    "brand": {
                        "type": "string",
                        "description": "snippet: brand name, so brand words do not count as the subject"
                    },
                    "query": {
                        "type": "string",
                        "description": "snippet: one search query whose top results give the competing titles"
                    },
                    "urls": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "urls: the addresses to check (max 200)"
                    },
                    "sitemap_url": {
                        "type": "string",
                        "description": "urls: take the addresses from this sitemap; crawl_rules: an extra sitemap to read"
                    },
                    "html_by_url": {
                        "type": "object",
                        "description": "urls: {url: html} for pages already fetched"
                    },
                    "site": {
                        "type": "string",
                        "description": "crawl_rules: the site root, e.g. https://www.shop.lt"
                    },
                    "important_urls": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "crawl_rules: the pages that must be crawlable and in the sitemap"
                    },
                    "staging_hosts": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "crawl_rules: staging hostnames that must not appear in robots.txt or the urls"
                    },
                    "schema": {
                        "type": "string",
                        "description": "structured_data: JSON-LD to evaluate as if it were on the page"
                    },
                    "rendered_text": {
                        "type": "string",
                        "description": "crawler_content: the page text after JavaScript, from browser_snapshot"
                    },
                    "required_facts": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "crawler_content: facts (specs, prices, guarantees) that must be in the HTML"
                    },
                    "decision_fields": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "templates: override of price, availability, delivery, reviews, specs, images, local_info"
                    },
                    "queries": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "serp: the queries to search"
                    },
                    "brand_domain": {
                        "type": "string",
                        "description": "serp: the brand's domain (counted as owned)"
                    },
                    "product": {
                        "type": "string",
                        "description": "serp: product name for generated model and price_specs queries"
                    },
                    "category": {
                        "type": "string",
                        "description": "serp: category phrase for generated category and shortlist queries"
                    },
                    "lang": {
                        "type": "string",
                        "description": "serp: lt or en for generated queries"
                    },
                    "stages": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "serp: subset of category, shortlist, model, price_specs"
                    },
                    "owned_domains": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "competitors": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "serp: results read per query, default 8"
                    },
                    "term_column": {
                        "type": "string",
                        "description": "keywords: search-term column; auto-detected from Google Ads headers (EN/LT) when omitted"
                    },
                    "clicks_column": {
                        "type": "string"
                    },
                    "cost_column": {
                        "type": "string"
                    },
                    "conversions_column": {
                        "type": "string"
                    },
                    "value_column": {
                        "type": "string"
                    },
                    "group_column": {
                        "type": "string",
                        "description": "keywords: ad-group column for split candidates"
                    },
                    "sheet": {
                        "type": "string",
                        "description": "keywords: XLSX sheet name"
                    },
                    "protect_terms": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "keywords: words whose terms are never proposed as negatives"
                    },
                    "brand_terms": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "keywords: brand words; their clusters are marked and excluded from negatives"
                    },
                    "min_cost": {
                        "type": "number",
                        "description": "keywords: a term must have spent at least this to be a negative candidate, default 10"
                    },
                    "max_conversions": {
                        "type": "number",
                        "description": "keywords: default 0"
                    },
                    "min_share": {
                        "type": "number",
                        "description": "keywords: a cluster under this share of its ad group's spend is a split candidate, default 0.2"
                    }
                },
                "required": [
                    "mode"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "survey_check",
            "description": "Audit a questionnaire by code before anyone is asked a question. Call it whenever a worker drafts survey, poll, interview-guide or awareness-study questions, or a blind vs branded comparison protocol, and before writing the deliverable; also call it on the client's existing survey when the goal is to review one. Write the questions as JSON first (this tool never invents them). It flags double_barrelled, leading, vague_quantifier, no_escape_option, scale_mismatch, jargon, motive_question, uncorroborated, recall_event, single_where_multi, open_field_short, multi_no_primary, unrouted_experience_question, dead_end, familiarity_blocks_missing (an awareness survey must ask unaided recall, aided familiarity, ad recall and source of first contact separately), order_not_randomised and arm_not_blinded. Returns findings with level block|warn|info, routing paths with counts, counts by code, estimated_minutes, a five-person pilot_plan and clean=true only when nothing blocks or warns. Fix every block, then re-run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "description": "the questionnaire in order; required unless only a protocol is checked",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string"
                                },
                                "text": {
                                    "type": "string",
                                    "description": "the question as the respondent reads it, Lithuanian or English"
                                },
                                "type": {
                                    "type": "string",
                                    "enum": [
                                        "single",
                                        "multi",
                                        "scale",
                                        "open",
                                        "number"
                                    ]
                                },
                                "options": {
                                    "type": "array",
                                    "items": {
                                        "type": "string"
                                    }
                                },
                                "scale": {
                                    "type": "object",
                                    "properties": {
                                        "min": {
                                            "type": "number"
                                        },
                                        "max": {
                                            "type": "number"
                                        },
                                        "labels": {
                                            "type": "array",
                                            "items": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                },
                                "ask_if": {
                                    "type": "string",
                                    "description": "routing condition on an earlier question or the screener, e.g. 'screener = Taip', 'q3 in [A, B]'"
                                },
                                "requires_experience": {
                                    "type": "boolean"
                                },
                                "exclusive": {
                                    "type": "boolean"
                                },
                                "main_choice_followup": {
                                    "type": "string"
                                },
                                "corroborated_by": {
                                    "type": "string",
                                    "description": "for why/attitude questions: id of the behaviour question that backs it"
                                },
                                "max_len": {
                                    "type": "number"
                                }
                            },
                            "required": [
                                "id",
                                "text",
                                "type"
                            ]
                        }
                    },
                    "screener": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string"
                            },
                            "question": {
                                "type": "string"
                            },
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                }
                            }
                        }
                    },
                    "language": {
                        "type": "string",
                        "enum": [
                            "lt",
                            "en"
                        ]
                    },
                    "audience": {
                        "type": "string"
                    },
                    "jargon": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "protocol": {
                        "type": "object",
                        "description": "blind comparison design: arms with label, blinded, samples and order randomised|counterbalanced|fixed, plus n_per_arm and metric",
                        "properties": {
                            "arms": {
                                "type": "array",
                                "items": {
                                    "type": "object"
                                }
                            },
                            "n_per_arm": {
                                "type": "integer"
                            },
                            "metric": {
                                "type": "string"
                            }
                        }
                    }
                },
                "required": [
                    "questions"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "outcome_scores",
            "description": "Validate Jobs-to-be-Done outcome statements and compute opportunity scores, or check a job map. Call it whenever the worker writes customer outcomes from VOC (each must be direction + metric + object + situation; generic words like 'geresnis', 'patikimas', 'better', 'fast' and solution words like 'app', 'funkcija', 'feature' are rejected), whenever importance/satisfaction data exists, or when the goal asks for a job map or customer journey. Scores: opportunity = importance + max(importance − satisfaction, 0); class underserved (≥ threshold_underserved, default 12 on a 10-point scale), appropriately served, or overserved (satisfaction > importance). With satisfaction per brand it returns gap_vs_best_competitor and comparable (only when n ≥ 30 for both brands); overserved outcomes with cost_per_unit are listed under excess as a saving to test. Mode job_map reports missing phases out of the eight, phase order, steps repeated/skipped/out_of_order today and steps with no evidence_url. Quote the score, class and n; never estimate a missing satisfaction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "outcomes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "direction": {
                                    "type": "string",
                                    "enum": [
                                        "minimize",
                                        "increase"
                                    ]
                                },
                                "metric": {
                                    "type": "string",
                                    "enum": [
                                        "time",
                                        "likelihood",
                                        "number",
                                        "cost",
                                        "other"
                                    ]
                                },
                                "object": {
                                    "type": "string"
                                },
                                "situation": {
                                    "type": "string"
                                },
                                "importance": {
                                    "type": "number"
                                },
                                "satisfaction": {
                                    "description": "0..scale_max, or {brand: score}"
                                },
                                "n": {
                                    "description": "respondents, a number or {brand: n}"
                                },
                                "cost_per_unit": {
                                    "type": "number"
                                }
                            },
                            "required": [
                                "direction",
                                "metric",
                                "object"
                            ]
                        }
                    },
                    "scale_max": {
                        "type": "number"
                    },
                    "focus_brand": {
                        "type": "string"
                    },
                    "threshold_underserved": {
                        "type": "number"
                    },
                    "mode": {
                        "type": "string",
                        "enum": [
                            "outcomes",
                            "job_map"
                        ]
                    },
                    "job": {
                        "type": "string"
                    },
                    "executor": {
                        "type": "string"
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "phase": {
                                    "type": "string",
                                    "enum": [
                                        "define",
                                        "locate",
                                        "prepare",
                                        "confirm",
                                        "execute",
                                        "monitor",
                                        "modify",
                                        "conclude"
                                    ]
                                },
                                "title": {
                                    "type": "string"
                                },
                                "outcomes": {
                                    "type": "array"
                                },
                                "today": {
                                    "type": "string",
                                    "enum": [
                                        "ok",
                                        "repeated",
                                        "skipped",
                                        "out_of_order",
                                        "unknown"
                                    ]
                                },
                                "evidence_url": {
                                    "type": "string"
                                }
                            },
                            "required": [
                                "phase",
                                "title"
                            ]
                        }
                    },
                    "language": {
                        "type": "string",
                        "enum": [
                            "lt",
                            "en"
                        ]
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "research_plan",
            "description": "Design a concept test or a buying-criteria survey the marketer can field; it never runs research. Call it when a lead-user or new idea must be validated with typical buyers (kind concept_test: at least two concepts with price and is_lead_user) or when focus-group or review criteria are to be checked in a survey before they become headlines (kind survey: criteria with source and n_mentions, a screener and the product). concept_test returns the quota per concept, sample sufficiency through experiment_plan from a stated top-two-box baseline (default 30 %) and the caller's minimum_effect, an instrument of open benefit, open use and 1–5 intent questions, and a decision rule with min_comprehension 0.7 and top_two_box_min 0.4 labelled as defaults; findings single_concept, lead_user_only, sample_too_small, no_follow_up. survey returns one anchored frequency question per criterion plus an open question, routed by the screener, a quota of at least 100 qualified respondents, and single_voice_criterion for anything one person said. Both run survey_check on the instrument and return its findings under instrument_check.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "concept_test",
                            "survey"
                        ]
                    },
                    "population": {
                        "type": "object",
                        "properties": {
                            "description": {
                                "type": "string"
                            },
                            "size": {
                                "type": "integer"
                            },
                            "reach": {
                                "type": "string"
                            }
                        }
                    },
                    "sample_n": {
                        "type": "integer"
                    },
                    "language": {
                        "type": "string",
                        "enum": [
                            "lt",
                            "en"
                        ]
                    },
                    "concepts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "price": {
                                    "type": "number"
                                },
                                "is_lead_user": {
                                    "type": "boolean"
                                }
                            },
                            "required": [
                                "name"
                            ]
                        }
                    },
                    "minimum_effect": {
                        "type": "number"
                    },
                    "effect_kind": {
                        "type": "string",
                        "enum": [
                            "absolute",
                            "relative"
                        ]
                    },
                    "baseline_top_two_box_pct": {
                        "type": "number"
                    },
                    "measures": {
                        "type": "object",
                        "properties": {
                            "preference": {
                                "type": "string",
                                "enum": [
                                    "rank",
                                    "choose_one"
                                ]
                            },
                            "design": {
                                "type": "string",
                                "enum": [
                                    "monadic",
                                    "sequential"
                                ]
                            }
                        }
                    },
                    "follow_up": {
                        "type": "object",
                        "properties": {
                            "launch_date": {
                                "type": "string"
                            },
                            "metric": {
                                "type": "string"
                            }
                        }
                    },
                    "min_comprehension": {
                        "type": "number"
                    },
                    "top_two_box_min": {
                        "type": "number"
                    },
                    "horizon": {
                        "type": "string"
                    },
                    "criteria": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string"
                                },
                                "source": {
                                    "type": "string"
                                },
                                "n_mentions": {
                                    "type": "integer"
                                }
                            },
                            "required": [
                                "text"
                            ]
                        }
                    },
                    "screener": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string"
                            },
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                }
                            },
                            "qualify": {
                                "type": "string"
                            }
                        }
                    },
                    "product": {
                        "type": "string"
                    },
                    "questions": {
                        "type": "array"
                    }
                },
                "required": [
                    "kind",
                    "population",
                    "sample_n"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "media_plan",
            "description": "Media-budget arithmetic in one tool with a mode; call it instead of computing in prose. ledger: budget lines → shares per channel on a media-only and an all-in basis, brand/activation split with other teams' always-on spend reconciled. sov: spend + market share → share of voice, share of market, excess share of voice in points, spend per share point, spend for a target ESOV. budget: activities with unit costs and expected results → total, coverage of the objective, cost per result, headroom or shortfall, cut candidates, split against a sourced B2B (46/54) or B2C (60/40) benchmark; adjustments applied once. offers: proposals priced as served CPM, viewable CPM, CPC, CPL or CPA → one denominator; unnamed payment events are not ranked. volume: periods with population, penetration, purchases per buyer, units per purchase → computed volume, reconciliation, change split into penetration / frequency / basket effects. calendar: year + always_on + occasions (date or key: kaledos, motinos_diena, black_friday, velykos, naujieji, mokslo_metai, jonines, valentino, kovo_8, tevo_diena, vasario_16, kovo_11, helovinas, velines) → 52-week table, totals, overlaps, short leads, budget cap. channel_fit: platform, segment, content_job, evidence with source_url, posts and hours → verdict fit / stretch / unfit / unknown with capacity check. classify: spend rows with creative_text → brand / activation / mixed by the creative's own markers, never by channel. Every mode returns missing, warnings and a summary sentence to quote as is.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": [
                            "ledger",
                            "sov",
                            "budget",
                            "offers",
                            "volume",
                            "calendar",
                            "channel_fit",
                            "classify"
                        ]
                    },
                    "currency": {
                        "type": "string",
                        "description": "currency label, default €"
                    },
                    "period": {
                        "type": "string",
                        "description": "ledger / sov: period label"
                    },
                    "lines": {
                        "type": "array",
                        "description": "ledger: budget lines",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string"
                                },
                                "channel": {
                                    "type": "string"
                                },
                                "cost_type": {
                                    "type": "string",
                                    "enum": [
                                        "media",
                                        "production",
                                        "fees",
                                        "tech",
                                        "other"
                                    ]
                                },
                                "team": {
                                    "type": "string"
                                },
                                "amount": {
                                    "type": "number"
                                },
                                "role": {
                                    "type": "string",
                                    "enum": [
                                        "brand",
                                        "activation"
                                    ]
                                }
                            },
                            "required": [
                                "label",
                                "channel",
                                "amount"
                            ]
                        }
                    },
                    "always_on": {
                        "description": "ledger: other teams' always-on blocks [{label, team, channel, monthly_amount, months | amount, role?}]; calendar: {weekly_budget, posts_per_week, content_types?}"
                    },
                    "quoted_shares": {
                        "type": "array",
                        "description": "ledger: shares someone quoted, to check against both bases",
                        "items": {
                            "type": "object",
                            "properties": {
                                "channel": {
                                    "type": "string"
                                },
                                "pct": {
                                    "type": "number"
                                },
                                "basis": {
                                    "type": "string"
                                }
                            }
                        }
                    },
                    "mentioned_spend": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "ledger: spend names the brief mentions — flagged activation_uncounted when no line carries them"
                    },
                    "brands": {
                        "type": "array",
                        "description": "sov: one object per brand, exactly one with is_own",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "share_of_market_pct": {
                                    "type": "number"
                                },
                                "media_spend": {
                                    "type": "number"
                                },
                                "is_own": {
                                    "type": "boolean"
                                }
                            },
                            "required": [
                                "name",
                                "media_spend"
                            ]
                        }
                    },
                    "brand_spend": {
                        "type": "number",
                        "description": "sov: own spend, alternative to brands"
                    },
                    "competitor_spends": {
                        "type": "object",
                        "description": "sov: {competitor name: spend}",
                        "additionalProperties": {
                            "type": "number"
                        }
                    },
                    "category_total_spend": {
                        "type": "number",
                        "description": "sov: whole-category media spend"
                    },
                    "market_share_pct": {
                        "type": "number",
                        "description": "sov: own market share (with brand_spend)"
                    },
                    "share_basis": {
                        "type": "string",
                        "enum": [
                            "value",
                            "volume"
                        ]
                    },
                    "planned_spend": {
                        "type": "number",
                        "description": "sov: spend under consideration"
                    },
                    "target_esov_points": {
                        "type": "number",
                        "description": "sov: ESOV to reach → spend_for_target_esov"
                    },
                    "series": {
                        "type": "array",
                        "description": "sov: periods for ESOV vs next-period share change",
                        "items": {
                            "type": "object",
                            "properties": {
                                "period": {
                                    "type": "string"
                                },
                                "brand_spend": {
                                    "type": "number"
                                },
                                "category_total_spend": {
                                    "type": "number"
                                },
                                "market_share_pct": {
                                    "type": "number"
                                }
                            },
                            "required": [
                                "brand_spend",
                                "category_total_spend",
                                "market_share_pct"
                            ]
                        }
                    },
                    "budget_total": {
                        "type": "number",
                        "description": "budget: the cap"
                    },
                    "market": {
                        "type": "string",
                        "enum": [
                            "b2b",
                            "b2c"
                        ],
                        "description": "budget: picks the default benchmark (46/54 or 60/40)"
                    },
                    "benchmark_brand_pct": {
                        "type": "number",
                        "description": "budget: sector benchmark brand share if you have a better one; give benchmark_source and sector"
                    },
                    "benchmark_source": {
                        "type": "string"
                    },
                    "sector": {
                        "type": "string"
                    },
                    "activities": {
                        "type": "array",
                        "description": "budget: planned activities",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "brand",
                                        "activation"
                                    ]
                                },
                                "unit": {
                                    "type": "string"
                                },
                                "unit_cost": {
                                    "type": "number"
                                },
                                "units": {
                                    "type": "number"
                                },
                                "cost": {
                                    "type": "number",
                                    "description": "alternative to unit_cost × units"
                                },
                                "expected_result": {
                                    "type": "number"
                                },
                                "result_unit": {
                                    "type": "string"
                                }
                            },
                            "required": [
                                "name"
                            ]
                        }
                    },
                    "objective": {
                        "type": "object",
                        "description": "budget: {metric, target}",
                        "properties": {
                            "metric": {
                                "type": "string"
                            },
                            "target": {
                                "type": "number"
                            }
                        }
                    },
                    "sector_traits": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "budget: traits the benchmark sector already has; adjustments naming them are dropped"
                    },
                    "adjustments": {
                        "type": "array",
                        "description": "budget: proposed moves from the benchmark, each applied once",
                        "items": {
                            "type": "object",
                            "properties": {
                                "feature": {
                                    "type": "string"
                                },
                                "direction": {
                                    "type": "string",
                                    "enum": [
                                        "brand",
                                        "activation"
                                    ]
                                },
                                "points": {
                                    "type": "number"
                                },
                                "reason": {
                                    "type": "string"
                                }
                            },
                            "required": [
                                "feature",
                                "direction",
                                "points"
                            ]
                        }
                    },
                    "max_total_points": {
                        "type": "number",
                        "description": "budget: cap on the net adjustment (default 20)"
                    },
                    "offers": {
                        "type": "array",
                        "description": "offers: media proposals",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "price": {
                                    "type": "number"
                                },
                                "price_basis": {
                                    "type": "string",
                                    "enum": [
                                        "cpm_served",
                                        "cpm_viewable",
                                        "cpc",
                                        "cpl",
                                        "cpa"
                                    ]
                                },
                                "quantity": {
                                    "type": "number",
                                    "description": "impressions (for CPM bases), clicks or events the price buys"
                                },
                                "viewability_pct": {
                                    "type": "number"
                                },
                                "ctr_pct": {
                                    "type": "number"
                                },
                                "conversion_pct": {
                                    "type": "number"
                                },
                                "payment_event": {
                                    "type": "string",
                                    "description": "required for cpl / cpa: the contract's words for what is paid for"
                                }
                            },
                            "required": [
                                "name",
                                "price",
                                "price_basis"
                            ]
                        }
                    },
                    "target_event": {
                        "type": "string",
                        "description": "offers: the event you actually want to pay for"
                    },
                    "step_rates": {
                        "type": "array",
                        "description": "offers: conversion steps between events",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {
                                    "type": "string"
                                },
                                "to": {
                                    "type": "string"
                                },
                                "rate_pct": {
                                    "type": "number"
                                }
                            },
                            "required": [
                                "from",
                                "to",
                                "rate_pct"
                            ]
                        }
                    },
                    "compare_on": {
                        "type": "string",
                        "enum": [
                            "vcpm",
                            "cpm_served",
                            "cpc",
                            "cpl",
                            "cpa",
                            "cost_per_target_event"
                        ],
                        "description": "offers: force the ranking basis"
                    },
                    "periods": {
                        "type": "array",
                        "description": "volume: two or more periods (or markets)",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {
                                    "type": "string"
                                },
                                "population": {
                                    "type": "number"
                                },
                                "penetration_pct": {
                                    "type": "number"
                                },
                                "buyers": {
                                    "type": "number"
                                },
                                "purchases_per_buyer": {
                                    "type": "number"
                                },
                                "units_per_purchase": {
                                    "type": "number"
                                },
                                "reported_volume": {
                                    "type": "number"
                                }
                            }
                        }
                    },
                    "unit": {
                        "type": "string",
                        "description": "volume: unit label"
                    },
                    "year": {
                        "type": "integer",
                        "description": "calendar: the year"
                    },
                    "occasions": {
                        "type": "array",
                        "description": "calendar: flights",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "date": {
                                    "type": "string",
                                    "description": "YYYY-MM-DD or MM-DD"
                                },
                                "key": {
                                    "type": "string",
                                    "description": "known occasion key instead of a date"
                                },
                                "lead_days": {
                                    "type": "integer"
                                },
                                "duration_days": {
                                    "type": "integer"
                                },
                                "burst_budget": {
                                    "type": "number"
                                },
                                "posts": {
                                    "type": "number"
                                },
                                "channels": {
                                    "type": "array",
                                    "items": {
                                        "type": "string"
                                    }
                                }
                            },
                            "required": [
                                "name"
                            ]
                        }
                    },
                    "total_budget": {
                        "type": "number",
                        "description": "calendar: the cap"
                    },
                    "team_posts_per_week_max": {
                        "type": "number"
                    },
                    "platform": {
                        "type": "string",
                        "description": "channel_fit: the platform under consideration"
                    },
                    "segment": {
                        "type": "string"
                    },
                    "content_job": {
                        "type": "string",
                        "description": "channel_fit: what the audience uses the platform for"
                    },
                    "evidence": {
                        "type": "array",
                        "description": "channel_fit: usage evidence with URLs",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string"
                                },
                                "source_url": {
                                    "type": "string"
                                }
                            },
                            "required": [
                                "text"
                            ]
                        }
                    },
                    "target_posts_per_week": {
                        "type": "number"
                    },
                    "hours_per_post": {
                        "type": "number"
                    },
                    "team_hours_per_week": {
                        "type": "number"
                    },
                    "existing_platforms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string"
                                },
                                "posts_per_week": {
                                    "type": "number"
                                },
                                "hours_per_week": {
                                    "type": "number"
                                }
                            }
                        }
                    },
                    "rows": {
                        "type": "array",
                        "description": "classify: spend rows with the creative text",
                        "items": {
                            "type": "object",
                            "properties": {
                                "campaign": {
                                    "type": "string"
                                },
                                "channel": {
                                    "type": "string"
                                },
                                "spend": {
                                    "type": "number"
                                },
                                "creative_text": {
                                    "type": "string"
                                }
                            },
                            "required": [
                                "campaign",
                                "spend"
                            ]
                        }
                    }
                },
                "required": [
                    "mode"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "campaign_calc",
            "description": "Campaign arithmetic a worker must not do in prose; pick the mode that answers the marketer's question and quote its figures, method, missing and warnings. lift_compare: which sampling, coupon, deal or geo arm paid off and what a (gross or incremental) buyer cost, Wilson interval on the conversion; without baseline_buyers the result says gross, never incremental. referral_loop: the viral coefficient K (invites x acceptance x activation), amplification 1/(1-K), users after n cycles, break-even incentive per referred user. seeding_compare: seeded vs organic on cost per review and per 1 000 reach. bass: adoption forecast and peak from market_size and >=6 periods of history (p, q fitted) or given p, q; defaults flagged. buyer_overlap: brands sharing buyers more than their size predicts (duplication law, partitions). network_value: reachable counterparties after an integration under linear and Metcalfe models, break-even users for a fee. commission_compare: payout per sale and per rep, whether a plan rewards low-margin sales, the payout-neutral gross-profit rate. catchment_compare: best site for its rent with weights stated and sensitivity. account_penetration: growth left in existing accounts vs new logos. mental_availability: buying situations where the brand is missing from unaided recall. benchmark_fit: whether an external average applies to this brand and the expectation range, never a bare point. line_metrics: words, syllables, reading seconds, first-line benefit. audience_panel: whether a respondent list covers the target shares.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": [
                            "lift_compare",
                            "referral_loop",
                            "seeding_compare",
                            "bass",
                            "buyer_overlap",
                            "network_value",
                            "commission_compare",
                            "catchment_compare",
                            "account_penetration",
                            "mental_availability",
                            "benchmark_fit",
                            "line_metrics",
                            "audience_panel"
                        ],
                        "description": "which calculation to run"
                    },
                    "currency": {
                        "type": "string",
                        "description": "money unit label, default EUR"
                    },
                    "arms": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "lift_compare: [{name, units, unit_cost, extra_cost, buyers, population, baseline_buyers}]; seeding_compare: [{name, kind, units, cost, reviews, reach}]"
                    },
                    "invites_per_user": {
                        "type": "number",
                        "description": "referral_loop: invitations sent per user"
                    },
                    "acceptance_rate": {
                        "type": "number",
                        "description": "referral_loop: share of invites accepted, 0-1 (40 is read as 0.4)"
                    },
                    "activation_rate": {
                        "type": "number",
                        "description": "referral_loop: share of accepted invitees who become users, 0-1"
                    },
                    "cycle_days": {
                        "type": "number",
                        "description": "referral_loop: days from a user's activation to the invitee's activation"
                    },
                    "horizon_days": {
                        "type": "number",
                        "description": "referral_loop: horizon for cycles, default 90"
                    },
                    "users": {
                        "type": "number",
                        "description": "referral_loop: seed users; network_value: own network size"
                    },
                    "cycles": {
                        "type": "integer",
                        "description": "referral_loop: cycles to project, default 3"
                    },
                    "contribution_per_user": {
                        "type": "number",
                        "description": "referral_loop: contribution a user brings, sets the break-even incentive"
                    },
                    "incentive_per_referred_user": {
                        "type": "number",
                        "description": "referral_loop: what the programme pays per referred user"
                    },
                    "paid_cac": {
                        "type": "number",
                        "description": "referral_loop: paid acquisition cost per user, for the blended CAC"
                    },
                    "min_reviews": {
                        "type": "integer",
                        "description": "seeding_compare: reviews under which the base is small, default 20"
                    },
                    "market_size": {
                        "type": "number",
                        "description": "bass: the finite pool of potential adopters"
                    },
                    "history": {
                        "type": "array",
                        "items": {
                            "type": "number"
                        },
                        "description": "bass: new adopters per period so far; p and q are fitted from 6 or more"
                    },
                    "p": {
                        "type": "number",
                        "description": "bass: coefficient of innovation (0-1), with q"
                    },
                    "q": {
                        "type": "number",
                        "description": "bass: coefficient of imitation (0-1), with p"
                    },
                    "horizon": {
                        "type": "integer",
                        "description": "bass: periods to forecast, default 12"
                    },
                    "adopters_to_date": {
                        "type": "number",
                        "description": "bass: cumulative adopters so far when it differs from the history sum"
                    },
                    "period": {
                        "type": "string",
                        "description": "bass: period label (month, quarter, year)"
                    },
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "buyer_overlap: [{buyer, brand, units}]; mental_availability: [{respondent, situation, brand, aided}] one row per mention"
                    },
                    "threshold": {
                        "type": "number",
                        "description": "buyer_overlap: ratio above which a pair is a partition candidate, default 1.5"
                    },
                    "min_buyers": {
                        "type": "integer",
                        "description": "buyer_overlap: brands with fewer buyers are left out, default 50"
                    },
                    "baseline": {
                        "type": "string",
                        "enum": [
                            "duplication_law",
                            "independence"
                        ],
                        "description": "buyer_overlap: expected duplication baseline, default duplication_law"
                    },
                    "focus_brand": {
                        "type": "string",
                        "description": "buyer_overlap / mental_availability: the marketer's brand"
                    },
                    "partner_users": {
                        "type": "number",
                        "description": "network_value: size of the network to integrate with"
                    },
                    "overlap_fraction": {
                        "type": "number",
                        "description": "network_value: share of partner users already in own network, 0-1; unknown gives an upper bound"
                    },
                    "counterparty_relevance": {
                        "type": "number",
                        "description": "network_value: share of own users who deal with partner-network firms, 0-1"
                    },
                    "interactions_per_user": {
                        "type": "number",
                        "description": "network_value: interactions per user per period"
                    },
                    "value_per_interaction": {
                        "type": "number",
                        "description": "network_value: value of one interaction"
                    },
                    "fee_per_user": {
                        "type": "number",
                        "description": "network_value: fee per user per period"
                    },
                    "fixed_costs": {
                        "type": "number",
                        "description": "network_value: fixed costs per period for break-even users"
                    },
                    "cost_per_user": {
                        "type": "number",
                        "description": "network_value: variable cost per user per period"
                    },
                    "products": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "commission_compare: [{name, price, variable_cost or margin_pct, units_per_period, discount_pct}]"
                    },
                    "plans": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "commission_compare: [{name, kind: revenue|gross_profit, rate_pct, base, threshold}]; the first plan sets the payout-neutral rate"
                    },
                    "sellers": {
                        "type": "integer",
                        "description": "commission_compare: number of reps, for payroll"
                    },
                    "sites": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "catchment_compare: [{name, bands: [{label, population}] nearest first, competitors, drive_time_minutes, rent}]"
                    },
                    "weights": {
                        "type": "object",
                        "description": "catchment_compare: {population, competitors, drive_time}, default 0.5 / 0.3 / 0.2"
                    },
                    "band_weights": {
                        "type": "array",
                        "items": {
                            "type": "number"
                        },
                        "description": "catchment_compare: weight per band, default 1, 0.5, 0.25"
                    },
                    "universe_accounts": {
                        "type": "number",
                        "description": "account_penetration: potential buying organisations, from a sourced count"
                    },
                    "customer_accounts": {
                        "type": "number",
                        "description": "account_penetration: accounts already won"
                    },
                    "revenues": {
                        "type": "array",
                        "items": {},
                        "description": "account_penetration: revenue per account (numbers or [{account, revenue}])"
                    },
                    "avg_revenue": {
                        "type": "number",
                        "description": "account_penetration: average revenue per account when the list is unavailable"
                    },
                    "ceiling": {
                        "type": "number",
                        "description": "account_penetration: realistic revenue ceiling per account; default 90th percentile, flagged assumed"
                    },
                    "growth_last_period": {
                        "type": "number",
                        "description": "account_penetration: revenue growth last period"
                    },
                    "growth_from_existing_pct": {
                        "type": "number",
                        "description": "account_penetration: share of that growth from existing accounts, 0-100"
                    },
                    "min_n": {
                        "type": "integer",
                        "description": "mental_availability: respondents per situation under which confidence is low, default 30"
                    },
                    "n_respondents": {
                        "type": "integer",
                        "description": "mental_availability: all respondents, including those who named nobody"
                    },
                    "respondents_per_situation": {
                        "type": "object",
                        "description": "mental_availability: {situation: n} when some respondents named nobody"
                    },
                    "benchmark": {
                        "type": "object",
                        "description": "benchmark_fit: {metric, value, unit, source_url, selection: awards|self_submitted|panel|census|platform|own|unknown, industries, markets, budget_min, budget_max, objective, sample_n, period}"
                    },
                    "own": {
                        "type": "object",
                        "description": "benchmark_fit: {industry, market, budget, objective, period} of the brand"
                    },
                    "own_history": {
                        "type": "object",
                        "description": "benchmark_fit: {values: [metric per comparable past piece], new_value, min_n (default 5)}"
                    },
                    "lines": {
                        "type": "array",
                        "items": {},
                        "description": "line_metrics: strings or [{name, text}]"
                    },
                    "language": {
                        "type": "string",
                        "enum": [
                            "lt",
                            "en"
                        ],
                        "description": "line_metrics: language for syllables and benefit cues, default lt"
                    },
                    "wpm": {
                        "type": "number",
                        "description": "line_metrics: reading speed, default 200"
                    },
                    "respondents": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "audience_panel: [{dimension: value, ...}] one object per respondent"
                    },
                    "target": {
                        "type": "object",
                        "description": "audience_panel: {dimension: {value: share_pct}}, shares per dimension sum to 100"
                    },
                    "tolerance_pp": {
                        "type": "number",
                        "description": "audience_panel: shortfall in percentage points that counts as a gap, default 5"
                    }
                },
                "required": [
                    "mode"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "plan_check",
            "description": "Check one structured marketing plan by code before it reaches the marketer; kind picks the checker. content_calendar: items with dates, owners, goals, stages, formats and lifespans against capacity and audience evidence (week gaps, channel clashes, stage gaps, formats unsupported, crowded launch window, missing review or expiry, stale overlap). pr_board: press release, story, announcement, sponsorship pack or message house — news hook, attributed quote, boilerplate ≤ 100 words, contact, date, a sourced fact per figure (claim_check), byline disclosure, partners' own sourced goals, affected groups in their own words, reader reason per publication, one core sentence with ≤ 3 evidenced pillars kept by every variant. demo_board: one claim with a number, an act the visitor performs, the result last, the product early. response_board: escalation tiers with owner, minutes, EUR thresholds, a sensitive tier that never answers in public, drills that reach escalation. partner_board: obligations both ways, support items with the obstacle removed and a ship signal, exclusivity, sourced investment signals. qualification: a sales sheet (need, means, access, timing, decision process with evidence or a question) or a recruit screener (questions that discriminate between segments). evidence_log: append sourced observations to runs/notes/evidence.jsonl and audit a secondary source's measure, population and period. community_rules: allowed, removed, who decides, appeal, response time. retell and ad_pretest: a cold reader model that has never seen the brief repeats the message or names the offer; scored on which must_survive terms survived; without a reader model the prompts are returned and nothing is judged. Every kind returns findings, missing, counts, clean and summary; it never fills a slot the worker left empty.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "content_calendar",
                            "pr_board",
                            "demo_board",
                            "response_board",
                            "partner_board",
                            "qualification",
                            "evidence_log",
                            "community_rules",
                            "retell",
                            "ad_pretest"
                        ]
                    },
                    "language": {
                        "type": "string",
                        "enum": [
                            "lt",
                            "en"
                        ]
                    },
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "content_calendar: [{title, publish_date, channel, format, owner, stage, goal, source, lifespan, review_date, expires_on, event_date, hours}]"
                    },
                    "launch_date": {
                        "type": "string"
                    },
                    "capacity": {
                        "type": "object",
                        "description": "content_calendar: {items_per_week, hours_per_week}"
                    },
                    "audience_evidence": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "inventory": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "content_calendar: existing content [{title, url, updated}]"
                    },
                    "competitors": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "today": {
                        "type": "string"
                    },
                    "draft": {
                        "type": "string"
                    },
                    "draft_path": {
                        "type": "string"
                    },
                    "doc_kind": {
                        "type": "string",
                        "enum": [
                            "press_release",
                            "story",
                            "announcement",
                            "sponsorship"
                        ]
                    },
                    "parts": {
                        "type": "object"
                    },
                    "facts": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "pr_board: [{figure, source, status}]"
                    },
                    "sources": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "byline": {
                        "type": "object"
                    },
                    "sponsor": {
                        "type": "string"
                    },
                    "partners": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "beneficiaries": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "affected": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "publications": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "readers": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "message_house": {
                        "type": "object",
                        "description": "pr_board: {core, pillars [{text, evidence {text, source}}], variants [{channel, text}]}"
                    },
                    "audience": {
                        "type": "string"
                    },
                    "claim": {
                        "type": "string"
                    },
                    "situation": {
                        "type": "string"
                    },
                    "result": {
                        "type": "string"
                    },
                    "judged_by": {
                        "type": "string"
                    },
                    "takeaway": {
                        "type": "string"
                    },
                    "session_minutes": {
                        "type": "number"
                    },
                    "first_product_minute": {
                        "type": "number"
                    },
                    "materials": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "live": {
                        "type": "boolean"
                    },
                    "audience_reasons": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "pre_talk_questions": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "tiers": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "scenarios": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "contacts_confirmed": {
                        "type": "boolean"
                    },
                    "support_items": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "exclusivity": {
                        "type": "object"
                    },
                    "sheet": {
                        "type": "string",
                        "enum": [
                            "sales",
                            "screener"
                        ]
                    },
                    "prospect": {
                        "type": "string"
                    },
                    "conditions": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "disqualifiers": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "segments": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "routes": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        }
                    },
                    "entries": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "evidence_log: [{kind modification|quote|finding|source_audit, text, source_url, quote, tags, measure, population, period}]"
                    },
                    "needed": {
                        "type": "object"
                    },
                    "known_urls": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "path": {
                        "type": "string"
                    },
                    "text": {
                        "type": "string"
                    },
                    "statements": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "retell: [{id, text, must_survive}]"
                    },
                    "buyer_type": {
                        "type": "string"
                    },
                    "ads": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "ad_pretest: [{id, headline, primary_text, must_survive, image_path, image_description}]"
                    },
                    "mode": {
                        "type": "string",
                        "enum": [
                            "glance",
                            "reference",
                            "identification"
                        ]
                    },
                    "exposure_ms": {
                        "type": "integer"
                    },
                    "job": {
                        "type": "string"
                    },
                    "n_readers": {
                        "type": "integer"
                    },
                    "model": {
                        "type": "string",
                        "description": "reader model id, default google/gemini-3.8-flash"
                    }
                },
                "required": [
                    "kind"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_check",
            "description": "Web checks a marketer asks for by eye, done by code. Pick the mode: name_check (candidate names → spellings a LT/EN listener would type, what each finds in search, collisions with another brand or a dictionary word; verdict always untested_with_buyers), mention_scan (brand + domain + qualifiers → results sorted ours / ambiguous / other, ambiguity share, the qualifier that disambiguates best), policy_finder (site_url, topic delivery|returns|exceptions, start_url → follows footer/header/help/FAQ links, returns policy_url, clicks_from_start and the promise fields with verbatim quotes), listing_check (canonical NAP + listing pages → per field match / mismatch / not_found after digit normalisation), search_intent (queries → informational / commercial / transactional / navigational / mixed with evidence tokens and SERP shapes), site_crawl (BFS within max_pages/max_depth → depth and inbound per page, orphan important pages with suggested link sources), keyword_inspect (who ranks for a keyword and what their pages carry next to your own), link_prospects (which candidates link to a target, the citing sentence, relevance then reach), locale_check (lang attr, hreflang, currency, date, number and phone formats against an expected locale), kit_check (press or partner kit against required items), email_auth (SPF/DKIM/DMARC results parsed from a pasted raw email; DNS lookups land under missing with the dig commands), search_region (region-aware query variants; the searcher has no region parameter). A url, query or lookup that could not be done lands under missing, never guessed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": [
                            "name_check",
                            "mention_scan",
                            "policy_finder",
                            "listing_check",
                            "search_intent",
                            "site_crawl",
                            "keyword_inspect",
                            "link_prospects",
                            "locale_check",
                            "kit_check",
                            "email_auth",
                            "search_region"
                        ],
                        "description": "which check to run"
                    },
                    "names": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "name_check: 1-10 candidate names"
                    },
                    "langs": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "name_check: listener languages, lt and/or en (default both)"
                    },
                    "category": {
                        "type": "string",
                        "description": "name_check: product category words to look for in result titles"
                    },
                    "intended_url": {
                        "type": "string",
                        "description": "name_check: the site the name should lead to"
                    },
                    "brand": {
                        "type": "string",
                        "description": "mention_scan: brand name"
                    },
                    "domain": {
                        "type": "string",
                        "description": "mention_scan: brand domain; email_auth: sending domain when no headers"
                    },
                    "qualifiers": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "mention_scan: 2-5 words that identify the brand"
                    },
                    "variants": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "mention_scan: misspellings or product names; auto-generated when empty"
                    },
                    "products": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "mention_scan: product names for brand+product variants"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "mention_scan: cap on results kept (default 60)"
                    },
                    "site_url": {
                        "type": "string",
                        "description": "policy_finder: the shop's home address"
                    },
                    "topic": {
                        "type": "string",
                        "enum": [
                            "delivery",
                            "returns",
                            "exceptions"
                        ],
                        "description": "policy_finder: which policy to find"
                    },
                    "start_url": {
                        "type": "string",
                        "description": "policy_finder: where the buyer starts; site_crawl: crawl start"
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "policy_finder: page budget (default 15); site_crawl: crawl cap (default 300)"
                    },
                    "canonical": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string"
                            },
                            "address": {
                                "type": "string"
                            },
                            "phone": {
                                "type": "string"
                            },
                            "hours": {
                                "type": "string"
                            },
                            "website": {
                                "type": "string"
                            }
                        },
                        "description": "listing_check: the correct record"
                    },
                    "listings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {
                                    "type": "string"
                                },
                                "path": {
                                    "type": "string"
                                },
                                "html": {
                                    "type": "string"
                                }
                            }
                        },
                        "description": "listing_check: directory pages, one source each"
                    },
                    "queries": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "search_intent: the queries to classify"
                    },
                    "language": {
                        "type": "string",
                        "description": "search_intent, keyword_inspect: lt or en"
                    },
                    "site": {
                        "type": "string",
                        "description": "search_intent: own domain"
                    },
                    "fetch_serp": {
                        "type": "boolean",
                        "description": "search_intent: run the searcher for SERP shapes (default true)"
                    },
                    "important_urls": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "site_crawl: pages that must be reachable"
                    },
                    "important_path": {
                        "type": "string",
                        "description": "site_crawl: workspace file with one url per line"
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "site_crawl: click depth cap (default 4)"
                    },
                    "same_host_only": {
                        "type": "boolean",
                        "description": "site_crawl: stay on the start host (default true)"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "keyword_inspect: the keyword"
                    },
                    "result_urls": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "keyword_inspect: up to 10 ranking urls; omitted = run the searcher"
                    },
                    "own_url": {
                        "type": "string",
                        "description": "keyword_inspect: the marketer's page"
                    },
                    "own_html": {
                        "type": "string",
                        "description": "keyword_inspect: the marketer's page as html"
                    },
                    "kd": {
                        "type": "number",
                        "description": "keyword_inspect: difficulty, passed through"
                    },
                    "volume": {
                        "type": "number",
                        "description": "keyword_inspect: monthly searches, passed through"
                    },
                    "target_urls": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "link_prospects: pages the candidates should link to"
                    },
                    "topic_terms": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "link_prospects: 3-10 topic words for relevance"
                    },
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {
                                    "type": "string"
                                },
                                "reach": {
                                    "type": "number"
                                }
                            }
                        },
                        "description": "link_prospects: candidate referring pages with DR/traffic as reach"
                    },
                    "max_fetch": {
                        "type": "integer",
                        "description": "link_prospects: fetch cap (default 50)"
                    },
                    "url": {
                        "type": "string",
                        "description": "locale_check, kit_check: the page address"
                    },
                    "path": {
                        "type": "string",
                        "description": "locale_check, kit_check: workspace HTML file"
                    },
                    "html": {
                        "type": "string",
                        "description": "locale_check, kit_check, listing_check: page source pasted"
                    },
                    "text": {
                        "type": "string",
                        "description": "locale_check: copy to check without a page"
                    },
                    "expected_locale": {
                        "type": "string",
                        "description": "locale_check: e.g. lt-LT, lv-LV, et-EE, pl-PL, de-DE, en-GB"
                    },
                    "files": {
                        "type": "array",
                        "items": {},
                        "description": "kit_check: file names (or {path, width, height, alt}) of the kit"
                    },
                    "boilerplate": {
                        "type": "string",
                        "description": "kit_check: the boilerplate text when not on the page"
                    },
                    "contact": {
                        "type": "string",
                        "description": "kit_check: contact line when not on the page"
                    },
                    "required": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "kit_check: subset of logo_vector, logo_raster, boilerplate, contact, images_sized, images_alt"
                    },
                    "headers_text": {
                        "type": "string",
                        "description": "email_auth: the raw email source with headers"
                    },
                    "dkim_selectors": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "email_auth: selectors to name in the dig commands"
                    },
                    "query": {
                        "type": "string",
                        "description": "search_region: the query"
                    },
                    "region": {
                        "type": "string",
                        "description": "search_region: market code such as lt-lt, lv-lv, pl-pl, de-de"
                    },
                    "local_terms": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "search_region: native-language terms supplied by the caller"
                    }
                },
                "required": [
                    "mode"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Open a URL in a real Chromium browser (JS-rendered pages) and return visible text.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": "Return the current browser page title, URL, and visible text.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "Close the Chromium browser session.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skills_list",
            "description": "List available skills (deep-research, marketing-pipeline, voc-to-copy, copywriting, xlsx, docx, pptx, pdf, …). Call before research, copy, or Office files.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_view",
            "description": "Read a skill's full instructions by name (deep-research, voc-to-copy, copywriting, pptx, xlsx, …).",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "office_read",
            "description": "Extract text from .xlsx .docx .pptx .pdf .csv using markitdown.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brief",
            "description": "The workspace brief: audience, offer, price, margin, volume, channels, KPI, budget, constraints, market, competitors, deadline, language — the inputs the marketer has and did not type. Call action=get BEFORE asking the user anything: if the brief has the field, use it; if a kind of work lists it under missing_for, ask for that and nothing else. When you learn a field (from the user, a file or a page), call action=set with source and evidence (URL or workspace file) — a worker-set field without evidence is recorded as an assumption and shown as such to every role. When your deliverable recommends spending, call action=decision with the internal strength/weakness, the external opportunity/threat, the investment and what evidence would change the mind. Tools default missing arguments from the brief; action=block returns what every role already sees.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "get",
                            "set",
                            "decision",
                            "clear",
                            "block"
                        ]
                    },
                    "fields": {
                        "type": "object",
                        "description": "set: field → value. Allowed: brand, url, product, audience, audience_size, offer, price (number or {amount, currency}), currency, variable_cost, margin_pct, units_per_period, period, channel, channels[], kpi {name, baseline?, target?, unit?}, budget {amount, currency?, period?}, language (lt|en), constraints[], market, competitors[], deadline (YYYY-MM-DD), source_notes. clear: list of names (nothing = everything)."
                    },
                    "source": {
                        "type": "string",
                        "enum": [
                            "user",
                            "worker",
                            "file"
                        ],
                        "description": "set: who the value comes from; default worker"
                    },
                    "evidence": {
                        "type": "string",
                        "description": "set: URL or workspace file that shows the value; without it a worker's value is an assumption"
                    },
                    "internal": {
                        "type": "object",
                        "description": "decision: {kind: strength|weakness, text, source}"
                    },
                    "external": {
                        "type": "object",
                        "description": "decision: {kind: opportunity|threat, text, source}"
                    },
                    "investment": {
                        "type": "object",
                        "description": "decision: {what, amount?, currency?}"
                    },
                    "would_change_mind": {
                        "type": "string",
                        "description": "decision: the evidence that would reverse it"
                    }
                },
                "required": [
                    "action"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory",
            "description": (
                "Add, replace, or remove a durable memory entry. "
                "target=user is global preferences (~/.rocky/memories/USER.md, 1375 chars). "
                "target=memory is facts about THIS workspace (.rocky/MEMORY.md, 2200 chars). "
                "Writes hit disk now but the prompt snapshot refreshes next run. "
                "Store compact facts, not transcripts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "replace", "remove"]},
                    "target": {"type": "string", "enum": ["memory", "user"]},
                    "text": {"type": "string"},
                    "old_text": {"type": "string"},
                },
                "required": ["action", "target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ad_board",
            "description": (
                "Publish Facebook / Instagram ads to deliverables/ads.html, a page showing each ad as it looks in the feed "
                "with copy buttons. Never write that file yourself. Every number in the copy needs a claims entry whose "
                "source URL is in runs/notes/facts.md. The result says what to fix: call again with the whole corrected "
                "pack. Visuals named deliverables/visual-<ad number>.png attach by themselves."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "brand": {"type": "string"},
                    "url": {"type": "string", "description": "the page the ads link to"},
                    "ads": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "primary_text": {"type": "string", "description": "the feed shows about 125 characters"},
                                "headline": {"type": "string", "description": "up to 40 characters"},
                                "description": {"type": "string", "description": "up to 30 characters"},
                                "cta": {"type": "string", "description": "button label"},
                                "angle": {"type": "string"},
                                "audience": {"type": "string"},
                                "test_role": {
                                    "type": "string",
                                    "enum": ["control", "challenger"],
                                    "description": "control is the site's current pitch as an ad; challenger is a new message",
                                },
                                "segment": {
                                    "type": "object",
                                    "description": "whether this ad's segment is worth a distinct offer; mark estimates as estimates",
                                    "properties": {
                                        "size": {"type": "string", "description": "how many such buyers there are"},
                                        "buying_power": {"type": "string"},
                                        "reach_cost": {"type": "string"},
                                        "distinct": {"type": "string", "description": "why this segment would respond differently"},
                                    },
                                },
                                "mix": {
                                    "type": "object",
                                    "description": "the offer that makes this ad's promise true, from the facts",
                                    "properties": {
                                        "product": {"type": "string", "description": "what the buyer gets"},
                                        "price": {"type": "string", "description": "what it costs, or that it is not published"},
                                        "place": {"type": "string", "description": "where and how the buyer gets it"},
                                    },
                                },
                            },
                            "required": ["primary_text", "headline", "cta"],
                        },
                    },
                    "growth": {
                        "type": "object",
                        "description": "whether these ads sell more in a market the brand already serves, or open a new one",
                        "properties": {
                            "kind": {"type": "string", "enum": ["penetration", "development"]},
                            "reach": {"type": "string", "description": "for development: the evidence that these buyers can be reached"},
                        },
                    },
                    "table_stakes": {
                        "type": "array",
                        "description": "what buyers expect from anyone in this category",
                        "items": {
                            "type": "object",
                            "properties": {
                                "outcome": {"type": "string"},
                                "minimum": {"type": "string", "description": "the lowest acceptable level"},
                                "status": {"type": "string", "enum": ["met", "unmet", "unknown"]},
                            },
                            "required": ["outcome", "minimum", "status"],
                        },
                    },
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}, "source": {"type": "string"}},
                            "required": ["text", "source"],
                        },
                    },
                },
                "required": ["brand", "url", "ads"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recipe",
            "description": (
                "List, view, or save plan recipes (ordered step skeletons Rocky enforces for a kind of goal, "
                "e.g. marketing: research → VOC → copy → pptx+xlsx). "
                "save writes <workspace>/.rocky/recipes/<name>.toml for the NEXT run; the user should review it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "view", "save"]},
                    "name": {"type": "string", "description": "for view"},
                    "recipe": {
                        "type": "object",
                        "description": "for save: {name, description, deliverable_path, notes, match:{domains,hints}, steps:[{id,title,detail,depends_on,requires}]}",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "runs_search",
            "description": (
                "Search prior Rocky runs in this workspace (goal, status, summary). "
                "Use when the user refers to earlier work ('that article', 'the last deck')."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
    },
]

SERVER_TOOLS = [
    {"type": "openrouter:web_search", "parameters": {"max_results": 5, "max_uses": 4}},
    {"type": "openrouter:web_fetch"},
    {"type": "openrouter:datetime"},
]

READ_TOOL_SPECS = [
    spec
    for spec in CLIENT_TOOL_SPECS
    if spec["function"]["name"] in {"read_file", "list_dir", "grep"}
]

# What the verifier may touch: read-only, plus Office/PDF extraction so it can open a deck.
VERIFY_TOOL_NAMES = frozenset({"read_file", "list_dir", "grep", "office_read", "claim_check"})
VERIFY_TOOL_SPECS = [
    spec for spec in CLIENT_TOOL_SPECS if spec["function"]["name"] in VERIFY_TOOL_NAMES
]


def _extended_ad_board_spec(spec: dict) -> dict:
    """The ad_board schema plus every field the adchecks modules declare."""
    import copy

    from rocky import adchecks

    additions = adchecks.schema()
    if not additions["ad"] and not additions["pack"]:
        return spec
    spec = copy.deepcopy(spec)
    props = spec["function"]["parameters"]["properties"]
    ad_props = props["ads"]["items"]["properties"]
    # A module may add fields, never redefine a core one (ads, brand, url, claims, growth, table_stakes …).
    ad_props.update({k: v for k, v in additions["ad"].items() if k not in ad_props})
    props.update({k: v for k, v in additions["pack"].items() if k not in props})
    return spec


def worker_tools(include_server: bool = True) -> list[dict]:
    tools = [
        _extended_ad_board_spec(spec) if spec.get("function", {}).get("name") == "ad_board" else spec
        for spec in CLIENT_TOOL_SPECS
    ]
    if include_server:
        tools.extend(SERVER_TOOLS)
    return tools


def _with_brief_defaults(tools, name: str, args: dict) -> dict:
    """Arguments a tool needs that the worker left out but the workspace brief holds."""
    try:
        from rocky.brief import TOOL_DEFAULTS, defaults_for
    except ImportError:  # pragma: no cover
        return args
    if name not in TOOL_DEFAULTS or tools is None:
        return args
    try:
        defaults = defaults_for(tools.root, name)
    except Exception:  # noqa: BLE001 — a broken brief must not stop a tool call
        return args
    filled = {k: v for k, v in defaults.items() if args.get(k) in (None, "")}
    return {**filled, **args} if filled else args


def _text_fetcher(searcher):
    """A fetch(url) -> text callable for claim_check, or None when this role has no web."""
    if searcher is None or not hasattr(searcher, "fetch"):
        return None

    def fetch(url: str) -> str:
        text = searcher.fetch(url)
        if text.startswith(("fetch failed", "url must start")):
            raise ValueError(text)
        return text

    return fetch


def dispatch(
    tools: WorkspaceTools,
    name: str,
    arguments: str | dict,
    searcher=None,
    ask_user=None,
    llm=None,
    browser=None,
    mcp=None,
    spend=None,
) -> str:
    if isinstance(arguments, str):
        args = parse_json_object(arguments) if arguments.strip() else {}
    else:
        args = arguments or {}
    args = _with_brief_defaults(tools, name, args)
    try:
        if name == "brief":
            from rocky.brief import brief_tool

            return brief_tool(tools, args)
        if name.startswith("mcp__"):
            if mcp is None:
                raise ToolError("no MCP servers configured (.rocky/mcp.json)")
            from rocky.mcp import MCPError

            try:
                return _clip(mcp.call(name, args))
            except MCPError as exc:
                raise ToolError(str(exc)) from exc
        if name == "bash":
            return tools.bash(args.get("command") or "")
        if "_raw" in args and len(args) == 1:
            raise ToolError(cut_off_message(name, str(args["_raw"])))
        if name == "write_file":
            return tools.write_file(args.get("path") or "", args.get("content") or "")
        if name == "append_file":
            return tools.append_file(args.get("path") or "", args.get("content") or "")
        if name == "read_file":
            return tools.read_file(args.get("path") or "")
        if name == "list_dir":
            return tools.list_dir(args.get("path") or ".")
        if name == "grep":
            return tools.grep(args.get("pattern") or "", args.get("path") or ".")
        if name == "web_search":
            if searcher is None:
                raise ToolError("web_search is not configured")
            return searcher.search(args.get("query") or "")
        if name == "web_fetch":
            if searcher is None:
                raise ToolError("web_fetch is not configured")
            return searcher.fetch(args.get("url") or "")
        if name == "patch":
            return tools.patch(
                args.get("path") or "",
                args.get("old_string") or "",
                args.get("new_string") or "",
            )
        if name == "todo":
            items = args.get("items")
            if items:
                return tools.todo_write(items)
            return tools.todo_list()
        if name == "clarify":
            question = str(args.get("question") or "").strip()
            if not question:
                raise ToolError("clarify needs a question")
            if ask_user is None:
                return "no user available; continue with documented assumptions"
            answer = ask_user(question, args.get("options"))
            return f"user: {answer}"
        if name == "vision_analyze":
            if llm is None:
                raise ToolError("vision_analyze needs an LLM client")
            from rocky.extras import analyze_image

            return analyze_image(
                llm,
                str(args.get("path") or args.get("url") or ""),
                str(args.get("prompt") or ""),
                tools,
                spend=spend,
            )
        if name == "image_generate":
            if llm is None:
                raise ToolError("image_generate needs an LLM client")
            from rocky.extras import generate_image

            return generate_image(
                llm,
                str(args.get("prompt") or ""),
                str(args.get("path") or ""),
                tools,
                reference_path=str(args.get("reference_path") or ""),
                aspect_ratio=str(args.get("aspect_ratio") or ""),
                spend=spend,
            )
        if name == "browser_navigate":
            if browser is None:
                raise ToolError("browser is not configured")
            return browser.navigate(str(args.get("url") or ""))
        if name == "browser_snapshot":
            if browser is None:
                raise ToolError("browser is not configured")
            return browser.snapshot()
        if name == "browser_close":
            if browser is None:
                raise ToolError("browser is not configured")
            return browser.close()
        if name == "skills_list":
            from rocky.skills import list_skills

            rows = list_skills()
            if not rows:
                return "(no skills)"
            return "\n".join(f"- {s['name']}: {s['description']}" for s in rows)
        if name == "skill_view":
            from rocky.skills import view_skill

            try:
                return view_skill(str(args.get("name") or ""))
            except (FileNotFoundError, ValueError) as exc:
                raise ToolError(str(exc)) from exc
        if name == "office_read":
            from rocky.skills import read_office

            path = tools.resolve(str(args.get("path") or ""))
            if not path.exists():
                raise ToolError(f"not found: {args.get('path')}")
            return read_office(path)[:50000] or "(empty)"
        if name == "memory":
            from rocky.memory import apply_memory

            return apply_memory(tools.root, args)
        if name == "ad_board":
            from rocky.adboard import ad_board_tool

            return ad_board_tool(tools, args)
        if name == "unit_economics":
            from rocky.calc import unit_economics_tool

            return unit_economics_tool(args)
        if name == "experiment_plan":
            mode = str(args.get("mode") or "plan").strip().lower()
            if mode != "plan":
                from rocky.experiments import experiments_tool

                return experiments_tool(args)
            from rocky.calc import experiment_plan_tool

            return experiment_plan_tool({k: v for k, v in args.items() if k != "mode"})
        if name == "customer_economics":
            from rocky.customer_econ import customer_economics_tool

            return customer_economics_tool(args)
        if name == "funnel_analyze":
            from rocky.funnel import funnel_analyze_tool

            return funnel_analyze_tool(tools, args)
        if name == "table_analyze":
            from rocky.table import table_analyze_tool

            return table_analyze_tool(tools, args)
        if name == "repeat_buying":
            from rocky.panel import repeat_buying_tool

            return repeat_buying_tool(tools, args)
        if name == "plan_check":
            from rocky.plans import plan_check_tool

            return plan_check_tool(tools, args, llm=llm)
        if name == "campaign_calc":
            from rocky.campaign import campaign_calc_tool

            return campaign_calc_tool(args)
        if name == "media_plan":
            from rocky.media import media_plan_tool

            return media_plan_tool(args)
        if name == "reach_overlap":
            from rocky.reach import reach_overlap_tool

            return reach_overlap_tool(args)
        if name in {"survey_check", "outcome_scores", "research_plan"}:
            from rocky import research

            return getattr(research, f"{name}_tool")(args)
        if name == "web_check":
            from rocky.webcheck import web_check_tool

            return web_check_tool(
                tools,
                args,
                fetch_html=getattr(searcher, "fetch_html", None) if searcher is not None else None,
                fetch_meta=getattr(searcher, "fetch_meta", None) if searcher is not None else None,
                search=getattr(searcher, "search", None) if searcher is not None else None,
            )
        if name == "seo_inspect":
            from rocky.seo import seo_inspect_tool

            return seo_inspect_tool(
                tools,
                args,
                fetch_html=getattr(searcher, "fetch_html", None) if searcher is not None else None,
                search=getattr(searcher, "search", None) if searcher is not None else None,
            )
        if name == "claim_check":
            from rocky.claims import claim_check_tool

            return claim_check_tool(tools, args, fetch=_text_fetcher(searcher))
        if name == "page_inspect":
            from rocky.pageinspect import page_inspect_tool

            fetch_html = getattr(searcher, "fetch_html", None) if searcher is not None else None
            fetch_meta = getattr(searcher, "fetch_meta", None) if searcher is not None else None
            return page_inspect_tool(tools, args, fetch_html=fetch_html, fetch_meta=fetch_meta)
        if name == "recipe":
            from rocky.recipes import recipe_tool

            return recipe_tool(tools.root, args)
        if name == "runs_search":
            from rocky.memory import search_runs

            return search_runs(tools.root, str(args.get("query") or ""))
        raise ToolError(f"unknown tool: {name}")
    except ToolError:
        raise
    except Exception as extra:  # noqa: BLE001
        raise ToolError(str(extra)) from extra
