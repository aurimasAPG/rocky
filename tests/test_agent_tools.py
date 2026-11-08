"""Workers use the data and media tools through a normal run, and ask when an input is missing."""

import json

from rocky.engine import orchestrate
from rocky.llm import Completion, ToolCall
from tests.test_loop import MODELS, ScriptedClient

CSV = """date,channel,visits,product_views,add_to_cart,checkout,orders
2026-09-01,facebook,4000,2100,600,320,110
2026-09-02,google,5000,3000,900,500,150
2026-09-03,facebook,3400,1800,400,260,50
"""


class _TrivialClient(ScriptedClient):
    deliverable = "answer.md"

    def complete(self, *, model, messages, tools=None, json_schema=None, extra=None):
        system = messages[0]["content"] if messages else ""
        if "You assess tasks" in system:
            return Completion(
                content=json.dumps(
                    {
                        "goal": "answer from data",
                        "complexity": "trivial",
                        "domain": "marketing",
                        "success_criteria": ["the answer file exists"],
                        "needs_web": False,
                        "deliverable": self.deliverable,
                    }
                )
            )
        if "You execute this subtask" in system:
            self.worker_round += 1
            last = messages[-1]
            if last.get("role") == "tool":
                if last["content"].startswith("wrote "):
                    return Completion(content="done")
                return self.after_tool(last["content"])
            return self.first_turn()
        return super().complete(model=model, messages=messages, tools=tools, json_schema=json_schema, extra=extra)


class FunnelClient(_TrivialClient):
    def first_turn(self):
        return Completion(
            content="",
            tool_calls=[
                ToolCall(
                    id="f",
                    name="funnel_analyze",
                    arguments={
                        "path": "data/orders.csv",
                        "stages": ["visits", "product_views", "add_to_cart", "checkout", "orders"],
                        "date_column": "date",
                        "group_by": "channel",
                    },
                )
            ],
        )

    def after_tool(self, content):
        out = json.loads(content)
        drop = out["largest_dropoff"]
        text = f"{out['summary']}\nWorst channel: {out['worst_group']}\nFix first: {drop['from_stage']} → {drop['to_stage']} ({drop['lost']} lost)"
        return Completion(content="", tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": "deliverables/answer.md", "content": text})])


def test_a_worker_reads_the_marketers_csv_through_the_funnel_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text(CSV, encoding="utf-8")
    events = []
    result = orchestrate(
        goal="Where does our checkout funnel leak? Data in data/orders.csv",
        workspace=tmp_path,
        client=FunnelClient(),
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
    )
    assert result.passed is True
    call = next(e.payload for e in events if e.type == "tool_call" and e.payload["name"] == "funnel_analyze")
    assert call["arguments"]["path"] == "data/orders.csv"
    text = (tmp_path / "deliverables" / "answer.md").read_text()
    # 12 400 visits → 310 orders, and the biggest absolute loss is visits → product_views (5 500)
    assert "12,400" in text or "12 400" in text
    assert "310" in text
    assert "visits → product_views (5500 lost)" in text
    assert "Worst channel:" in text and "facebook" in text.lower()


class ReachClient(_TrivialClient):
    """First call has an unknown overlap; the worker asks, then calls again with it."""

    def __init__(self):
        super().__init__()
        self.asked = False
        self.first_result = None

    def first_turn(self):
        return Completion(content="", tool_calls=[ToolCall(id="r1", name="reach_overlap", arguments=self._args())])

    def _args(self, shared=None):
        args = {
            "audience": 400000,
            "channels": [{"name": "Facebook", "reach": 200000, "cost": 4000}, {"name": "Instagram", "reach": 150000, "cost": 2500}],
        }
        if shared is not None:
            args["overlaps"] = [{"a": "Facebook", "b": "Instagram", "shared": shared}]
        return args

    def after_tool(self, content):
        if content.startswith("user:"):
            return Completion(content="", tool_calls=[ToolCall(id="r2", name="reach_overlap", arguments=self._args(shared=100000))])
        out = json.loads(content)
        if out["missing"] and not self.asked:
            self.asked = True
            self.first_result = out
            return Completion(
                content="",
                tool_calls=[ToolCall(id="q", name="clarify", arguments={"question": out["narrows_most"]["question"] if isinstance(out["narrows_most"], dict) else str(out["narrows_most"])})],
            )
        combined = out["combined"]
        text = f"Combined reach {combined['point']} ({combined['lower']}–{combined['upper']}) of 400000."
        return Completion(content="", tool_calls=[ToolCall(id="w", name="write_file", arguments={"path": "deliverables/answer.md", "content": text})])


def test_a_worker_asks_for_the_unknown_overlap_before_quoting_a_combined_reach(tmp_path, monkeypatch):
    monkeypatch.setenv("ROCKY_HOME", str(tmp_path / "home"))
    events = []
    questions = []

    def ask_user(question, options=None):
        questions.append(question)
        return "about 100 000 people see both"

    client = ReachClient()
    result = orchestrate(
        goal="Facebook reaches 200k and Instagram 150k of our 400k audience. What is the combined reach?",
        workspace=tmp_path,
        client=client,
        models=MODELS,
        emit=events.append,
        max_loops=1,
        budget_eur=1.0,
        eur_rate=0.92,
        ask_user=ask_user,
    )
    assert result.passed is True
    names = [e.payload["name"] for e in events if e.type == "tool_call"]
    assert names == ["reach_overlap", "clarify", "reach_overlap", "write_file"]
    assert len(questions) == 1 and "Instagram" in questions[0] and "Facebook" in questions[0]
    first = client.first_result
    assert first["combined"]["point"] is None and first["combined"]["lower"] == 200000 and first["combined"]["upper"] == 350000
    assert (tmp_path / "deliverables" / "answer.md").read_text() == "Combined reach 250000 (250000–250000) of 400000."
