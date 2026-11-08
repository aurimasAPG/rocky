"""The ad_board extension point: a topic module adds fields, checks, messages and elements without touching adboard.py."""

import types

import pytest

from rocky import adchecks, playbook
from rocky.adboard import Check, check_pack, message, normalize
from rocky.tools import worker_tools


def _stub_module():
    m = types.ModuleType("rocky.adchecks.stub")
    m.AD_FIELDS = ("promise",)
    m.PACK_FIELDS = ("job",)
    m.SCHEMA = {
        "ad": {"promise": {"type": "string", "description": "what the ad promises"}},
        "pack": {"job": {"type": "string", "description": "the durable job"}},
    }
    m.MESSAGES = {
        "en": {"job_missing": "The pack has no job line.", "promise_long": "The promise is {n} words."},
        "lt": {"job_missing": "Pakete nėra darbo eilutės."},
    }

    def check(pack, facts, images):
        out = []
        if not pack.get("job"):
            out.append(Check("warn", "job_missing"))
        for n, ad in enumerate(pack["ads"], 1):
            words = len(str(ad.get("promise") or "").split())
            if words > 3:
                out.append(Check("warn", "promise_long", ad=n, n=words))
        return out

    m.check = check
    return m


def _broken_module():
    m = types.ModuleType("rocky.adchecks.broken")
    m.MESSAGES = {"en": {}}

    def check(pack, facts, images):
        raise RuntimeError("boom")

    m.check = check
    return m


@pytest.fixture
def stub(monkeypatch):
    module = _stub_module()
    monkeypatch.setattr(adchecks, "modules", lambda: (module,))
    return module


def _pack(**extra):
    return {
        "brand": "X",
        "url": "https://x.lt",
        "ads": [{"primary_text": "a", "headline": "b", "cta": "c", "promise": "one two three four"}],
        **extra,
    }


def test_normalize_keeps_declared_extension_fields_and_drops_unknown_ones(stub):
    pack = normalize(_pack(job="Get paid on time", mystery="x"))
    assert pack["job"] == "Get paid on time"
    assert pack["ads"][0]["promise"] == "one two three four"
    assert "mystery" not in pack and "mystery" not in pack["ads"][0]


def test_extension_fields_may_be_objects_sent_as_json_strings(stub):
    pack = normalize(_pack(job='{"statement": "s", "before": "b"}'))
    assert pack["job"] == {"statement": "s", "before": "b"}


def test_check_pack_runs_the_module_and_messages_render_in_both_languages(stub):
    checks = check_pack(normalize(_pack()), None, {1: "img"})
    codes = [c.code for c in checks]
    assert "job_missing" in codes and "promise_long" in codes
    long = next(c for c in checks if c.code == "promise_long")
    assert message(long) == "The promise is 4 words."
    assert message(next(c for c in checks if c.code == "job_missing"), "lt") == "Pakete nėra darbo eilutės."
    assert "job_missing" not in [c.code for c in check_pack(normalize(_pack(job="Get paid")), None, {1: "img"})]


def test_an_unknown_code_still_renders_instead_of_crashing_the_page():
    assert message(Check("warn", "never_registered", value="v")) == "never_registered: v"


def test_a_module_that_raises_becomes_one_warning(monkeypatch):
    monkeypatch.setattr(adchecks, "modules", lambda: (_broken_module(),))
    checks = check_pack(normalize(_pack()), None, {1: "img"})
    failed = [c for c in checks if c.code == "check_failed"]
    assert len(failed) == 1 and "broken: boom" in failed[0].value
    assert "could not run" in message(failed[0])


def test_the_tool_spec_grows_with_the_module(stub):
    spec = next(t for t in worker_tools(False) if t["function"]["name"] == "ad_board")
    props = spec["function"]["parameters"]["properties"]
    assert props["job"]["description"] == "the durable job"
    assert props["ads"]["items"]["properties"]["promise"]["type"] == "string"
    # the base spec is untouched
    from rocky.tools import CLIENT_TOOL_SPECS

    base = next(t for t in CLIENT_TOOL_SPECS if t["function"]["name"] == "ad_board")
    assert "job" not in base["function"]["parameters"]["properties"]


def test_real_registry_is_empty_or_well_formed():
    for m in adchecks.modules():
        assert callable(m.check) and "en" in m.MESSAGES


def test_playbook_loads_topic_element_files_and_the_core_file_wins(tmp_path, monkeypatch):
    (tmp_path / "elements.toml").write_text(
        '[[element]]\nnumber = 1\ntitle = "Core"\ntopic = "t"\napplies = ["ads"]\nenforced = "check"\ncodes = ["a"]\n'
        'question.en = "q"\nquestion.lt = "k"\nwhy.en = "w"\nwhy.lt = "k"\n',
        encoding="utf-8",
    )
    (tmp_path / "elements-pricing.toml").write_text(
        '[[element]]\nnumber = 1\ntitle = "Dup"\ntopic = "t"\napplies = ["ads"]\nenforced = "check"\ncodes = ["z"]\n'
        'question.en = "q"\nquestion.lt = "k"\nwhy.en = "w"\nwhy.lt = "k"\n\n'
        '[[element]]\nnumber = 200\ntitle = "Absolute versus relative price"\ntopic = "Pricing"\napplies = ["ads"]\n'
        'enforced = "check"\ncodes = ["price_position_missing"]\nquestion.en = "q"\nquestion.lt = "k"\nwhy.en = "w"\nwhy.lt = "k"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(playbook, "ELEMENTS_DIR", tmp_path)
    monkeypatch.setattr(playbook, "ELEMENTS_PATH", tmp_path / "elements.toml")
    playbook.elements.cache_clear()
    try:
        rows = {e.number: e for e in playbook.elements()}
        assert rows[1].title == "Core" and rows[1].codes == ("a",)
        assert rows[200].codes == ("price_position_missing",)
        assert playbook.for_code("price_position_missing").number == 200
    finally:
        playbook.elements.cache_clear()
