import json

import pytest

from rocky.pageinspect import content_words, detect_language, find_prices, page_inspect, page_inspect_tool, parse_amount, same_stem
from rocky.tools import ToolError, WorkspaceTools

FILLER_LT = " ".join(["Mes tvarkome apskaitą, deklaracijas ir atlyginimus, kad jūs galėtumėte dirbti su klientais."] * 20)
FILLER_EN = " ".join(["We handle the books, the filings and payroll so that you can spend the week with your customers."] * 20)


def lt_page(h1="Buhalterijos paslaugos smulkiam verslui", price="€49", button='<a class="btn" href="/registracija">Registruotis dabar</a>', extra="", lang="lt", filler=FILLER_LT):
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<title>Buhalterija verslui | Firma</title>
<meta name="description" content="Apskaita smulkiam verslui nuo {price} per mėnesį.">
<style>.btn {{ color: red }}</style>
<script>var price = "€999";</script>
</head>
<body>
<header><nav><a href="/">Pradžia</a> <a href="/kainos">Kainos</a></nav></header>
<main>
<h1>{h1}</h1>
<p>Tik {price} per mėnesį, be ilgalaikių sutarčių.</p>
{button}
<h2>Kas įeina</h2>
<img src="a.png" alt="Komanda"> <img src="b.png">
<p>{filler}</p>
{extra}
</main>
<noscript>Įjunkite JavaScript</noscript>
</body></html>"""


def en_page(h1="Bookkeeping for small businesses", button='<button type="submit">Sign up free</button>', forms=True, filler=FILLER_EN, extra=""):
    form = f'<form action="/signup"><input type="email" name="email"> {button}</form>' if forms else button
    return f"""<html lang="en"><head><title>Bookkeeping | Firm</title></head>
<body><h1>{h1}</h1><p>Only $59 a month.</p>{form}<p>{filler}</p>{extra}</body></html>"""


def by_code(out, code):
    hits = [f for f in out["findings"] if f["code"] == code]
    return hits[0] if hits else None


# --- promise -------------------------------------------------------------


def test_promise_found_in_h1_names_the_element_and_the_shared_words():
    ad = {"headline": "Buhalterija smulkiam verslui nuo 49 €", "cta": "Registruotis"}
    out = page_inspect(None, {"ad": ad, "html": lt_page()})
    f = by_code(out, "promise_match")
    assert f["level"] == "info" and f["page_element"] == "h1"
    assert f["ad_field"] == "headline" and f["ad_value"] == ad["headline"]
    assert f["page_value"] == "Buhalterijos paslaugos smulkiam verslui"
    assert "smulkiam" in f["note"] and "verslui" in f["note"]
    assert by_code(out, "promise_missing") is None
    assert "promise found in H1" in out["summary"]


def test_promise_missing_when_the_page_restates_nothing():
    ad = {"headline": "Nemokama konsultacija dėl mokesčių", "primary_text": "Užsiregistruok ir sutaupyk laiko."}
    out = page_inspect(None, {"ad": ad, "html": lt_page(h1="Sveiki atvykę", filler=" ".join(["lorem ipsum dolor sit amet"] * 40))})
    f = by_code(out, "promise_missing")
    assert f["level"] == "warn" and f["page_element"] == "h1" and f["page_value"] == "Sveiki atvykę"
    assert f["ad_value"] == ad["headline"]
    assert "not restated" in out["summary"]


# --- price ------------------------------------------------------------------


def test_price_mismatch_is_a_block_with_both_values_and_the_page_snippet():
    ad = {"headline": "Buhalterija smulkiam verslui", "price": "€49", "cta": "Registruotis"}
    out = page_inspect(None, {"ad": ad, "html": lt_page(price="€59")})
    f = by_code(out, "price_mismatch")
    assert f["level"] == "block"
    assert f["ad_field"] == "price" and f["ad_value"] == "€49"
    assert f["page_element"] == "prices" and f["page_value"] == "€59"
    assert "Tik €59 per mėnesį" in f["note"]
    assert out["counts"]["block"] == 1
    assert out["findings"][0]["code"] == "price_mismatch"  # blocks come first
    assert "price €49 in ad, page shows €59 (block)" in out["summary"]
    # the script's "€999" never counts as a page price
    assert all(p["value"] != 999 for p in out["page"]["prices"])


def test_price_in_the_copy_counts_when_no_price_field_is_given():
    ad = {"headline": "Apskaita nuo 49 € per mėnesį"}
    out = page_inspect(None, {"ad": ad, "html": lt_page(price="49 €")})
    f = by_code(out, "price_match")
    assert f and f["ad_field"] == "headline" and f["ad_value"] == "49 €" and f["page_value"] == "49 €"


def test_price_formats_normalise_so_1_200_50_matches_1_200_50():
    assert parse_amount("1 200,50") == 1200.5
    assert parse_amount("1,200.50") == 1200.5
    assert parse_amount("1.200,50") == 1200.5
    assert parse_amount("1,200") == 1200
    assert parse_amount("49,99") == 49.99
    assert parse_amount("1200") == 1200
    ad = {"headline": "Kasos aparatas verslui", "price": "1 200,50 €"}
    out = page_inspect(None, {"ad": ad, "html": en_page(h1="Cash register for business", extra="<p>Price: €1,200.50 incl. VAT</p>")})
    f = by_code(out, "price_match")
    assert f and f["page_value"] == "€1,200.50" and by_code(out, "price_mismatch") is None


def test_price_absent_when_the_page_shows_no_price():
    ad = {"headline": "Buhalterija smulkiam verslui", "price": 49}
    html = lt_page(price="").replace("nuo  per mėnesį", "")
    out = page_inspect(None, {"ad": ad, "html": html})
    f = by_code(out, "price_absent")
    assert f and f["level"] == "warn" and f["page_value"] is None
    assert out["page"]["prices"] == []


def test_find_prices_keeps_the_surrounding_words():
    prices = find_prices("Planas Pro kainuoja 1 200,50 € per metus, o Start tik $9.")
    assert [p["value"] for p in prices] == [1200.5, 9.0]
    assert "kainuoja 1 200,50 € per metus" in prices[0]["context"]
    assert find_prices("Europoje 49 Eurovizijos dalyviai") == []  # 'Eur' inside a word is not a currency


# --- call to action ---------------------------------------------------------


def test_cta_matches_a_link_styled_as_a_button():
    ad = {"headline": "Buhalterija smulkiam verslui", "cta": "Registruotis"}
    out = page_inspect(None, {"ad": ad, "html": lt_page()})
    f = by_code(out, "cta_match")
    assert f["level"] == "info" and f["page_element"] == "button" and f["page_value"] == "Registruotis dabar"
    assert "Registruotis dabar" in out["page"]["buttons"]
    assert "Kainos" not in out["page"]["buttons"]  # plain navigation is not a button
    assert "CTA 'Registruotis' matches button 'Registruotis dabar'" in out["summary"]


def test_cta_mismatch_lists_the_page_buttons():
    ad = {"headline": "Bookkeeping for small businesses", "cta": "Sign up"}
    out = page_inspect(None, {"ad": ad, "html": en_page(button='<button>Download brochure</button>')})
    f = by_code(out, "cta_mismatch")
    assert f["level"] == "warn" and f["ad_value"] == "Sign up" and f["page_value"] == ["Download brochure"]
    assert "matches no page button (warn)" in out["summary"]


def test_cta_stem_match_tolerates_lithuanian_endings():
    ad = {"headline": "Buhalterija smulkiam verslui", "cta": "Registruokis"}
    out = page_inspect(None, {"ad": ad, "html": lt_page()})
    assert by_code(out, "cta_match")["page_value"] == "Registruotis dabar"


def test_a_page_with_no_buttons_or_forms_blocks():
    ad = {"headline": "Bookkeeping for small businesses", "cta": "Sign up"}
    out = page_inspect(None, {"ad": ad, "html": en_page(button="", forms=False)})
    f = by_code(out, "no_cta_on_page")
    assert f["level"] == "block" and f["page_value"] == []
    assert by_code(out, "cta_mismatch") is None
    assert out["page"]["forms"] == {"count": 0, "inputs": []}
    assert "no button or form (block)" in out["summary"]


def test_submit_inputs_and_form_fields_are_read():
    ad = {"headline": "Bookkeeping for small businesses", "cta": "Sign up"}
    out = page_inspect(None, {"ad": ad, "html": en_page(button='<input type="submit" value="Sign up now">')})
    assert by_code(out, "cta_match")["page_value"] == "Sign up now"
    assert out["page"]["forms"] == {"count": 1, "inputs": ["email", "submit"]}


# --- audience, language, offer ----------------------------------------------


def test_audience_words_present_and_absent():
    ad = {"headline": "Buhalterija smulkiam verslui", "audience": "smulkaus verslo savininkai", "cta": "Registruotis"}
    present = page_inspect(None, {"ad": ad, "html": lt_page(extra="<p>Dirbame su smulkaus verslo savininkais.</p>")})
    assert by_code(present, "audience_match") and by_code(present, "audience_missing") is None
    absent = page_inspect(None, {"ad": {**ad, "audience": "kirpėjos ir grožio salonai"}, "html": lt_page()})
    f = by_code(absent, "audience_missing")
    assert f["level"] == "warn" and f["ad_value"] == "kirpėjos ir grožio salonai"
    assert "kirpėjos" in f["note"] and "salonai" in f["note"]
    assert "audience words absent" in absent["summary"]


def test_language_mismatch_between_lithuanian_ad_and_english_page():
    ad = {"headline": "Buhalterija smulkiam verslui", "primary_text": "Apskaita ir deklaracijos už 49 € per mėnesį."}
    out = page_inspect(None, {"ad": ad, "html": en_page()})
    f = by_code(out, "language_mismatch")
    assert f["level"] == "warn" and f["ad_value"] == "lt"
    assert f["page_element"] == "html[lang]" and f["page_value"] == "en"
    assert "ad in lt, page in en (warn)" in out["summary"]
    same = page_inspect(None, {"ad": ad, "html": lt_page()})
    assert by_code(same, "language_mismatch") is None


def test_language_detected_from_the_body_when_the_lang_attribute_is_absent():
    ad = {"headline": "Bookkeeping for small businesses", "language": "English"}
    html = lt_page(lang="").replace(' lang=""', "")
    out = page_inspect(None, {"ad": ad, "html": html})
    f = by_code(out, "language_mismatch")
    assert f and f["page_element"] == "body" and "'lt'" in f["note"]
    assert out["page"]["lang"] is None and out["page"]["lang_detected"] == "lt"


def test_offer_words_found_or_not_found():
    ad = {"headline": "Buhalterija smulkiam verslui", "offer": "nemokama konsultacija"}
    found = page_inspect(None, {"ad": ad, "html": lt_page(extra="<p>Pirma konsultacija nemokama.</p>")})
    assert by_code(found, "offer_match")
    missing = page_inspect(None, {"ad": ad, "html": lt_page()})
    f = by_code(missing, "offer_missing")
    assert f["level"] == "warn" and "nemokama" in f["note"] and "konsultacija" in f["note"]


# --- the page itself ----------------------------------------------------------


def test_thin_page_and_missing_h1_warn():
    ad = {"headline": "Bookkeeping for small businesses"}
    html = '<html lang="en"><body><p>Only $59 a month. Sign up free.</p><a class="btn" href="#">Sign up</a></body></html>'
    out = page_inspect(None, {"ad": ad, "html": html})
    thin = by_code(out, "thin_page")
    assert thin["level"] == "warn" and thin["page_value"] == out["page"]["word_count"] < 150
    assert by_code(out, "no_h1")["level"] == "warn"
    assert "no H1 (warn)" in out["summary"]
    assert by_code(page_inspect(None, {"ad": ad, "html": en_page()}), "thin_page") is None


def test_page_extraction_ignores_scripts_styles_and_counts_images():
    out = page_inspect(None, {"ad": {"headline": "Buhalterija smulkiam verslui"}, "html": lt_page()})
    page = out["page"]
    assert page["title"] == "Buhalterija verslui | Firma"
    assert page["meta_description"].startswith("Apskaita smulkiam verslui")
    assert page["lang"] == "lt" and page["h1"] == ["Buhalterijos paslaugos smulkiam verslui"] and page["h2"] == ["Kas įeina"]
    assert page["images_total"] == 2 and page["images_with_alt"] == 1
    assert "color: red" not in page["first_screen_text"] and "JavaScript" not in page["first_screen_text"]
    assert page["word_count"] > 150


# --- sources ------------------------------------------------------------------


def test_html_fixture_via_path_in_the_workspace(tmp_path):
    tools = WorkspaceTools(tmp_path)
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "landing.html").write_text(lt_page(), encoding="utf-8")
    out = page_inspect(tools, {"ad": {"headline": "Buhalterija smulkiam verslui", "cta": "Registruotis"}, "path": "pages/landing.html"})
    assert out["page"]["source"] == "pages/landing.html"
    assert by_code(out, "promise_match") and by_code(out, "cta_match")


def test_html_fixture_via_html_string():
    out = page_inspect(None, {"ad": {"headline": "Buhalterija smulkiam verslui"}, "html": lt_page()})
    assert out["page"]["source"] == "html" and out["missing"] == []


def test_url_fetched_through_the_callable():
    seen = []

    def fake_fetch(url):
        seen.append(url)
        return en_page()

    out = page_inspect(None, {"ad": {"headline": "Bookkeeping for small businesses", "cta": "Sign up"}, "url": "https://example.com/signup"}, fetch_html=fake_fetch)
    assert seen == ["https://example.com/signup"]
    assert out["page"]["source"] == "https://example.com/signup"
    assert by_code(out, "promise_match")["page_element"] == "h1" and by_code(out, "cta_match")


def test_url_without_a_fetcher_goes_to_missing_not_error():
    out = page_inspect(None, {"ad": {"headline": "Bookkeeping for small businesses"}, "url": "https://example.com/"})
    assert out["findings"] == [] and out["counts"] == {"block": 0, "warn": 0, "info": 0}
    assert len(out["missing"]) == 1 and "not fetched" in out["missing"][0] and "https://example.com/" in out["missing"][0]
    assert out["page"] == {"source": "https://example.com/", "fetched": False}
    assert "not fetched" in out["summary"]


def test_a_failing_fetch_is_a_plain_error():
    def broken(url):
        raise OSError("connection refused")

    with pytest.raises(ToolError, match="could not fetch https://example.com/: connection refused"):
        page_inspect(None, {"ad": {"headline": "x y"}, "url": "https://example.com/"}, fetch_html=broken)


# --- errors -------------------------------------------------------------------


def test_errors_in_plain_words(tmp_path):
    tools = WorkspaceTools(tmp_path)
    with pytest.raises(ToolError, match="the ad has no text"):
        page_inspect(tools, {"ad": {"cta": "Sign up"}, "html": en_page()})
    with pytest.raises(ToolError, match="the ad has no text"):
        page_inspect(tools, {"html": en_page()})
    with pytest.raises(ToolError, match="no page source"):
        page_inspect(tools, {"ad": {"headline": "Bookkeeping"}})
    with pytest.raises(ToolError, match="path missing: pages/none.html"):
        page_inspect(tools, {"ad": {"headline": "Bookkeeping"}, "path": "pages/none.html"})
    with pytest.raises(ToolError, match="give one page source, not html, url"):
        page_inspect(tools, {"ad": {"headline": "Bookkeeping"}, "html": en_page(), "url": "https://example.com/"})
    with pytest.raises(ToolError, match="the page is empty"):
        page_inspect(tools, {"ad": {"headline": "Bookkeeping"}, "url": "https://example.com/"}, fetch_html=lambda u: "   ")
    with pytest.raises(ToolError, match="escapes workspace"):
        page_inspect(tools, {"ad": {"headline": "Bookkeeping"}, "path": "../../etc/passwd"})


# --- the tool wrapper ---------------------------------------------------------


def test_page_inspect_tool_returns_json_with_findings_ordered_by_level():
    ad = {"headline": "Buhalterija smulkiam verslui", "price": "€49", "cta": "Skambinti", "audience": "kirpėjos"}
    raw = page_inspect_tool(None, {"ad": ad, "html": lt_page(price="€59")})
    out = json.loads(raw)
    levels = [f["level"] for f in out["findings"]]
    assert levels == sorted(levels, key=["block", "warn", "info"].index)
    assert out["counts"] == {"block": 1, "warn": 2, "info": 1}
    assert "€49" in raw and "€59" in raw  # ensure_ascii=False keeps the euro sign readable
    assert set(out["findings"][0]) == {"code", "level", "ad_field", "ad_value", "page_element", "page_value", "note"}


# --- helpers --------------------------------------------------------------------


def test_stems_tolerate_lithuanian_endings_but_not_different_words():
    assert same_stem("kaina", "kainos") and same_stem("registruotis", "registruokis") and same_stem("price", "prices")
    assert not same_stem("konsultacija", "konstrukcija") and not same_stem("greitas", "greičiau")
    assert content_words("Jūsų verslui – daugiau laiko su klientais") == ["verslui", "laiko", "klientais"]
    assert detect_language("Apskaita ir deklaracijos už 49 €") == "lt"
    assert detect_language("Bookkeeping for your business") == "en"
    assert detect_language("Lorem ipsum") is None



def test_dispatch_passes_the_web_fetcher_and_the_recipe_runs_the_check(tmp_path):
    import json as _json

    from rocky.recipes import load_recipes
    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    class Searcher:
        def fetch_html(self, url):
            return "<html lang='lt'><body><h1>Nemokama konsultacija verslui</h1><a class='btn'>Registruotis</a></body></html>"

    out = _json.loads(
        dispatch(
            WorkspaceTools(tmp_path),
            "page_inspect",
            {"ad": {"headline": "Nemokama konsultacija verslui", "cta": "Registruotis"}, "url": "https://x.lt"},
            searcher=Searcher(),
        )
    )
    assert out["counts"]["block"] == 0 and any(f["code"] == "cta_match" for f in out["findings"])
    without = _json.loads(dispatch(WorkspaceTools(tmp_path), "page_inspect", {"ad": {"headline": "x y"}, "url": "https://x.lt"}))
    assert without["missing"] and not without["findings"]
    assert "page_inspect" in [t["function"]["name"] for t in worker_tools(False)]
    ads_step = load_recipes()["ads"].render_steps("goal")[1]["detail"]
    assert "page_inspect for each ad" in ads_step


# =============================================================================
# Optional checks: query/intent, credibility, links, trust, price ladder, crawler,
# data use, emails, structured data, navigation, promises, touchpoint, UTM.
# =============================================================================


def codes(out):
    return [f["code"] for f in out["findings"]]


# --- query and intent (#343, #322) -------------------------------------------


def test_query_terms_are_matched_above_the_fold_and_a_missing_place_blocks():
    html = f'<html lang="lt"><head><title>Mūsų paslaugos | Firma</title></head><body><h1>Mūsų paslaugos</h1><p>Apskaita, mokesčiai, audito paruošimas.</p><a class="btn" href="/kontaktai">Susisiekti</a><p>{FILLER_LT}</p></body></html>'
    ad = {"headline": "Buhalterija smulkiam verslui – nemokama pirma konsultacija", "cta": "Registruotis"}
    out = page_inspect(None, {"ad": ad, "query": "buhalterinės paslaugos Vilniuje", "html": html})
    q = out["query_match"]
    assert q["terms"] == ["buhalterinės", "paslaugos", "vilniuje"] and q["found"] == ["paslaugos"] and q["coverage"] == 0.33
    assert q["first_screen_quote"].startswith("Mūsų paslaugos Apskaita")
    f = by_code(out, "query_not_answered")
    assert f["level"] == "warn" and f["ad_value"] == "buhalterinės paslaugos Vilniuje" and f["page_value"] == "Mūsų paslaugos"
    loc = by_code(out, "query_location_missing")
    assert loc["level"] == "block" and loc["ad_value"] == "Vilniuje"
    # the ad comparison still runs beside the query check
    assert by_code(out, "promise_missing") and by_code(out, "cta_mismatch")
    assert out["intent_inferred"] is True and out["intent_fit"]["intent"] == "local" and out["intent_fit"]["fits"] is False
    assert "query 'buhalterinės paslaugos Vilniuje' coverage 0.33 (warn)" in out["summary"]


def test_intent_fit_passes_a_transactional_page_and_fails_an_informational_one():
    pricing = f'<html lang="en"><head><title>Pricing | InvoHub</title></head><body><h1>Invoicing software pricing</h1><p>From €9 a month.</p><button>Start free</button><h2>Plans</h2><h2>FAQ</h2><p>{FILLER_EN}</p></body></html>'
    out = page_inspect(None, {"query": "invoicing software price", "intent": "transactional", "brand": "InvoHub", "html": pricing})
    fit = out["intent_fit"]
    assert fit["fits"] is True and fit["signals"] == {"has_price": True, "cta_buttons": 1, "forms": 0, "word_count": fit["signals"]["word_count"], "h2_count": 2, "brand_in_title": True}
    assert out["query_match"]["coverage"] == 1.0 and by_code(out, "intent_mismatch") is None
    assert out["checks_run"] == ["intent", "query"]
    guide = page_inspect(None, {"intent": "informational", "html": pricing})
    f = by_code(guide, "intent_mismatch")
    assert f["level"] == "warn" and f["ad_value"] == "informational"
    assert any("words" in r for r in guide["intent_fit"]["reasons"])
    with pytest.raises(ToolError, match="unknown intent"):
        page_inspect(None, {"intent": "buying", "html": pricing})


# --- credibility (#150) -----------------------------------------------------


def test_credibility_reads_the_lithuanian_identity_block_and_flags_unsourced_claims():
    html = f"""<html lang="lt"><body><h1>Greitos paskolos</h1><p>Lietuvos Nr. 1 greitųjų paskolų platforma. 98 % patenkintų klientų.</p>
<a class="btn" href="/registracija">Registruotis</a><p>{FILLER_LT}</p>
<footer>UAB „Greita Paskola“, įmonės kodas 304567890, Savanorių pr. 12, Kaunas. Tel. +370 600 00000, el. paštas info@greitaspaskola.lt. Direktorius Jonas Jonaitis.
<a href="/kontaktai">Kontaktai</a> <a href="/privatumo-politika">Privatumo politika</a></footer></body></html>"""
    out = page_inspect(None, {"checks": ["credibility"], "html": html})
    ident = out["credibility"]["identity"]
    assert ident["company_name"]["value"] == "UAB „Greita Paskola“" and ident["company_name"]["where"].startswith("footer")
    assert ident["reg_code"]["value"] == "304567890"
    assert ident["address"]["value"] == "Savanorių pr. 12, Kaunas"
    assert ident["phone"]["value"] == "+370 600 00000" and ident["email"]["value"] == "info@greitaspaskola.lt"
    assert ident["named_person"]["value"] == "Jonas Jonaitis"
    assert out["credibility"]["links"]["contact"] == "/kontaktai" and out["credibility"]["links"]["terms"] is None
    gaps = [f for f in out["findings"] if f["code"] == "credibility_gap"]
    assert [g["page_element"] for g in gaps] == ["terms_link"]
    claims = [f["page_value"] for f in out["findings"] if f["code"] == "unsupported_claim"]
    assert claims == ["Greitos paskolos Lietuvos Nr. 1 greitųjų paskolų platforma.", "98 % patenkintų klientų"]


def test_credibility_gap_per_missing_item_on_an_english_page():
    html = f'<html lang="en"><body><h1>Fast loans</h1><p>The best loans in town, guaranteed.</p><a class="btn" href="/apply">Apply</a><p>{FILLER_EN}</p><footer>Contact: <a href="mailto:hi@fast.example">hi@fast.example</a> · <a href="/terms">Terms</a></footer></body></html>'
    out = page_inspect(None, {"checks": ["credibility"], "html": html})
    assert out["credibility"]["gaps"] == ["company_name", "reg_code", "address", "phone", "named_person", "contact_link", "privacy_link"]
    assert out["credibility"]["identity"]["email"]["value"] == "hi@fast.example"
    assert sum(1 for c in codes(out) if c == "credibility_gap") == 7
    claim = by_code(out, "unsupported_claim")
    assert claim["level"] == "warn" and claim["page_value"] == "Fast loans The best loans in town, guaranteed."
    assert "promise_missing" not in codes(out)  # no ad was given, so nothing is compared


# --- links (#150, #161) and UTM tags (#485) ---------------------------------------


SITE = f"""<html lang="lt"><body><header><a href="/" class="logo"><img src="logo.png" alt="Shop"></a><nav><a href="/kainos">Kainos</a><a href="/apie">Apie</a><a href="/blog/2023">Blog</a></nav><a href="/krepselis">Krepšelis</a></header>
<h1>Parduotuvė</h1><a href="/blog/2023-palukanos">Palūkanos 2023</a> <a href="https://partner.example.org/x">Partneris</a> <a href="/apie-mus/komanda">Komanda</a> <a href="/senas">Senas</a><button>Pirkti</button><p>{FILLER_LT}</p></body></html>"""


def test_internal_links_are_checked_through_fetch_meta_and_broken_ones_quote_href_and_anchor():
    seen = []

    def meta(url):
        seen.append(url)
        table = {
            "https://shop.lt/blog/2023-palukanos": {"status": 404},
            "https://shop.lt/apie-mus/komanda": {"status": 500},
            "https://shop.lt/senas": {"status": 200, "final_url": "https://shop.lt/naujas", "redirects": 2},
        }
        return table.get(url, {"status": 200, "final_url": url})

    out = page_inspect(None, {"checks": ["links"], "html": SITE, "base_url": "https://shop.lt/"}, fetch_meta=meta)
    links = out["links"]
    assert links["status"] == "checked" and links["checked"] == 8 and links["internal_total"] == 8 and links["external_total"] == 1
    assert "https://partner.example.org/x" not in seen
    assert links["broken"] == [
        {"href": "https://shop.lt/blog/2023-palukanos", "text": "Palūkanos 2023", "status": 404},
        {"href": "https://shop.lt/apie-mus/komanda", "text": "Komanda", "status": 500},
    ]
    assert links["redirects"] == [{"href": "https://shop.lt/senas", "text": "Senas", "final_url": "https://shop.lt/naujas", "hops": 2}]
    broken = [f for f in out["findings"] if f["code"] == "broken_link"]
    assert [(f["level"], f["page_value"]) for f in broken] == [("block", "Komanda"), ("warn", "Palūkanos 2023")]
    assert "https://shop.lt/apie-mus/komanda" in broken[0]["page_element"] and "500" in broken[0]["note"]
    assert by_code(out, "redirect_chain")["level"] == "info"
    assert "links: 2 broken of 8 checked (block)" in out["summary"].lower()


def test_links_without_fetch_meta_land_under_missing_and_max_links_caps_the_check():
    out = page_inspect(None, {"checks": ["links"], "html": SITE, "base_url": "https://shop.lt/"})
    assert out["links"]["status"] == "not_checked" and out["links"]["checked"] == 0 and len(out["links"]["internal"]) == 8
    assert out["missing"] == ["links: 8 internal link(s) listed but not checked (no fetch_meta here)"] and out["findings"] == []
    calls = []
    capped = page_inspect(None, {"checks": ["links"], "max_links": 3, "html": SITE, "base_url": "https://shop.lt/"}, fetch_meta=lambda u: calls.append(u) or {"status": 200, "final_url": u})
    assert len(calls) == 3 and capped["links"]["checked"] == 3
    assert any("5 internal link(s) beyond max_links=3" in m for m in capped["missing"])

    def broken(url):
        raise OSError("timeout")

    flaky = page_inspect(None, {"checks": ["links"], "max_links": 1, "html": SITE, "base_url": "https://shop.lt/"}, fetch_meta=broken)
    assert flaky["links"]["unreachable"][0]["error"] == "timeout" and flaky["links"]["broken"] == []


def test_tracked_links_check_utm_keys_case_and_survival_through_redirects():
    page = f'<html><head><script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC"></script></head><body><h1>Juodasis penktadienis</h1><button>Pirkti</button><p>{FILLER_LT}</p></body></html>'

    def meta(url):
        if url.startswith("https://sh.lt/bf"):
            return {"status": 200, "final_url": "https://shop.lt/juodasis-penktadienis", "redirects": 1}
        return {"status": 200, "final_url": url}

    email = "https://shop.lt/juodasis-penktadienis?utm_source=mailchimp&utm_medium=email&utm_campaign=BF2026"
    out = page_inspect(None, {"tracked_links": [email, "https://sh.lt/bf"], "expected_tags": {"campaign": "bf2026"}, "html": page, "base_url": "https://shop.lt/"}, fetch_meta=meta)
    rows = out["tracked_links"]["links"]
    assert rows[0]["utm"]["campaign"] == "BF2026" and rows[0]["status"] == "fix" and rows[0]["tags_kept"] is True
    assert rows[1]["missing"] == ["source", "medium", "campaign"] and rows[1]["redirects"] == 1 and rows[1]["final_url"] == "https://shop.lt/juodasis-penktadienis"
    inconsistent = by_code(out, "utm_inconsistent")
    assert inconsistent["ad_value"] == email and "campaign 'BF2026' differs from expected 'bf2026'" in inconsistent["page_value"]
    assert by_code(out, "utm_missing")["ad_value"] == "https://sh.lt/bf"
    assert out["tracked_links"]["trackers"] == ["Google Tag Manager"] and by_code(out, "no_tracker_on_page") is None
    # a tagged shortener that drops its tags on redirect is a block
    lost = page_inspect(None, {"tracked_links": ["https://sh.lt/bf?utm_source=sms&utm_medium=sms&utm_campaign=bf2026"], "html": page, "base_url": "https://shop.lt/"}, fetch_meta=meta)
    f = by_code(lost, "utm_lost_on_redirect")
    assert f["level"] == "block" and f["page_value"] == "https://shop.lt/juodasis-penktadienis"
    assert "GA4" in lost["tracked_links"]["note"]
    untracked_page = page_inspect(None, {"tracked_links": [email], "html": en_page()})
    assert by_code(untracked_page, "no_tracker_on_page")["level"] == "warn" and any("tracked_links: redirects not followed" in m for m in untracked_page["missing"])


# --- choice architecture and trust (#164) -------------------------------------


def test_pressure_cues_are_found_in_lithuanian_text_and_form_controls():
    html = f"""<html lang="lt"><body><h1>Kainos</h1><p>Pasiūlymas baigiasi už 09:59. Liko tik 3 vietos! 12 žmonių dabar žiūri šį pasiūlymą.</p>
<form action="/cart"><label><input type="checkbox" name="insurance" checked> Pridėti draudimą (€2,99)</label>
<label><input type="radio" name="plan" value="y" checked> Metinis planas €4,99/mėn.</label><input type="radio" name="plan" value="m"> Mėnesinis €6,99/mėn.
<button type="submit">Pirkti</button></form><p>+ pristatymo mokestis nuo €3,99 apskaičiuojamas užsakymo metu.</p><a href="#">Ne, nenoriu sutaupyti</a><p>{FILLER_LT}</p></body></html>"""
    out = page_inspect(None, {"checks": ["trust"], "html": html})
    els = {e["kind"]: e for e in out["choice_elements"]["elements"]}
    assert set(els) == {"timer", "scarcity", "social_counter", "preselected_addon", "default_plan", "late_fee", "confirm_shame"}
    assert els["scarcity"]["text"] == "Liko tik 3 vietos" and els["scarcity"]["numeric_values"] == [3.0]
    assert els["social_counter"]["text"] == "12 žmonių dabar žiūri šį pasiūlymą"
    assert els["preselected_addon"]["text"] == "Pridėti draudimą (€2,99)" and els["preselected_addon"]["numeric_values"] == [2.99]
    assert els["default_plan"]["text"] == "Metinis planas €4,99/mėn." and els["confirm_shame"]["text"] == "Ne, nenoriu sutaupyti"
    assert els["late_fee"]["text"].startswith("+ pristatymo mokestis nuo €3,99")
    dark = [f for f in out["findings"] if f["code"] == "dark_pattern"]
    assert len(dark) == 7 and all(f["level"] == "warn" for f in dark)
    assert {f["page_value"] for f in dark} == {e["text"] for e in els.values()}
    assert "pressure cues:" in out["summary"].lower()


def test_pressure_cues_in_english_with_select_defaults_and_a_shaming_button():
    html = f"""<html lang="en"><body><h1>Checkout</h1><p>Offer ends in 2 hours. Only 2 left in stock. 37 people bought this today.</p>
<form><input type="checkbox" id="ins" checked><label for="ins">Add shipping insurance ($4.99)</label><select><option>Monthly $9</option><option selected>Yearly plan $89</option></select><button>Pay now</button></form>
<p>Service fee $2.50 added at checkout.</p><button>No thanks, I don't want to save money</button><p>{FILLER_EN}</p></body></html>"""
    out = page_inspect(None, {"checks": ["trust"], "html": html})
    texts = {(e["kind"], e["text"]) for e in out["choice_elements"]["elements"]}
    assert texts == {
        ("timer", "Offer ends in 2 hours"), ("scarcity", "Only 2 left in stock"), ("social_counter", "37 people bought this today"),
        ("preselected_addon", "Add shipping insurance ($4.99)"), ("default_plan", "Yearly plan $89"),
        ("late_fee", "Service fee $2.50 added at checkout"), ("confirm_shame", "No thanks, I don't want to save money"),
    }
    assert sum(1 for c in codes(out) if c == "dark_pattern") == 7
    clean = page_inspect(None, {"checks": ["trust"], "html": en_page()})
    assert clean["choice_elements"]["elements"] == [] and "no pressure cues found" in clean["summary"].lower()


# --- price ladder (#183) -------------------------------------------------------


def test_price_ladder_from_given_tiers_names_the_step_that_buys_nothing_new():
    tiers = [
        {"name": "Start", "price": "€19", "period": "month", "features": ["5 projektai", "1 vartotojas", "el. pašto pagalba"]},
        {"name": "Pro", "price": "€49", "period": "month", "features": ["50 projektų", "5 vartotojai", "integracijos", "el. pašto pagalba"]},
        {"name": "Business", "price": "€99", "period": "month", "features": ["50 projektų", "5 vartotojai", "prioritetinė pagalba"]},
    ]
    out = page_inspect(None, {"tiers": tiers, "html": en_page()})
    ladder = out["price_ladder"]
    assert ladder["status"] == "ok" and ladder["source"] == "tiers" and [t["name"] for t in ladder["tiers"]] == ["Start", "Pro", "Business"]
    s1, s2 = ladder["steps"]
    assert (s1["increment"], s1["increment_pct"]) == (30.0, 157.9)
    assert s1["added_features"] == ["50 projektų (was 5 projektai)", "5 vartotojai (was 1 vartotojas)", "integracijos"] and s1["removed_features"] == []
    assert s1["added_kinds"] == {"capability": 1, "limit": 2, "support": 0} and s1["codes"] == ["step_increment_over_2x"]
    assert (s2["increment"], s2["increment_pct"]) == (50.0, 102.0) and s2["added_features"] == ["prioritetinė pagalba"]
    assert set(s2["codes"]) == {"step_no_new_capability", "step_increment_over_2x", "step_increment_unexplained"}
    f = by_code(out, "ladder_illogical")
    assert f["level"] == "warn" and f["ad_value"] == "Pro → Business" and "prioritetinė pagalba" in f["note"]
    assert ladder["redesign_candidates"] == ["Business"] and by_code(out, "ladder_anchor_missing") is None


def test_price_ladder_parsed_from_pricing_cards_and_tiers_not_found():
    html = f"""<html lang="en"><body><h1>Pricing</h1><div class="plans">
<div class="plan"><h3>Basic</h3><p class="price">$10/mo</p><ul><li>1 user</li><li>Email support</li></ul></div>
<div class="plan popular"><h3>Team</h3><p class="price">$25/mo</p><ul><li>5 users</li><li>Email support</li><li>API access</li><li>Integrations</li></ul></div>
<div class="plan"><h3>Enterprise</h3><p class="price">$30/mo</p><ul><li>5 users</li><li>Email support</li><li>API access</li><li>Integrations</li></ul></div>
</div><button>Start trial</button><p>{FILLER_EN}</p></body></html>"""
    out = page_inspect(None, {"checks": ["price_ladder"], "html": html})
    ladder = out["price_ladder"]
    assert ladder["source"] == "page" and [(t["name"], t["price"], t["period"], t["highlighted"]) for t in ladder["tiers"]] == [("Basic", 10.0, "month", False), ("Team", 25.0, "month", True), ("Enterprise", 30.0, "month", False)]
    assert ladder["tiers"][1]["features"] == ["5 users", "Email support", "API access", "Integrations"]
    f = by_code(out, "ladder_illogical")
    assert f["ad_value"] == "Team → Enterprise" and "adds nothing" in f["note"] and ladder["steps"][1]["codes"] == ["step_increment_unexplained", "tier_feature_overlap"]
    none = page_inspect(None, {"checks": ["price_ladder"], "html": en_page()})
    assert none["price_ladder"]["status"] == "tiers_not_found" and none["findings"] == []
    assert any("tiers_not_found" in m for m in none["missing"])
    two = page_inspect(None, {"tiers": [{"name": "A", "price": 10, "features": ["x"]}, {"name": "B", "price": 240, "period": "per year", "features": ["x", "y"]}], "html": en_page()})
    assert two["price_ladder"]["assumptions"] == ["mixed periods: yearly prices divided by 12 to compare monthly", "tiers without a period assumed monthly: A"]
    assert two["price_ladder"]["steps"][0]["increment"] == 10.0 and by_code(two, "ladder_anchor_missing")["level"] == "warn"


# --- crawler-visible content (#323, #391) -------------------------------------


def test_required_facts_are_html_rendered_only_or_missing_with_the_right_levels():
    html = f'<html lang="lt"><body><h1>Trek FX 2</h1><p>Kaina €749. Puikus miesto dviratis.</p><img src="specs.png"><button>Į krepšelį</button><p>{FILLER_LT}</p></body></html>'
    rendered = "Trek FX 2 Kaina €749. Puikus miesto dviratis. Svoris 11,5 kg. Rėmo dydis M L XL. Garantija 2 metai nuo pirkimo dienos"
    out = page_inspect(None, {"required_facts": ["svoris", "rėmo dydis", "11,5 kg", "miesto dviratis", "karbono rėmas"], "rendered_text": rendered, "html": html})
    facts = {f["fact"]: f["status"] for f in out["crawler"]["facts"]}
    assert facts == {"svoris": "rendered_only", "rėmo dydis": "rendered_only", "11,5 kg": "rendered_only", "miesto dviratis": "html", "karbono rėmas": "missing"}
    hidden = [f for f in out["findings"] if f["code"] == "fact_hidden_from_crawlers"]
    assert [f["ad_value"] for f in hidden] == ["svoris", "rėmo dydis", "11,5 kg"] and all(f["level"] == "warn" for f in hidden)
    assert "Svoris 11,5 kg" in hidden[0]["page_value"]
    missing = by_code(out, "fact_missing")
    assert missing["level"] == "block" and missing["ad_value"] == "karbono rėmas" and "specs.png" in missing["note"]
    assert out["crawler"]["images_without_alt"] == 1 and out["crawler"]["rendered_only_phrases"][0].startswith("Svoris 11 5 kg")
    assert "1 of 5 required facts in the HTML, 4 hidden or missing (block)" in out["summary"]


def test_crawler_without_rendered_text_says_so_and_required_content_adds_the_mobile_block():
    html = '<html lang="en"><body><h1>Kefir 2.5%</h1><h2>Ingredients</h2><ul><li>Milk, live cultures</li></ul><h2>Probiotics</h2><p>Lactobacillus strains listed here.</p>' + "<img src='a.png'>" * 16 + f"<p>{FILLER_EN}</p></body></html>"
    out = page_inspect(None, {"required_content": ["ingredients", "probiotics", "comparison with Rokiškio"], "html": html})
    statuses = {f["fact"]: f["status"] for f in out["crawler"]["facts"]}
    assert statuses == {"ingredients": "html", "probiotics": "html", "comparison with Rokiškio": "missing"}
    assert out["crawler"]["rendered_text_chars"] is None and out["crawler"]["rendered_only_phrases"] is None
    assert any("no rendered_text" in m for m in out["missing"])
    assert out["mobile"]["viewport_meta"] is False and out["mobile"]["images"] == 16 and out["mobile"]["html_bytes"] == out["page"]["html_bytes"]
    assert by_code(out, "page_heavy")["level"] == "warn" and "16 images" in by_code(out, "page_heavy")["note"]
    assert by_code(out, "no_viewport_meta") and by_code(out, "fact_missing")["ad_value"] == "comparison with Rokiškio"
    assert sorted(out["checks_run"]) == ["crawler", "mobile"]


# --- data use and consent (#315) -------------------------------------------------


def test_trackers_before_consent_forms_without_privacy_link_and_policy_gaps_in_lithuanian():
    html = f"""<html lang="lt"><head><script src="https://connect.facebook.net/en_US/fbevents.js"></script><script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXX"></script></head>
<body><div id="cookie-banner">Šioje svetainėje naudojami slapukai. <button>Sutinku</button></div><h1>InvoHub</h1>
<form action="/contact"><input name="vardas"><input type="email" name="el_pastas"><input name="telefonas"><input name="imones_kodas"><button>Siųsti</button></form><p>{FILLER_LT}</p>
<footer><a href="/privatumo-politika">Privatumo politika</a></footer></body></html>"""
    out = page_inspect(None, {"checks": ["data_use"], "html": html, "policy": "Mes naudojame Google Analytics. Renkame jūsų el. pašto adresą."})
    du = out["data_use"]
    assert [(t["vendor"], t["kind"]) for t in du["trackers"]] == [("Meta Pixel", "pixel"), ("Google Analytics 4", "analytics")]
    assert du["consent"]["present"] is True and du["consent"]["texts"] == ["Šioje svetainėje naudojami slapukai."]
    assert du["forms"] == [{"action": "/contact", "fields": ["vardas", "el_pastas", "telefonas", "imones_kodas"], "privacy_link_nearby": False}]
    assert du["privacy_link"] == "/privatumo-politika"
    before = by_code(out, "tracker_before_consent")
    assert before["level"] == "warn" and before["page_value"] == ["Meta Pixel", "Google Analytics 4"] and "before the consent banner" in before["note"]
    assert by_code(out, "undisclosed_recipient")["page_value"] == "Meta Pixel"
    assert by_code(out, "unexplained_collection")["page_value"] == ["imones_kodas", "telefonas", "vardas"]
    assert by_code(out, "no_privacy_link_at_form")["page_element"] == "form[action=/contact]"
    assert du["policy"] == {"given": True, "recipients_named": ["Google Analytics 4"], "undisclosed": ["Meta Pixel"], "unexplained_collection": ["imones_kodas", "telefonas", "vardas"]}


def test_consent_vendor_inline_pixels_and_unknown_third_parties_on_an_english_page(tmp_path):
    html = f"""<html lang="en"><head><script src="https://cdn.example.com/app.js"></script></head><body><h1>Shop</h1><p>{FILLER_EN}</p><button>Buy</button>
<script src="https://consent.cookiebot.com/uc.js"></script><script src="https://static.hotjar.com/c/hotjar-1.js"></script><script>fbq('init','1');</script></body></html>"""
    out = page_inspect(None, {"checks": ["privacy"], "html": html, "base_url": "https://shop.example.com/"})
    du = out["data_use"]
    assert [(t["vendor"], t["kind"], t["via"]) for t in du["trackers"]] == [
        ("unknown_third_party", "unknown", "script"), ("Cookiebot", "consent", "script"), ("Hotjar", "session_replay", "script"), ("Meta Pixel", "pixel", "inline"),
    ]
    assert du["consent"] == {"present": True, "vendor": "Cookiebot", "texts": []}
    assert by_code(out, "tracker_before_consent") is None  # trackers come after the CMP script
    assert "data_use: no policy given, so disclosure was not compared" in out["missing"]
    bare = page_inspect(None, {"checks": ["data_use"], "html": html.replace('<script src="https://consent.cookiebot.com/uc.js"></script>', "")})
    assert "no consent banner anywhere" in by_code(bare, "tracker_before_consent")["note"]
    tools = WorkspaceTools(tmp_path)
    (tmp_path / "policy.md").write_text("We use Hotjar and Meta for analytics.", encoding="utf-8")
    with_policy = page_inspect(tools, {"checks": ["data_use"], "html": html, "policy": "policy.md"})
    assert with_policy["data_use"]["policy"]["undisclosed"] == [] and by_code(with_policy, "undisclosed_recipient") is None


# --- transactional emails (#157) and images off (#244) ----------------------------


def test_receipt_email_fields_found_and_missing_in_lithuanian():
    html = """<html><head><title>Jūsų užsakymas #VS-20431</title></head><body><p>Ačiū! Jūsų užsakymas #VS-20431 priimtas.</p>
<p>Vitamino C serumas 30 ml — 1 vnt. — 24,90 €</p><p>Pristatymas 3,99 €</p><p>Iš viso: 28,89 €</p></body></html>"""
    out = page_inspect(None, {"kind": "receipt", "expected": {"amount": 28.89, "order_ref": "VS-20431", "items": ["Vitamino C serumas"]}, "html": html})
    m = out["message"]
    assert m["fields"]["amount"] == {"found": True, "value": 28.89, "text": "28,89 €", "labelled": True}
    assert m["fields"]["order_ref"] == {"found": True, "value": "VS-20431"}
    assert m["fields"]["items"]["values"] == ["Vitamino C serumas"] and m["fields"]["delivery"]["found"] is False
    assert m["missing_fields"] == ["delivery", "support_contact"] and m["score"] == 0.6
    missing = [f for f in out["findings"] if f["code"] == "email_missing_field"]
    assert [(f["level"], f["page_element"]) for f in missing] == [("warn", "delivery"), ("warn", "support_contact")]
    assert by_code(out, "thin_page") is None and by_code(out, "no_h1") is None  # an email is not a landing page
    assert "receipt email: 3/5 fields, missing delivery, support_contact" in out["summary"].lower()
    wrong = page_inspect(None, {"kind": "receipt", "expected": {"amount": "30,00 €", "order_ref": "VS-99999"}, "html": html})
    mism = [f for f in wrong["findings"] if f["code"] == "email_field_mismatch"]
    assert [(f["ad_field"], f["page_value"]) for f in mism] == [("expected.amount", "28,89 €"), ("expected.order_ref", "VS-20431")]
    assert all(f["level"] == "block" for f in mism)


def test_shipping_and_welcome_emails_in_english():
    ship = """<html><body><p>Good news! Order 10234 has shipped.</p><p>Track it: <a href="https://tracking.dpd.com/parcel/0123">Track your parcel</a></p><p>Questions? support@shop.example or +44 20 7946 0958</p></body></html>"""
    out = page_inspect(None, {"kind": "shipping", "html": ship})
    m = out["message"]["fields"]
    assert m["order_ref"]["value"] == "10234" and m["delivery"] == {"found": True, "kind": "tracking_url", "value": "https://tracking.dpd.com/parcel/0123"}
    assert m["support_contact"]["value"] == "support@shop.example"
    assert [(f["level"], f["page_element"]) for f in out["findings"] if f["code"] == "email_missing_field"] == [("warn", "items")]
    no_tracking = page_inspect(None, {"kind": "shipping", "html": ship.replace('<a href="https://tracking.dpd.com/parcel/0123">Track your parcel</a>', "it arrives in 2 days")})
    assert no_tracking["message"]["fields"]["delivery"]["kind"] == "date"
    assert any("no tracking link" in f["note"] for f in no_tracking["findings"] if f["code"] == "email_missing_field")
    welcome = """<html><body><h1>Welcome to InvoHub</h1><p>Send your first invoice in two minutes.</p><a class="btn" href="https://app.invohub.lt/start">Create an invoice</a><p>Help: help@invohub.lt</p><a href="https://invohub.lt/unsubscribe">Unsubscribe</a></body></html>"""
    ok = page_inspect(None, {"kind": "welcome", "brand": "InvoHub", "html": welcome})
    assert ok["message"]["score"] == 1.0 and ok["message"]["fields"]["next_step"]["value"] == "Create an invoice" and ok["message"]["fields"]["unsubscribe"]["found"]
    bad = page_inspect(None, {"kind": "welcome", "brand": "Acme", "html": welcome.replace('<a href="https://invohub.lt/unsubscribe">Unsubscribe</a>', "")})
    assert [(f["level"], f["page_element"]) for f in bad["findings"] if f["code"] == "email_missing_field"] == [("block", "brand"), ("warn", "unsubscribe")]
    with pytest.raises(ToolError, match="unknown message kind"):
        page_inspect(None, {"kind": "invoice", "html": ship})


def test_images_off_flags_image_only_buttons_missing_alt_and_essentials_in_images():
    html = """<html><body><img src="hero.png"><p>-30 % sodo įrankiams iki sekmadienio 23:59</p><img src="code.png" alt=""><a href="https://shop.lt/x"><img src="btn.png"></a></body></html>"""
    ad = {"headline": "-30 % sodo įrankiams", "offer": "-30 % sodo įrankiams", "deadline": "sekmadienio 23:59", "cta": "Pirkti dabar"}
    out = page_inspect(None, {"checks": ["images_off"], "ad": ad, "html": html})
    io = out["images_off"]
    assert io["essentials"] == {"offer": {"found": True, "where": "text"}, "deadline": {"found": True, "where": "text"}, "cta": {"found": False, "where": "none"}}
    assert [i["src"] for i in io["images"] if i["inside_link"]] == ["btn.png"]
    f = by_code(out, "image_only_cta")
    assert f["level"] == "warn" and f["page_element"] == "a[href=https://shop.lt/x] > img" and f["page_value"] == ["btn.png"]
    assert by_code(out, "missing_alt")["page_value"] == ["hero.png", "code.png", "btn.png"]
    assert by_code(out, "essential_in_image")["ad_field"] == "cta" and by_code(out, "deadline_not_in_text") is None
    late = page_inspect(None, {"kind": "receipt", "ad": {"headline": "Your order", "deadline": "Friday 18:00"}, "html": '<html><body><p>Order 12345 total $10 paid. Items: Mug x 1</p><a href="/t"><img src="go.png"></a></body></html>'})
    assert by_code(late, "deadline_not_in_text")["ad_value"] == "Friday 18:00"
    assert by_code(late, "image_only_cta")["level"] == "block"  # in an email the only button being an image blocks


# --- structured data (#329) ----------------------------------------------------------


def test_json_ld_values_are_compared_with_the_visible_page():
    html = f"""<html lang="lt"><body><h1>Vitaminas D3</h1><p>€24,90</p><p>4,6 iš 5 (212 atsiliepimų)</p><button>Į krepšelį</button><p>{FILLER_LT}</p>
<script type="application/ld+json">{{"@type":"Product","name":"Vitaminas D3","offers":{{"@type":"Offer","price":"22.90","priceCurrency":"EUR"}},"aggregateRating":{{"@type":"AggregateRating","ratingValue":"4.9","reviewCount":"212"}}}}</script></body></html>"""
    out = page_inspect(None, {"checks": ["schema"], "html": html})
    blocks = {b["type"]: b["fields"] for b in out["structured_data"]["blocks"]}
    assert blocks["Offer"] == {"price": 22.9, "currency": "EUR"} and blocks["AggregateRating"] == {"ratingValue": 4.9, "reviewCount": 212.0}
    bad = [(f["ad_value"], f["page_value"]) for f in out["findings"] if f["code"] == "schema_field_not_visible"]
    assert bad == [("Offer.price=22.9", ["€24,90"]), ("AggregateRating.ratingValue=4.9", ["4,6"])]
    assert all(f["level"] == "block" for f in out["findings"] if f["code"] == "schema_field_not_visible")
    assert [f["ad_value"] for f in out["findings"] if f["code"] == "schema_matches_visible"] == ["Product.name", "AggregateRating.reviewCount"]
    none = page_inspect(None, {"checks": ["schema"], "html": html.split("<script")[0] + "</body></html>"})
    assert none["structured_data"]["present"] is False and none["structured_data"]["candidates"] == ["Offer (visible price €24,90)", "AggregateRating (visible 4,6 iš 5)"]
    assert sum(1 for c in codes(none) if c == "schema_missing_for_entity") == 2
    broken = page_inspect(None, {"checks": ["schema"], "html": '<html><body><h1>x y</h1><button>Go</button><script type="application/ld+json">{"@type": "Product",</script></body></html>'})
    assert by_code(broken, "schema_invalid_json")["level"] == "block"


# --- navigation (#149), promises (#392), trust signals (#398), touchpoint (#268), listing (#88), top tasks (#148)


def test_navigation_conventions_by_markup():
    out = page_inspect(None, {"checks": ["navigation"], "html": SITE, "base_url": "https://shop.lt/"})
    nav = out["navigation"]
    assert nav["home_link"] == {"pass": True, "href": "/"} and nav["cart_link"] == {"pass": True, "href": "/krepselis"} and nav["menu"]["pass"] is True
    assert not [c for c in codes(out) if c.startswith("nav_")]
    redesign = '<html lang="en"><body><header><img class="logo" src="l.png"><a href="#">Menu</a></header><h1>Shop</h1><button>Buy</button><p>' + FILLER_EN + "</p></body></html>"
    bad = page_inspect(None, {"checks": ["navigation"], "html": redesign})
    assert bad["navigation"]["home_link"]["pass"] is False and bad["navigation"]["cart_link"]["pass"] is False
    assert by_code(bad, "nav_home_link_missing")["level"] == "warn" and by_code(bad, "nav_menu_weak")
    assert by_code(bad, "dead_click_target")["page_value"] == "Menu"
    assert "not a user test" in bad["navigation"]["note"]


def test_promise_ledger_and_trust_signals_from_a_product_page():
    html = f'<html lang="lt"><body><h1>Samsung 55" QLED</h1><p class="price">€699</p><p class="delivery-badge">Atsiimk per 2 val. parduotuvėje</p><p>Yra sandėlyje. 30 dienų grąžinimas. Garantija 2 metai. Mokėkite kortele, PayPal arba lizingu.</p><p>4,5 iš 5 (88 atsiliepimai)</p><button>Į krepšelį</button><p>{FILLER_LT}</p></body></html>'
    out = page_inspect(None, {"checks": ["promises", "trust_signals"], "html": html})
    ledger = {p["kind"]: p for p in out["promises"]}
    assert ledger["price"]["value"] == 699.0 and (ledger["pickup_time"]["value"], ledger["pickup_time"]["unit"]) == (2.0, "hours")
    assert ledger["return_window"]["value"] == 30.0 and ledger["warranty"]["value"] == 2.0 and ledger["stock"]["value"] == "Yra sandėlyje"
    assert "Atsiimk per 2 val." in ledger["pickup_time"]["quote"] and ledger["payment"]["value"] == ["PayPal", "kortele", "lizingu"]
    assert "promises not found on the page: delivery_time" in out["missing"]
    ts = out["trust_signals"]
    assert ts["reviews"]["present"] and ts["reviews"]["score"] == 4.5 and ts["returns_policy"]["days"] == 30.0 and ts["payment_badges"] == ["PayPal", "kortele", "lizingu"]
    assert by_code(out, "trust_signal_missing")["page_element"] == "company_identity"
    plain = page_inspect(None, {"checks": ["trust_signals"], "html": en_page()})
    assert [f["page_element"] for f in plain["findings"] if f["code"] == "trust_signal_missing"] == ["reviews", "returns_policy", "company_identity", "payment_badges"]


def test_touchpoint_brand_value_line_and_tracked_route():
    invoice = '<html><body><h1>Sąskaita faktūra Nr. INV-001</h1><p>Suma 120,00 €</p><p>Mokėti iki 2026-12-01</p><footer><img src="logo.png" alt="InvoHub"> <a href="https://invohub.lt">invohub.lt</a></footer></body></html>'
    out = page_inspect(None, {"product_name": "InvoHub", "html": invoice})
    tp = out["touchpoint"]
    assert tp["brand_present"] is True and tp["footer_only"] is True and tp["value_line"] is None
    assert tp["try_links"] == [{"href": "https://invohub.lt", "text": "invohub.lt", "tracked": False, "param": None}]
    assert codes(out) == ["touchpoint_no_value", "touchpoint_untracked"]
    good = invoice.replace("<footer>", '<footer><p>Išrašyta per InvoHub — išbandykite nemokamai savo sąskaitoms.</p><a href="https://invohub.lt/try?ref=invoice">Išbandyti</a>')
    ok = page_inspect(None, {"product_name": "InvoHub", "html": good})
    assert ok["touchpoint"]["value_line"].startswith("Išrašyta per InvoHub") and ok["touchpoint"]["try_links"][0]["tracked"] is True and ok["findings"] == []
    none = page_inspect(None, {"product_name": "Acme", "html": invoice})
    assert by_code(none, "touchpoint_no_brand")["level"] == "block"


def test_listing_modes_and_local_top_tasks():
    listing = f'<html lang="lt"><body><h1>Robotai siurbliai</h1><form><select name="sort"><option>Populiariausi</option></select><input name="price_min"></form><p>4,4 iš 5 (340 atsiliepimų)</p><a href="/p/1">Roborock S8</a><button>Į krepšelį</button><p>{FILLER_LT}</p></body></html>'
    out = page_inspect(None, {"checks": ["listing"], "html": listing})
    assert out["listing"]["discovery"] == [] and [e["kind"] for e in out["listing"]["evaluation"]] == ["spec_filter", "review_block"]
    assert by_code(out, "listing_mode_missing")["page_element"] == "discovery"
    both = page_inspect(None, {"checks": ["listing"], "html": listing.replace("<form>", '<a href="/gidas">Kaip išsirinkti robotą siurblį</a><form>')})
    assert both["listing"]["discovery"][0]["kind"] == "guide_link" and by_code(both, "listing_mode_missing") is None
    clinic = f'<html lang="lt"><head><meta name="viewport" content="width=device-width"></head><body><header><a href="tel:+37061234567">+370 612 34567</a></header><h1>Odontologijos klinika Kaune</h1><p>{FILLER_LT}</p><a class="btn" href="/registracija">Registruotis vizitui</a><footer>Savanorių pr. 12, Kaunas. <a href="https://maps.google.com/?q=x">Žemėlapis</a></footer></body></html>'
    top = page_inspect(None, {"checks": ["top_tasks"], "intent": "local", "query": "odontologas Kaune", "html": clinic})
    tasks = {t["task"]: t["status"] for t in top["top_tasks"]["tasks"]}
    assert tasks == {"address": "below", "phone": "first_screen", "hours": "absent", "map": "below", "cta": "below"}
    assert by_code(top, "top_task_missing")["page_element"] == "hours" and by_code(top, "top_task_below_fold")["page_value"] == "Savanorių pr. 12, Kaunas"
    assert top["intent_fit"]["fits"] is True and top["query_match"]["coverage"] == 1.0
    hours = page_inspect(None, {"checks": ["top_tasks"], "html": clinic.replace("<h1>", "<p>Darbo laikas: I–V 8:00–18:00</p><h1>")})
    assert {t["task"]: t["status"] for t in hours["top_tasks"]["tasks"]}["hours"] == "first_screen"


# --- the gate: ad optional only when a check is asked for; helpers stay public -----------


def test_checks_run_without_an_ad_but_a_bare_page_still_needs_one():
    from rocky.pageinspect import CHECKS

    with pytest.raises(ToolError, match="the ad has no text"):
        page_inspect(None, {"html": en_page()})
    with pytest.raises(ToolError, match="unknown check 'speed'"):
        page_inspect(None, {"checks": ["speed"], "html": en_page()})
    out = page_inspect(None, {"checks": "credibility, trust", "html": en_page()})
    assert out["checks_run"] == ["credibility", "trust"] and "promise_missing" not in codes(out)
    everything = page_inspect(None, {"checks": ["all"], "html": en_page(), "base_url": "https://firm.example/"})
    assert set(everything["checks_run"]) == set(CHECKS)
    raw = json.loads(page_inspect_tool(None, {"checks": ["trust"], "query": "bookkeeping small businesses", "html": en_page()}))
    assert raw["query_match"]["coverage"] == 1.0 and raw["choice_elements"]["elements"] == []
    # CTA competition: three or more other actions in the first screen warn
    crowded = en_page(button='<button>Sign up free</button><a class="btn" href="/c">Compare plans</a><a class="btn" href="/t">Talk to sales</a><a class="btn" href="/n">Newsletter</a>')
    comp = page_inspect(None, {"ad": {"headline": "Bookkeeping for small businesses", "cta": "Sign up"}, "html": crowded})
    assert by_code(comp, "cta_competition")["page_value"] == ["Compare plans", "Talk to sales", "Newsletter"] and comp["page"]["competing_actions"] == 3
    # find_prices no longer reads a digit glued to a word as a price
    assert [p["text"] for p in find_prices("Vitaminas D3 €24,90")] == ["€24,90"]



def test_spec_and_fetch_meta_wiring(tmp_path):
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "page_inspect")
    props = spec["function"]["parameters"]["properties"]
    assert "checks" in props and "query" in props and spec["function"]["parameters"]["required"] == []

    class Searcher:
        def fetch_html(self, url):
            return "<html><body><h1>Batai</h1><a href='/dead'>senas</a><a class='btn'>Pirkti</a></body></html>"

        def fetch_meta(self, url):
            return {"status": 404 if url.endswith("/dead") else 200, "final_url": url, "headers": {}}

    out = _json.loads(dispatch(WorkspaceTools(tmp_path), "page_inspect", {"ad": {"headline": "Batai"}, "url": "https://x.lt/batai", "checks": ["links"]}, searcher=Searcher()))
    assert any(f["code"] == "broken_link" for f in out["findings"])
