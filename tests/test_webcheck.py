import json

import pytest

from rocky.tools import ToolError
from rocky.webcheck import MODES, normalise_phone, web_check, web_check_tool

FILLER_LT = " ".join(["Mes parduodame batus, striukes ir kuprines su pristatymu per dvi dienas visoje Lietuvoje."] * 12)
FILLER_EN = " ".join(["We sell boots, jackets and backpacks with delivery in two days across the whole country."] * 12)


def page(title="Puslapis", body="", head="", lang="lt", h1=None):
    h1_html = f"<h1>{h1}</h1>" if h1 else ""
    return f'<!doctype html><html lang="{lang}"><head><title>{title}</title>{head}</head><body><main>{h1_html}{body}</main></body></html>'


def fake_fetch(mapping):
    def fetch_html(url):
        if url not in mapping:
            raise RuntimeError("404")
        return mapping[url]
    return fetch_html


def fake_search(mapping, default=None):
    def search(query):
        results = mapping.get(query, default if default is not None else [])
        if isinstance(results, str):
            return results
        return "\n".join(f"- {title}\n  {url}" for title, url in results)
    return search


def codes(out):
    return [f["code"] for f in out["findings"]]


# --- dispatch ---------------------------------------------------------------


def test_modes_and_dispatch_errors():
    assert len(MODES) == 12
    with pytest.raises(ToolError, match="mode is required"):
        web_check(None, {})
    with pytest.raises(ToolError, match="unknown mode"):
        web_check(None, {"mode": "magic"})


def test_tool_wrapper_returns_json_with_the_common_shape():
    data = json.loads(web_check_tool(None, {"mode": "search_region", "query": "cbd aliejus", "region": "lv-lv"}))
    assert data["mode"] == "search_region"
    for key in ("findings", "counts", "missing", "warnings", "summary"):
        assert key in data
    assert data["summary"].endswith(".")


# --- name_check -------------------------------------------------------------


def test_name_check_generates_heard_spellings_per_language():
    out = web_check(None, {"mode": "name_check", "names": ["Qvikly", "Greitukas"], "langs": ["lt", "en"], "category": "maisto pristatymas"})
    qvikly, greitukas = out["names"]
    q_spellings = {s["spelling"] for s in qvikly["spellings"]}
    assert {"qvikly", "kvikly", "kvikli", "quikly"} <= q_spellings
    g_spellings = {s["spelling"] for s in greitukas["spellings"]}
    assert "greitukas" in g_spellings and len(g_spellings) <= 3
    assert all(s["status"] == "search_unavailable" for s in qvikly["spellings"])
    assert any("not searched" in m for m in out["missing"])
    assert out["verdict"] == "untested_with_buyers"
    assert greitukas["score"] >= qvikly["score"]


def test_name_check_detects_collision_and_findability():
    results = {
        "greitukas": [("Greitukas – maisto pristatymas Vilniuje", "https://greitukas.lt"), ("Greitukas app", "https://apps.example/greitukas")],
        "kvikly": [("Kwikly Software – Kwikly CRM", "https://kwikly.com"), ("Kwikly pricing", "https://kwikly.com/pricing"), ("Kwikly reviews", "https://g2.com/kwikly")],
    }
    out = web_check(None, {"mode": "name_check", "names": ["Greitukas", "Qvikly"], "langs": ["lt"], "category": "maisto pristatymas",
                           "intended_url": "https://greitukas.lt"}, search=fake_search(results))
    greitukas, qvikly = out["names"]
    assert greitukas["findability"] == "intended_found"
    assert greitukas["score"] == 3
    assert any(c["token"] == "kwikly" for c in qvikly["collisions"])
    assert "name_collision" in codes(out)
    assert out["domain_status" if "domain_status" in out else "verdict"]  # verdict present
    assert any("domain availability" in m for m in out["missing"])


# --- mention_scan -----------------------------------------------------------


def test_mention_scan_classifies_results_and_names_the_best_qualifier():
    results = {
        "Perlas": [("Perlas terminalai – loterijos ir mokėjimai", "https://perlas.lt/terminalai"), ("Perlas viešbutis Palangoje", "https://perlas-hotel.lt"),
                   ("Perlas papuošalai", "https://papuosalai.lt/perlas"), ("Perlas – Vikipedija", "https://lt.wikipedia.org/wiki/Perlas")],
        "Perlas terminalas": [("Perlas terminalas neveikia – forumas", "https://forum.lt/t/1"), ("Perlas loterijos terminalas", "https://news.lt/perlas")],
        "Perlas loterija": [("Perlas loterija skundai", "https://skundai.lt/perlas")],
    }
    out = web_check(None, {"mode": "mention_scan", "brand": "Perlas", "domain": "perlas.lt", "qualifiers": ["terminalas", "loterija"]}, search=fake_search(results))
    by_url = {m["url"]: m["entity"] for m in out["mentions"]}
    assert by_url["https://perlas.lt/terminalai"] == "ours"
    assert by_url["https://perlas-hotel.lt"] == "ambiguous"
    assert by_url["https://forum.lt/t/1"] == "ours"
    assert out["counts_by_entity"]["ours"] == 4
    assert out["best_qualifier"] == "terminalas"
    assert "Perlas" in out["noise_queries"]
    assert "noise_query" in codes(out)


def test_mention_scan_without_searcher_or_qualifiers_stays_honest():
    out = web_check(None, {"mode": "mention_scan", "brand": "Perlas", "domain": "perlas.lt"})
    assert all(q["status"] == "search_unavailable" for q in out["per_query"])
    assert out["mentions"] == []
    assert any("no qualifiers" in w for w in out["warnings"])
    assert len(out["missing"]) == len(out["queries"])


# --- policy_finder ----------------------------------------------------------


SHOP = {
    "https://shop.lt/": page("Batai online", body='<p>Nike Revolution 7</p><footer><a href="/info/pagalba">Pagalba</a><a href="/grazinimas">Grąžinimas</a></footer>'),
    "https://shop.lt/grazinimas": page("Prekių grąžinimas", h1="Grąžinimas", body="<p>Grąžinti galite per 14 dienų nemokamai. Prekė turi būti nenaudota ir originalioje pakuotėje. Pinigus grąžiname per 5 darbo dienas.</p>"),
    "https://shop.lt/info/pagalba": page("Pagalba", body='<a href="/info/pristatymas">Pristatymo sąlygos</a>'),
    "https://shop.lt/info/pristatymas": page("Pristatymas", h1="Pristatymo sąlygos", body="<p>Pristatome per 1–2 darbo dienas su Omniva. Užsakymai iki 14:00 išsiunčiami tą pačią dieną. Pristatymas kainuoja 2,99 €. Jei siunta vėluoja, informuosime el. paštu ir nurodysime naują pristatymo datą.</p>"),
}


def test_policy_finder_follows_footer_link_and_quotes_return_fields():
    out = web_check(None, {"mode": "policy_finder", "site_url": "https://shop.lt/", "topic": "returns"}, fetch_html=fake_fetch(SHOP))
    assert out["policy_url"] == "https://shop.lt/grazinimas"
    assert out["clicks_from_start"] == 1
    assert out["found_via"] == "footer"
    assert out["fields"]["return_window_days"] == 14
    assert out["fields"]["return_shipping_paid_by"] == "shop"
    assert out["fields"]["refund_days"] == 5
    assert "nenaudota" in out["fields"]["conditions"][0]
    assert "Grąžinti galite per 14 dienų nemokamai." in out["quotes"]["return_window_days"]
    assert "return_steps" in out["fields_missing"]


def test_policy_finder_reaches_delivery_page_through_help_and_reads_delay_promise():
    out = web_check(None, {"mode": "policy_finder", "site_url": "https://shop.lt/", "topic": "delivery"}, fetch_html=fake_fetch(SHOP))
    assert out["policy_url"] == "https://shop.lt/info/pristatymas"
    assert out["clicks_from_start"] == 2
    assert out["found_via"] == "faq"
    f = out["fields"]
    assert (f["delivery_days_min"], f["delivery_days_max"]) == (1, 2)
    assert f["carriers"] == ["omniva"]
    assert f["cutoff_time"].startswith("14:00")
    assert f["delay_notice_channel"] == "email"
    assert f["revised_estimate_promised"] is True
    assert "no_delay_notice" not in codes(out)


def test_policy_finder_stops_at_max_pages_and_reports_partial():
    site = {"https://big.lt/": page("Big", body="".join(f'<a href="/p{i}">Pristatymas {i}</a>' for i in range(10)))}
    for i in range(10):
        site[f"https://big.lt/p{i}"] = page(f"Puslapis {i}", body=f"<p>Nieko apie siuntas čia nėra {i}.</p>")
    out = web_check(None, {"mode": "policy_finder", "site_url": "https://big.lt/", "topic": "delivery", "max_pages": 3}, fetch_html=fake_fetch(site))
    assert out["pages_fetched"] == 3
    assert out["policy_url"] is None
    assert out["partial"] is True
    assert "policy_not_found" in codes(out)
    assert any("budget exhausted" in m for m in out["missing"])


def test_policy_finder_without_fetcher_lists_the_start_page_under_missing():
    out = web_check(None, {"mode": "policy_finder", "site_url": "https://shop.lt/", "topic": "returns"})
    assert out["policy_url"] is None
    assert any("https://shop.lt/" in m and "not fetched" in m for m in out["missing"])


# --- listing_check ----------------------------------------------------------


CANON = {"name": "Odontologijos klinika Šypsena", "address": "Savanorių pr. 12, Kaunas", "phone": "+370 37 123456", "hours": "I–V 08:00–18:00", "website": "https://sypsena.lt"}


def test_listing_phone_normalisation():
    assert normalise_phone("+370 37 123456") == normalise_phone("8 37 123456") == normalise_phone("(37) 123 456") == "37037123456"
    assert normalise_phone("00370 37 123456") == "37037123456"
    assert normalise_phone("123") is None


def test_listing_check_marks_phone_mismatch_hours_not_found_and_json_ld_match():
    listing_1588 = page("Šypsena – 1588.lt", body="<p>Odontologijos klinika Šypsena, Savanorių pr. 12, Kaunas. Tel. +370 37 654321. 4,8 / 5 iš 120 atsiliepimų. www.sypsena.lt</p>")
    jsonld = json.dumps({"@type": "Dentist", "name": "Odontologijos klinika Šypsena", "telephone": "8 37 123456", "url": "https://sypsena.lt",
                         "address": {"@type": "PostalAddress", "streetAddress": "Savanorių pr. 12", "addressLocality": "Kaunas"}, "openingHours": "Mo-Fr 08:00-18:00",
                         "aggregateRating": {"ratingValue": "4.9", "reviewCount": "88"}})
    rekvizitai = page("Šypsena – rekvizitai", head=f'<script type="application/ld+json">{jsonld}</script>', body=f"<p>{FILLER_LT}</p>")
    out = web_check(None, {"mode": "listing_check", "canonical": CANON, "listings": [{"html": listing_1588}, "https://rekvizitai.vz.lt/sypsena", "https://maps.google.com/x"]},
                    fetch_html=fake_fetch({"https://rekvizitai.vz.lt/sypsena": rekvizitai, "https://maps.google.com/x": page("Maps", body="<p>Loading</p>")}))
    first, second, third = out["listings"]
    assert first["fields"]["phone"]["status"] == "mismatch"
    assert first["fields"]["phone"]["found"] == "+37037654321"
    assert first["fields"]["hours"]["status"] == "not_found"
    assert first["fields"]["address"]["status"] == "match"
    assert first["fields"]["website"]["status"] == "match"
    assert first["rating"] == 4.8 and first["review_count"] == 120
    assert second["status"] == "consistent"
    assert second["fields"]["phone"]["status"] == "match"
    assert second["fields"]["hours"]["status"] == "match"
    assert second["rating"] == 4.9 and second["review_count"] == 88
    assert third["status"] == "unreachable"
    assert any("browser_snapshot" in m for m in out["missing"])
    assert out["summary_counts"] == {"consistent": 1, "mismatched": 1, "unreachable": 1, "incomplete": 0}
    assert "listing_mismatch" in codes(out)


def test_listing_check_url_without_fetcher_is_unreachable_not_scored():
    out = web_check(None, {"mode": "listing_check", "canonical": CANON, "urls": ["https://www.1588.lt/sypsena"]})
    assert out["listings"][0]["status"] == "unreachable"
    assert any("not fetched" in m for m in out["missing"])


# --- search_intent ----------------------------------------------------------


def test_search_intent_classifies_lt_queries_from_modifiers_rule_only():
    out = web_check(None, {"mode": "search_intent", "queries": ["geriausi žygio batai 2026", "geriausios vietos žygiams Lietuvoje", "žygio batai moterims pirkti", "kalnubatai.lt kontaktai"],
                           "language": "lt", "site": "kalnubatai.lt"})
    rows = {r["query"]: r for r in out["queries"]}
    assert rows["geriausi žygio batai 2026"]["intent"] == "commercial"
    assert rows["geriausi žygio batai 2026"]["best_context"]["kind"] == "product_class"
    assert rows["geriausios vietos žygiams Lietuvoje"]["intent"] == "informational"
    assert rows["geriausios vietos žygiams Lietuvoje"]["best_context"]["kind"] == "place_or_experience"
    assert rows["žygio batai moterims pirkti"]["intent"] == "transactional"
    assert "pirkti" in rows["žygio batai moterims pirkti"]["modifier_evidence"]["transactional"]
    assert rows["kalnubatai.lt kontaktai"]["intent"] == "navigational"
    assert all(r["serp_evidence"] == "unavailable" and r["confidence"] == "low" for r in out["queries"])
    assert len(out["missing"]) == 4


def test_search_intent_uses_serp_shapes_when_a_searcher_is_given():
    serp = {"best hiking boots": [("Best hiking boots 2026: 12 pairs compared", "https://outdoorgear.com/best-hiking-boots"), ("Hiking boots review and comparison", "https://gearlab.com/review"),
                                  ("Top 10 hiking boots vs trail runners", "https://trailmag.com/top"), ("Hiking boots – buy online", "https://shop.com/boots")]}
    out = web_check(None, {"mode": "search_intent", "queries": ["best hiking boots"], "language": "en"}, search=fake_search(serp))
    row = out["queries"][0]
    assert row["intent"] == "commercial"
    assert row["dominant_shape"]["shape"] == "comparison" and row["dominant_shape"]["count"] == 3
    assert row["serp_evidence"] == "available" and row["confidence"] == "high"
    assert "comparison" in row["recommended_page_type"]


# --- site_crawl -------------------------------------------------------------


SITE = {
    "https://shop.example.lt/": page("Dviračių parduotuvė", body='<nav><a href="/kategorijos/dviraciai.html">Dviračiai</a><a href="/kategorijos/salmai.html">Šalmai</a><a href="/apie">Apie</a><a href="/kontaktai">Kontaktai</a><a href="/blog">Blog</a></nav>'),
    "https://shop.example.lt/kategorijos/dviraciai.html": page("Dviračiai", body='<a href="/produktas/trek-fx2.html">Trek FX 2</a><a href="/">Pradžia</a>'),
    "https://shop.example.lt/kategorijos/salmai.html": page("Dviračių šalmai", h1="Šalmai", body="<p>Visi šalmai.</p>"),
    "https://shop.example.lt/produktas/trek-fx2.html": page("Trek FX 2", body="<p>Dviratis.</p>"),
    "https://shop.example.lt/produktas/giro-salmas.html": page("Giro Register šalmas", h1="Giro Register šalmas", body="<p>Šalmas.</p>"),
    "https://shop.example.lt/apie": page("Apie"), "https://shop.example.lt/kontaktai": page("Kontaktai"), "https://shop.example.lt/blog": page("Blog"),
}
IMPORTANT = ["/", "/kategorijos/dviraciai.html", "/kategorijos/salmai.html", "/produktas/trek-fx2.html", "/produktas/giro-salmas.html"]


def test_site_crawl_finds_the_orphan_and_suggests_a_source():
    out = web_check(None, {"mode": "site_crawl", "start_url": "https://shop.example.lt/", "important_urls": IMPORTANT, "max_pages": 50},
                    fetch_html=fake_fetch(SITE), fetch_meta=lambda url: {"status": 200, "final_url": url, "headers": {}})
    assert out["pages_crawled"] == 7 and out["partial"] is False
    assert out["important_total"] == 5
    assert len(out["orphans"]) == 1
    orphan = out["orphans"][0]
    assert orphan["url"] == "https://shop.example.lt/produktas/giro-salmas.html"
    assert orphan["title"] == "Giro Register šalmas" and orphan["exists"] is True
    assert orphan["suggested_sources"][0]["url"] == "https://shop.example.lt/kategorijos/salmai.html"
    assert "šalmas" in orphan["suggested_sources"][0]["shared_words"]
    depth = {r["url"]: r["depth"] for r in out["reached"]}
    assert depth["https://shop.example.lt/produktas/trek-fx2.html"] == 2
    inbound = {r["url"]: r["inbound"] for r in out["reached"]}
    assert inbound["https://shop.example.lt/"] == 1
    assert "orphan_pages" in codes(out)


def test_site_crawl_at_cap_is_partial_and_marks_important_pages_unverified():
    out = web_check(None, {"mode": "site_crawl", "start_url": "https://shop.example.lt/", "important_urls": IMPORTANT, "max_pages": 2}, fetch_html=fake_fetch(SITE))
    assert out["partial"] is True and out["pages_crawled"] == 2
    assert out["orphans"] == []
    assert "https://shop.example.lt/produktas/giro-salmas.html" in out["unverified"]
    assert "partial_crawl" in codes(out)


def test_site_crawl_without_fetcher_or_list_is_honest():
    out = web_check(None, {"mode": "site_crawl", "start_url": "https://shop.example.lt/"})
    assert out["pages_crawled"] == 0 and out["reached"] == []
    assert any("nothing crawled" in m for m in out["missing"])
    out2 = web_check(None, {"mode": "site_crawl", "start_url": "https://shop.example.lt/", "max_pages": 50}, fetch_html=fake_fetch(SITE))
    assert out2["orphans"] is None and "no_important_list" in codes(out2)


# --- keyword_inspect --------------------------------------------------------


def guide(n):
    body = "<table><tr><td>Implantas</td><td>890 €</td></tr><tr><td>Karūnėlė</td><td>450 €</td></tr></table>" + "<h2>Dažniausiai užduodami klausimai</h2>" + f"<p>{' '.join([FILLER_LT] * 10)}</p>"
    return page(f"Dantų implantai – gidas {n}", h1="Dantų implantai", body=body)


def test_keyword_inspect_reports_host_kinds_prices_and_own_page_gaps():
    urls = [f"https://klinikos-tinklas{i}.lt/implantai" for i in range(3)] + ["https://rekvizitai.vz.lt/odontologai", "https://klinika.lt/implantai"]
    pages = {u: guide(i) for i, u in enumerate(urls[:3])}
    pages["https://rekvizitai.vz.lt/odontologai"] = page("Odontologai Kaune – katalogas", body="<p>Sąrašas.</p>")
    pages["https://klinika.lt/implantai"] = page("Implantai", h1="Implantai", body=f"<p>{FILLER_LT}</p>")
    out = web_check(None, {"mode": "keyword_inspect", "keyword": "dantų implantai", "result_urls": urls + ["https://down.lt/x"], "own_url": "https://klinika.lt/implantai", "kd": 58},
                    fetch_html=fake_fetch(pages))
    kinds = {r["url"]: r["host_kind"] for r in out["results"]}
    assert kinds["https://rekvizitai.vz.lt/odontologai"] == "aggregator"
    assert kinds["https://klinika.lt/implantai"] == "own"
    assert kinds["https://klinikos-tinklas0.lt/implantai"] == "clinic"
    assert out["gap"]["results_with_prices"] == 3
    assert out["difficulty"]["strong_results"] == 3
    assert out["own"]["has_price_table"] is False and out["own"]["keyword_in_h1"] is False
    assert any(g.startswith("price table") for g in out["gap"]["element_gaps"])
    assert out["not_measured"] == ["authority", "backlinks"]
    assert out["kd"] == 58
    assert any("https://down.lt/x" in m for m in out["missing"])
    assert any(r["url"] == "https://down.lt/x" and r["fetched"] is False for r in out["results"])


def test_keyword_inspect_without_searcher_or_fetcher_lists_missing():
    out = web_check(None, {"mode": "keyword_inspect", "keyword": "dantų implantai"})
    assert out["results"] == []
    assert any("not searched" in m for m in out["missing"])
    assert out["confidence"] == "low"


# --- link_prospects ---------------------------------------------------------


PROSPECTS = {
    "https://sveikata.lt/miegas-ir-stuburas": page("Miegas ir stuburas: kaip čiužinys veikia laikyseną", h1="Miegas ir stuburas", body='<p>Tikslių duomenų, kiek metų lietuviai naudoja čiužinį, nėra. Daugiau apie <a href="https://patalyne.lt/ciuziniai">čiužinių tipus</a> rašo patalyne.lt.</p><h2>Laikysena naktį</h2>'),
    "https://delfi.lt/verslas/akcijos": page("Savaitės akcijos ir nuolaidos", body='<p>Nuolaidos elektronikai.</p><footer><a href="https://patalyne.lt/">patalyne.lt</a></footer>'),
    "https://mamoslt.lt/kudikio-miegas": page("Kūdikio miegas", body='<p>Kainos svyruoja nuo 200 iki 800 €, bet palyginimų Lietuvoje trūksta, rašo <a href="https://www.patalyne.lt/ciuziniai/vaikams">patalyne.lt</a> apie čiužinį.</p>'),
    "https://kelioniuforumas.lt/thread-99": page("Kelionės į Italiją", body='<p>Diskusija apie viešbučius. <a href="https://booking.com">Booking</a></p>'),
}


def test_link_prospects_ranks_by_relevance_and_quotes_the_citing_sentence():
    candidates = [{"url": "https://delfi.lt/verslas/akcijos", "reach": 78}, {"url": "https://sveikata.lt/miegas-ir-stuburas", "reach": 35}, {"url": "https://mamoslt.lt/kudikio-miegas", "reach": 41},
                  {"url": "https://kelioniuforumas.lt/thread-99", "reach": 30}, {"url": "https://senas-blogas.lt", "reach": 20}]
    out = web_check(None, {"mode": "link_prospects", "target_urls": ["https://patalyne.lt/ciuziniai"], "topic_terms": ["čiužinys", "miegas", "stuburas", "laikysena"], "candidates": candidates},
                    fetch_html=fake_fetch(PROSPECTS))
    order = [r["url"] for r in out["ranked"]]
    assert order == ["https://sveikata.lt/miegas-ir-stuburas", "https://mamoslt.lt/kudikio-miegas", "https://delfi.lt/verslas/akcijos"]
    top = out["ranked"][0]
    assert top["relevance"] >= 0.7
    assert "Tikslių duomenų" in top["cited_context"] or "čiužinių tipus" in top["cited_context"]
    assert top["anchor_text"] == "čiužinių tipus"
    delfi = out["ranked"][2]
    assert delfi["boilerplate"] is True and delfi["cited_context"] is None and delfi["relevance"] <= 0.25
    assert [r["url"] for r in out["no_link"]] == ["https://kelioniuforumas.lt/thread-99"]
    assert [r["url"] for r in out["unverified"]] == ["https://senas-blogas.lt"]
    assert out["summary_counts"] == {"fetched": 4, "linking": 3, "no_link": 1, "failed": 1}
    assert "high_reach_low_relevance" in codes(out)
    all_urls = {r["url"] for r in out["ranked"] + out["no_link"] + out["unverified"]}
    assert all_urls == {c["url"] for c in candidates}


def test_link_prospects_without_candidates_or_fetcher_invents_nothing():
    out = web_check(None, {"mode": "link_prospects", "target_urls": ["https://patalyne.lt"], "topic_terms": ["miegas"]})
    assert out["ranked"] == [] and "no candidates" in out["reason"]
    out2 = web_check(None, {"mode": "link_prospects", "target_urls": ["https://patalyne.lt"], "topic_terms": ["miegas"], "candidates": ["https://sveikata.lt/a"]})
    assert out2["unverified"][0]["status"] == "fetch_failed"
    assert any("not fetched" in m for m in out2["missing"]) and any("reach" in m for m in out2["missing"])


# --- locale_check -----------------------------------------------------------


def test_locale_check_flags_lang_currency_date_number_and_phone_mismatches():
    html = page("Siltumsūknis par 0 € avansu", lang="lt",
                head='<link rel="alternate" hreflang="lt" href="https://x.lt/"><link rel="alternate" hreflang="lv" href="https://x.lt/lv/"><link rel="alternate" hreflang="x-default" href="https://x.lt/">',
                body="<p>Siltumsūknis par 0 € avansu, 1,890.50 € atlaide līdz 2026-12-31. Zvaniet +370 600 12345. Cena 2 500,00 €.</p>")
    out = web_check(None, {"mode": "locale_check", "html": html, "expected_locale": "lv-LV"})
    checks = {m["check"]: m for m in out["mismatches"]}
    assert checks["lang_attr"]["found"] == "lt"
    assert "ymd_dash" in checks["date_format"]["found"]
    assert checks["number_format"]["found"] == ["1,890.50"]
    assert checks["phone_format"]["found"] == ["+370"]
    assert "currency" not in checks
    assert out["hreflang"] == ["lt", "lv", "x-default"] and out["profile"] == "Latvian"
    assert "locale_lang_attr" in codes(out)


def test_locale_check_clean_page_and_missing_fetcher():
    html = page("Žieminiai batai", lang="lt", body=f"<p>Kaina 49,99 €, pristatymas iki 2026-11-20, tel. +370 600 12345. {FILLER_LT}</p>")
    out = web_check(None, {"mode": "locale_check", "html": html, "expected_locale": "lt-LT"})
    assert out["mismatches"] == [] and out["summary"].startswith("no locale mismatches")
    out2 = web_check(None, {"mode": "locale_check", "url": "https://x.lt/lv/", "expected_locale": "lv-LV"})
    assert out2["mismatches"] == [] and any("not fetched" in m for m in out2["missing"])


# --- kit_check --------------------------------------------------------------


def test_kit_check_from_a_press_page_lists_gaps():
    html = page("Kavos Draugas – partneriams", body='<p>Kavos Draugas yra 2015 m. įkurta kavos skrudykla Vilniuje, tiekianti šviežiai skrudintą kavą 40 partnerių parduotuvių visoje Lietuvoje ir siūlanti sezonines kampanijas kiekvienam partneriui.</p>'
                                                    '<a href="/files/logo.svg">Logotipas (SVG)</a><a href="/files/logo.png">Logotipas (PNG)</a>'
                                                    '<img src="/kit/ad1-1080x1080.png" alt="Rudens kampanija"><img src="/kit/story.png">'
                                                    '<p>Kontaktai: rinkodara@kavosdraugas.lt, +370 600 11111</p>')
    out = web_check(None, {"mode": "kit_check", "html": html})
    items = out["items"]
    assert items["logo_vector"]["files"] == ["/files/logo.svg"] and items["logo_raster"]["files"] == ["/files/logo.png"]
    assert items["boilerplate"]["present"] and items["boilerplate"]["within_limit"] is True
    assert items["contact"]["emails"] == ["rinkodara@kavosdraugas.lt"]
    assert items["images_sized"]["without_size"] == ["/kit/story.png"]
    assert items["images_alt"]["without_alt"] == ["/kit/story.png"]
    assert set(out["gaps"]) == {"images_sized", "images_alt"}


def test_kit_check_from_a_files_list_and_long_boilerplate():
    files = ["kit/ad1-1080x1080.png", "kit/ad1-1080x1920.png", {"path": "kit/fb.png", "width": 1200, "height": 628, "alt": "FB"}, "kit/INSTRUKCIJA.md"]
    out = web_check(None, {"mode": "kit_check", "files": files, "boilerplate": " ".join(["žodis"] * 130), "contact": "info@kavosdraugas.lt"})
    assert "logo_vector" in out["gaps"] and "logo_raster" in out["gaps"]
    assert "boilerplate_too_long" in out["gaps"] and out["items"]["boilerplate"]["words"] == 130
    assert out["items"]["images_sized"]["present"] is True
    assert "images_alt" in out["gaps"]
    assert "kit_missing_logo_vector" in codes(out)
    with pytest.raises(ToolError, match="give the kit page"):
        web_check(None, {"mode": "kit_check"})


# --- email_auth -------------------------------------------------------------


RAW_EMAIL = """Delivered-To: me@example.com
Return-Path: <bounce-123@mlsend.com>
Authentication-Results: mx.google.com;
       dkim=pass header.i=@news.kavosnamai.lt header.s=ml header.b=abc123;
       spf=pass (google.com: domain of bounce-123@mlsend.com designates 1.2.3.4 as permitted sender) smtp.mailfrom=bounce-123@mlsend.com;
       dmarc=fail (p=NONE sp=NONE dis=NONE) header.from=news.kavosnamai.lt
Received-SPF: pass (google.com: domain of bounce-123@mlsend.com designates 1.2.3.4 as permitted sender) client-ip=1.2.3.4;
DKIM-Signature: v=1; a=rsa-sha256; c=relaxed/relaxed; d=news.kavosnamai.lt; s=ml;
 h=From:To:Subject; bh=xyz=; b=signature==
From: Kavos Namai <naujienos@news.kavosnamai.lt>
To: me@example.com
Subject: Rudens kava

Labas,
"""


def test_email_auth_parses_headers_and_reports_dns_under_missing():
    out = web_check(None, {"mode": "email_auth", "headers_text": RAW_EMAIL})
    assert out["from_domain"] == "news.kavosnamai.lt"
    assert out["spf"]["result"] == "pass" and out["spf"]["domain"] == "mlsend.com"
    assert out["dkim"]["result"] == "pass" and out["dkim"]["domains"] == ["news.kavosnamai.lt"] and out["dkim"]["selectors"] == ["ml"]
    assert out["dmarc"]["result"] == "fail" and out["dmarc"]["policy"] == "none"
    assert out["alignment"] == {"spf": False, "dkim": True}
    assert out["overall"] == "fail"
    assert any("_dmarc.news.kavosnamai.lt" in m for m in out["missing"])
    assert out["dns"]["status"] == "not_looked_up"
    assert "email_auth_fail" in codes(out)


def test_email_auth_with_only_a_signature_is_weak_and_domain_only_is_unknown():
    partial = "From: a@shop.lt\nDKIM-Signature: v=1; a=rsa-sha256; d=shop.lt; s=k1; bh=x; b=y\nSubject: hi\n\nbody"
    out = web_check(None, {"mode": "email_auth", "headers_text": partial})
    assert out["dkim"]["result"] == "signed_not_verified" and out["overall"] == "weak"
    out2 = web_check(None, {"mode": "email_auth", "domain": "news.kavosnamai.lt", "dkim_selectors": ["ml"]})
    assert out2["overall"] == "unknown"
    assert any("selectors ml" in m for m in out2["missing"])
    with pytest.raises(ToolError, match="headers_text"):
        web_check(None, {"mode": "email_auth"})


# --- search_region ----------------------------------------------------------


def test_search_region_reports_missing_region_support_and_builds_variants():
    serp = {"CBD aliejus site:.lv": [("CBD eļļa – veikals", "https://cbd.lv/shop")], "CBD eļļa site:.lv": [("CBD eļļa atsauksmes", "https://veselam.lv/cbd")]}
    out = web_check(None, {"mode": "search_region", "query": "CBD aliejus", "region": "lv-lv", "local_terms": ["CBD eļļa"]}, search=fake_search(serp))
    assert out["region_supported"] is False and out["tld"] == "lv"
    assert any("no region parameter" in m for m in out["missing"])
    assert [v["query"] for v in out["variants"]] == ["CBD aliejus", "CBD aliejus site:.lv", "CBD eļļa", "CBD eļļa site:.lv"]
    assert out["local_hosts"] == {"cbd.lv": 1, "veselam.lv": 1}
    out2 = web_check(None, {"mode": "search_region", "query": "CBD aliejus", "region": "pl-pl"})
    assert all(v["status"] == "search_unavailable" for v in out2["variants"])
    with pytest.raises(ToolError, match="unsupported region"):
        web_check(None, {"mode": "search_region", "query": "x", "region": "mars"})



def test_dispatch_routes_web_check(tmp_path):
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    out = _json.loads(dispatch(WorkspaceTools(tmp_path), "web_check", {"mode": "search_intent", "queries": ["pigūs batai internetu"], "fetch_serp": False}))
    assert out["mode"] == "search_intent" if "mode" in out else True
    assert "web_check" in [t["function"]["name"] for t in worker_tools(False)]
