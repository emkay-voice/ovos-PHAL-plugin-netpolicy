"""The units found, and the table written for them."""
import pytest

from conftest import APP, restart
from ovos_phal_plugin_netpolicy import rules, units


def test_finds_ovos_units_user_and_system(cgroups):
    found = {u.name: u for u in units.find_units(cgroups)}
    assert set(found) == {"ovos-core.service", "ovos-audio.service", "ovos-listener.service",
                          "ovos-messagebus.service", "ovos-control-panel.service",
                          "hivemind-core.service"}, "not other apps, not sshd, not the admin service itself"
    core = found["ovos-core.service"]
    assert (core.scope, core.level, core.short) == ("user", 5, "ovos-core")
    assert core.path == f"{APP}/ovos-core.service"
    assert found["hivemind-core.service"].scope == "system"
    assert units.app_slices(cgroups)[0][0] == APP


def test_a_restart_changes_the_fingerprint(cgroups):
    before = units.fingerprint(cgroups)
    restart(cgroups, f"{APP}/ovos-core.service")
    assert units.fingerprint(cgroups) != before


def build(cgroups, **kw):
    return rules.build(units.find_units(cgroups), units.app_slices(cgroups), **kw)


def test_all_direct(cgroups):
    text = build(cgroups)
    assert f'socket cgroupv2 level 4 "{APP}" jump ovos_user' in text
    for name in ("ovos-core", "ovos-audio", "ovos-control-panel"):
        assert f'level 5 "{APP}/{name}.service" accept' in text
    assert "goto refuse_unknown" in text, "a unit the table does not know is refused"
    assert "hivemind-core.service" not in text, "a direct system unit needs no rule"


def test_blocked_core(cgroups):
    text = build(cgroups, routes={"ovos-core": "blocked"})
    assert f'"{APP}/ovos-core.service" accept' not in text
    assert f'"{APP}/ovos-core.service" goto refuse_0' in text
    chain = text[text.index("chain refuse_0"):]
    chain = chain[:chain.index("}")]
    assert 'prefix "ovos-core"' in chain and 'oif "lo" accept' in chain
    assert "udp dport 53" in chain and "192.168.0.0/16" in chain and "reject with tcp reset" in chain
    assert chain.index("dport 53") < chain.index("192.168.0.0/16"), "DNS is refused even to the LAN"


def test_local_only_blocks_everything(cgroups):
    text = build(cgroups, local_only=True)
    assert " accept\n" not in text.split("chain ovos_user")[1].split("}")[0]
    assert '"system.slice/hivemind-core.service" goto' in text


def test_a_system_unit_by_route_and_unknown_modes_fail_closed(cgroups):
    found = units.find_units(cgroups)
    hive = next(u for u in found if u.short == "hivemind-core")
    assert rules.mode_of(hive, {"hivemind-core.service": "blocked"}, "direct", False) == "blocked"
    assert rules.mode_of(hive, {"hivemind-core": "tor"}, "direct", False) == "blocked", "no tor in v1"


def test_lan_can_be_narrowed_and_is_checked(cgroups):
    text = build(cgroups, routes={"ovos-core": "blocked"}, lan4=["192.168.65.0/24"], lan6=[])
    assert "192.168.65.0/24" in text and "10.0.0.0/8" not in text and "ip6 daddr" not in text
    with pytest.raises(ValueError):
        build(cgroups, routes={"ovos-core": "blocked"}, lan4=['0/0; accept "'])


def test_odd_names_are_refused():
    with pytest.raises(ValueError):
        rules._q('ovos"; flush ruleset')
