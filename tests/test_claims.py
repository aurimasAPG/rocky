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


# --- scope and inference (#309) ---------------------------------------------------------


def test_scope_lt_lab_conditions_dropped_and_generalised(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file(FACTS_PATH, "Laboratoriniuose bandymuose (ISO 11092, 20 °C, 65 % RH) audinys sugėrė 3 kartus daugiau drėgmės nei medvilnės kontrolinis pavyzdys. https://supplier.example/report.pdf\n")
    out = claim_check(tools, {"draft": "Sugeria 3 kartus daugiau drėgmės nei medvilnė – sausa visą treniruotę.", "check_scope": True})
    claim = out["claims"][0]
    assert claim["status"] == "supported_scope_narrower"
    assert claim["source"] == FACTS_PATH and claim["location"] == "line 1"
    assert claim["conditions"] == ["Laboratoriniuose", "bandymuose", "ISO 11092", "20 °C", "65 % RH"]
    assert claim["condition_dropped"] == claim["conditions"]
    assert claim["scope_hint"].startswith("State the condition: Laboratoriniuose")
    assert [f["code"] for f in out["findings"]] == ["misleading_inference"]
    assert "'visą' generalises" in out["findings"][0]["detail"]
    assert out["counts"] == {"supported": 0, "number_differs": 0, "unsupported": 0, "total": 1, "supported_scope_narrower": 1}
    assert out["summary"] == "1 claim: 0 supported, 1 supported with a narrower scope. Findings: misleading_inference."
    assert out["corrections"][0]["correction"].startswith("Pridėti sąlygą prie „Sugeria 3 kartus")
    kept = claim_check(tools, {"draft": "Laboratoriniuose bandymuose audinys sugėrė 3 kartus daugiau drėgmės nei medvilnė.", "check_scope": True})
    assert kept["claims"][0]["status"] == "supported" and kept["claims"][0]["condition_dropped"] == [] and kept["findings"] == []
    # off by default: the same draft is plain 'supported' and the counts keep their four keys
    plain = claim_check(tools, {"draft": "Sugeria 3 kartus daugiau drėgmės nei medvilnė – sausa visą treniruotę."})
    assert plain["claims"][0]["status"] == "supported" and "conditions" not in plain["claims"][0]
    assert set(plain["counts"]) == {"supported", "number_differs", "unsupported", "total"} and plain["findings"] == []


def test_scope_en_only_period_place_population(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("study.md", "In 2024 only 42% of surveyed customers in Vilnius renewed their subscription within 30 days.\n")
    args = {"sources": ["study.md"], "include_facts": False, "check_scope": True}
    out = claim_check(tools, {"draft": "42% of customers renewed their subscription within 30 days, every single time.", **args})
    claim = out["claims"][0]
    assert claim["status"] == "supported_scope_narrower" and claim["location"] == "line 1"
    assert claim["condition_dropped"] == ["only", "In 2024", "in Vilnius", "surveyed"]
    assert out["findings"][0]["code"] == "misleading_inference" and "'every'" in out["findings"][0]["detail"]
    assert out["corrections"][0]["correction"].startswith('Add the condition to "42% of customers')
    kept = claim_check(tools, {"draft": "In 2024 only 42% of surveyed customers in Vilnius renewed their subscription within 30 days.", **args})
    assert kept["claims"][0]["status"] == "supported" and kept["claims"][0]["condition_dropped"] == []
    bare = claim_check(tools, {"draft": "Revenue grew 12% to 3 million.", **args})
    assert bare["claims"][0]["status"] == "unsupported" and "conditions" not in bare["claims"][0]  # unsupported claims are not judged for scope


# --- quotes (#305, #283, #255) ----------------------------------------------------------

INTERVIEW_LT = "# Interviu: Rūta Jonaitienė, UAB Balansas\n\nAnksčiau mėnesio uždarymas užtrukdavo savaitę.\nUždarymą sutrumpinome nuo 6 iki 2 dienų ir niekas nebedirba savaitgaliais.\n"
STORY_LT = (
    "„Uždarymą sutrumpinome nuo 6 iki 2 dienų ir niekas nebedirba savaitgaliais“, – sako Rūta Jonaitienė, UAB Balansas vadovė.\n"
    "Kita įmonė teigia: „Sutaupome 40 valandų per mėnesį vien dėl automatinio sąskaitų siuntimo“.\n"
    "„Uždarymą sutrumpinome nuo 6 iki 3 dienų ir niekas nebedirba savaitgaliais“, – priduria Jonas Petraitis.\n"
    "Trumpa „citata čia“ nėra tikrinama.\n"
)


def test_quotes_lt_verbatim_altered_missing_and_attribution(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("runs/notes/interviews/uab-balansas.md", INTERVIEW_LT)
    tools.write_file(FACTS_PATH, "Kaina 19 €/mėn. — https://invohub.lt/kainos\n")
    out = claim_check(tools, {"draft": STORY_LT, "quote_sources": ["runs/notes/interviews/uab-balansas.md"]})
    quotes = [c for c in out["claims"] if c["kind"] == "quote"]
    assert [q["text"][:30] for q in quotes] == ["Uždarymą sutrumpinome nuo 6 ik", "Sutaupome 40 valandų per mėnes", "Uždarymą sutrumpinome nuo 6 ik"]
    verbatim, missing, altered = quotes
    assert verbatim["status"] == "supported" and verbatim["source"] == "runs/notes/interviews/uab-balansas.md" and verbatim["location"] == "line 4"
    assert verbatim["passage"] == "Uždarymą sutrumpinome nuo 6 iki 2 dienų ir niekas nebedirba savaitgaliais."
    assert verbatim["attributed_to"] == "Rūta Jonaitienė" and verbatim["attribution"] == "ok"
    assert missing["status"] == "unsupported" and missing["source"] is None and missing["attributed_to"] is None
    assert missing["note"].startswith("No interview source carries this quote")
    assert altered["status"] == "altered" and altered["location"] == "line 4" and altered["attributed_to"] == "Jonas Petraitis"
    assert altered["attribution"] == "attribution_unverified"
    assert [f["code"] for f in out["findings"]] == ["attribution_unverified"] and out["findings"][0]["line"] == 3
    assert out["counts"]["altered"] == 1 and out["counts"]["total"] == len(out["claims"])
    # the number sentence holding the verbatim quote is supported by the interview note itself
    assert _by_text(out, "sako Rūta")["status"] == "supported" and _by_text(out, "sako Rūta")["location"] == "line 4"
    assert any(r["correction"].startswith("Citata „Sutaupome 40 valandų") and "nerasta interviu" in r["correction"] for r in out["corrections"])
    assert any(r["status"] == "altered" and "cituoti pažodžiui" in r["correction"] for r in out["corrections"])
    assert [s for s in out["sources_read"] if s.get("role") == "quotes"][0]["source"] == "runs/notes/interviews/uab-balansas.md"
    assert "1 quote altered" in out["summary"]


def test_quotes_en_attribution_within_the_sentence(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("notes/interview-en.md", "Interview with John Smith, owner of Smith Bakery\n\nWe used to spend every Sunday on invoices; now the books close on Friday afternoon and nobody works the weekend.\n")
    tools.write_file("facts.md", "Price: 19 EUR a month — https://example.com/pricing\n")
    draft = ('As John Smith put it: "Now the books close on Friday afternoon and nobody works the weekend." '
             'Another owner said "we saved forty hours a month thanks to automatic reminders". '
             '"Nobody works the weekend now and the books close on Friday", adds Mary Brown.')
    out = claim_check(tools, {"draft": draft, "quote_sources": ["notes/interview-en.md"], "sources": ["facts.md"], "include_facts": False})
    first, second, third = out["claims"]
    assert first["status"] == "supported" and first["location"] == "line 3" and first["attributed_to"] == "John Smith" and first["attribution"] == "ok"
    assert second["status"] == "unsupported" and second["attributed_to"] is None
    assert third["status"] == "altered" and third["attributed_to"] == "Mary Brown" and third["attribution"] == "attribution_unverified"
    assert third["shared_words"] == ["books", "close", "frida", "nobod", "weeke", "works"]
    assert out["corrections"][0]["correction"].startswith('Quote "we saved forty hours a month')


def test_quotes_without_sources_are_missing_not_guessed(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("facts.md", "Price: 19 EUR a month.\n")
    out = claim_check(tools, {"draft": STORY_LT, "quote_sources": [], "sources": ["facts.md"], "include_facts": False})
    quotes = [c for c in out["claims"] if c["kind"] == "quote"]
    assert len(quotes) == 3 and all(q["status"] == "unsupported" and q["note"] == "no interview sources given (quote_sources is empty)" for q in quotes)
    assert "quote_sources: none given; quotes cannot be verified" in out["missing"]
    unreadable = claim_check(tools, {"draft": STORY_LT, "quote_sources": ["nope.md"], "sources": ["facts.md"], "include_facts": False})
    assert "nope.md: not found" in unreadable["missing"]
    assert all(q["note"] == "no interview source could be read" for q in unreadable["claims"] if q["kind"] == "quote")
    # without quote_sources no quote claims are extracted at all (default behaviour)
    plain = claim_check(tools, {"draft": STORY_LT, "sources": ["facts.md"], "include_facts": False})
    assert all(c["kind"] != "quote" for c in plain["claims"])


# --- reply in a thread (#302) -----------------------------------------------------------

REPLY_LT = ("Gijoje teigiama, kad po 500 GB greitis ribojamas iki 50 Mbps. Plane „Namai 300“ duomenų limito nėra; 300 Mbps greitis galioja tik "
            "šviesolaidžio jungtyje (https://example.lt/namai-300). Rūta Jonaitienė, tinklo paslaugų vadovė")
REPLY_ARGS_LT = {
    "mode": "reply",
    "thread_url": "https://www.reddit.com/r/lithuania/comments/abc123",
    "criticism_points": ["po 500 GB greitis ribojamas iki 50 Mbps", "klientų aptarnavimas neatsako savaitėmis"],
    "facts": [{"text": "Plane Namai 300 duomenų limito nėra", "source": FACTS_PATH}, {"text": "300 Mbps greitis galioja tik šviesolaidžio jungtyje", "source": FACTS_PATH}],
    "spokesperson": {"name": "Rūta Jonaitienė", "role": "tinklo paslaugų vadovė"},
    "reply_text": REPLY_LT,
}


def test_reply_lt_points_claims_and_spokesperson(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file(FACTS_PATH, "Planas Namai 300: 300 Mbps tik šviesolaidžiu, be duomenų limito. Šaltinis: https://example.lt/namai-300\n")
    out = claim_check(tools, REPLY_ARGS_LT)
    assert out["mode"] == "reply" and out["language"] == "lt"
    reply = out["reply"]
    assert reply["thread_url"] == REPLY_ARGS_LT["thread_url"]
    assert reply["points"] == [
        {"text": "po 500 GB greitis ribojamas iki 50 Mbps", "answered": True, "fact": "300 Mbps greitis galioja tik šviesolaidžio jungtyje"},
        {"text": "klientų aptarnavimas neatsako savaitėmis", "answered": False, "fact": None},
    ]
    assert reply["spokesperson_ok"] is True and reply["thread_ok"] is True
    assert [f["code"] for f in out["findings"]] == ["point_unanswered"]
    plan = _by_text(out, "Namai 300“")
    assert plan["status"] == "supported" and plan["source"] == FACTS_PATH and plan["location"] == "line 1"
    assert plan["passage"] == "Planas Namai 300: 300 Mbps tik šviesolaidžiu, be duomenų limito. Šaltinis: https://example.lt/namai-300"
    assert _by_text(out, "Gijoje teigiama")["status"] == "unsupported"
    assert out["missing"] == [] and out["summary"].endswith("Findings: point_unanswered.")


def test_reply_lt_generic_spokesperson_and_other_url(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file(FACTS_PATH, "Planas Namai 300: 300 Mbps tik šviesolaidžiu, be duomenų limito. Šaltinis: https://example.lt/namai-300\n")
    bad = dict(REPLY_ARGS_LT, spokesperson={"name": "Komanda", "role": ""}, reply_text=REPLY_LT + " Tęskime pokalbį čia: https://facebook.com/telia")
    out = claim_check(tools, bad)
    codes = [f["code"] for f in out["findings"]]
    assert codes == ["point_unanswered", "no_spokesperson", "thread_mismatch"]
    assert out["reply"]["spokesperson_ok"] is False and out["reply"]["thread_ok"] is False
    assert out["findings"][2]["urls"] == ["https://facebook.com/telia"]
    placeholder = dict(REPLY_ARGS_LT, spokesperson={"name": "[Vardas, Pareigos]", "role": "Pareigos"})
    assert "no_spokesperson" in [f["code"] for f in claim_check(tools, placeholder)["findings"]]
    absent = dict(REPLY_ARGS_LT, spokesperson={"name": "Jonas Petraitis", "role": "vadovas"})
    out = claim_check(tools, absent)
    assert any(f["code"] == "no_spokesperson" and "Jonas Petraitis" in f["detail"] for f in out["findings"])


def test_reply_en_and_missing_inputs(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("facts-en.md", "Plan Home 300: 300 Mbps on fibre only, no data cap, no throttling after 500 GB. Source: https://example.com/home-300\n")
    out = claim_check(tools, {
        "mode": "reply", "thread_url": "https://news.ycombinator.com/item?id=1", "include_facts": False,
        "criticism_points": ["they throttle to 50 Mbps after 500 GB"],
        "facts": [{"text": "Home 300 is not throttled after 500 GB: there is no data cap", "source": "facts-en.md"}],
        "spokesperson": {"name": "Jane Doe", "role": "Head of Network"},
        "reply_text": "The thread says we throttle to 50 Mbps after 500 GB. Home 300 is not throttled after 500 GB: there is no data cap, and 300 Mbps applies on fibre only (https://example.com/home-300). — Jane Doe, Head of Network",
    })
    assert out["language"] == "en"
    assert out["reply"]["points"] == [{"text": "they throttle to 50 Mbps after 500 GB", "answered": True, "fact": "Home 300 is not throttled after 500 GB: there is no data cap"}]
    assert out["reply"]["thread_ok"] is True  # the source's own URL is a citation, not a new place to continue
    assert out["findings"] == []
    fibre = _by_text(out, "fibre only")
    assert fibre["status"] == "supported" and fibre["source"] == "facts-en.md" and fibre["location"] == "line 1"
    empty = claim_check(tools, {"mode": "reply", "reply_text": "We answer: there is no cap.", "include_facts": False})
    assert empty["missing"] == [
        "sources: none readable (give sources or write runs/notes/facts.md)",
        "thread_url: not given (the reply cannot be tied to the thread)",
        "criticism_points: none given (nothing to answer point by point)",
        "facts: none given (no fact can answer a point)",
    ]
    assert [f["code"] for f in empty["findings"]] == ["no_spokesperson", "no_sources"]
    with pytest.raises(ToolError, match="mode must be one of"):
        claim_check(tools, {"mode": "dance", "draft": "x 5"})


# --- price anchor (#182) ---------------------------------------------------------------


def test_price_anchor_lt_reference_found_in_evidence(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("pricing-history.md", "# Kainų istorija\n\n2026-01-15: Python kursas — 249 € (https://web.archive.org/web/2026-01/kursai.lt/python)\n2026-03-01: Python kursas — 149 €\n")
    out = claim_check(tools, {"mode": "price_anchor", "offer_price": "149 €", "reference_price": "€249", "reference_kind": "previous_price",
                              "evidence": "pricing-history.md", "charged_identical": True, "draft": "Python kursas dabar 149 € (buvo 249 €).", "include_facts": False})
    anchor = out["anchor"]
    assert anchor["offer_price"] == 149 and anchor["reference_price"] == 249 and anchor["reference_kind"] == "previous_price"
    assert anchor["reference_found"] is True and anchor["source"] == "pricing-history.md" and anchor["location"] == "line 3"
    assert anchor["passage"] == "2026-01-15: Python kursas — 249 € (https://web.archive.org/web/2026-01/kursai.lt/python)"
    assert anchor["kind_words"] == ["buvo"] and anchor["implied_kind"] == "previous_price"
    assert out["findings"] == [] and out["missing"] == []
    lied = claim_check(tools, {"mode": "price_anchor", "offer_price": 149, "reference_price": 249, "reference_kind": "previous_price", "evidence": "pricing-history.md",
                               "charged_identical": False, "draft": "Python kursas dabar 149 € — pas konkurentus 249 €.", "include_facts": False})
    assert [f["code"] for f in lied["findings"]] == ["anchor_misdescribed", "anchor_never_charged"]
    assert "'konkurentus' says competitor" in lied["findings"][0]["detail"]


def test_price_anchor_en_unsupported_and_missing(tmp_path):
    tools = WorkspaceTools(tmp_path)
    page = "<html><body><h1>Price history</h1><table><tr><th>Date</th><th>Price</th></tr>\n<tr><td>2026-01</td><td>€299</td></tr></table></body></html>"
    url = "https://web.archive.org/web/2026-01/courses.example/python"
    base = {"mode": "price_anchor", "offer_price": "$149", "reference_price": 299, "reference_kind": "previous_price", "evidence": url, "charged_identical": True, "include_facts": False}
    out = claim_check(tools, {**base, "draft": "Python course now €149, was €299."}, fetch=lambda u: page)
    anchor = out["anchor"]
    assert anchor["reference_found"] is True and anchor["source"] == url
    row = page.index("<tr><td>2026-01")
    assert anchor["location"] == f"chars {row}–{row + len('Date: 2026-01 · Price: €299')}" and anchor["passage"] == "Date: 2026-01 · Price: €299"
    assert anchor["implied_kind"] == "previous_price" and out["findings"] == []
    rrp = claim_check(tools, {**base, "reference_price": 349, "draft": "Python course now €149, RRP €349 — cheaper than at competitors."}, fetch=lambda u: page)
    assert [f["code"] for f in rrp["findings"]] == ["anchor_unsupported", "anchor_misdescribed"]
    assert rrp["findings"][0]["detail"] == f"Reference price 349 is not in the evidence: {url} never shows 349."
    assert rrp["anchor"]["implied_kind"] == ["rrp", "competitor"]
    below = claim_check(tools, {**base, "offer_price": 299, "reference_price": 299}, fetch=lambda u: page)
    assert "anchor_below_offer" in [f["code"] for f in below["findings"]]
    unfetched = claim_check(tools, {**base, "draft": "Now €149, was €299."})
    assert unfetched["anchor"]["reference_found"] is False and unfetched["findings"][0]["detail"].endswith("the evidence could not be read.")
    bare = claim_check(tools, {"mode": "price_anchor", "include_facts": False})
    assert bare["missing"] == [
        "sources: none readable (give sources or write runs/notes/facts.md)", "draft has no text", "offer_price: not given", "reference_price: not given",
        "reference_kind: not given", "evidence: not given", "charged_identical: not given (was the reference price ever charged for the identical offer?)",
    ]
    assert bare["anchor"]["reference_found"] is False and bare["claims"] == []
    with pytest.raises(ToolError, match="reference_kind must be one of"):
        claim_check(tools, {"mode": "price_anchor", "reference_kind": "wishful", "include_facts": False})


# --- credentials, backers, fact sheet, corrections (#307, #284, #289, #293, #283) --------------------


def test_credential_lt_issuer_backer_party_and_fact_sheet(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file(FACTS_PATH, "APG Media yra Google Premier Partner nuo 2021 m. — https://partnersdirectory.withgoogle.com\nMūsų agentūra yra sertifikuota — https://apgmedia.lt/apie\nAptarnaujame 180 klientų Lietuvoje — https://apgmedia.lt/apie\n")
    draft = "Esame Google Premier Partner agentūra.\nEsame Meta Business Partner agentūra.\nEsame sertifikuota agentūra.\nAptarnaujame 200 klientų Lietuvoje.\n"
    out = claim_check(tools, {"draft": draft, "own_domains": ["https://apgmedia.lt"]})
    google = _by_text(out, "Google")
    assert google["status"] == "supported" and google["issuer"] == "Google Premier" and google["backer"] == "industry_body"
    assert google["source_domain"] == "partnersdirectory.withgoogle.com" and google["party"] == "third_party" and google["attributed"] is False
    meta = _by_text(out, "Meta")
    assert meta["status"] == "unsupported" and meta["issuer"] == "Meta Business" and "backer" not in meta
    cert = _by_text(out, "sertifikuota")
    assert cert["status"] == "supported" and cert["issuer"] is None and cert["backer"] == "self" and cert["party"] == "own"
    assert [f["code"] for f in out["findings"]] == ["attribution_missing", "attribution_missing", "attribution_missing"]
    assert out["findings"][0]["line"] == 1 and "partnersdirectory.withgoogle.com (third_party)" in out["findings"][0]["detail"]
    assert [r["claim"] for r in out["fact_sheet"]] == ["Esame Google Premier Partner agentūra.", "Esame sertifikuota agentūra."]
    assert out["fact_sheet"][0]["backer"] == "industry_body" and out["fact_sheet"][0]["location"] == "line 1"
    assert out["fact_sheet_table"].splitlines()[2] == "| 1 | Esame Google Premier Partner agentūra. | — | runs/notes/facts.md | line 1 | APG Media yra Google Premier Partner nuo 2021 m. — https://partnersdirectory.withgoogle.com |"
    corrections = {r["status"]: r["correction"] for r in out["corrections"]}
    assert corrections["number_differs"] == "Ne 200 klientų, o 180 klientų — šaltinis: runs/notes/facts.md (line 3): „Aptarnaujame 180 klientų Lietuvoje — https://apgmedia.lt/apie“"
    assert corrections["unsupported"] == "„Esame Meta Business Partner agentūra.“ — šaltinio nėra: įrašyti faktą su URL į runs/notes/facts.md arba išbraukti."


def test_credential_en_issuer_must_be_named_by_the_source(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("about.md", "We are a certified accounting agency with 12 staff.\nRated 4.8 out of 5 by 120 clients on Trustpilot — https://www.trustpilot.com/review/example.com\nThe Lithuanian Chamber of Commerce awarded the agency its export prize in 2025.\nTrustpilot gave the agency its Trusted Partner award in 2025 — https://www.trustpilot.com/review/example.com\n")
    out = claim_check(tools, {"draft": "We are an ISO 9001 certified accounting agency.\nWe are a certified accounting agency.\nThe Chamber of Commerce awarded us its export prize.\nClients rate us 4.8 out of 5 on Trustpilot.", "sources": ["about.md"], "include_facts": False})
    iso = _by_text(out, "ISO 9001")
    assert iso["status"] == "unsupported" and iso["issuer"] == "ISO 9001"
    assert iso["nearest"]["location"] == "line 1" and iso["note"].endswith("but does not name the issuer ISO 9001.")
    plain = _by_text(out, "We are a certified")
    assert plain["status"] == "supported" and plain["issuer"] is None and plain["backer"] == "self" and plain["location"] == "line 1"
    chamber = _by_text(out, "Chamber")
    assert chamber["status"] == "supported" and chamber["issuer"] == "Chamber Commerce" and chamber["backer"] == "industry_body" and chamber["location"] == "line 3"
    rating = _by_text(out, "4.8")
    assert rating["status"] == "supported" and rating["location"] == "line 2"
    assert [r["claim"] for r in out["fact_sheet"]] == [plain["text"], chamber["text"], rating["text"]]
    assert out["fact_sheet"][2]["value"] == "4.8, 5"
    assert out["corrections"] == [{"line": 1, "status": "unsupported", "claim": iso["text"], "source": None, "location": None,
                                   "correction": '"We are an ISO 9001 certified accounting agency." — no source: add the fact with its URL to runs/notes/facts.md or remove it.'}]
    backer = claim_check(tools, {"draft": "The agency holds the Trustpilot Trusted Partner award.", "sources": ["about.md"], "include_facts": False})
    assert backer["claims"][0]["status"] == "supported" and backer["claims"][0]["location"] == "line 4"
    assert backer["claims"][0]["issuer"] == "Trustpilot Trusted" and backer["claims"][0]["backer"] == "independent_review"


def test_rumor_lt_pairs_the_figure_with_the_sourced_correction(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file(FACTS_PATH, "Mokestis nuo 2027-01-01: 390 €/mėn. https://perlas.lt/kainos\n")
    out = claim_check(tools, {"draft": "Nuo sausio mokestis yra 390 €/mėn., ne 450 € — oficiali kainų lentelė: https://perlas.lt/kainos", "rumor": "nuo sausio mokestis bus 450 €"})
    assert out["rumor_corrections"] == [{
        "subject": "mokes sausi", "rumor_value": "450", "fact_value": "390", "status": "supported", "source": FACTS_PATH, "location": "line 1",
        "source_passage": "Mokestis nuo 2027-01-01: 390 €/mėn. https://perlas.lt/kainos", "source_url": "https://perlas.lt/kainos", "source_cited": True, "draft_line": 1,
    }]
    uncited = claim_check(tools, {"draft": "Nuo sausio mokestis yra 390 €/mėn., ne 450 €.", "rumor": "nuo sausio mokestis bus 450 €"})
    assert uncited["rumor_corrections"][0]["source_cited"] is False and uncited["rumor_corrections"][0]["status"] == "supported"
    absent = claim_check(tools, {"draft": "Darželis veikia nuo 7 val.", "rumor": "nuo sausio mokestis bus 450 €"})
    assert absent["rumor_corrections"][0]["status"] == "missing_in_draft" and absent["rumor_corrections"][0]["fact_value"] is None


def test_rumor_en_unsourced_correction_stays_unsupported(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("facts.md", "Opening hours: 8:00–20:00 every day.\n")
    out = claim_check(tools, {"draft": "The monthly fee stays at €390, not €450 as the group claims.", "rumor": "the monthly fee rises to €450 in January", "sources": ["facts.md"], "include_facts": False})
    row = out["rumor_corrections"][0]
    assert row["rumor_value"] == "450" and row["fact_value"] == "390" and row["status"] == "unsupported" and row["source"] is None
    assert row["subject"] == "month"


# --- HTML tables and alt text (#236, #255) ------------------------------------------------


def test_html_table_rows_and_alt_text_are_passages_lt(tmp_path):
    tools = WorkspaceTools(tmp_path)
    html = ("<html><body>\n<h1>Rezultatai</h1>\n<table>\n<tr><th>Rodiklis</th><th>Reikšmė</th></tr>\n<tr><td>Klientai Lietuvoje</td><td>180</td></tr>\n"
            "<tr><td>Kampanijų konversija</td><td>2,5 %</td></tr>\n</table>\n<img src=\"chart.png\" alt=\"Grafikas: pajamos išaugo 35 % per 2025 metus\">\n<p>Pabaiga.</p></body></html>")
    tools.write_file("report.html", html)
    out = claim_check(tools, {"draft": "Aptarnaujame 180 klientų Lietuvoje. Pajamos išaugo 35 % per 2025 metus. Konversija siekia 2,5 % kampanijose.", "sources": ["report.html"], "include_facts": False})
    assert out["sources_read"] == [{"source": "report.html", "passages": 6, "chars": len(html)}]
    clients, revenue, conversion = out["claims"]
    assert clients["status"] == "supported" and clients["location"] == "line 5" and clients["passage"] == "Rodiklis: Klientai Lietuvoje · Reikšmė: 180"
    assert revenue["status"] == "supported" and revenue["location"] == "line 8" and revenue["passage"] == "Grafikas: pajamos išaugo 35 % per 2025 metus"
    assert conversion["status"] == "supported" and conversion["location"] == "line 6" and conversion["passage"] == "Rodiklis: Kampanijų konversija · Reikšmė: 2,5 %"


def test_html_table_row_from_a_fetched_page_en(tmp_path):
    tools = WorkspaceTools(tmp_path)
    page = "<html><body><table><tr><th>Metric</th><th>Value</th></tr><tr><td>Clients in Lithuania</td><td>180</td></tr><tr><td>Staff</td><td>12</td></tr></table></body></html>"
    out = claim_check(tools, {"draft": "We serve 180 clients in Lithuania. We employ 12 staff.", "sources": ["https://x.example/report"], "include_facts": False}, fetch=lambda u: page)
    clients, staff = out["claims"]
    row = page.index("<tr><td>Clients")
    assert clients["status"] == "supported" and clients["passage"] == "Metric: Clients in Lithuania · Value: 180"
    assert clients["location"] == f"chars {row}–{row + len(clients['passage'])}"
    assert staff["status"] == "unsupported"  # 'Staff: 12' shares one word only; a bare cell is not a passage of its own
    headerless = "<table><tr><td>Clients in Lithuania</td><td>180</td></tr></table>"
    out = claim_check(tools, {"draft": "We serve 180 clients in Lithuania.", "sources": ["https://x.example/r2"], "include_facts": False}, fetch=lambda u: headerless)
    assert out["claims"][0]["passage"] == "Clients in Lithuania · 180" and out["claims"][0]["status"] == "supported"


# --- consistency, length, trends (#299, #293, #490) ------------------------------------------


def test_self_consistency_and_length_lt(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("facts.md", "Kaina: 490 € (https://site.lt/kainos)\n")
    draft = "Audito rezultatus pristatome per 14 dienų.\nAudito rezultatus gausite per 21 dienų.\nKaina 490 €.\n"
    out = claim_check(tools, {"draft": draft, "self_consistency": True, "max_minutes": 0.05, "sources": ["facts.md"], "include_facts": False})
    assert out["conflicts"] == [{"subject": "dienų", "status": "conflict", "values": [
        {"value": "14", "text": "Audito rezultatus pristatome per 14 dienų.", "line": 1}, {"value": "21", "text": "Audito rezultatus gausite per 21 dienų.", "line": 2}]}]
    assert out["length"] == {"words": 11, "wpm": 130.0, "minutes": 0.08, "max_minutes": 0.05, "over_by_words": 5}
    assert [f["code"] for f in out["findings"]] == ["conflict", "too_long"]
    assert out["findings"][0]["detail"] == "dienų: 14 (line 1) vs 21 (line 2)."
    assert _by_text(out, "pristatome")["operational"] is True and "operational" not in _by_text(out, "Kaina")
    fine = claim_check(tools, {"draft": "Kaina 490 €.", "self_consistency": True, "max_minutes": 3, "sources": ["facts.md"], "include_facts": False})
    assert fine["conflicts"] == [] and fine["length"]["over_by_words"] == 0 and fine["findings"] == []
    assert "conflicts" not in claim_check(tools, {"draft": draft, "sources": ["facts.md"], "include_facts": False})


def test_self_consistency_en_and_trends_index(tmp_path):
    tools = WorkspaceTools(tmp_path)
    tools.write_file("facts.md", "'heat pump' gets 100 searches a month in Lithuania — https://trends.google.com/trends/explore?geo=LT&q=heat%20pump\n")
    out = claim_check(tools, {"draft": "The audit costs €490 for a Shopify store. A Shopify store audit is €590 including the report.\n'heat pump' gets 100 searches a month in Lithuania.",
                              "self_consistency": True, "sources": ["facts.md"], "include_facts": False})
    assert out["conflicts"][0]["subject"] == "audit shopi store" and [v["value"] for v in out["conflicts"][0]["values"]] == ["490", "590"]
    trends = _by_text(out, "heat pump")
    assert trends["status"] == "supported" and trends["trends"] == ["absolute_count_from_index"]
    finding = next(f for f in out["findings"] if f["code"] == "trends_relative")
    assert finding["reasons"] == ["absolute_count_from_index"] and finding["line"] == 2
    tools.write_file("facts2.md", "heat pump index peaked at 100 — https://trends.google.com/trends/explore?q=heat\n")
    vague = claim_check(tools, {"draft": "The heat pump index peaked at 100.", "sources": ["facts2.md"], "include_facts": False})
    assert vague["claims"][0]["trends"] == ["missing_window", "missing_geo"]


def test_default_output_is_additive_only(ws):
    out = claim_check(ws, {"draft": DRAFT})
    assert out["mode"] == "check" and out["findings"] == []
    assert set(out["counts"]) == {"supported", "number_differs", "unsupported", "total"}
    assert len(out["fact_sheet"]) == out["counts"]["supported"] == 3
    assert {r["status"] for r in out["corrections"]} == {"number_differs", "unsupported"}
    assert out["corrections"][0]["correction"].startswith("Ne 200+ klientų, o 180 klientų — šaltinis: runs/notes/facts.md (line 2)")
    assert "scope" not in out["summary"] and "Findings" not in out["summary"]
    for key in ("reply", "anchor", "rumor_corrections", "conflicts", "length"):
        assert key not in out
    assert all(k not in c for c in out["claims"] for k in ("party", "source_domain", "conditions", "attribution"))



def test_the_tool_spec_advertises_the_modes_and_options():
    from rocky.tools import worker_tools

    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "claim_check")
    props = spec["function"]["parameters"]["properties"]
    assert props["mode"]["enum"] == ["check", "reply", "price_anchor"]
    for key in ("check_scope", "quote_sources", "own_domains", "thread_url", "reference_price", "self_consistency"):
        assert key in props, key
