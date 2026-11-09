"""Terminal splash: a happy golden retriever named Rocky."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from rocky.theme import APRICOT, CORAL, DIM, INK, PERI, SEA

# Apricot coat, coral tongue — no metallic gold.
ROCKY_DOG = f"""\
[#{APRICOT}]            ∩_____∩[/]
[#{APRICOT}]           /  ^   ^  ╲[/]
[#{CORAL}]          |   •   •   |      ∩[/]
[#{CORAL}]          |     ▽     |     /[/]
[#{APRICOT}]           ╲   ╰─╯   /    /[/]
[#{SEA}]           /|       |╲  /[/]
[#{SEA}]          ( |   ▔   | )[/]
[#{DIM}]            ∪       ∪[/]"""

# Name-tag wordmark, not a gold FIGlet block (that is the Hermes look).
ROCKY_LOGO = f"""\
[#{SEA}]  ╭──────────────────────────╮[/]
[italic bold #{CORAL}]  │   r o c k y              │[/]
[#{DIM}]  │   the retriever          │[/]
[#{SEA}]  ╰──────────────────────────╯[/]"""

ROCKY_DOG_COMPACT = f"""\
[#{APRICOT}]    ∩_____∩[/]
[#{CORAL}]   /  •   •  ╲   ∩[/]
[#{APRICOT}]  |     ▽     | /[/]
[#{SEA}]   ╲   ╰─╯   /[/]
[#{SEA}]   /|  ▔  |╲[/]
[#{DIM}]    ∪     ∪[/]
[italic #{INK}]  rocky  ·  good boy[/]"""


def print_banner(
    *,
    workspace: Path | str | None = None,
    budget_eur: float | None = None,
    interactive: bool = True,
    console: Console | None = None,
) -> None:
    out = console or Console()
    out.print(ROCKY_LOGO)
    layout = Table.grid(padding=(0, 3))
    layout.add_column("dog", justify="left")
    layout.add_column("copy", justify="left", vertical="middle")
    right = f"[italic #{INK}]a happy golden retriever with a job.[/]\n"
    right += f"[#{CORAL}]plan[/] [#{DIM}]·[/] [#{SEA}]work[/] [#{DIM}]·[/] [#{PERI}]verify[/]  [#{DIM}]OpenRouter[/]\n"
    if workspace is not None:
        right += f"[#{DIM}]{workspace}[/]\n"
    if budget_eur is not None:
        right += f"[#{DIM}]default cap €{float(budget_eur):.2f}  ·  raise only if the task looks hard[/]\n"
    if interactive:
        right += f"\n[#{INK}]Type a task. Empty line or q to quit.[/]"
    layout.add_row(ROCKY_DOG, right)
    out.print(layout)
    out.print()


def tui_splash() -> str:
    return ROCKY_DOG_COMPACT


def composer_brand(*, compact: bool = False) -> Text:
    """Keep Rocky's existing mascot and name-tag visible beside the task editor."""
    dog = Text.from_markup(ROCKY_DOG_COMPACT).split("\n")[:4 if compact else 6]
    right = Text.from_markup(ROCKY_LOGO).split("\n")
    if not compact:
        right.extend([
            Text("What should Rocky deliver?", style=f"bold #{INK}"),
            Text("Paste a brief. Include the output and any limits.", style=f"#{DIM}"),
        ])
    brand = Text()
    for index, line in enumerate(dog):
        if index:
            brand.append("\n")
        brand.append(line)
        brand.append(" " * (22 - line.cell_len))
        brand.append(right[index])
    return brand
