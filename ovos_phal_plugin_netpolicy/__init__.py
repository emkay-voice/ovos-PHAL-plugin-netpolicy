"""ovos-PHAL-plugin-netpolicy: which of OVOS' services may reach the internet.

A PHAL admin plugin (it needs root for nftables). Per OVOS service –
ovos-core with all the skills, ovos-audio, the listener, the control panel …
– the mode is ``direct`` (normal) or ``blocked`` (localhost and the LAN only),
enforced in the kernel so a skill cannot go around it, and closed rather
than open when in doubt. On top of that, **local only**: every service
blocked, switched from the bus with the messages ovos-skill-guardian and its
page in the control panel already use:

    ovos.local_only          {"enabled": bool}
    ovos.local_only.status   {}
    -> .response             {"enabled", "stt", "tts"} (+ "error" when refused)
    ovos.local_only.changed  {"enabled", "stt", "tts"}

The bus can only switch between the configured routes and everything
blocked; it can never open more than root's configuration does.

Every refused connection is sent as an ``ovos.network.request`` entry with
``"blocked": true`` – the network log's format – so Guardian can tell what
tried to go out, and offer to go online for one question.

    ovos.netpolicy.status    {} -> units and their modes, local only, refusals
    ovos.netpolicy.reload    {} -> read the configuration again and re-apply

Configuration, in mycroft.conf under PHAL.admin.ovos-PHAL-plugin-netpolicy:

    enabled               false; true to use it
    default               "direct": the mode of a service not in routes
    routes                {"ovos-core": "blocked", ...} by unit name
    lan_allow, lan_allow6 what stays open when blocked (private ranges)
    require_local_speech  true: refuse local only when STT or TTS is a server
    local_speech          more on-device STT/TTS modules than the known ones
    nflog_group           4711
    state_file            /var/lib/ovos-netpolicy/state.json (local only is
                          remembered across restarts)

mycroft.conf belongs to the ovos user (ovos-installer points the admin
service at it), so OVOS itself – a skill, the control panel – could change
these. Where that matters, put the policy in /etc/ovos-netpolicy/policy.json
instead: it is read only when root owns it and nobody else may write it, and
what it says wins over mycroft.conf. The status says which one is in force.
"""
import collections
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from ovos_bus_client.message import Message
from ovos_plugin_manager.templates.phal import AdminPlugin, AdminValidator
from ovos_utils.log import LOG

from ovos_phal_plugin_netpolicy import rules, speech, units
from ovos_phal_plugin_netpolicy.nflog import NflogReader

NAME = "ovos-PHAL-plugin-netpolicy"
STATE_FILE = "/var/lib/ovos-netpolicy/state.json"
#: the policy only root can change
ROOT_POLICY = "/etc/ovos-netpolicy/policy.json"
#: what the policy says (the rest of the configuration is the plugin's own)
POLICY_KEYS = ("default", "routes", "lan_allow", "lan_allow6", "require_local_speech", "local_speech")
#: how often a restarted service is looked for (s)
WATCH_EVERY = 0.25
#: the same unit, address and port again within this long is one refusal (s)
SAME_REFUSAL = 2.0
#: refusals kept for the status
KEEP_REFUSALS = 50


class NetPolicyAdminValidator(AdminValidator):
    @staticmethod
    def validate(config=None):
        # opt-in only: routing changes must never happen by accident
        return (config or {}).get("enabled", False) is True


class NetPolicyAdminPlugin(AdminPlugin):
    validator = NetPolicyAdminValidator

    def __init__(self, bus=None, config=None, cgroup_root=None, nft=None, start=True,
                 root_policy=ROOT_POLICY):
        super().__init__(bus=bus, name=NAME, config=config)
        self.root_policy = Path(root_policy)
        self._core_config = None  # tests set it
        self.policy, self.policy_source = self.read_policy()
        self.cgroup_root = Path(cgroup_root or units.CGROUP_ROOT)
        self._nft = nft or self._run_nft
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.error = ""
        self.applied = None        # the units the table was written for
        self.refusals = collections.deque(maxlen=KEEP_REFUSALS)
        self._recent = {}
        self._today = (time.strftime("%Y-%m-%d"), 0)
        self.nflog_error = ""
        self.local_only = self._load_state().get("local_only", False)
        # not the template's _on: older ovos-plugin-manager releases lack it
        self.handlers = [("ovos.local_only", self.handle_local_only),
                         ("ovos.local_only.status", self.handle_local_only_status),
                         ("ovos.netpolicy.status", self.handle_status),
                         ("ovos.netpolicy.reload", self.handle_reload)]
        for topic, handler in self.handlers:
            self.bus.on(topic, handler)
        self.apply()
        if start:
            threading.Thread(target=self._watch, name="netpolicy-watch", daemon=True).start()
            threading.Thread(target=self._read_nflog, name="netpolicy-nflog", daemon=True).start()

    # -- configuration and state ---------------------------------------------

    def read_policy(self) -> tuple:
        """The policy and where it came from: root's file when it is safe,
        else mycroft.conf."""
        policy = {k: self.config[k] for k in POLICY_KEYS if k in self.config}
        try:
            st = self.root_policy.stat()
        except OSError:
            return policy, "mycroft.conf"
        if st.st_uid != 0 or st.st_mode & 0o022:
            LOG.error(f"netpolicy: {self.root_policy} is not root's alone, so it is not used")
            return policy, "mycroft.conf"
        try:
            own = json.loads(self.root_policy.read_text())
        except (OSError, ValueError) as e:
            LOG.error(f"netpolicy: {self.root_policy} cannot be read: {e}")
            return policy, "mycroft.conf"
        policy.update({k: own[k] for k in POLICY_KEYS if k in own})
        return policy, str(self.root_policy)

    @property
    def state_file(self) -> Path:
        return Path(self.config.get("state_file") or STATE_FILE)

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"local_only": self.local_only}))
            os.replace(tmp, self.state_file)
        except OSError as e:
            LOG.error(f"netpolicy: could not save {self.state_file}: {e}")

    def core_config(self) -> dict:
        """mycroft.conf as it is now (a voice chosen in the panel since the
        plugin started counts)."""
        if self._core_config is not None:
            return self._core_config
        try:
            from ovos_config import Configuration
            return Configuration()
        except Exception:  # noqa: BLE001
            return self.config_core

    def speech(self) -> dict:
        return speech.check(self.core_config(), self.policy.get("local_speech") or ())

    # -- the table -----------------------------------------------------------

    @staticmethod
    def _run_nft(text: str):
        subprocess.run(["nft", "-f", "-"], input=text, text=True, check=True,
                       capture_output=True)

    def ruleset(self, found=None, slices=None) -> str:
        found = units.find_units(self.cgroup_root) if found is None else found
        slices = units.app_slices(self.cgroup_root) if slices is None else slices
        return rules.build(found, slices, routes=self.policy.get("routes") or {},
                           default=self.policy.get("default", "direct"),
                           local_only=self.local_only,
                           lan4=self.policy.get("lan_allow"), lan6=self.policy.get("lan_allow6"),
                           group=int(self.config.get("nflog_group", 4711)))

    def apply(self):
        """Write the table for the units running now, in one transaction
        (the old table goes and the new one comes at once)."""
        with self.lock:
            found = units.find_units(self.cgroup_root)
            slices = units.app_slices(self.cgroup_root)
            try:
                text = self.ruleset(found, slices)
                self._nft(f"table inet {rules.TABLE} {{}}\ndelete table inet {rules.TABLE}\n" + text)
                self.applied = found
                self.error = ""
            except FileNotFoundError:
                self.error = "nft_missing"
                LOG.error("netpolicy: nft is not installed (install nftables)")
            except subprocess.CalledProcessError as e:
                self.error = "nft_failed"
                LOG.error(f"netpolicy: nft refused the table: {(e.stderr or '').strip()}")
            except (ValueError, PermissionError) as e:
                self.error = "nft_failed"
                LOG.error(f"netpolicy: {e}")
            return not self.error

    def anything_blocked(self) -> bool:
        routes = self.policy.get("routes") or {}
        default = self.policy.get("default", "direct")
        return self.local_only or default != "direct" or any(m != "direct" for m in routes.values())

    def remove(self):
        """Take the plugin's own table away, and nothing else."""
        try:
            self._nft(f"table inet {rules.TABLE} {{}}\ndelete table inet {rules.TABLE}\n")
        except Exception as e:  # noqa: BLE001
            LOG.warning(f"netpolicy: could not remove the table: {e}")

    def _watch(self):
        seen = units.fingerprint(self.cgroup_root)
        while not self.stopping.wait(WATCH_EVERY):
            now = units.fingerprint(self.cgroup_root)
            if now != seen:
                self.apply()
                seen = now

    # -- refusals --------------------------------------------------------------

    def _read_nflog(self):
        try:
            reader = NflogReader(int(self.config.get("nflog_group", 4711)))
        except OSError as e:
            self.nflog_error = str(e)
            LOG.error(f"netpolicy: cannot read NFLOG, refusals are not reported: {e}")
            return
        try:
            while not self.stopping.is_set():
                for prefix, _family, address, port in reader.read():
                    self.refused(prefix, address, port)
        finally:
            reader.close()

    def refused(self, who: str, address: str, port: int, now: float = None):
        """One refused connection: kept for the status, and sent on the bus
        in the network log's format."""
        now = time.time() if now is None else now
        key = (who, address, port)
        if now - self._recent.get(key, 0) < SAME_REFUSAL:
            return  # a retry of the same connection
        self._recent = {k: t for k, t in self._recent.items() if now - t < SAME_REFUSAL}
        self._recent[key] = now
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        self._today = (day, (self._today[1] if self._today[0] == day else 0) + 1)
        dns = port == 53
        entry = {"t": int(now * 1000), "host": address, "method": "DNS" if dns else "CONNECT",
                 "status": 0, "bytes": 0, "who": who or "ovos", "kind": "dns" if dns else "",
                 "service": f"{address}:{port}" if port else address, "blocked": True}
        self.refusals.appendleft(entry)
        self.bus.emit(Message("ovos.network.request", entry))

    # -- the bus -------------------------------------------------------------

    def _local_only_data(self) -> dict:
        s = self.speech()
        return {"enabled": self.local_only, "stt": s["stt"], "tts": s["tts"]}

    def handle_local_only(self, message: Message):
        """Local only on or off. Refused, unchanged, when the device could not
        hear or speak without the internet, or when the table cannot be
        written (then nothing would be enforced)."""
        enabled = bool(message.data.get("enabled"))
        with self.lock:
            data = self._local_only_data()
            if enabled and self.policy.get("require_local_speech", True) and not self.speech()["local"]:
                self.bus.emit(message.response(dict(data, error="speech_not_local")))
                return
            if self.error and enabled:
                self.bus.emit(message.response(dict(data, error=self.error)))
                return
            changed = enabled != self.local_only
            self.local_only = enabled
            self.apply()
            self._save_state()
            data = self._local_only_data()
        LOG.info(f"netpolicy: local only {'on' if enabled else 'off'}")
        self.bus.emit(message.response(data))
        if changed:
            self.bus.emit(Message("ovos.local_only.changed", data))

    def handle_local_only_status(self, message: Message):
        self.bus.emit(message.response(self._local_only_data()))

    def status(self) -> dict:
        with self.lock:
            found = self.applied or []
            routes = self.policy.get("routes") or {}
            default = self.policy.get("default", "direct")
            today = self._today[1] if self._today[0] == time.strftime("%Y-%m-%d") else 0
            return {
                "enforced": not self.error, "error": self.error, "nflog_error": self.nflog_error,
                "policy_source": self.policy_source, "policy": self.policy,
                "local_only": self.local_only, "speech": self.speech(),
                "units": [{"unit": u.short, "scope": u.scope,
                           "mode": rules.mode_of(u, routes, default, self.local_only),
                           "configured": rules.mode_of(u, routes, default, False)} for u in found],
                "refused_today": today,
                # the same as words, for the control panel's page
                "state": "on" if not self.error else self.error,
                "local": "on" if self.local_only else "off",
                "speech_server": not self.speech()["local"],
                "nflog_off": bool(self.nflog_error),
                "refusals": [dict(e, id=str(n), when=e["t"] / 1000) for n, e in enumerate(self.refusals)],
            }

    def handle_status(self, message: Message):
        self.bus.emit(message.response(self.status()))

    def handle_reload(self, message: Message):
        """Read this plugin's configuration again (as root wrote it) and re-apply."""
        try:
            from ovos_config import Configuration
            Configuration.reload()
            core = Configuration()
        except Exception:  # noqa: BLE001
            core = self.core_config()
        self.config = ((core.get("PHAL") or {}).get("admin") or {}).get(NAME, self.config)
        self.policy, self.policy_source = self.read_policy()
        ok = self.apply()
        self.bus.emit(message.response({"enforced": ok, "error": self.error}))

    def shutdown(self):
        self.stopping.set()
        for topic, handler in self.handlers:
            self.bus.remove(topic, handler)
        # closed stays closed: while something is blocked the table is left
        # in place, so a restart of the admin service (or of the device,
        # until the plugin is back) never lets OVOS out in between
        if not self.anything_blocked():
            self.remove()
        try:
            super().shutdown()
        except Exception:  # noqa: BLE001 - nothing of the template's to undo
            pass
