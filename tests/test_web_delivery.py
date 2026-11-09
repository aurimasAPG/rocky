"""Client state checks, without provider calls or browser dependencies."""

from html.parser import HTMLParser
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.parents = {}
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        ident = attrs.get("id")
        if ident:
            self.parents[ident] = [item[1] for item in self.stack]
        if tag == "script":
            self.scripts.append(urlsplit(attrs.get("src", "")).path)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, ident))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def test_question_and_stop_remain_outside_the_collapsible_task_composer():
    page = Structure()
    page.feed((ROOT / "rocky/static/index.html").read_text())
    for ident in ("ask-form", "ask-input", "stop"):
        assert "handover" in page.parents[ident]
        assert "task-composer" not in page.parents[ident]
        assert "task-journey" not in page.parents[ident]
        assert "run-details" not in page.parents[ident]
    assert (
        page.scripts.index("/static/delivery.js")
        < page.scripts.index("/static/journey.js")
        < page.scripts.index("/static/app.js")
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed for browser-script state checks")
def test_delivery_client_behavior():
    result = subprocess.run(
        [shutil.which("node"), str(ROOT / "tests/web_delivery.cjs")],
        capture_output=True, text=True, cwd=ROOT, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "35 delivery behavior checks passed" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed for journey state checks")
def test_journey_state_transitions():
    result = subprocess.run(
        [shutil.which("node"), str(ROOT / "tests/web_journey.cjs")],
        capture_output=True, text=True, cwd=ROOT, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "journey state checks passed" in result.stdout
