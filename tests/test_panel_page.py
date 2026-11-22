"""The page in ovos-control-panel, described in controlpanel.json: the file, its
words in every language, and what it asks of the plugin's status."""
import json
import re
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "ovos_phal_plugin_netpolicy"
TEXT = (PKG / "controlpanel.json").read_text(encoding="utf-8")
PAGE = json.loads(TEXT)


def test_own_messages_and_an_icon():
    assert PAGE["controlpanel"] == 1 and PAGE["bus"] == "ovos.netpolicy."
    assert (PKG / PAGE["icon"]).is_file()
    sent = set(re.findall(r'"(?:query|send)": "([^"]+)"', TEXT))
    assert sent == {"ovos.netpolicy.status", "ovos.netpolicy.reload"}


def test_every_language_has_every_word():
    files = {p.parent.name: json.loads(p.read_text(encoding="utf-8")) for p in (PKG / "locale").glob("*/controlpanel.json")}
    assert len(files) == 12
    keys = set(files["en-US"])
    for lang, table in files.items():
        assert set(table) == keys, lang
        assert all(v.strip() for v in table.values()), lang
    used = set(re.findall(r'"(netpolicy\.[\w.]+)"', TEXT))
    labelled = {k for k in keys if re.match(r"netpolicy\.(state|local|mode|scope)\.", k)}
    assert used | labelled == keys, "every word is used, and every one used is there"


def test_the_status_has_what_the_page_shows(cgroups, tmp_path):
    from test_plugin import Bus, LOCAL
    from ovos_phal_plugin_netpolicy import NetPolicyAdminPlugin
    p = NetPolicyAdminPlugin(bus=Bus(), config={"enabled": True, "state_file": str(tmp_path / "s.json")},
                             cgroup_root=cgroups, start=False, nft=lambda t: None, root_policy=tmp_path / "x")
    p._core_config = LOCAL
    p.refused("ovos-core", "192.0.2.1", 443)
    status = p.status()
    rows = {**status["units"][0], **status["refusals"][0]}
    texts = TEXT + "".join((PKG / "locale/en-US/controlpanel.json").read_text())
    for field in re.findall(r"\{(\w+)[@}:]", texts) + re.findall(r'"(?:when|items|key|by)": "!?(\w+)"', TEXT):
        assert field in status or field in rows, field
    for unit in status["units"]:
        assert set(unit) >= {"unit", "mode", "scope"}


def test_the_panel_reads_it():
    described = pytest.importorskip("ovos_webui.described")
    page, _ = described.check(PAGE, "ovos-PHAL-plugin-netpolicy")
    assert [b["type"] for b in page["blocks"]] == ["text", "status", "list", "list", "text"]
