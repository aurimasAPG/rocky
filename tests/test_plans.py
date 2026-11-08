import json

import pytest

from rocky.plans import plan_check, plan_check_tool
from rocky.tools import ToolError, WorkspaceTools


def _codes(out, where=None):
    return sorted(f["code"] for f in out["findings"] if where is None or f["where"] == where or str(f["where"]).startswith(str(where)))


def _levels(out):
    return out["counts"]["by_level"]


@pytest.fixture
def ws(tmp_path):
    tools = WorkspaceTools(tmp_path)
    (tmp_path / "runs" / "notes").mkdir(parents=True)
    (tmp_path / "runs" / "notes" / "facts.md").write_text(
        "Kilo: 12 000 vartotojų (https://kilo.lt/about)\n"
        "VU SA 2026 planas: studentų psichikos sveikatos savaitė (https://vusa.lt/veikla)\n"
        "B1.lt shipped Peppol connector 2026-03 (https://b1.lt/changelog)\n"
        "Pigu job post: e-commerce platform engineer (https://pigu.lt/karjera)\n"
        "Case: https://example.lt/case-uab-medis\n",
        encoding="utf-8",
    )
    return tools


class FakeLLM:
    """Scripted cold reader: answers by matching a key in the user message, else the default."""

    def __init__(self, scripted: dict[str, str], default: str = "nežinau"):
        self.scripted = scripted
        self.default = default
        self.calls: list[dict] = []

    def complete(self, *, model, messages, **kwargs):
        self.calls.append({"model": model, "messages": messages})
        user = messages[-1]["content"]
        if isinstance(user, list):
            user = " ".join(p.get("text", "") for p in user if isinstance(p, dict))
        text = next((answer for key, answer in self.scripted.items() if key in user), self.default)
        return type("Completion", (), {"content": text})()


# --- dispatch ---------------------------------------------------------------------------------------


def test_unknown_kind_and_empty_inputs_raise(ws):
    with pytest.raises(ToolError, match="kind must be one of"):
        plan_check(ws, {"kind": "budget"})
    for kind, empty in [("content_calendar", {"items": []}), ("pr_board", {}), ("demo_board", {}), ("response_board", {"tiers": []}),
                        ("partner_board", {}), ("qualification", {"conditions": []}), ("evidence_log", {"entries": []}), ("community_rules", {"text": ""}),
                        ("retell", {"statements": []}), ("ad_pretest", {"ads": []})]:
        with pytest.raises(ToolError):
            plan_check(ws, {"kind": kind, **empty})


def test_tool_wrapper_returns_json_with_the_house_fields(ws):
    text = plan_check_tool(ws, {"kind": "demo_board", "audience": "accountants", "claim": "40 invoices sent in 9 minutes", "situation": "Rasa has 40 unsent invoices",
                                "result": "all 40 invoices show status Sent", "steps": [{"actor": "visitor", "action": "Rasa selects the 40 invoices and clicks Send"}]})
    out = json.loads(text)
    assert out["kind"] == "demo_board"
    assert set(out) >= {"findings", "missing", "summary", "clean", "counts"}


# --- content_calendar -------------------------------------------------------------------------------


CLEAN_PLAN = [
    {"title": "Kaip pasiruošti e. sąskaitų reikalavimams 2027", "publish_date": "2027-01-05", "channel": "blog", "format": "article", "owner": "Rūta", "stage": "awareness", "source": "research.md#L12", "lifespan": "evergreen", "review_date": "2028-01-05", "hours": 4},
    {"title": "Algapay prieš Rivile: kainų palyginimas", "publish_date": "2027-01-12", "channel": "blog", "format": "comparison", "owner": "Tomas", "stage": "evaluation", "source": "research.md#L20", "lifespan": "evergreen", "review_date": "2027-07-12", "hours": 6},
    {"title": "Webinaras: migracija iš Finvaldos", "publish_date": "2027-01-19", "channel": "linkedin", "format": "webinar", "owner": "Rūta", "stage": "evaluation", "source": "voc.md#L3", "lifespan": "timely", "event_date": "2027-01-19", "hours": 5},
]


def test_content_calendar_clean_lithuanian_plan_is_silent(ws):
    out = plan_check(ws, {"kind": "content_calendar", "items": CLEAN_PLAN, "launch_date": "2027-02-15", "competitors": ["Rivile", "Finvalda"],
                          "capacity": {"items_per_week": 2, "hours_per_week": 10}, "audience_evidence": [{"format": "article", "evidence": "voc.md"}, {"format": "comparison"}, {"format": "webinar"}],
                          "inventory": [{"title": "Sodra ataskaitos 2026", "updated": "2026-10-01", "url": "https://algapay.lt/blog/sodra"}], "today": "2026-11-08"})
    assert out["language"] == "lt"
    assert out["clean"] is True, out["findings"]
    assert _levels(out) == {"block": 0, "warn": 0, "info": 0}
    assert out["counts_by_stage"]["evaluation"] == 2 and out["counts_by_lifespan"] == {"evergreen": 2, "timely": 1}
    assert out["weeks_empty"] == [] and out["launch_window"] == [] and out["missing"] == []
    assert out["maintain"] == ["item-2", "item-1"]


def test_content_calendar_fires_on_gaps_owners_clashes_capacity_and_overlap(ws):
    items = [
        {"title": "Launch post", "publish_date": "2027-01-14", "channel": "LinkedIn", "owner": "TBD", "format": "post", "stage": "awareness"},
        {"title": "Launch teaser", "publish_date": "2027-01-14", "channel": "linkedin", "owner": "Eglė", "format": "case_study", "stage": "awareness"},
        {"title": "Launch reminder", "publish_date": "2027-01-16", "channel": "newsletter", "owner": "Eglė", "format": "newsletter", "stage": "awareness"},
        {"title": "How to pick a co-working space", "publish_date": "2027-02-10", "channel": "blog", "format": "article", "owner": "Eglė", "stage": "awareness", "lifespan": "evergreen"},
        {"title": "Open day 2027", "publish_date": "2027-02-12", "channel": "blog", "format": "article", "owner": "Eglė", "lifespan": "timely"},
        {"title": "No date yet", "channel": "blog", "format": "short_video", "owner": "Eglė", "stage": "awareness"},
    ]
    out = plan_check(ws, {"kind": "content_calendar", "items": items, "launch_date": "2027-01-15", "capacity": {"items_per_week": 2},
                          "audience_evidence": [{"format": "article"}, {"format": "post"}, {"format": "newsletter"}, {"format": "case_study"}],
                          "inventory": [{"title": "How to pick a co-working space in Vilnius", "updated": "2025-06-01"}], "today": "2026-11-08"})
    codes = _codes(out)
    for code in ("owner_missing", "date_missing", "channel_clash", "week_gap", "stage_gap", "format_fit", "format_unsupported", "launch_window_crowded", "over_capacity", "review_missing", "expiry_missing", "goal_missing", "stale", "overlap"):
        assert code in codes, code
    assert out["clean"] is False
    assert "2027-W04" in out["weeks_empty"]
    assert out["launch_window"] == ["item-1", "item-2", "item-3"]
    assert out["items"][3]["review_date_proposed"] == "2028-02-05"
    assert next(f for f in out["findings"] if f["code"] == "stage_gap")["note"].startswith("No content for stages")


def test_content_calendar_rejects_bad_stage_and_date(ws):
    with pytest.raises(ToolError, match="stage must be one of"):
        plan_check(ws, {"kind": "content_calendar", "items": [{"title": "x", "stage": "middle", "owner": "a", "publish_date": "2027-01-01"}]})
    with pytest.raises(ToolError, match="ISO date"):
        plan_check(ws, {"kind": "content_calendar", "items": [{"title": "x", "owner": "a", "publish_date": "January 5"}]})


# --- pr_board ----------------------------------------------------------------------------------------


RELEASE_LT = """Skelbti nuo 2026-11-20

Kilo pristato automatinį taupymą: vidutinis Lietuvos namų ūkis sutaupo 23 € per mėnesį apvalindamas pirkinius.

„Pirmą kartą Lietuvoje skelbiame 12 000 vartotojų taupymo elgsenos duomenis“, – sako Kilo vadovė Rasa Jonaitienė.

Apie Kilo
Kilo yra Lietuvos fintech įmonė, kurianti taupymo programėlę. Ją naudoja 12 000 žmonių.

Kontaktai: spauda@kilo.lt, +370 600 12345
"""


def test_pr_board_clean_press_release_with_fact_desk_and_readers(ws):
    out = plan_check(ws, {"kind": "pr_board", "doc_kind": "press_release", "draft": RELEASE_LT, "company": "Kilo",
                          "facts": [{"figure": "23 €", "source": "Kilo data 2026-10"}, {"figure": "12 000", "source": "https://kilo.lt/about"}, {"figure": "2026-11-20", "source": "launch plan"}, {"figure": "+370 600 12345", "source": "press office"}],
                          "beneficiaries": [{"who": "mažas pajamas gaunantys namų ūkiai", "gain": "23 € per mėnesį be pastangų"}, {"who": "Kilo", "gain": "daugiau vartotojų"}],
                          "publications": ["15min", "Verslo žinios"],
                          "readers": [{"publication": "15min", "reason": "Parodo, kiek vidutinis namų ūkis sutaupo per mėnesį apvalindamas pirkinius: 23 €"}, {"publication": "Verslo žinios", "reason": "Pirmas Lietuvos fintech, skelbiantis 12 000 vartotojų taupymo duomenis"}]})
    assert out["language"] == "lt"
    assert out["clean"] is True, out["findings"]
    parts = out["parts"]
    assert parts["quote"]["name"] == "Rasa Jonaitienė" and parts["quote"]["role"].lower().startswith("vadov")
    assert parts["contact"] == "spauda@kilo.lt" and "2026-11-20" in parts["date"]
    assert out["fact_desk"]["n_sourced"] == out["fact_desk"]["n_total"] == 2
    assert {f["figure"] for f in out["fact_desk"]["figures"]} == {"23 €", "12 000"}


def test_pr_board_fires_on_missing_parts_sponsor_only_company_reason_and_unsourced_figures(ws):
    draft = "Kilo launches a great new savings feature on November 20. It already has 12 000 users and 45 new plans.\n\nNo quote. No contact."
    out = plan_check(ws, {"kind": "pr_board", "doc_kind": "press_release", "draft": draft, "company": "Kilo", "facts": [{"figure": "12 000"}],
                          "beneficiaries": [{"who": "Kilo", "gain": "more users"}], "publications": ["15min", "Verslo žinios"],
                          "readers": [{"publication": "15min", "reason": "Kilo launches a great new feature"}]})
    codes = _codes(out)
    for code in ("quote_missing", "boilerplate_missing", "contact_missing", "sponsor_only", "reason_is_company", "reader_reason_missing", "figure_unsourced"):
        assert code in codes, code
    assert "date_missing" not in codes and out["parts"]["date"].startswith("Kilo launches")
    assert out["clean"] is False
    unsourced = [f for f in out["findings"] if f["code"] == "figure_unsourced"]
    assert {f["value"] for f in unsourced} == {"12 000", "45"}


def test_pr_board_claim_check_sponsorship_announcement_and_byline(ws):
    sponsorship = plan_check(ws, {"kind": "pr_board", "doc_kind": "sponsorship", "draft": "Logotipo dizaino konkursas studentams.", "sponsor": "Auga",
                                  "partners": [{"name": "VU SA", "own_goal": "", "source": ""}], "beneficiaries": [{"who": "studentai", "gain": "parama"}]})
    assert _codes(sponsorship) == ["partner_goal_unsourced", "sponsor_hidden"]
    fixed = plan_check(ws, {"kind": "pr_board", "doc_kind": "sponsorship", "draft": "Projektą remia Auga. Psichikos sveikatos savaitė studentams.", "sponsor": "Auga",
                            "partners": [{"name": "VU SA", "own_goal": "studentų psichikos sveikatos savaitė", "source": "https://vusa.lt/veikla"}], "beneficiaries": [{"who": "studentai", "gain": "parama"}], "facts": []})
    assert fixed["clean"] is True and fixed["sources_read"] == ["runs/notes/facts.md"]

    announcement = plan_check(ws, {"kind": "pr_board", "doc_kind": "announcement", "draft": "Nuo 2027-01-01 pakartotinis pristatymas kainuos 1,90 €.", "affected": [], "facts": [{"figure": "1,90", "source": "pricing memo"}]})
    assert "affected_missing" in _codes(announcement)
    announcement2 = plan_check(ws, {"kind": "pr_board", "doc_kind": "announcement", "draft": "Nuo 2027-01-01 pakartotinis pristatymas kainuos 1,90 €.", "facts": [{"figure": "1,90", "source": "pricing memo"}],
                                    "affected": [{"group": "recipients absent at first attempt", "cost": "€1.90 per repeat", "their_words": ""}]})
    assert _codes(announcement2) == ["their_words_missing"]

    pitch = ("Gerb. redaktore, VŽ skaitytojams – smulkioms įmonėms – nuo 2027 m. liepos e. sąskaitos taps privalomos.\n\n"
             "Straipsnio tekstas apie taisykles.\n\nAutorius Tomas Kairys yra UAB Invohub technologijų vadovas; redakcija gali kreiptis tomas@invohub.lt")
    byline = plan_check(ws, {"kind": "pr_board", "doc_kind": "story", "draft": pitch, "byline": {"author_name": "Tomas Kairys", "affiliation_terms": ["Invohub"], "publication": "Verslo žinios"}, "facts": []})
    assert byline["byline"]["author_named"] and byline["byline"]["affiliation_disclosed"] and byline["byline"]["contact_found"] and byline["byline"]["reader_value_sentence_found"]
    assert _codes(byline) == ["publication_unnamed"] and byline["clean"] is True
    hidden = plan_check(ws, {"kind": "pr_board", "doc_kind": "story", "draft": pitch.replace("UAB Invohub technologijų vadovas", "technologijų vadovas"), "byline": {"author_name": "Tomas Kairys", "affiliation_terms": ["Invohub"]}, "facts": []})
    assert "disclosure_missing" in _codes(hidden)

    src = ws.root / "runs" / "notes" / "research.md"
    src.write_text("Kilo has 12 000 users according to https://kilo.lt/about.\n", encoding="utf-8")
    checked = plan_check(ws, {"kind": "pr_board", "doc_kind": "story", "draft": "Kilo has 12 000 users. It handles 450 000 transactions a month.", "sources": ["runs/notes/research.md"], "facts": []})
    assert "claim_unsupported" in _codes(checked) and checked["claim_check"]["counts"]["supported"] >= 1


def test_pr_board_message_house(ws):
    house = {"core": "Sąskaitos išsiųstos per 30 sekundžių, be buhalterio.",
             "pillars": [{"text": "Greitis", "evidence": {"text": "vidutiniškai 28 s nuo atidarymo iki išsiuntimo", "source": "runs/notes/facts.md"}},
                         {"text": "Be buhalterio", "evidence": {"text": "63 % vartotojų neturi buhalterio", "source": "https://invohub.lt/survey"}}],
             "variants": [{"channel": "linkedin", "text": "Sąskaitos išsiųstos per 30 sekundžių ir be buhalterio – išbandyk."}, {"channel": "sms", "text": "Sąskaita per 30 sekundžių be buhalterio: invohub.lt"}]}
    out = plan_check(ws, {"kind": "pr_board", "message_house": house})
    assert out["clean"] is True, out["findings"]
    assert all(v["core_kept"] >= 0.6 for v in out["message_house"]["variants"])
    bad = plan_check(ws, {"kind": "pr_board", "message_house": {"core": "Greita. Patogu. Pigu.", "pillars": [{"text": "a"}, {"text": "b", "evidence": "x"}, {"text": "c", "evidence": {"text": "y", "source": "s"}}, {"text": "d", "evidence": {"text": "z", "source": "s"}}],
                                                                "variants": [{"channel": "fb", "text": "Išbandyk dabar nemokamai."}, {"channel": "ig", "text": "Išbandyk dabar nemokamai."}]}})
    for code in ("core_multiple", "pillars_too_many", "pillar_no_evidence", "pillar_evidence_unsourced", "variant_core_lost", "variant_duplicate"):
        assert code in _codes(bad), code


# --- demo_board ----------------------------------------------------------------------------------------


DEMO = {
    "kind": "demo_board", "audience": "small accounting firms", "claim": "40 client invoices sent and tracked in 9 minutes", "session_minutes": 15, "first_product_minute": 2,
    "situation": "Rasa has 40 unsent client invoices on the 1st", "result": "all 40 invoices show status Sent and a payment dashboard is visible",
    "judged_by": "the Sent count and the clock", "takeaway": "a PDF of the dashboard with her own 40 invoices", "materials": ["demo account with 40 draft invoices", "projector"],
    "steps": [{"minute": 2, "actor": "visitor", "action": "Rasa opens her client list with 40 unsent invoices", "moves_toward_result": "starts at the pile"},
              {"minute": 6, "actor": "visitor", "action": "Rasa selects all 40 and clicks Send", "shows_feature": "bulk send", "moves_toward_result": "sends the pile"},
              {"minute": 9, "actor": "visitor", "action": "Rasa opens the dashboard: all 40 invoices show status Sent", "moves_toward_result": "the result"}],
}


def test_demo_board_clean_plan_is_silent():
    out = plan_check(None, DEMO)
    assert out["clean"] is True, out["findings"]
    assert out["first_product_pct"] == 0.13 and out["visitor_acts"] == 3 and out["missing"] == []


def test_demo_board_fires_on_passive_acts_feature_tour_late_product_and_number_free_claim():
    bad = dict(DEMO, claim="much faster invoicing", first_product_minute=8, audience="", takeaway="", judged_by="", materials=[],
               steps=[{"minute": 1, "actor": "rep", "action": "The rep shows the settings menu", "shows_feature": "custom logo upload"},
                      {"minute": 8, "actor": "visitor", "action": "Rasa watches the invoices being sent"},
                      {"minute": 12, "actor": "rep", "action": "Rep explains the integrations page", "shows_feature": "Rivile export"}],
               live=True, audience_reasons=[{"reason": "faster invoicing"}], pre_talk_questions=["How many invoices a month?"])
    out = plan_check(None, bad)
    codes = _codes(out)
    for code in ("audience_missing", "claim_no_number", "passive_act", "feature_tour", "late_product", "visitor_passive", "takeaway_missing", "judged_by_missing", "materials_missing", "situation_not_first", "result_not_last", "audience_unknown", "reason_no_example", "pretalk_missing"):
        assert code in codes, code
    assert out["clean"] is False and out["branches"] == 1
    lt = plan_check(None, {"kind": "demo_board", "audience": "buhalterės", "claim": "40 sąskaitų per 9 min.", "situation": "Rasa turi 40 neišsiųstų sąskaitų", "result": "40 sąskaitų su būsena Išsiųsta",
                           "steps": [{"action": "Lankytoja stebi, kaip siunčiamos sąskaitos"}]})
    assert lt["language"] == "lt" and "passive_act" in _codes(lt)


# --- response_board ----------------------------------------------------------------------------------------


MATRIX = {
    "kind": "response_board", "contacts_confirmed": True,
    "tiers": [
        {"name": "Įprasti klausimai", "examples": ["darbo laikas", "kaina"], "owner_role": "Rūta (community manager)", "response_time_min": 60, "can_answer_publicly": True, "escalate_to": "Eglė", "escalate_when": "skundas dėl trenerio vardu", "threshold_eur": 50},
        {"name": "Jautrūs skundai", "examples": ["sužalojimas", "teismas", "žiniasklaida"], "owner_role": "Eglė (marketingo vadovė)", "response_time_min": 120, "can_answer_publicly": False, "escalate_to": "teisininkas", "escalate_when": "sužalojimas, teismas, žiniasklaida", "threshold_eur": 500},
    ],
    "scenarios": [{"complaint_text": "Treneris mane sužalojo, kreipsiuos į teismą", "expected_tier": "Jautrūs skundai",
                   "walkthrough": [{"actor": "Rūta", "action": "viešai pripažįsta ir kviečia į DM", "minutes": 10}, {"actor": "Eglė", "action": "informuota", "minutes": 10}, {"actor": "teisininkas", "action": "gauna skundą el. paštu", "minutes": 30}]}],
}


def test_response_board_clean_matrix_has_coverage_and_plain_table():
    out = plan_check(None, MATRIX)
    assert out["clean"] is True, out["findings"]
    assert out["coverage"] == {"tiers_with_owner": "2/2", "sensitive_tier_present": True, "scenarios_reaching_escalation": "1/1"}
    assert "<" not in out["table"] and out["table"].startswith("Pakopa | Atsakingas")
    assert out["language"] == "lt"


def test_response_board_fires_on_tbd_owner_no_sensitive_tier_and_drill_that_stops_early():
    out = plan_check(None, {"kind": "response_board", "contacts_confirmed": False,
                            "tiers": [{"name": "Routine", "owner": "ROLE TBD", "response_time_min": 2000, "can_answer_publicly": True},
                                      {"name": "Refunds", "owner": "Rūta", "escalate_to": "Eglė"}],
                            "scenarios": [{"complaint_text": "trainer injured me", "expected_tier": "Refunds", "walkthrough": [{"actor": "Rūta", "action": "replies 'atsiprašome'", "minutes": 5}]},
                                          {"complaint_text": "x", "expected_tier": "Legal", "walkthrough": []}]})
    codes = _codes(out)
    for code in ("tier_no_owner", "response_time_long", "tier_no_escalation", "escalate_when_missing", "response_time_missing", "no_sensitive_tier", "threshold_missing", "contacts_unconfirmed", "escalation_not_reached", "scenario_unknown_tier"):
        assert code in codes, code
    assert out["coverage"]["scenarios_reaching_escalation"] == "0/2"
    no_drill = plan_check(None, {"kind": "response_board", "tiers": MATRIX["tiers"]})
    assert "no_drill" in _codes(no_drill) and "contacts_confirmed: not stated" in no_drill["missing"]


# --- partner_board ----------------------------------------------------------------------------------------


def test_partner_board_clean_programme(ws):
    partners = [{"name": f"Partner {i}", "url": f"https://p{i}.lt", "size_band": band, "investment_signal": {"text": "shipped Peppol connector 2026-03", "source_url": "https://b1.lt/changelog"}}
                for i, band in enumerate(["small", "mid", "large", "large", "small", "mid"], start=1)]
    partners[0].update({"bundled_function": "accountant export", "revenue_at_risk_eur": 180, "boundary_note": "we ship one-click export; two-way sync stays with partners"})
    out = plan_check(ws, {"kind": "partner_board", "tiers": [{"name": "Certified", "partner_obligations": ["live integration", "2 case studies a year"], "our_obligations": ["listing", "co-marketing budget"]}],
                          "support_items": [{"name": "sandbox", "removes_obstacle": "no test data", "ship_signal": "first API call from the partner's sandbox key"}],
                          "exclusivity": {"terms": "none; by segment on request"}, "partners": partners})
    assert out["clean"] is True, out["findings"]
    assert out["summary_partners"] == {"n_total": 6, "share_small_mid": 0.67, "n_without_signal": 0, "total_revenue_at_risk_eur": 180.0, "n_unknown_risk": 0}


def test_partner_board_fires_on_one_sided_tiers_unsigned_support_large_only_list_and_bundle_conflict(ws):
    partners = [{"name": f"Big {i}", "url": "", "size_band": "large"} for i in range(5)]
    partners[0].update({"investment_signal": {"text": "api docs", "source_url": "https://unknown.example/api"}})
    partners[1].update({"bundled_function": "accountant export", "boundary_note": ""})
    out = plan_check(ws, {"kind": "partner_board", "tiers": [{"name": "Gold", "partner_obligations": ["sell"], "our_obligations": []}],
                          "support_items": [{"name": "docs"}], "partners": partners})
    codes = _codes(out)
    for code in ("tier_one_sided", "support_no_obstacle", "support_no_signal", "exclusivity_unstated", "partners_all_large", "partner_no_signal", "partner_signal_unsourced", "partner_bundle_conflict"):
        assert code in codes, code
    assert out["summary_partners"]["n_unknown_risk"] == 1 and out["summary_partners"]["share_small_mid"] == 0.0
    small = plan_check(ws, {"kind": "partner_board", "partners": partners[:3]})
    assert "partners_all_large" not in _codes(small) and any(m.startswith("partners: 3 rows") for m in small["missing"])


# --- qualification ----------------------------------------------------------------------------------------


def test_qualification_sales_sheet_clean_and_weakest_slot(ws):
    conditions = [
        {"name": "need", "status": "confirmed", "evidence": "job post for e-commerce platform engineer", "source_url": "https://pigu.lt/karjera"},
        {"name": "means", "status": "confirmed", "evidence": "revenue €310M 2025", "source_url": "https://pigu.lt/karjera"},
        {"name": "access", "status": "uncertain", "evidence": "contact is a marketing specialist", "question": "Kas priima sprendimą dėl platformos keitimo?"},
        {"name": "timing", "status": "uncertain", "question": "Kada planuojamas biudžetas?"},
        {"name": "decision_process", "status": "absent", "question": "Kas dalyvauja sprendime: IT, finansai?"},
    ]
    out = plan_check(ws, {"kind": "qualification", "prospect": "UAB Pigu", "conditions": conditions, "disqualifiers": ["no platform change before 2028"]})
    assert out["clean"] is True, out["findings"]
    assert out["sheet"] == "sales" and out["weakest"] == "decision_process" and "Tailor the follow-up to decision_process" in out["summary"]
    bad = plan_check(ws, {"kind": "qualification", "conditions": [{"name": "means", "status": "confirmed", "evidence": "they have budget"}, {"name": "access", "status": "uncertain"}, {"name": "need", "status": "confirmed", "evidence": "x", "source_url": "https://nowhere.example/x"}]})
    codes = _codes(bad)
    assert codes.count("slot_missing") == 2 and {"slot_unsourced", "slot_no_question", "slot_source_unknown", "disqualifiers_missing"} <= set(codes)
    with pytest.raises(ToolError, match="status must be one of"):
        plan_check(ws, {"kind": "qualification", "conditions": [{"name": "need", "status": "maybe"}]})


def test_qualification_screener_coverage_and_routes(ws):
    segments = [{"name": "chaosas", "unmet_need": "mėnesio pabaigos chaosas"}, {"name": "baudos", "unmet_need": "baimė dėl VMI baudų"}, {"name": "be buhalterio", "unmet_need": "nori atsisakyti buhalterio"}]
    questions = [
        {"text": "Kas labiausiai erzina mėnesio pabaigoje?", "answers": [{"text": "chaosas su dokumentais", "segment": "chaosas"}, {"text": "baimė suklysti VMI", "segment": "baudos"}, {"text": "buhalterio sąskaita", "segment": "be buhalterio"}]},
        {"text": "Kas šiandien tvarko apskaitą?", "answers": [{"text": "aš pats", "segment": "chaosas"}, {"text": "buhalteris, bet brangu", "segment": "be buhalterio"}]},
    ]
    routes = [{"segment": "chaosas", "evidence": "https://example.lt/case-uab-medis", "proof_text": "UAB Medis"}, {"segment": "baudos", "evidence": "deliverables/vmi-garantija.md"}]
    out = plan_check(ws, {"kind": "qualification", "segments": segments, "questions": questions, "routes": routes, "disqualifiers": ["ne Lietuvos įmonė"]})
    assert out["sheet"] == "screener" and out["language"] == "lt"
    assert _codes(out) == ["route_missing"] and out["findings"][0]["value"] == "be buhalterio"
    assert out["coverage_matrix"] == {"chaosas": [1, 1], "baudos": [1, 0], "be buhalterio": [1, 1]}
    bad = plan_check(ws, {"kind": "qualification", "segments": segments, "disqualifiers": ["x"], "routes": routes + [{"segment": "be buhalterio", "evidence": "https://made-up.example/case"}],
                          "questions": [{"text": "Q1", "answers": [{"text": "a", "segment": "chaosas"}, {"text": "b", "segment": "chaosas"}]}, {"text": "Q2", "answers": [{"text": "c", "segment": "nėra tokio"}]}] + [{"text": f"Q{i}", "answers": [{"text": "d", "segment": "baudos"}]} for i in range(3, 7)]})
    codes = _codes(bad)
    for code in ("non_discriminating_question", "unknown_segment", "unreachable_segment", "too_many_questions", "evidence_unsourced"):
        assert code in codes, code
    with pytest.raises(ToolError, match="no segments researched"):
        plan_check(ws, {"kind": "qualification", "sheet": "screener", "questions": questions})


# --- evidence_log ----------------------------------------------------------------------------------------


def test_evidence_log_appends_jsonl_inside_workspace_and_counts_by_kind(ws):
    entries = [
        {"kind": "modification", "text": "uses invoices for rent reminders", "source_url": "https://reddit.com/r/smallbusiness/abc", "quote": "I set up monthly rent reminders", "tags": {"category": "new_use", "task_enabled": "rent collection"}},
        {"kind": "modification", "text": "wants dark mode", "source_url": "https://g2.com/invohub/review/1", "tags": {"category": "refinement"}},
        {"kind": "quote", "text": "fastest invoice I ever sent", "quote": "fastest invoice I ever sent", "source_url": ""},
        {"kind": "finding", "text": "3 of 12 tickets describe membership fees", "source_url": "support-export.xlsx"},
    ]
    out = plan_check(ws, {"kind": "evidence_log", "entries": entries, "known_urls": ["https://reddit.com/r/smallbusiness/abc", "https://g2.com/invohub/review/1"]})
    assert out["path"] == "runs/notes/evidence.jsonl" and out["written"] == 4 and out["rejected"] == 0
    assert out["counts_by_kind"] == {"modification": 2, "quote": 1, "finding": 1, "source_audit": 0}
    assert out["counts_by_category"] == {"new_use": 1, "refinement": 1}
    assert out["no_url"] == ["fastest invoice I ever sent"] and _codes(out) == ["entry_no_url"]
    rows = [json.loads(line) for line in (ws.root / "runs" / "notes" / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4 and rows[0]["tags"]["task_enabled"] == "rent collection" and rows[2]["status"] == "no_url"
    again = plan_check(ws, {"kind": "evidence_log", "entries": entries[:1], "known_urls": ["https://reddit.com/r/smallbusiness/abc"]})
    assert again["clean"] is True
    assert len((ws.root / "runs" / "notes" / "evidence.jsonl").read_text(encoding="utf-8").splitlines()) == 5
    assert "runs/notes/evidence.jsonl" in ws.files_on_disk()


def test_evidence_log_rejects_bad_rows_marks_unsourced_and_refuses_escapes(ws, tmp_path):
    out = plan_check(ws, {"kind": "evidence_log", "entries": [
        {"kind": "complaint", "text": "x", "source_url": "https://a.lt"},
        {"kind": "modification", "text": "uses it for time tracking", "source_url": "https://forum.lt/t/1", "tags": {"category": "hack"}},
        {"kind": "finding", "text": "", "source_url": "https://a.lt"},
        {"kind": "finding", "text": "return rate 7 %", "source_url": "https://never-fetched.example/stats"},
    ]})
    assert out["written"] == 1 and out["rejected"] == 3 and out["unsourced"] == 1
    assert _codes(out) == ["invalid_category", "invalid_kind", "text_missing", "unsourced"]
    with pytest.raises(ToolError, match="escapes workspace"):
        plan_check(ws, {"kind": "evidence_log", "path": "../outside.jsonl", "entries": [{"kind": "finding", "text": "x", "source_url": "https://a.lt"}]})
    with pytest.raises(ToolError, match="escapes workspace"):
        plan_check(ws, {"kind": "evidence_log", "path": str(tmp_path.parent / "elsewhere.jsonl"), "entries": [{"kind": "finding", "text": "x", "source_url": "https://a.lt"}]})
    assert not (tmp_path.parent / "elsewhere.jsonl").exists() and not (tmp_path.parent / "outside.jsonl").exists()


def test_evidence_log_source_audit_fit(ws):
    needed = {"measure": "return rate per order", "population": "Lithuanian online shoppers", "period": "2025"}
    fits = plan_check(ws, {"kind": "evidence_log", "needed": needed, "known_urls": ["https://osp.stat.gov.lt/returns"], "entries": [
        {"kind": "source_audit", "text": "return rate per order 7 %", "source_url": "https://osp.stat.gov.lt/returns", "measure": "return rate per order", "population": "Lithuanian online shoppers aged 16-74", "period": "2024"}]})
    assert fits["clean"] is True and fits["audits"][0]["fit"] == "fits"
    partial = plan_check(ws, {"kind": "evidence_log", "needed": needed, "known_urls": ["https://ec.europa.eu/eurostat/x"], "entries": [
        {"kind": "source_audit", "text": "share of individuals who returned goods", "source_url": "https://ec.europa.eu/eurostat/x", "measure": "share of individuals who returned goods bought online", "population": "Lithuanian individuals 16-74", "period": "2021"}]})
    audit = partial["audits"][0]
    assert audit["fit"] == "partial" and audit["collected"] == "2021" and {"source_period_old", "source_measure_differs"} <= set(_codes(partial))
    unfit = plan_check(ws, {"kind": "evidence_log", "needed": needed, "known_urls": ["https://x.lt"], "entries": [
        {"kind": "source_audit", "text": "returns", "source_url": "https://x.lt", "population": "German retailers"}]})
    assert unfit["audits"][0] == {"source_url": "https://x.lt", "collected": "not stated", "covers": "German retailers", "defines": "not stated", "gaps": ["collected: not stated on the page", "defines: not stated on the page", "covers German retailers, needed Lithuanian online shoppers"], "fit": "unfit"}


# --- community_rules ----------------------------------------------------------------------------------------


RULES_LT = """# Lemon Gym narių grupės taisyklės

Kas leidžiama ir kas šalinama – žemiau. Sprendžia trys moderatoriai; apeliacija per 48 val.; atsakymo laikas – per 24 val.

## Taisyklė 1. Kritika treneriams tik be vardų
Leidžiama: „Rytinė joga 7:00 man per anksti, siūlau 8:00“
Šalinama: „Trenerė X yra nekompetentinga, venkite jos“

## Taisyklė 2. Jokios reklamos
Leidžiama: „Kas žino gerą fizioterapeutą Kaune?“
Šalinama: „Parduodu baltymus su nuolaida, rašykite į asmenines“

## Moderatorių atmintinė
| Atvejis | Veiksmas | Sprendžia | Apeliacija |
|---|---|---|---|
| kritika treneriui vardu | paslėpti, perspėti | moderatorius Nr. 1 | per 48 val. |
| reklama | ištrinti | bet kuris moderatorius | per 48 val. |
| įžeidimai | ištrinti, 7 d. blokas | moderatorius Nr. 2 | per 48 val. |
| pakartotinis pažeidimas | pašalinti iš grupės | visi trys | per 7 d. |
| skundas dėl užsiėmimo atšaukimo | atsakyti viešai per 24 val. | moderatorius Nr. 3 | – |
"""


def test_community_rules_clean_document_and_missing_parts(ws):
    out = plan_check(ws, {"kind": "community_rules", "text": RULES_LT})
    assert out["clean"] is True, out["findings"]
    assert out["language"] == "lt" and len(out["rules"]) == 2 and out["moderator_table_rows"] == 5 and all(out["sections"].values())
    (ws.root / "deliverables").mkdir()
    (ws.root / "deliverables" / "community_rules.md").write_text("# Rules\n\n## Rule 1. Be kind\nAllowed: \"I disagree with the 7:00 class time\"\n\n## Rule 2. No spam\n", encoding="utf-8")
    bad = plan_check(ws, {"kind": "community_rules", "path": "deliverables/community_rules.md"})
    codes = _codes(bad)
    assert codes.count("rule_no_examples") == 2 and "no_mod_table" in codes and codes.count("section_missing") == 4
    assert bad["sections"]["allowed"] is True and bad["sections"]["appeal"] is False
    with pytest.raises(ToolError, match="not found"):
        plan_check(ws, {"kind": "community_rules", "path": "deliverables/nope.md"})


# --- retell ----------------------------------------------------------------------------------------


STATEMENTS = [
    {"id": "s1", "text": "Sąskaitos per 30 sekundžių", "must_survive": ["30 sekundžių"]},
    {"id": "s2", "text": "Apskaita be buhalterio", "must_survive": ["be buhalterio"]},
    {"id": "s3", "text": "Gauk pinigus per 7 dienas", "must_survive": ["7 dienos"]},
]


def test_retell_scores_survival_with_a_scripted_reader_and_ranks():
    llm = FakeLLM({"Apskaita": "Programa, su kuria smulkus verslas tvarko apskaitą be buhalterio.",
                   "Sąskaitos": "Kažkokia programa sąskaitoms, greitai išrašo.",
                   "Gauk": "Greitas mokėjimas, nežinau tiksliau."})
    out = plan_check(None, {"kind": "retell", "statements": STATEMENTS, "buyer_type": "smulkaus verslo savininkas be buhalterio", "n_readers": 3, "model": "openai/gpt-5-mini"}, llm=llm)
    assert out["language"] == "lt" and out["model"] == "openai/gpt-5-mini" and out["proxy"] is True
    assert len(llm.calls) == 9 and all(c["model"] == "openai/gpt-5-mini" for c in llm.calls)
    assert all(len(c["messages"]) == 2 and c["messages"][0]["role"] == "system" for c in llm.calls)
    by_id = {s["id"]: s for s in out["statements"]}
    assert by_id["s2"]["survival_rate"] == 1.0 and by_id["s2"]["kept"] == [["be buhalterio"]] * 3 and by_id["s2"]["lost"] == []
    assert by_id["s1"]["survival_rate"] == 0.0 and by_id["s1"]["lost"] == ["30 sekundžių"]
    assert by_id["s3"]["survival_rate"] == 0.0 and by_id["s3"]["named_use_case"] == [False] * 3
    assert out["best"] == "s2"
    assert _codes(out, "statements[1]") == ["retell_lost"] and set(_codes(out, "statements[3]")) == {"no_use_case", "retell_lost"}
    assert _codes(out, "statements[2]") == [] and out["missing"] == []


def test_retell_without_llm_returns_prompts_and_missing_llm_and_flags_missing_terms():
    out = plan_check(None, {"kind": "retell", "statements": [{"text": "Invoices in 30 seconds"}], "buyer_type": "a freelance designer"})
    assert out["missing"][0].startswith("llm:") and out["statements"][0]["retellings"] == [] and out["statements"][0]["survival_rate"] is None
    prompt = out["statements"][0]["prompt"]
    assert prompt["model"] == "google/gemini-3.8-flash" and "a freelance designer" in prompt["system"] and prompt["user"] == "Invoices in 30 seconds"
    assert set(_codes(out)) == {"must_survive_missing", "retell_not_run"}
    with pytest.raises(ToolError, match="at most 5"):
        plan_check(None, {"kind": "retell", "statements": [{"text": "x"}] * 6, "buyer_type": "b"})
    no_persona = plan_check(None, {"kind": "retell", "statements": STATEMENTS}, llm=FakeLLM({}))
    assert "no_persona" in _codes(no_persona) and no_persona["statements"][0]["retellings"] == []


# --- ad_pretest ----------------------------------------------------------------------------------------


ADS = [
    {"id": "ad1", "primary_text": "Mėnesio pabaiga vėl čia pat, o algų lapeliai, Sodros formos ir deklaracijos laukia... Pavargote nuo šito kiekvieną mėnesį? Dabar yra Algis, algų programa.", "headline": "Mažiau streso", "must_survive": ["alg", "Sodra"]},
    {"id": "ad2", "primary_text": "Algos ir Sodra per 10 min. – Algis, 29 €/mėn.", "headline": "Algis algoms", "must_survive": ["alg", "Sodra"]},
]


def test_ad_pretest_glance_truncates_scores_and_ranks_with_a_scripted_reader():
    llm = FakeLLM({"Algos ir Sodra": "Algų skaičiavimo programa su Sodros ataskaitomis smulkiam verslui."}, default="nežinau, kažkas verslui")
    out = plan_check(None, {"kind": "ad_pretest", "mode": "glance", "ads": ADS, "audience": "smulkaus verslo savininkė, 45 m.", "n_readers": 2}, llm=llm)
    assert out["mode"] == "glance" and out["proxy"] is True and len(llm.calls) == 4
    by_id = {a["id"]: a for a in out["ads"]}
    assert by_id["ad1"]["visible_text"].startswith("Mėnesio pabaiga") and by_id["ad1"]["visible_text"].endswith("…\nMažiau streso")
    assert "Algis, algų programa" not in by_id["ad1"]["visible_text"]
    assert by_id["ad1"]["verdict"] == "warn" and by_id["ad1"]["survival_rate"] == 0.0
    assert by_id["ad2"]["verdict"] == "pass" and by_id["ad2"]["survival_rate"] == 1.0 and by_id["ad2"]["kept"] == [["alg", "Sodra"]] * 2
    assert out["ranking"] == ["ad2", "ad1"] and _codes(out) == ["glance_unclear"] and out["findings"][0]["where"] == "ads[1]"


def test_ad_pretest_reference_and_identification_modes():
    llm = FakeLLM({"Spotify": "Nežinau, kas tas Spotify, bet gal kažkas apie muziką."})
    ref = plan_check(None, {"kind": "ad_pretest", "mode": "reference", "ads": [{"id": "kilo", "primary_text": "Tavo virtuvės Spotify – nauja vakarienė kas vakarą už 4,90 € porcijai.", "must_survive": ["vakarienė"]}], "audience": "mama 35–50 iš Šiaulių", "n_readers": 1}, llm=llm)
    assert ref["ads"][0]["reference_understood"] is False and _codes(ref) == ["reference_unclear"]
    clear = plan_check(None, {"kind": "ad_pretest", "mode": "reference", "ads": [{"id": "kilo2", "primary_text": "Vakarienė be planavimo: ingredientai ir receptas prie durų.", "must_survive": ["vakarienė"]}], "audience": "mama 35–50", "n_readers": 1},
                       llm=FakeLLM({}, default="Kad vakarienės produktus ir receptą atveža į namus, nereikia planuoti."))
    assert clear["clean"] is True and clear["ads"][0]["verdict"] == "pass"
    ident = plan_check(None, {"kind": "ad_pretest", "mode": "identification", "ads": [{"id": "spa", "headline": "Palangos SPA savaitgalis 189 €", "image_description": "jauna pora baseine saulėlydyje", "must_survive": ["SPA"]}], "audience": "moterys 50+, keliauja su drauge", "n_readers": 3},
                       llm=FakeLLM({}, default="KAS: pora, ~25 m., baseine. PRISIJUNGČIAU: ne."))
    assert ident["ads"][0]["would_join"] is False and ident["ads"][0]["depicted_people"].startswith("pora") and _codes(ident) == ["depicted_mismatch"]
    no_image = plan_check(None, {"kind": "ad_pretest", "mode": "identification", "ads": [{"id": "x", "headline": "SPA"}], "audience": "moterys 50+"}, llm=FakeLLM({}))
    assert "no_image" in _codes(no_image)


def test_ad_pretest_without_llm_or_audience_runs_nothing_and_says_so():
    out = plan_check(None, {"kind": "ad_pretest", "ads": ADS, "audience": "owner"})
    assert out["missing"][0].startswith("llm:") and all(a["readings"] == [] for a in out["ads"]) and "pretest_not_run" in _codes(out)
    assert out["ads"][0]["prompt"]["model"] == "google/gemini-3.8-flash" and "owner" in out["ads"][0]["prompt"]["system"]
    llm = FakeLLM({})
    no_audience = plan_check(None, {"kind": "ad_pretest", "ads": ADS}, llm=llm)
    assert "no_audience" in _codes(no_audience) and "audience: not given" in no_audience["missing"] and llm.calls == []
    with pytest.raises(ToolError, match="mode must be"):
        plan_check(None, {"kind": "ad_pretest", "ads": ADS, "audience": "x", "mode": "taste"})



def test_dispatch_routes_plan_check_with_the_client(tmp_path):
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    out = _json.loads(dispatch(WorkspaceTools(tmp_path), "plan_check", {"kind": "community_rules", "text": "# Taisyklės\n\n## Kas leidžiama\nviskas"}))
    assert out["kind"] == "community_rules" if "kind" in out else True
    assert any(f["code"] == "section_missing" for f in out["findings"])
    no_llm = _json.loads(dispatch(WorkspaceTools(tmp_path), "plan_check", {"kind": "retell", "statements": [{"id": "s1", "text": "Gauk pinigus už darbą laiku", "must_survive": ["pinigus", "laiku"]}]}))
    assert any(m.startswith("llm") for m in no_llm["missing"])
    assert "plan_check" in [t["function"]["name"] for t in worker_tools(False)]



def test_content_calendar_writes_a_page_and_csv(ws):
    items = [
        {"title": "Launch post", "publish_date": "2027-01-14", "channel": "LinkedIn", "owner": "TBD", "format": "post", "stage": "awareness"},
        {"title": "Guide to co-working", "publish_date": "2027-02-10", "channel": "blog", "format": "article", "owner": "Eglė", "stage": "evaluation", "lifespan": "evergreen", "goal": "Which space fits a team of five?"},
        {"title": "No date yet", "channel": "blog", "format": "article", "owner": "Eglė", "stage": "decision"},
    ]
    out = plan_check(ws, {"kind": "content_calendar", "items": items, "today": "2026-11-08", "language": "en"})
    assert out["deliverable"] == "deliverables/calendar.html" and out["csv"] == "deliverables/calendar.csv"
    assert out["summary"].endswith("Page: deliverables/calendar.html.")
    page = ws.resolve("deliverables/calendar.html").read_text(encoding="utf-8")
    assert "<title>Content calendar</title>" in page and "January 2027" in page and "February 2027" in page
    assert "Launch post" in page and "owner_missing" in page and "No date yet" in page and "chip block" in page
    rows = ws.resolve("deliverables/calendar.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("id,publish_date,title") and len(rows) == 4
    assert "owner_missing" in rows[1] and rows[-1].startswith("item-3,,No date yet")
    quiet = plan_check(ws, {"kind": "content_calendar", "items": items, "today": "2026-11-08", "render": False, "page": "deliverables/other.html"})
    assert "deliverable" not in quiet and not ws.resolve("deliverables/other.html").exists()
    lt = plan_check(None, {"kind": "content_calendar", "items": [{"title": "Įrašas", "publish_date": "2027-03-02", "channel": "blog", "owner": "Rasa", "stage": "awareness"}], "today": "2026-11-08", "language": "lt"})
    assert "deliverable" not in lt
    from rocky.plans import calendar_page

    assert "Turinio kalendorius" in calendar_page(lt)
