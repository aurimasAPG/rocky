import json

import pytest

from rocky.claims import FACTS_PATH, _numbers, claim_check, claim_check_tool
from rocky.tools import ToolError, WorkspaceTools

FACTS = """# Faktai
- APG Media aptarnauja 180 klientų Lietuvoje ir Latvijoje — https://apgmedia.lt/apie
- Vidutinė kampanijų konversija 2,5 proc. per 2025 m. — https://apgmedia.lt/rezultatai
- APG Media yra Google Premier Partner nuo 2021 m. — https://partnersdirectory.withgoogle.com
- Valdomas biudžetas 12 400 € per mėnesį, 15 kampanijų
"""

DRAFT = """Mes aptarnaujame 200+ klientų Lietuvoje.
Kampanijų konversija siekia 2,5 % kiekvieną mėnesį.
Esame Google Premier Partner agentūra.
Greičiausia agentūra Baltijos šalyse.
Valdome 12.400 € biudžetą kas mėnesį.
Mūsų komanda dirba su meile.
"""


@pytest.fixture
def ws(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file(FACTS_PATH, FACTS)
    return tools


def _by_text(result, fragment):
    return next(c for c in result["claims"] if fragment in c["text"])


def test_lithuanian_draft_against_facts_file(ws):
    out = claim_check(ws, {"draft": DRAFT, "language": "lt"})
    assert out["language"] == "lt"
    assert [s["source"] for s in out["sources_read"]] == [FACTS_PATH]
    assert out["sources_read"][0]["passages"] == 5

    conversion = _by_text(out, "2,5 %")
    assert conversion["status"] == "supported"
    assert conversion["source"] == FACTS_PATH and conversion["location"] == "line 3"
    assert "2,5 proc." in conversion["passage"]
    assert conversion["numbers"][0] == {"raw": "2,5", "value": 2.5, "unit": "%", "context": "kiekvieną"}
    assert conversion["line"] == 2

    partner = _by_text(out, "Premier Partner")
    assert partner["kind"] == "credential" and partner["status"] == "supported"
    assert partner["location"] == "line 4" and "Google Premier Partner" in partner["passage"]

    budget = _by_text(out, "12.400")
    assert budget["status"] == "supported" and budget["location"] == "line 5"
    assert budget["numbers"][0]["unit"] == "€"

    clients = _by_text(out, "200+")
    assert clients["status"] == "number_differs"
    assert clients["differs"] == [{"claim": "200+", "source": "180", "context": "klientų"}]
    assert clients["location"] == "line 2" and "180 klientų" in clients["passage"]
    assert "draft says 200+, source says 180" in clients["note"]

    fastest = _by_text(out, "Greičiausia")
    assert fastest["kind"] == "superlative" and fastest["status"] == "unsupported"
    assert fastest["source"] is None and fastest["passage"] is None

    assert all("meile" not in c["text"] for c in out["claims"])
    assert out["counts"] == {"supported": 3, "number_differs": 1, "unsupported": 1, "total": 5}
    assert out["summary"].startswith("5 claims: 3 supported, 1 number differs (200+ vs 180 klientų — review), 1 unsupported ('Greičiausia")
    assert out["missing"] == []


def test_supported_superlative_needs_the_same_word_and_a_shared_subject(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("src.md", "Įmonė tapo pirmoji Lietuvoje sertifikuota elektroninės prekybos agentūra.\nKita eilutė apie orą.")
    out = claim_check(tools, {"draft": "Esame pirmas Lietuvoje tokio tipo partneris.", "sources": ["src.md"]})
    claim = out["claims"][0]
    assert claim["kind"] == "superlative" and claim["status"] == "supported"
    assert claim["location"] == "line 1" and "lietu" in claim["shared_words"]
    nobody = claim_check(tools, {"draft": "Esame didžiausia agentūra Estijoje.", "sources": ["src.md"]})
    assert nobody["claims"][0]["status"] == "unsupported"


def test_number_formats_normalise_the_same_way():
    assert _numbers("12 400 klientų", "lt")[0].value == 12400
    assert _numbers("12 400 klientų", "lt")[0].value == 12400
    assert _numbers("1 200,50 €", "lt")[0].value == 1200.5
    lt = _numbers("12,400", "lt")[0]
    en = _numbers("12,400", "en")[0]
    assert lt.value == 12.4 and en.value == 12400
    assert lt.candidates == en.candidates == frozenset({"12.4", "12400"})
    plus = _numbers("200+ klientų", "en")[0]
    assert plus.value == 200 and plus.approx and plus.context == "klientų"
    assert _numbers("2.5% conversion", "en")[0].cls == "percent"
    assert _numbers("1,200,300 users", "en")[0].value == 1200300
    assert _numbers("2024-05-12", "en")[0].value == "2024-05-12"
    assert _numbers("12.05.2024", "lt")[0].value == "2024-05-12"


def test_thin_space_and_comma_formats_match_across_draft_and_source(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("facts.txt", "Revenue reached 12,400 EUR from 1,250 customers last year.")
    out = claim_check(tools, {"draft": "Pajamos siekė 12 400 € iš 1 250 klientų pernai (customers, revenue).", "sources": ["facts.txt"]})
    claim = out["claims"][0]
    assert claim["status"] == "supported"
    assert [n["value"] for n in claim["numbers"]] == [12400, 1250]


def test_percent_must_match_as_percent(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("facts.txt", "Conversion rate for the campaign was 2.5 last year.\nConversion rate for the campaign was 3 %.")
    out = claim_check(tools, {"draft": "Campaign conversion rate reached 2.5 %.", "sources": ["facts.txt"]})
    claim = out["claims"][0]
    assert claim["status"] == "number_differs"
    assert claim["differs"][0] == {"claim": "2.5", "source": "3", "context": None}
    assert claim["location"] == "line 2"


def test_html_source_has_tags_stripped(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file(
        "about.html",
        "<html><head><title>About</title><style>p{color:red}</style></head>\n<body>\n<h1>Company</h1>\n"
        "<p>We serve <b>180</b> clients across Lithuania and Latvia.</p>\n<script>var x = 999;</script>\n</body></html>",
    )
    out = claim_check(tools, {"draft": "We serve 180 clients across Lithuania.", "sources": ["about.html"]})
    claim = out["claims"][0]
    assert claim["status"] == "supported"
    assert claim["passage"] == "We serve 180 clients across Lithuania and Latvia."
    assert claim["location"] == "line 4"
    assert "<b>" not in claim["passage"]


def test_url_source_without_fetch_is_missing_and_with_fetch_is_read(tmp_path):
    tools = WorkspaceTools(tmp_path)
    url = "https://example.com/about"
    args = {"draft": "We serve 180 clients across Lithuania.", "sources": [url]}
    out = claim_check(tools, args)
    assert out["missing"] == [f"{url}: not fetched (no fetch available; save the page into the workspace instead)"]
    assert out["sources_read"] == []
    assert out["claims"][0]["status"] == "unsupported"

    page = "<html><body><h1>About</h1>\n<p>Founded in 2010.</p>\n<p>We serve 180 clients across Lithuania and Latvia.</p></body></html>"
    calls = []

    def fake_fetch(u):
        calls.append(u)
        return page

    out = claim_check(tools, args, fetch=fake_fetch)
    assert calls == [url] and out["missing"] == []
    claim = out["claims"][0]
    assert claim["status"] == "supported" and claim["source"] == url
    start = page.index("We serve 180")
    assert claim["location"] == f"chars {start}–{start + len('We serve 180 clients across Lithuania and Latvia.')}"

    def broken(u):
        raise OSError("timeout")

    out = claim_check(tools, args, fetch=broken)
    assert out["missing"] == [f"{url}: fetch failed: timeout"]


def test_draft_path_and_default_facts_file(ws):
    ws.write_file("deliverables/draft.md", "# Pasiūlymas\n\n1. Aptarnaujame 180 klientų Lietuvoje ir Latvijoje.\n")
    out = claim_check(ws, {"draft_path": "deliverables/draft.md"})
    assert out["sources_read"][0]["source"] == FACTS_PATH
    claim = out["claims"][0]
    assert claim["status"] == "supported" and claim["line"] == 3
    assert claim["numbers"] == [{"raw": "180", "value": 180.0, "unit": None, "context": "klientų"}]

    html = "<html><body><p>Aptarnaujame <strong>180</strong> klientų Lietuvoje.</p></body></html>"
    ws.write_file("deliverables/draft.html", html)
    out = claim_check(ws, {"draft_path": "deliverables/draft.html"})
    assert out["claims"][0]["text"] == "Aptarnaujame 180 klientų Lietuvoje." and out["claims"][0]["status"] == "supported"


def test_explicit_sources_skip_facts_only_when_asked(ws):
    ws.write_file("other.md", "Turime 200 klientų Lietuvoje.")
    both = claim_check(ws, {"draft": "Turime 200 klientų Lietuvoje.", "sources": ["other.md"]})
    assert [s["source"] for s in both["sources_read"]] == [FACTS_PATH, "other.md"]
    assert both["claims"][0]["status"] == "supported" and both["claims"][0]["source"] == "other.md"
    only = claim_check(ws, {"draft": "Turime 200 klientų Lietuvoje.", "sources": ["other.md"], "include_facts": False})
    assert [s["source"] for s in only["sources_read"]] == ["other.md"]
    custom = claim_check(ws, {"draft": "Turime 200 klientų Lietuvoje.", "facts_path": "other.md"})
    assert [s["source"] for s in custom["sources_read"]] == ["other.md"]


def test_missing_sources_are_named_not_guessed(ws):
    ws.write_file("image.png", "not really a png")
    out = claim_check(ws, {"draft": "Turime 200 klientų Lietuvoje.", "sources": ["nope.md", "image.png"]})
    assert out["missing"] == ["nope.md: not found", "image.png: unsupported file type .png (text, markdown, html, json or csv only)"]
    assert [s["source"] for s in out["sources_read"]] == [FACTS_PATH]
    ws.write_file("deliverables/empty.md", "   \n\n")
    empty = claim_check(ws, {"draft_path": "deliverables/empty.md"})
    assert empty["claims"] == [] and empty["missing"] == ["draft has no text"]
    assert empty["counts"]["total"] == 0
    assert empty["summary"] == "No measurable claims found in the draft (draft has no text)."


def test_errors_are_plain(tmp_path, ws):
    with pytest.raises(ToolError, match="draft"):
        claim_check(ws, {})
    with pytest.raises(ToolError, match="draft_path not found"):
        claim_check(ws, {"draft_path": "missing.md"})
    bare = WorkspaceTools(tmp_path / "bare")
    with pytest.raises(ToolError, match="nothing to check against"):
        claim_check(bare, {"draft": "Turime 200 klientų."})
    with pytest.raises(ToolError, match="escapes workspace"):
        claim_check(ws, {"draft": "Turime 200 klientų.", "sources": ["../../etc/passwd"]})
    with pytest.raises(ToolError, match="sources must be a list"):
        claim_check(ws, {"draft": "Turime 200 klientų.", "sources": 5})


def test_claim_check_tool_returns_json_whose_counts_add_up(ws):
    raw = claim_check_tool(ws, {"draft": DRAFT})
    out = json.loads(raw)
    counts = out["counts"]
    assert counts["supported"] + counts["number_differs"] + counts["unsupported"] == counts["total"] == len(out["claims"])
    assert "ą" in raw or "ų" in raw  # ensure_ascii=False keeps Lithuanian letters readable
    for claim in out["claims"]:
        if claim["status"] == "supported":
            assert claim["passage"] and claim["source"] and claim["location"]


def test_same_input_gives_same_output(ws):
    a = claim_check_tool(ws, {"draft": DRAFT})
    b = claim_check_tool(ws, {"draft": DRAFT})
    assert a == b



def test_the_verifier_has_claim_check_and_dispatch_routes_it(tmp_path):
    import json as _json

    from rocky.tools import VERIFY_TOOL_NAMES, VERIFY_TOOL_SPECS, WorkspaceTools, dispatch, worker_tools

    assert "claim_check" in VERIFY_TOOL_NAMES
    assert "claim_check" in [t["function"]["name"] for t in VERIFY_TOOL_SPECS]
    assert "claim_check" in [t["function"]["name"] for t in worker_tools(False)]
    tools = WorkspaceTools(tmp_path)
    tools.write_file("runs/notes/facts.md", "Mums pasitiki daugiau nei 200 klientų — https://x.lt/apie\n")
    out = _json.loads(dispatch(tools, "claim_check", {"draft": "Mums pasitiki 200 klientų."}))
    assert out["counts"]["supported"] == 1

    class Searcher:
        def fetch(self, url):
            return "fetch failed: boom"

    out = _json.loads(dispatch(tools, "claim_check", {"draft": "Mums pasitiki 200 klientų.", "sources": ["https://down.lt"]}, searcher=Searcher()))
    assert any("down.lt" in m for m in out["missing"])
