import json

import pytest

from rocky.seo import MODES, seo_inspect, seo_inspect_tool
from rocky.tools import ToolError, WorkspaceTools

FILLER = " ".join(["Mes parduodame batus, striukes ir kuprines su pristatymu per dvi dienas visoje Lietuvoje."] * 20)
FILLER_EN = " ".join(["We sell boots, jackets and backpacks with delivery in two days across the whole country."] * 20)


def page(title="Žieminiai batai vyrams | Batuva", meta="Žieminiai batai vyrams nuo €49, pristatymas per 2 dienas.", h1="Žieminiai batai vyrams",
         body=None, price="€49", canonical=None, robots=None, jsonld=None, extra_head="", images='<img src="a.jpg" alt="Batai">', lang="lt"):
    body = body if body is not None else f"<p>Tik {price} už porą. {FILLER}</p>"
    head = ""
    if title is not None:
        head += f"<title>{title}</title>"
    if meta is not None:
        head += f'<meta name="description" content="{meta}">'
    if canonical:
        head += f'<link rel="canonical" href="{canonical}">'
    if robots:
        head += f'<meta name="robots" content="{robots}">'
    if jsonld is not None:
        head += f'<script type="application/ld+json">{jsonld}</script>'
    head += extra_head
    h1_html = f"<h1>{h1}</h1>" if h1 else ""
    return f'<!doctype html><html lang="{lang}"><head>{head}</head><body><main>{h1_html}{images}{body}</main></body></html>'


def codes(out):
    return [f["code"] for f in out["findings"]]


def by_code(out, code):
    hits = [f for f in out["findings"] if f["code"] == code]
    return hits[0] if hits else None


def fake_search(mapping):
    def search(query):
        results = mapping.get(query, [])
        if isinstance(results, str):
            return results
        return "\n".join(f"- {title}\n  {url}" for title, url in results)
    return search


# --- dispatch and errors ----------------------------------------------------


def test_unknown_mode_and_missing_mode_fail_in_plain_words():
    with pytest.raises(ToolError, match="mode is required"):
        seo_inspect(None, {})
    with pytest.raises(ToolError, match="unknown mode"):
        seo_inspect(None, {"mode": "magic"})


def test_tool_wrapper_returns_json_with_the_common_shape():
    text = seo_inspect_tool(None, {"mode": "snippet", "pages": [{"html": page()}]})
    data = json.loads(text)
    assert data["mode"] == "snippet"
    for key in ("findings", "counts", "missing", "warnings", "summary"):
        assert key in data
    assert data["summary"].endswith(".")


def test_url_without_fetcher_lands_in_missing_not_in_an_error():
    out = seo_inspect(None, {"mode": "snippet", "pages": [{"url": "https://shop.lt/batai"}, {"html": page()}]})
    assert any("https://shop.lt/batai" in m and "not fetched" in m for m in out["missing"])
    assert len(out["pages"]) == 1


def test_all_modes_are_listed():
    assert set(MODES) == {"snippet", "urls", "crawl_rules", "structured_data", "crawler_content", "templates", "serp", "keywords"}


# --- snippet ----------------------------------------------------------------


def test_snippet_clean_page_has_no_blocks_or_warns():
    out = seo_inspect(None, {"mode": "snippet", "pages": [{"html": page()}], "brand": "Batuva"})
    assert out["counts"]["block"] == 0 and out["counts"]["warn"] == 0
    row = out["pages"][0]
    assert row["title_chars"] == len("Žieminiai batai vyrams | Batuva") and not row["title_over_60"]
    assert row["brand_in_title"] and not row["meta_missing"]
    assert by_code(out, "snippet_promise_kept")
    assert "0 titles over 60" in out["summary"]


def test_snippet_long_title_brand_only_title_and_missing_meta_fire():
    long_title = "Pirkite žieminius batus vyrams internetu pigiau su nemokamu pristatymu visoje Lietuvoje šiandien"
    out = seo_inspect(None, {"mode": "snippet", "pages": [{"html": page(title=long_title, meta=None)}, {"html": page(title="Batuva")}], "brand": "Batuva"})
    assert by_code(out, "title_over_60")["value"] == long_title
    assert by_code(out, "meta_missing")
    f = by_code(out, "title_not_descriptive")
    assert f and f["value"] == "Batuva"
    assert out["pages"][1]["title_words"] == []


def test_snippet_duplicates_across_pages_are_reported_once_with_both_sources(tmp_path):
    (tmp_path / "a.html").write_text(page(), encoding="utf-8")
    (tmp_path / "b.html").write_text(page(h1="Kiti batai"), encoding="utf-8")
    tools = WorkspaceTools(tmp_path)
    out = seo_inspect(tools, {"mode": "snippet", "pages": [{"path": "a.html"}, {"path": "b.html"}]})
    dup = by_code(out, "duplicate_title")
    assert dup and "a.html" in dup["where"] and "b.html" in dup["where"]
    assert by_code(out, "duplicate_meta")
    assert out["pages"][0]["duplicate_title_of"] == ["b.html"]
    assert "2 with a duplicate title" in out["summary"]


def test_snippet_promise_missing_and_price_mismatch():
    html = page(title="Nemokamas pristatymas ir grąžinimas | Batuva", meta="Striukės nuo €19 su dovana.", h1="Batai", body=f"<p>Batai už €49. {FILLER}</p>")
    out = seo_inspect(None, {"mode": "snippet", "pages": [{"html": html}], "brand": "Batuva"})
    miss = by_code(out, "snippet_promise_missing")
    assert miss and "nemokamas" in miss["value"]
    price = by_code(out, "snippet_price_mismatch")
    assert price["level"] == "block" and price["value"] == ["€19"]
    assert out["findings"][0]["level"] == "block"


def test_snippet_competing_titles_via_search_and_missing_without_it():
    search = fake_search({"žieminiai batai": [("Žieminiai batai vyrams - Pigu.lt", "https://pigu.lt/batai"), ("Žieminiai batai | Varle", "https://varle.lt/batai")]})
    out = seo_inspect(None, {"mode": "snippet", "pages": [{"html": page(title="Žieminiai batai vyrams | Batuva")}], "brand": "Batuva", "query": "žieminiai batai"}, search=search)
    assert len(out["competing"]) == 2 and out["competing"][0]["domain"] == "pigu.lt"
    assert by_code(out, "title_undifferentiated")
    assert "2 competing results" in out["summary"]
    out2 = seo_inspect(None, {"mode": "snippet", "pages": [{"html": page()}], "query": "žieminiai batai"})
    assert any("no search" in m for m in out2["missing"])


def test_snippet_without_title_blocks():
    out = seo_inspect(None, {"mode": "snippet", "pages": [{"html": page(title=None)}]})
    assert by_code(out, "title_missing")["level"] == "block"


# --- urls -------------------------------------------------------------------


def test_urls_readability_flags_ids_sessions_and_upper_case_and_passes_clean_paths():
    out = seo_inspect(None, {"mode": "urls", "urls": [
        "https://shop.lt/batai/zieminiai-batai-vyrams",
        "https://shop.lt/Product.php?id=4839201&PHPSESSID=abc&utm_source=fb",
    ]})
    clean, dirty = out["urls"]
    assert clean["readable"] and clean["path_words"] == ["batai", "zieminiai", "batai", "vyrams"]
    assert not dirty["readable"]
    assert {"numeric_id", "tracking_params", "upper_case", "many_params", "script_extension"} <= set(dirty["issues"])
    assert by_code(out, "url_numeric_id")["level"] == "warn"
    assert "1 not readable" in out["summary"]
    assert any("fetch_meta" in m for m in out["missing"])


def test_urls_canonical_groups_consistent_and_inconsistent():
    target = "https://shop.lt/batai/zieminiai"
    html_by_url = {
        target: page(canonical=target),
        target + "?sort=price": page(canonical=target),
        target + "?utm_source=fb": page(canonical=target),
        "https://shop.lt/kuprines": page(canonical="https://shop.lt/kuprines"),
        "https://shop.lt/kuprines?sort=new": page(),
    }
    out = seo_inspect(None, {"mode": "urls", "urls": list(html_by_url), "html_by_url": html_by_url})
    groups = {g["canonical_target"]: g for g in out["groups"]}
    assert groups["shop.lt/batai/zieminiai"]["consistent"] and len(groups["shop.lt/batai/zieminiai"]["members"]) == 3
    bad = groups["shop.lt/kuprines"]
    assert not bad["consistent"] and "https://shop.lt/kuprines?sort=new" in bad["members"]
    inc = by_code(out, "canonical_inconsistent")
    assert inc["level"] == "block" and "1 without a canonical" in inc["note"]
    assert by_code(out, "canonical_missing")["where"] == "https://shop.lt/kuprines?sort=new"
    assert by_code(out, "canonical_consistent")
    assert "1 without a consistent canonical" in out["summary"]


def test_urls_noindex_and_canonical_elsewhere_and_redirect_chain_via_fetch_meta():
    fetched = []

    def fetch_html(url):
        fetched.append(url)
        return page(canonical="https://shop.lt/batai", robots="noindex, follow")

    def fetch_meta(url):
        return {"status": 301, "final_url": "https://shop.lt/batai", "headers": {"Link": '<https://shop.lt/batai>; rel="canonical"'}}

    out = seo_inspect(None, {"mode": "urls", "urls": ["http://old.shop.lt/batai-senas"]}, fetch_html=fetch_html, fetch_meta=fetch_meta)
    row = out["urls"][0]
    assert row["robots_noindex"] is True and row["canonical_points_elsewhere"] is True
    assert row["canonical_source"] == "header" and row["redirect_chain"][-1]["url"] == "https://shop.lt/batai"
    assert by_code(out, "noindex") and by_code(out, "redirects") and by_code(out, "canonical_elsewhere")
    assert fetched == ["http://old.shop.lt/batai-senas"]
    assert out["missing"] == []


def test_urls_from_text_file_and_no_urls_error(tmp_path):
    (tmp_path / "urls.txt").write_text("https://shop.lt/a\nnot a url\nhttps://shop.lt/b\n", encoding="utf-8")
    out = seo_inspect(WorkspaceTools(tmp_path), {"mode": "urls", "path": "urls.txt"})
    assert [u["url"] for u in out["urls"]] == ["https://shop.lt/a", "https://shop.lt/b"]
    with pytest.raises(ToolError, match="no urls"):
        seo_inspect(None, {"mode": "urls"})


# --- crawl_rules ------------------------------------------------------------

ROBOTS = """# live store
User-agent: Googlebot
Disallow: /batai/
Allow: /batai/zieminiai$

User-agent: *
Disallow: /admin/

Sitemap: https://shop.lt/sitemap_index.xml
"""

SITEMAP_INDEX = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://shop.lt/sitemap-products.xml</loc></sitemap>
<sitemap><loc>https://shop.lt/sitemap-pages.xml</loc></sitemap></sitemapindex>"""
SITEMAP_PRODUCTS = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://shop.lt/batai/zieminiai</loc></url><url><loc>https://shop.lt/batai/vasariniai</loc></url></urlset>"""
SITEMAP_PAGES = """<urlset><url><loc>https://shop.lt/kontaktai</loc></url></urlset>"""


def site_fetch(files):
    def fetch_html(url):
        if url in files:
            return files[url]
        raise RuntimeError("404 Not Found")
    return fetch_html


def test_crawl_rules_blocks_for_googlebot_but_not_for_star_and_recurses_sitemap_index():
    files = {
        "https://shop.lt/robots.txt": ROBOTS,
        "https://shop.lt/sitemap_index.xml": SITEMAP_INDEX,
        "https://shop.lt/sitemap-products.xml": SITEMAP_PRODUCTS,
        "https://shop.lt/sitemap-pages.xml": SITEMAP_PAGES,
    }
    out = seo_inspect(None, {"mode": "crawl_rules", "site": "https://shop.lt", "important_urls": ["https://shop.lt/batai/vasariniai", "/batai/zieminiai", "https://shop.lt/kuprines"]}, fetch_html=site_fetch(files))
    checked = {c["url"]: c["by_agent"] for c in out["important_urls"]}
    vasara = checked["https://shop.lt/batai/vasariniai"]
    assert vasara["Googlebot"]["allowed"] is False and vasara["Googlebot"]["rule"] == "Disallow: /batai/"
    assert vasara["*"]["allowed"] is True
    assert checked["https://shop.lt/batai/zieminiai"]["Googlebot"]["allowed"] is True  # longest match, Allow with $
    blocked = by_code(out, "url_blocked")
    assert blocked["level"] == "block" and blocked["where"] == "https://shop.lt/batai/vasariniai"
    assert out["sitemaps"]["url_count"] == 3
    kinds = {f["url"]: f["kind"] for f in out["sitemaps"]["files"]}
    assert kinds["https://shop.lt/sitemap_index.xml"] == "index" and kinds["https://shop.lt/sitemap-pages.xml"] == "urlset"
    assert out["sitemaps"]["not_in_sitemap"] == ["https://shop.lt/kuprines"]
    assert by_code(out, "not_in_sitemap")["where"] == "https://shop.lt/kuprines"
    assert out["sitemaps"]["blocked_count"] == 1 and out["sitemaps"]["blocked_by_robots"] == ["https://shop.lt/batai/vasariniai"]
    assert by_code(out, "sitemap_urls_blocked")
    assert "1 of 3 important urls blocked for Googlebot" in out["summary"] and "3 urls in 3 sitemap files" in out["summary"]


def test_crawl_rules_clean_site_has_no_blocks():
    files = {"https://shop.lt/robots.txt": "User-agent: *\nDisallow: /admin/\nSitemap: https://shop.lt/sitemap.xml\n", "https://shop.lt/sitemap.xml": SITEMAP_PRODUCTS}
    out = seo_inspect(None, {"mode": "crawl_rules", "site": "shop.lt", "important_urls": ["https://shop.lt/batai/zieminiai"]}, fetch_html=site_fetch(files))
    assert out["counts"]["block"] == 0 and out["counts"]["warn"] == 0
    assert by_code(out, "url_allowed")
    assert out["sitemaps"]["not_in_sitemap"] == []


def test_crawl_rules_staging_disallow_all_blocks_and_missing_sitemap_warns():
    files = {"https://shop.lt/robots.txt": "User-agent: *\nDisallow: /\n"}
    out = seo_inspect(None, {"mode": "crawl_rules", "site": "https://shop.lt", "important_urls": ["https://shop.lt/batai"], "staging_hosts": ["staging.shop.lt"]}, fetch_html=site_fetch(files))
    assert by_code(out, "site_blocked")["level"] == "block"
    assert by_code(out, "url_blocked")
    assert by_code(out, "sitemap_missing")["level"] == "warn"
    assert "no sitemap readable" in out["summary"]


def test_crawl_rules_without_fetcher_reports_missing_and_requires_site():
    out = seo_inspect(None, {"mode": "crawl_rules", "site": "https://shop.lt", "important_urls": ["https://shop.lt/x"]})
    assert any("robots.txt" in m for m in out["missing"])
    assert out["important_urls"][0]["by_agent"]["Googlebot"]["allowed"] is None
    with pytest.raises(ToolError, match="site is required"):
        seo_inspect(None, {"mode": "crawl_rules"})


def test_crawl_rules_sitemap_depth_cap_and_invalid_xml():
    files = {
        "https://shop.lt/robots.txt": "Sitemap: https://shop.lt/i1.xml\n",
        "https://shop.lt/i1.xml": '<sitemapindex><sitemap><loc>https://shop.lt/i2.xml</loc></sitemap></sitemapindex>',
        "https://shop.lt/i2.xml": '<sitemapindex><sitemap><loc>https://shop.lt/i3.xml</loc></sitemap></sitemapindex>',
        "https://shop.lt/i3.xml": '<sitemapindex><sitemap><loc>https://shop.lt/deep.xml</loc></sitemap></sitemapindex>',
        "https://shop.lt/deep.xml": SITEMAP_PRODUCTS,
        "https://shop.lt/bad.xml": "<<not xml",
    }
    out = seo_inspect(None, {"mode": "crawl_rules", "site": "https://shop.lt", "sitemap_url": "https://shop.lt/bad.xml"}, fetch_html=site_fetch(files))
    assert any("deeper than 3 levels" in w for w in out["warnings"])
    assert any("not valid XML" in w for w in out["warnings"])
    assert out["sitemaps"]["url_count"] == 0


# --- structured_data --------------------------------------------------------

PRODUCT_LD = '{"@context": "https://schema.org", "@type": "Product", "name": "Žieminiai batai", "offers": {"@type": "Offer", "price": "%s", "priceCurrency": "EUR", "availability": "https://schema.org/InStock"}, "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.8", "reviewCount": "132"}}'


def test_structured_data_price_mismatch_blocks_and_hidden_rating_warns():
    html = page(jsonld=PRODUCT_LD % "39.00", price="€49")
    out = seo_inspect(None, {"mode": "structured_data", "html": html})
    assert out["blocks"][0]["type"] == "Product" and out["blocks"][0]["fields"]["price"] == 39.0
    f = by_code(out, "schema_price_mismatch")
    assert f["level"] == "block" and "39" in f["note"] and "€49" in f["note"]
    assert by_code(out, "schema_rating_not_visible")
    assert by_code(out, "schema_review_count_not_visible")
    assert "disagree" in out["summary"]


def test_structured_data_matching_page_is_clean():
    html = page(jsonld=PRODUCT_LD % "49", price="€49", body=f"<p>Tik €49. Įvertinimas 4,8 iš 5 (132 atsiliepimai). Turime sandėlyje. {FILLER}</p>")
    out = seo_inspect(None, {"mode": "structured_data", "html": html})
    assert out["counts"]["block"] == 0 and out["counts"]["warn"] == 0
    assert by_code(out, "schema_price_match") and by_code(out, "schema_rating_visible")
    assert "0 field(s) disagree" in out["summary"]


def test_structured_data_missing_fields_invalid_json_and_no_markup():
    html = page(jsonld='{"@type": "Product", "name": "Batai"}')
    out = seo_inspect(None, {"mode": "structured_data", "html": html})
    missing_fields = [f["value"] for f in out["findings"] if f["code"] == "schema_field_missing"]
    assert set(missing_fields) == {"price", "availability"}
    out2 = seo_inspect(None, {"mode": "structured_data", "html": page(jsonld='{"@type": "Product", ')})
    assert by_code(out2, "schema_invalid_json")["level"] == "block"
    out3 = seo_inspect(None, {"mode": "structured_data", "html": page()})
    assert by_code(out3, "no_structured_data")["level"] == "warn"


def test_structured_data_schema_argument_and_graph_and_availability_mismatch():
    schema = {"@graph": [{"@type": "Organization", "name": "Batuva"}, {"@type": "Product", "name": "Batai", "offers": [{"price": 49, "availability": "OutOfStock"}]}]}
    html = page(price="€49", body=f"<p>€49. Turime sandėlyje. {FILLER}</p>")
    out = seo_inspect(None, {"mode": "structured_data", "html": html, "schema": schema})
    assert [b["type"] for b in out["blocks"]] == ["Organization", "Product"]
    assert by_code(out, "schema_availability_mismatch")
    assert by_code(out, "schema_price_match")
    out2 = seo_inspect(None, {"mode": "structured_data", "schema": json.dumps(schema)})
    assert out2["source"] is None and len(out2["blocks"]) == 2 and out2["page_prices"] == []


# --- crawler_content --------------------------------------------------------


def test_crawler_content_rendered_only_fact_blocks_and_html_fact_passes():
    html = page(body=f"<p>Svoris 1,2 kg. {FILLER}</p>", images='<img src="specs.png"><img src="b.png" alt="Batai">')
    rendered = "Žieminiai batai vyrams Svoris 1,2 kg Garantija 24 mėn. Tik €49 " + FILLER
    out = seo_inspect(None, {"mode": "crawler_content", "html": html, "rendered_text": rendered, "required_facts": ["Svoris 1,2 kg", "Garantija 24 mėn.", "Pristatymas per 1 dieną"]})
    facts = {f["fact"]: f for f in out["facts"]}
    assert facts["Svoris 1,2 kg"]["in_html"] is True
    assert facts["Garantija 24 mėn."] == {"fact": "Garantija 24 mėn.", "in_html": False, "in_rendered": True}
    assert by_code(out, "fact_only_rendered")["level"] == "block"
    assert by_code(out, "fact_missing")["value"] == "Pristatymas per 1 dieną"
    assert out["images_without_alt"] == 1 and by_code(out, "images_without_alt")["value"] == ["specs.png"]
    assert out["headings"][0] == {"level": 1, "text": "Žieminiai batai vyrams"}
    assert out["html_text_chars"] > 0 and out["rendered_text_chars"] == len(rendered)
    assert "1 of 3 required facts in the HTML, 1 only after rendering" in out["summary"]


def test_crawler_content_clean_page_without_rendered_text_asks_for_it():
    out = seo_inspect(None, {"mode": "crawler_content", "html": page(), "required_facts": ["€49"]})
    assert out["counts"]["block"] == 0 and out["counts"]["warn"] == 0
    assert by_code(out, "fact_in_html")
    assert any("rendered_text" in m for m in out["missing"])


def test_crawler_content_shell_page_vs_large_rendered_text_warns():
    shell = '<html><head><title>App</title></head><body><div id="root"></div></body></html>'
    out = seo_inspect(None, {"mode": "crawler_content", "html": shell, "rendered_text": FILLER_EN})
    assert by_code(out, "rendered_much_larger")["level"] == "warn"
    assert by_code(out, "thin_html_text") and by_code(out, "no_h1")


# --- templates --------------------------------------------------------------


def test_templates_duplicate_template_fires_and_rich_page_does_not():
    supplier = "Šie batai pagaminti iš natūralios odos, tinka šaltam orui ir yra patogūs kasdien nešioti. " * 15
    pages = [
        {"html": page(title="Batai A", h1="Batai A", body=f"<p>{supplier}</p>", images="")},
        {"html": page(title="Batai B", h1="Batai B", body=f"<p>{supplier}</p>", images="")},
        {"html": page(title="Batai C", h1="Batai C", body=f"<p>€59. Turime sandėlyje, pristatymas per 2 dienas. Atsiliepimai: 4,7 iš 5. Svoris 900 g, medžiaga oda. Vilnius, Gedimino pr. 1. "
                      + " ".join(f"Unikalus sakinys numeris {i} apie šį konkretų modelį ir jo savybes." for i in range(40)) + "</p>")},
    ]
    out = seo_inspect(None, {"mode": "templates", "pages": pages})
    rows = {r["title"]: r for r in out["pages"]}
    assert rows["Batai A"]["unique_ratio"] < 0.2 and rows["Batai B"]["unique_ratio"] < 0.2
    assert rows["Batai C"]["unique_ratio"] > 0.9
    assert rows["Batai C"]["decision_fields_missing"] == []
    assert len(rows["Batai A"]["decision_fields_missing"]) >= 3
    dup = [f for f in out["findings"] if f["code"] == "duplicate_template"]
    assert len(dup) == 2 and dup[0]["level"] == "block"
    assert by_code(out, "decision_fields_missing")
    assert "3 template pages" in out["summary"] and "2 under 50%" in out["summary"]


def test_templates_needs_three_to_ten_pages():
    with pytest.raises(ToolError, match="3–10"):
        seo_inspect(None, {"mode": "templates", "pages": [{"html": page()}, {"html": page()}]})


# --- serp -------------------------------------------------------------------


def test_serp_rank_of_owned_domain_competitors_and_ambiguous_intent():
    search = fake_search({
        "žieminiai batai kaina": [
            ("Žieminiai batai kaina nuo 30 € - Pigu.lt", "https://pigu.lt/batai"),
            ("Žieminiai batai - pirkti internetu | Batuva", "https://www.batuva.lt/batai"),
            ("Batai pigiau - akcija", "https://varle.lt/akcija"),
        ],
        "batai be klijų": [
            ("Kaip klijuoti batus namuose - gidas", "https://blog.lt/kaip"),
            ("Batai be klijų kaina", "https://pigu.lt/x"),
            ("Batų taisykla Vilniuje, adresas", "https://taisykla.lt"),
            ("Batai be klijų - kas tai", "https://wiki.lt/batai"),
        ],
        "batuva atsiliepimai": "no results",
    })
    out = seo_inspect(None, {"mode": "serp", "queries": ["žieminiai batai kaina", "batai be klijų", "batuva atsiliepimai"], "owned_domains": ["batuva.lt"], "competitors": ["pigu.lt", "varle.lt"]}, search=search)
    q1, q2, q3 = out["queries"]
    assert q1["owned_rank"] == 2 and q1["competitor_ranks"] == {"pigu.lt": 1, "varle.lt": 3}
    assert q1["dominant_intent"] == "price" and q1["ambiguous_modifier"] is False
    assert q2["owned_rank"] is None and q2["ambiguous_modifier"] is True
    assert q2["dominant_share"] < 0.6 and set(q2["intent_split"]) >= {"how_to", "price", "local"}
    assert by_code(out, "ambiguous_modifier")["where"] == "batai be klijų"
    assert by_code(out, "owned_absent")["where"] == "batai be klijų"
    assert by_code(out, "competitor_ahead")["value"] == {"pigu.lt": 1}
    assert q3["results"] == [] and by_code(out, "no_results")
    assert "owned domain in the top 8 for 1 of 2 searched" in out["summary"] and "best position 2" in out["summary"]


def test_serp_generates_the_query_ladder_and_reports_missing_without_search():
    out = seo_inspect(None, {"mode": "serp", "brand_domain": "batuva.lt", "product": "Batuva Arctic", "category": "žieminiai batai", "lang": "lt"})
    stages = [q["stage"] for q in out["queries"]]
    assert stages.count("category") == 3 and stages.count("price_specs") == 3 and len(out["queries"]) == 12
    assert {"query": "Batuva Arctic kaina", "stage": "price_specs"}.items() <= out["queries"][9].items()
    assert out["owned_domains"] == ["batuva.lt"]
    assert len(out["missing"]) == 12 and "no search" in out["missing"][0]
    assert "nothing searched" in out["summary"]
    with pytest.raises(ToolError, match="needs queries"):
        seo_inspect(None, {"mode": "serp"})


def test_serp_clear_intent_query_is_not_ambiguous():
    search = fake_search({"batai kaina": [("Batai kaina - Pigu", "https://pigu.lt"), ("Batų kainos | Varle", "https://varle.lt"), ("Batai pigiau €20", "https://a.lt")]})
    out = seo_inspect(None, {"mode": "serp", "queries": ["batai kaina"]}, search=search)
    q = out["queries"][0]
    assert q["dominant_intent"] == "price" and q["dominant_share"] == 1.0 and not q["ambiguous_modifier"]
    assert by_code(out, "ambiguous_modifier") is None


# --- keywords ---------------------------------------------------------------

TERMS_CSV = """Search term,Ad group,Clicks,Cost,Conversions,Conv. value
žieminiai batai vyrams,Batai,120,"€96,00",6,540
žieminiai batai moterims,Batai,80,64.00,4,360
batai žieminiai pigiai,Batai,30,24.00,1,90
kuprinės mokyklai,Batai,25,"20,00",0,0
kuprinė mokyklai pigiai,Batai,10,8.00,0,0
batų taisykla vilnius,Batai,70,60.00,0,0
batų taisykla kaunas,Batai,35,30.00,0,0
batuva parduotuvė,Brand,50,10.00,8,700
striukės žiemai,Striukės,60,48.00,3,270
striukė žiemai vyrams,Striukės,40,32.00,2,180
nemokami batai,Striukės,15,12.00,0,0
Total: all search terms,,490,361,24,2140
"""


def test_keywords_clusters_split_candidates_and_negatives_with_saving(tmp_path):
    (tmp_path / "terms.csv").write_text(TERMS_CSV, encoding="utf-8")
    out = seo_inspect(WorkspaceTools(tmp_path), {"mode": "keywords", "path": "terms.csv", "brand_terms": ["batuva"], "protect_terms": ["nemokam"]})
    assert out["columns"]["term"] == "Search term" and out["columns"]["cost"] == "Cost" and out["columns"]["group"] == "Ad group"
    assert out["term_count"] == 11 and any("skipped" in w for w in out["warnings"])
    by_label = {c["label"]: c for c in out["clusters"]}
    batai = next(c for c in out["clusters"] if "žieminiai batai vyrams" in c["terms"])
    assert {"žieminiai batai moterims", "batai žieminiai pigiai"} <= set(batai["terms"])
    assert batai["cost"] == 184.0 and batai["conversions"] == 11 and batai["cpa"] == round(184 / 11, 2)
    taisykla = next(c for c in out["clusters"] if "batų taisykla vilnius" in c["terms"])
    assert set(taisykla["terms"]) == {"batų taisykla vilnius", "batų taisykla kaunas"} and taisykla["conversions"] == 0
    brand = next(c for c in out["clusters"] if "batuva parduotuvė" in c["terms"])
    assert brand["brand"] is True
    group = next(g for g in out["groups"] if g["name"] == "Batai")
    assert group["mixed"] is True
    assert by_code(out, "group_mixed")["where"] == "Batai"
    kup = next(s for s in out["split_candidates"] if "kuprin" in s["cluster"])
    assert kup["group"] == "Batai" and kup["share"] < 0.2
    negatives = {n["term"]: n for n in out["negatives"]}
    assert "nemokami batai" not in negatives  # protected
    assert "batuva parduotuvė" not in negatives  # brand, converting
    assert set(negatives) == {"kuprinės mokyklai", "batų taisykla vilnius", "batų taisykla kaunas"}
    assert negatives["batų taisykla vilnius"]["match"] == "phrase" and negatives["batų taisykla vilnius"]["negative"] == "batų taisykla"
    assert negatives["kuprinės mokyklai"]["saving"] == 20.0
    assert out["estimated_saving"] == 110.0
    assert by_code(out, "negatives_found")["level"] == "warn"
    assert "3 negative candidates would save 110.00" in out["summary"]
    assert by_label  # clusters carry labels


def test_keywords_clean_export_has_no_negatives_and_min_cost_is_respected(tmp_path):
    csv_text = "Paieškos terminas;Paspaudimai;Kaina;Konversijos\nbatai vyrams;10;5,00;0\nbatai moterims;20;30,00;2\n"
    (tmp_path / "t.csv").write_text(csv_text, encoding="utf-8")
    out = seo_inspect(WorkspaceTools(tmp_path), {"mode": "keywords", "path": "t.csv"})
    assert out["columns"] == {"term": "Paieškos terminas", "clicks": "Paspaudimai", "cost": "Kaina", "conversions": "Konversijos", "value": None, "group": None}
    assert out["negatives"] == [] and by_code(out, "no_negatives")
    assert out["groups"] == []
    out2 = seo_inspect(WorkspaceTools(tmp_path), {"mode": "keywords", "path": "t.csv", "min_cost": 5})
    assert [n["term"] for n in out2["negatives"]] == ["batai vyrams"]
    assert out2["negatives"][0]["match"] == "exact"  # a converting sibling shares the cluster


def test_keywords_errors_for_missing_file_and_unknown_term_column(tmp_path):
    tools = WorkspaceTools(tmp_path)
    with pytest.raises(ToolError, match="not found"):
        seo_inspect(tools, {"mode": "keywords", "path": "nope.csv"})
    (tmp_path / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ToolError, match="term_column column not given"):
        seo_inspect(tools, {"mode": "keywords", "path": "x.csv"})
    with pytest.raises(ToolError, match="path is required"):
        seo_inspect(tools, {"mode": "keywords"})


def test_keywords_tool_json_roundtrip(tmp_path):
    (tmp_path / "terms.csv").write_text(TERMS_CSV, encoding="utf-8")
    data = json.loads(seo_inspect_tool(WorkspaceTools(tmp_path), {"mode": "keywords", "path": "terms.csv"}))
    assert data["mode"] == "keywords" and data["estimated_saving"] > 0



def test_dispatch_passes_the_web_callables_to_seo_inspect(tmp_path):
    import json as _json

    from rocky.tools import WorkspaceTools, dispatch, worker_tools

    class Searcher:
        def fetch_html(self, url):
            return "<html><head><title>Batai internetu – Shop</title><meta name='description' content='Pigūs batai'></head><body><h1>Batai internetu</h1></body></html>"

        def search(self, query):
            return "- Batai pigiau | Rival\n  https://rival.lt/batai\n"

    out = _json.loads(dispatch(WorkspaceTools(tmp_path), "seo_inspect", {"mode": "snippet", "pages": [{"url": "https://shop.lt/batai"}], "query": "batai"}, searcher=Searcher()))
    assert out["mode"] == "snippet" if "mode" in out else True
    assert not any("not fetched" in m for m in out.get("missing", []))
    assert "seo_inspect" in [t["function"]["name"] for t in worker_tools(False)]
