"""Recipes: plan skeletons the planner must follow when a goal matches.

A recipe is a small TOML file (see ``rocky/recipes/marketing.toml``). Rocky
loads, in order of increasing precedence: the bundled folder, every folder in
``ROCKY_RECIPES`` (comma-separated), and ``<workspace>/.rocky/recipes``. Same
name → later wins. Workers can save a new recipe with the ``recipe`` tool;
it is used from the next run on.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from rocky.tools import ToolError

_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
_EXPLICIT = re.compile(r"(?:^|\s)recipe:([a-z0-9_-]+)", re.I)


@dataclass
class Recipe:
    name: str
    description: str = ""
    deliverable_path: str = ""
    notes: str = ""
    cost_multiplier: float = 1.0
    domains: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    source: str = ""

    def render_steps(self, goal: str) -> list[dict]:
        brief = (goal or "").strip() or "the user's request"
        out = []
        for i, step in enumerate(self.steps, 1):
            out.append(
                {
                    "id": int(step.get("id") or i),
                    "title": str(step.get("title") or f"Step {i}"),
                    "detail": str(step.get("detail") or "").replace("{goal}", brief),
                    "depends_on": [int(d) for d in step.get("depends_on") or []],
                    "writes": [str(w) for w in step.get("writes") or []],
                }
            )
        return out

    def matches(self, goal: str, assessment: dict | None = None) -> bool:
        domain = str((assessment or {}).get("domain") or "").lower()
        if domain and domain in {d.lower() for d in self.domains}:
            return True
        blob = (
            f" {goal} {(assessment or {}).get('goal') or ''} "
            f"{(assessment or {}).get('deliverable') or ''} "
        ).lower()
        return any(h.lower() in blob for h in self.hints)

    def _matching_plan_step(self, recipe_step: dict, plan_steps: list[dict], taken: set[int]) -> int | None:
        requires = [str(r).lower() for r in recipe_step.get("requires") or []]
        for i, step in enumerate(plan_steps):
            if i in taken:
                continue
            blob = f"{step.get('title', '')} {step.get('detail', '')}".lower()
            if not requires or any(r in blob for r in requires):
                return i
        return None

    def covered_by(self, plan: dict) -> bool:
        """True when the planner's own steps already mention every required stage."""
        steps = list(plan.get("steps") or [])
        if len(steps) < len(self.steps):
            return False
        taken: set[int] = set()
        for step in self.steps:
            hit = self._matching_plan_step(step, steps, taken)
            if hit is None:
                return False
            taken.add(hit)
        return True

    def merge_writes(self, plan: dict) -> dict:
        """Carry each recipe step's `writes` onto the planner step that covers it."""
        steps = list(plan.get("steps") or [])
        taken: set[int] = set()
        for step in self.steps:
            hit = self._matching_plan_step(step, steps, taken)
            if hit is None:
                continue
            taken.add(hit)
            have = [str(w) for w in steps[hit].get("writes") or []]
            for path in step.get("writes") or []:
                if path not in have:
                    have.append(str(path))
            steps[hit]["writes"] = have
        plan["steps"] = steps
        return plan

    def apply(self, plan: dict, goal: str) -> dict:
        """Force this recipe onto a plan (or just fix the deliverable when already covered)."""
        if self.covered_by(plan):
            want = Path(self.deliverable_path).suffix
            have = Path(str(plan.get("deliverable_path") or "")).suffix
            if want and have != want:
                plan["deliverable_path"] = self.deliverable_path
            return self.merge_writes(plan)
        plan["steps"] = self.render_steps(goal)
        if self.deliverable_path:
            plan["deliverable_path"] = self.deliverable_path
        if self.notes:
            notes = str(plan.get("notes") or "").strip()
            plan["notes"] = f"{notes} {self.notes}".strip() if notes else self.notes
        return plan

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "deliverable_path": self.deliverable_path,
            "notes": self.notes,
            "cost_multiplier": self.cost_multiplier,
            "match": {"domains": list(self.domains), "hints": list(self.hints)},
            "steps": [dict(s) for s in self.steps],
        }


def bundled_dir() -> Path:
    return Path(__file__).resolve().parent / "recipes"


def workspace_dir(workspace: str | Path) -> Path:
    return Path(workspace).expanduser().resolve() / ".rocky" / "recipes"


def _env_dirs() -> list[Path]:
    raw = os.environ.get("ROCKY_RECIPES") or ""
    return [Path(p.strip()).expanduser() for p in raw.split(",") if p.strip()]


def parse_recipe(text: str, source: str = "") -> Recipe:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{source or 'recipe'}: invalid TOML ({exc})") from exc
    return from_dict(raw, source)


def from_dict(raw: dict, source: str = "") -> Recipe:
    name = str(raw.get("name") or "").strip().lower()
    if not _NAME.match(name):
        raise ValueError(f"{source or 'recipe'}: name must be 2-40 chars of a-z 0-9 _ - (got {name!r})")
    match = raw.get("match") or {}
    steps = []
    for i, step in enumerate(raw.get("steps") or [], 1):
        if not isinstance(step, dict) or not str(step.get("detail") or "").strip():
            raise ValueError(f"{source or name}: step {i} needs a detail")
        steps.append(
            {
                "id": int(step.get("id") or i),
                "title": str(step.get("title") or f"Step {i}"),
                "detail": str(step.get("detail")),
                "depends_on": [int(d) for d in step.get("depends_on") or []],
                "requires": [str(r) for r in step.get("requires") or []],
                "writes": [str(w) for w in step.get("writes") or []],
            }
        )
    if not steps:
        raise ValueError(f"{source or name}: a recipe needs at least one step")
    try:
        mult = float(raw.get("cost_multiplier") or 1.0)
    except (TypeError, ValueError):
        mult = 1.0
    return Recipe(
        name=name,
        description=str(raw.get("description") or "").strip(),
        deliverable_path=str(raw.get("deliverable_path") or "").strip(),
        notes=str(raw.get("notes") or "").strip(),
        cost_multiplier=mult,
        domains=[str(d) for d in match.get("domains") or []],
        hints=[str(h) for h in match.get("hints") or []],
        steps=steps,
        source=source,
    )


def load_recipes(workspace: str | Path | None = None) -> dict[str, Recipe]:
    folders = [bundled_dir(), *_env_dirs()]
    if workspace is not None:
        folders.append(workspace_dir(workspace))
    recipes: dict[str, Recipe] = {}
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.toml")):
            try:
                recipe = parse_recipe(path.read_text(encoding="utf-8"), str(path))
            except (ValueError, OSError):
                continue
            recipes[recipe.name] = recipe
    return recipes


def find_recipe(
    goal: str,
    assessment: dict | None = None,
    recipes: dict[str, Recipe] | None = None,
) -> Recipe | None:
    """Explicit ``recipe:<name>`` in the goal wins; then domain; then hint words."""
    recipes = recipes if recipes is not None else load_recipes()
    hit = _EXPLICIT.search(goal or "")
    if hit and hit.group(1).lower() in recipes:
        return recipes[hit.group(1).lower()]
    domain = str((assessment or {}).get("domain") or "").lower()
    if domain:
        for recipe in recipes.values():
            if domain in {d.lower() for d in recipe.domains}:
                return recipe
    for recipe in recipes.values():
        if recipe.matches(goal, assessment):
            return recipe
    return None


def catalog(recipes: dict[str, Recipe]) -> list[dict[str, str]]:
    return [{"name": r.name, "description": r.description} for r in recipes.values()]


# --- TOML writer for the `recipe` tool -----------------------------------

def _q(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def to_toml(recipe: Recipe) -> str:
    lines = [
        f"name = {_q(recipe.name)}",
        f"description = {_q(recipe.description)}",
        f"deliverable_path = {_q(recipe.deliverable_path)}",
        f"notes = {_q(recipe.notes)}",
        f"cost_multiplier = {float(recipe.cost_multiplier)}",
        "",
        "[match]",
        "domains = [" + ", ".join(_q(d) for d in recipe.domains) + "]",
        "hints = [" + ", ".join(_q(h) for h in recipe.hints) + "]",
    ]
    for step in recipe.steps:
        lines += [
            "",
            "[[steps]]",
            f"id = {int(step['id'])}",
            f"title = {_q(step['title'])}",
            "depends_on = [" + ", ".join(str(int(d)) for d in step.get("depends_on") or []) + "]",
            "requires = [" + ", ".join(_q(r) for r in step.get("requires") or []) + "]",
            "writes = [" + ", ".join(_q(w) for w in step.get("writes") or []) + "]",
            f"detail = {_q(step['detail'])}",
        ]
    return "\n".join(lines) + "\n"


def save_recipe(workspace: str | Path, raw: dict) -> Path:
    recipe = from_dict(raw, "recipe tool")
    folder = workspace_dir(workspace)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{recipe.name}.toml"
    path.write_text(to_toml(recipe), encoding="utf-8")
    return path


def recipe_tool(workspace: str | Path, args: dict) -> str:
    """Backs the worker-facing `recipe` tool: list | view | save."""
    action = str(args.get("action") or "list").strip().lower()
    recipes = load_recipes(workspace)
    if action == "list":
        if not recipes:
            return "(no recipes)"
        return "\n".join(f"- {r.name}: {r.description}" for r in recipes.values())
    if action == "view":
        name = str(args.get("name") or "").strip().lower()
        recipe = recipes.get(name)
        if recipe is None:
            raise ToolError(f"unknown recipe: {name}")
        return to_toml(recipe)
    if action == "save":
        body = args.get("recipe")
        if not isinstance(body, dict):
            raise ToolError("recipe save needs a 'recipe' object (name, description, match, steps, deliverable_path)")
        try:
            path = save_recipe(workspace, body)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        return f"saved {path.name} — used from the next run on. Ask the user to review it."
    raise ToolError("action must be list, view, or save")
