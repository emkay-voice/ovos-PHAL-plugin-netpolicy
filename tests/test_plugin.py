"""The plugin on a fake bus, with a fake cgroup tree and nft that only records."""
import json
import os

import pytest
from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus

from conftest import APP, restart
from ovos_phal_plugin_netpolicy import NetPolicyAdminPlugin, NetPolicyAdminValidator

LOCAL = {"stt": {"module": "ovos-stt-plugin-fasterwhisper"}, "tts": {"module": "ovos-tts-plugin-piper"}}
SERVER = {"stt": {"module": "ovos-stt-plugin-server"}, "tts": {"module": "ovos-tts-plugin-server"}}


class Bus(FakeBus):
    def __init__(self):
        super().__init__()
        self.sent = []

    def emit(self, message):
        self.sent.append(message)
        super().emit(message)

    def last(self, msg_type):
        return next(m for m in reversed(self.sent) if m.msg_type == msg_type)


@pytest.fixture
def make(cgroups, tmp_path):
    made = []

    def make(config=None, speech=LOCAL, root_policy=None, nft=None):
        tables = []
        bus = Bus()
        cfg = dict({"enabled": True, "state_file": str(tmp_path / "state.json")}, **(config or {}))
        p = NetPolicyAdminPlugin(bus=bus, config=cfg, cgroup_root=cgroups, start=False,
                                 nft=nft or tables.append, root_policy=root_policy or tmp_path / "none.json")
        p._core_config = dict(speech)
        p.tables = tables
        made.append(p)
        return p
    yield make


def ask(p, msg_type, **data):
    p.bus.emit(Message(msg_type, data))
    return p.bus.last(f"{msg_type}.response").data


def table(p):
    return p.tables[-1]


def test_off_unless_enabled():
    assert not NetPolicyAdminValidator.validate({})
    assert NetPolicyAdminValidator.validate({"enabled": True})


def test_starts_with_the_table_and_replaces_it_in_one_go(make):
    p = make({"routes": {"ovos-core": "blocked"}})
    text = table(p)
    assert text.startswith("table inet ovos_netpolicy {}\ndelete table inet ovos_netpolicy\n")
    assert f'"{APP}/ovos-core.service" goto refuse_0' in text


def test_local_only_on_and_off(make):
    p = make()
    answer = ask(p, "ovos.local_only", enabled=True)
    assert answer == {"enabled": True, "stt": "ovos-stt-plugin-fasterwhisper", "tts": "ovos-tts-plugin-piper"}
    assert p.bus.last("ovos.local_only.changed").data["enabled"]
    assert f'"{APP}/ovos-audio.service" accept' not in table(p)
    assert ask(p, "ovos.local_only.status") ["enabled"]
    assert not ask(p, "ovos.local_only", enabled=False)["enabled"]
    assert f'"{APP}/ovos-audio.service" accept' in table(p)


def test_local_only_is_remembered(make):
    p = make()
    ask(p, "ovos.local_only", enabled=True)
    again = make()
    assert again.local_only and f'"{APP}/ovos-core.service" accept' not in table(again)


def test_no_local_only_when_speech_needs_a_server(make):
    p = make(speech=SERVER)
    answer = ask(p, "ovos.local_only", enabled=True)
    assert answer["error"] == "speech_not_local" and not answer["enabled"] and not p.local_only
    assert answer["stt"] == "ovos-stt-plugin-server"
    p.policy["require_local_speech"] = False
    assert ask(p, "ovos.local_only", enabled=True)["enabled"]


def test_no_local_only_when_nothing_could_be_enforced(make):
    def broken(text):
        raise FileNotFoundError("nft")
    p = make(nft=broken)
    assert p.error == "nft_missing"
    assert ask(p, "ovos.local_only", enabled=True)["error"] == "nft_missing"
    assert ask(p, "ovos.netpolicy.status")["enforced"] is False


def test_the_bus_cannot_open_more_than_the_policy(make):
    p = make({"routes": {"ovos-core": "blocked"}})
    ask(p, "ovos.local_only", enabled=True)
    ask(p, "ovos.local_only", enabled=False)
    assert f'"{APP}/ovos-core.service" goto' in table(p), "back to the policy, not to all direct"


def test_refusals_go_on_the_bus_once(make):
    p = make()
    p.refused("ovos-core", "185.15.59.224", 443, now=1000.0)
    p.refused("ovos-core", "185.15.59.224", 443, now=1000.5)  # the TCP retry
    p.refused("ovos-core", "192.0.2.1", 53, now=1001.0)
    entries = [m.data for m in p.bus.sent if m.msg_type == "ovos.network.request"]
    assert len(entries) == 2
    assert entries[0] == {"t": 1000000, "host": "185.15.59.224", "method": "CONNECT", "status": 0, "bytes": 0,
                          "who": "ovos-core", "kind": "", "service": "185.15.59.224:443", "blocked": True}
    assert entries[1]["method"] == "DNS" and entries[1]["kind"] == "dns"


def test_status(make):
    p = make({"routes": {"ovos-core": "blocked"}})
    p.refused("ovos-core", "185.15.59.224", 443)
    s = ask(p, "ovos.netpolicy.status")
    modes = {u["unit"]: u["mode"] for u in s["units"]}
    assert modes["ovos-core"] == "blocked" and modes["ovos-audio"] == "direct"
    assert s["enforced"] and s["policy_source"] == "mycroft.conf" and s["refused_today"] == 1
    assert s["refusals"][0]["who"] == "ovos-core" and s["speech"]["local"]
    assert (s["state"], s["local"], s["speech_server"], s["nflog_off"]) == ("on", "off", False, False)


def test_a_restart_is_found_again(make, cgroups):
    p = make()
    restart(cgroups, f"{APP}/ovos-audio.service")
    before = len(p.tables)
    p.apply()
    assert len(p.tables) == before + 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root owns every file")
def test_root_policy_only_when_root_owns_it(make, tmp_path):
    f = tmp_path / "policy.json"
    f.write_text(json.dumps({"routes": {"ovos-core": "blocked"}}))
    p = make({"routes": {"ovos-core": "direct"}}, root_policy=f)
    assert p.policy_source == "mycroft.conf" and p.policy["routes"] == {"ovos-core": "direct"}


def test_root_policy_wins(make, tmp_path, monkeypatch):
    f = tmp_path / "policy.json"
    f.write_text(json.dumps({"routes": {"ovos-core": "blocked"}, "enabled": False}))
    real = type(f).stat

    def as_root(self, *a, **k):
        st = real(self, *a, **k)
        if self == f:
            return os.stat_result((0o100600, st.st_ino, st.st_dev, 1, 0, 0) + tuple(st)[6:])
        return st
    monkeypatch.setattr(type(f), "stat", as_root)
    p = make({"routes": {"ovos-core": "direct"}}, root_policy=f)
    assert p.policy_source == str(f) and p.policy["routes"] == {"ovos-core": "blocked"}
    assert "enabled" not in p.policy, "only the policy's own keys"


def test_shutdown_takes_its_table_away_when_nothing_is_blocked(make):
    p = make()
    p.shutdown()
    assert table(p) == "table inet ovos_netpolicy {}\ndelete table inet ovos_netpolicy\n"


@pytest.mark.parametrize("how", ["local_only", "route"])
def test_shutdown_leaves_it_closed(make, how):
    p = make({"routes": {"ovos-core": "blocked"}} if how == "route" else None)
    if how == "local_only":
        ask(p, "ovos.local_only", enabled=True)
    n = len(p.tables)
    p.shutdown()
    assert len(p.tables) == n, "the table stays until the plugin is back"
