"""Plan post-processing: force a matching recipe onto the planner's output.

The marketing helpers stay for callers that want them directly; they are thin
wrappers over the bundled ``marketing`` recipe.
"""

from __future__ import annotations

from rocky.recipes import Recipe, find_recipe, load_recipes


def is_marketing(goal: str, assessment: dict | None = None) -> bool:
    recipe = find_recipe(goal, assessment)
    return recipe is not None and recipe.name in {"marketing", "ads"}


def canonical_marketing_steps(goal: str) -> list[dict]:
    recipe = load_recipes().get("marketing")
    return recipe.render_steps(goal) if recipe else []


def ensure_pipeline(
    plan: dict,
    goal: str,
    assessment: dict | None = None,
    recipe: Recipe | None = None,
    workspace=None,
) -> dict:
    """Apply the matching recipe (explicit, domain, or hint match). No match → plan untouched."""
    if recipe is None:
        recipe = find_recipe(goal, assessment, load_recipes(workspace))
    if recipe is None:
        return plan
    return recipe.apply(plan, goal)
