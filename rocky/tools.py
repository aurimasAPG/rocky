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
            "description": (
                "Plan or read out an A/B test. mode plan (default) or plan_rate: sample per variant, duration from "
                "weekly_traffic and a decision rule for a rate given in percent; plan_rate also writes guardrails and a "
                "segment into the rule. mode plan_means: the same for a measured quantity (order value, time, score) from "
                "sd or baseline_mean + cv_pct and minimum_difference; paired designs with correlation need fewer people. "
                "mode evaluate: give arms with n and conversions (or mean and sd) and get per challenger the difference, "
                "95 % interval, p-value and a verdict — winner, loser, no difference detected, or underpowered: keep "
                "running when the planned sample was not reached; guardrail breaches are named. Use it instead of "
                "guessing how long to run or whether a result is real."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                                        "mode": {"type": "string", "enum": ["plan", "plan_rate", "plan_means", "evaluate"], "description": "plan (default) sizes a test on a rate; plan_rate = plan plus guardrails and segment; plan_means sizes a test on a measured quantity; evaluate reads out a finished test from arms"},
                    "arms": {"type": "array", "description": "evaluate: 2+ arms; the arm named control (or the first) is the control. Rates: {name, n, conversions}; means: {name, n, mean, sd}", "items": {"type": "object", "properties": {"name": {"type": "string"}, "n": {"type": "number", "description": "units exposed"}, "conversions": {"type": "number"}, "mean": {"type": "number"}, "sd": {"type": "number", "description": "standard deviation of that arm, required with mean"}}, "required": ["name", "n"]}},
                    "control": {"type": "string", "description": "evaluate: name of the control arm if it is not called control or first"},
                    "lower_is_better": {"type": "boolean", "description": "evaluate: true for bounce, refund or cost metrics"},
                    "guardrails": {"type": "array", "description": "plan_rate: names or {name, max_drop_pct}; evaluate: {name, arms (same shape as arms), max_drop_pct, lower_is_better} — a breach is named against the winner", "items": {"type": "object", "properties": {"name": {"type": "string"}, "max_drop_pct": {"type": "number", "description": "largest tolerated worsening, percent of the control value"}, "lower_is_better": {"type": "boolean"}, "arms": {"type": "array", "items": {"type": "object"}}}, "required": ["name"]}},
                    "planned": {"type": "object", "description": "evaluate: the plan written before the test; without it 'no difference' cannot be told from 'too little data'", "properties": {"sample_per_variant": {"type": "number"}, "minimum_effect": {"type": "number"}, "effect_kind": {"type": "string", "enum": ["relative", "absolute"]}}},
                    "sd": {"type": "number", "description": "plan_means: standard deviation of the metric in one arm, same units as the metric (or give baseline_mean and cv_pct)"},
                    "baseline_mean": {"type": "number", "description": "plan_means: current mean, used with cv_pct to derive sd"},
                    "cv_pct": {"type": "number", "description": "plan_means: spread as percent of the mean"},
                    "minimum_difference": {"type": "number", "description": "plan_means: smallest difference in means worth detecting, same units"},
                    "weekly_units": {"type": "number", "description": "plan_means: measured units per week across all variants"},
                    "units": {"type": "string", "description": "plan_means: unit label, e.g. EUR or seconds"},
                    "paired": {"type": "boolean", "description": "plan_means: true when every participant is measured under every variant (within-subject)"},
                    "correlation": {"type": "number", "description": "plan_means, paired: correlation between a participant's two measurements, default 0.5"},
                    "segment": {"type": "string", "description": "plan_rate: the population the test runs in, e.g. returning visitors; written into the decision rule"},
                    "baseline_rate": {"type": "number", "description": "current rate in percent, e.g. 3.2"},
                    "minimum_effect": {"type": "number", "description": "smallest lift worth detecting"},
                    "effect_kind": {"type": "string", "enum": ["relative", "absolute"], "description": "relative = percent of baseline (default); absolute = percentage points"},
                    "weekly_traffic": {"type": "number", "description": "eligible visitors or orders per week across all variants"},
                    "variants": {"type": "integer", "description": "arms including control, default 2"},
                    "alpha": {"type": "number", "description": "significance level, default 0.05"},
                    "power": {"type": "number", "description": "default 0.8"},
                    "two_sided": {"type": "boolean"},
                    "max_weeks": {"type": "number", "description": "how long the marketer can wait; returns what is detectable in that time"},
                    "metric": {"type": "string", "description": "what the rate measures, e.g. checkout conversion"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "funnel_analyze",
            "description": "Read the user's own campaign, order or event data (CSV or XLSX in the workspace) and compute the funnel: counts per stage, conversion from the previous stage and from the first, the step that loses the most, the date window actually covered, per-group funnels and the worst group. Call it whenever a brief asks where a funnel leaks, which step to fix, how conversion differs by channel or cohort, or whenever a data file with stage counts is in the workspace — never summarise such a file in prose or compute the percentages yourself. Wide files (one column per stage) take stages; long files (one row per event or per stage) take stage_column with count_column or id_column. The result lists blank cells, bad numbers, unreadable dates and empty windows under missing: report them, do not fill them in.",
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
                        "type": "string",
                        "description": "column such as channel, campaign or cohort: adds one funnel per value and names the worst group (groups with at least 30 at the first stage)"
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
            "description": "Combined and incremental reach of a media plan across channels, with bounds instead of reaches added up as if nobody saw two channels. Use it whenever a brief compares channels, allocates a media budget, or quotes a combined reach or cost per person reached. Give the audience and each channel's reach in people (or reach_unit=pct); list the pairwise overlaps you know under overlaps. Unknown pairs are returned under missing with lower and upper bounds, no point estimate unless assumption=independent, and narrows_most names the one overlap to ask for (use clarify or state the assumption).",
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
            "description": "Compare an ad, email or post with the page it links to, element by element: promise (H1, title, first screen), price, call to action (buttons and forms), audience words, offer words and language. Run it for every ad or email that links to a page, after the copy is written and before handing over, instead of judging message match by eye. Give the ad fields and exactly one page source: html, a workspace path, or a url. Each finding (block / warn / info) quotes the exact ad copy and the exact page element behind it; fix blocks before the ad runs. A url that could not be fetched lands under missing, so give the HTML or path then.",
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
                    }
                },
                "required": [
                    "ad"
                ]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "claim_check",
            "description": "Check every measurable claim in a draft against the sources. Run it on any draft that states numbers, percentages, prices, dates, rankings ('first', 'largest', 'No. 1', 'pirmas', 'didžiausias') or credentials ('certified', 'partner', 'award') before handing the draft over; the verifier runs it too, so an unsupported or differing claim will be found. Each claim comes back with the best source passage, its location (file line or char range) and a status: supported, number_differs (same subject, other figure — fix the draft or the fact) or unsupported (find a source, add it to runs/notes/facts.md with its URL, or remove the claim). runs/notes/facts.md is read automatically when it exists; add other workspace files or URLs under sources. Never call a figure supported yourself: quote this tool's result.",
            "parameters": {
                "type": "object",
                "properties": {
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
    props["ads"]["items"]["properties"].update(additions["ad"])
    props.update(additions["pack"])
    return spec


def worker_tools(include_server: bool = True) -> list[dict]:
    tools = [
        _extended_ad_board_spec(spec) if spec.get("function", {}).get("name") == "ad_board" else spec
        for spec in CLIENT_TOOL_SPECS
    ]
    if include_server:
        tools.extend(SERVER_TOOLS)
    return tools


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
    try:
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
            if mode in {"evaluate", "plan_means", "plan_rate"}:
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
        if name == "reach_overlap":
            from rocky.reach import reach_overlap_tool

            return reach_overlap_tool(args)
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
            return page_inspect_tool(tools, args, fetch_html=fetch_html)
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
