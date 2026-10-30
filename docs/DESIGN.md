# Design notes

This document collects the idea, the considerations behind it and the
challenges that need solving before this plugin can be trusted. Items marked
**[verify]** are assumptions that must be tested on a live system before code
depends on them.

## 1. The idea

OVOS can already run almost fully offline. What is missing is a middle ground:
a device that *does* use the internet, but in a controlled, private way. The
goal is to let the owner say, per part of the assistant:

- speech in/out (STT, TTS) → direct, to a server they trust
- skill lookups (search, Wikipedia, weather, news…) → via Tor
- something else → not at all

and to have that **enforced**, not just requested.

## 2. Why the OVOS process split makes this possible

OVOS already runs as separate services:

| service                 | does                         | typical network need     |
|-------------------------|------------------------------|--------------------------|
| `ovos-dinkum-listener`  | wake word, VAD, STT          | STT server               |
| `ovos-audio`            | TTS, audio playback          | TTS server, streams      |
| `ovos-core`             | intent pipeline + all skills | anything skills fetch    |
| `ovos-phal` / admin     | hardware, system             | little / none            |
| `ovos-messagebus`       | bus                          | localhost only           |

Because each is its own process (and, with ovos-installer, its own systemd
unit), each can get its own network policy.

## 3. Options considered

### 3a. Proxy environment variables (rejected as the main mechanism)

`ALL_PROXY` / `HTTPS_PROXY=socks5h://127.0.0.1:9050` in a systemd drop-in.

- ✅ trivial, no root at runtime
- ❌ voluntary: `aiohttp` (without `trust_env=True`), `urllib` with SOCKS,
  raw sockets, subprocesses with their own clients ignore it → silent leaks
- ❌ no fail-closed behaviour

Still useful as a documented "lite mode" for people who cannot run nftables.

### 3b. nftables + Tor transparent proxy, matched per cgroup (chosen)

Tor runs with `TransPort` and `DNSPort`. nftables rules match the cgroup of
each OVOS service (`socket cgroupv2 level N "<path>"`) and:

- `tor`: redirect TCP → TransPort, UDP/53 → DNSPort, drop everything else
  that is not in `lan_allow`
- `direct`: accept (optionally only to `direct_allow`)
- `blocked`: drop everything not in `lan_allow`

- ✅ enforced by the kernel, skills cannot opt out
- ✅ fail closed: if Tor is not listening, redirected connections just fail
- ❌ needs root → PHAL admin
- ❌ cgroup handling is fiddly (see challenges)

### 3c. Separate network namespace / gateway VM (Whonix style)

Run ovos-core in a container/VM whose only route is a Tor gateway.

- ✅ the strongest isolation
- ❌ not something a pip package can set up; it is a deployment design.
  Worth documenting as the "maximum" option, not implementing.

## 4. Fit with the OVOS way of doing things

- Package: `ovos-PHAL-plugin-netpolicy`, entry point group `opm.phal.admin`,
  class based on `ovos_plugin_manager.templates.phal.AdminPlugin`.
- Config: `mycroft.conf` → `PHAL.admin.ovos-PHAL-plugin-netpolicy`.
  (Confirmed in `ovos_PHAL/admin.py`: admin plugins get
  `PHAL.admin.<name>`, and a plugin listed in the *user* PHAL config is
  skipped by the admin service.)
- Bus: `ovos.netpolicy.status`, `ovos.netpolicy.new_identity`, `ovos.netpolicy.reload`.
- Runtime requirements: declare that it does not need internet before load
  (it is the thing that sets up the internet path).
- Should be harmless when disabled: on shutdown or `enabled: false`, remove its
  own nftables table and leave everything else untouched.

## 5. Challenges

### 5.1 cgroup ids go stale on service restart **[verify]**

As far as I understand, nft resolves the cgroupv2 path to a cgroup id when the
rule is loaded. When `ovos-core` restarts, systemd creates a new cgroup with a
new id, and the old rule no longer matches — the restarted service would then
fall through to the default policy.

Mitigations:
- default policy for the `ovos_netpolicy` table must be **drop** for anything that
  looks like it came from an OVOS cgroup but is not matched (fail closed)
- watch systemd (D-Bus `JobRemoved` / unit state changes) and/or bus
  `mycroft.skills.initialized`-style events, and re-apply rules on restart
- re-check periodically as a safety net

### 5.2 Finding the cgroup path

ovos-installer can install as **user** units
(`/user.slice/user-1000.slice/user@1000.service/app.slice/ovos-core.service`)
or **system** units (`/system.slice/ovos-core.service`). The plugin should
resolve the path from the unit name rather than hard-code it
(e.g. `systemctl show -p ControlGroup`). **[verify]** exact unit names on the
stable and alpha test machines.

### 5.3 Granularity is per process, not per skill

All skills live inside `ovos-core`, so "weather via Tor, Home Assistant
direct" is not possible with cgroup matching alone. Options for later:
- `lan_allow` already covers the common case (Home Assistant on the LAN)
- run selected skills as standalone processes (ovos-workshop has a skill
  launcher **[verify]**), each in its own unit/cgroup with its own policy

### 5.4 DNS

- every `tor` service must have UDP/TCP 53 redirected to the Tor DNSPort,
  otherwise names leak to the normal resolver
- systemd-resolved (127.0.0.53) is on localhost — `lan_allow` must not
  accidentally let lookups out through it. Redirect DNS *before* the localhost
  accept rule, or resolve all localhost:53 for `tor` cgroups to DNSPort
- `.onion` addresses only work through TransPort with `AutomapHostsOnResolve`

### 5.5 IPv6

Tor's TransPort can handle IPv6, but to keep v1 simple: drop all IPv6 from
`tor` cgroups (except `::1`). Otherwise IPv6 is an easy leak.

### 5.6 UDP and other protocols

Tor carries TCP only. Anything UDP (except DNS) from a `tor` service is
dropped. Skills that rely on UDP (e.g. some streaming, NTP-like lookups) will
break — and should, visibly, rather than leak.

### 5.7 Fail closed vs. a usable assistant

If Tor is down or still bootstrapping, `tor` services have no internet.
OVOS will then report "no internet" for skill lookups. That is the correct
behaviour, but:
- the plugin should emit a clear status so a skill/GUI can explain *why*
- startup ordering: Tor bootstrap can take tens of seconds after boot

### 5.8 Tor-hostile services and latency

- many APIs block or CAPTCHA Tor exit nodes; some skills will simply fail
- every lookup gets 1–3 s extra latency, noticeable in a voice assistant
- worth a compatibility list of skills tested under `tor` mode (could hook
  into the Klondike Mercantile test reports later)

### 5.9 Coexisting with other firewalls

firewalld (Fedora), ufw (Debian/Raspberry Pi OS), Docker's own rules. The
plugin must use its own nftables table with clear priorities and never flush
other tables.

### 5.10 Managing Tor itself — decision

The plugin **configures and runs** Tor, but does **not install** it.

- **Install:** a pip package should not call `apt`/`dnf` on its own — it is
  distro-specific and surprising. Installing the `tor` binary belongs in
  ovos-installer (which already installs system packages) and in the README.
  An explicit opt-in `"install_tor": true` can be offered for people who want
  it fully automatic.
- **Configure and run:** the plugin starts its **own Tor instance**, not the
  system one. It generates a torrc from `mycroft.conf` with its own
  `DataDirectory` (e.g. `/var/lib/ovos-netpolicy/tor`), `TransPort`,
  `DNSPort`, `ControlPort` (cookie auth) and `AutomapHostsOnResolve`, and
  supervises the process (restart on crash, report bootstrap progress).
  - never touches `/etc/tor/torrc` → no clash with a system Tor the user
    already runs, and no distro differences in torrc layout
  - the plugin always knows the exact ports and settings it relies on
  - room for Tor options in config later: bridges/obfs4 (censored networks),
    `ExitNodes` country, `StrictNodes`
- If the `tor` binary is missing: the `tor` mode fails closed and status says
  clearly that Tor is not installed.

Config sketch:

```json
"tor": {
  "binary": "tor",
  "install_tor": false,
  "trans_port": 9040,
  "dns_port": 5353,
  "control_port": 9051,
  "bridges": [],
  "exit_nodes": null
}
```

### 5.11 Docker installs

Containers have different cgroup paths and their own network namespaces.
Out of scope for v1; for Docker the cleaner answer is probably a Tor gateway
container in the compose file (option 3c).

### 5.12 Security model

- The plugin runs as root, so its config is a privilege boundary: only root-
  readable/writeable config should be able to change routing. A bus message
  must never be able to switch a service from `tor` to `direct` without that.
- `ovos.netpolicy.new_identity` is harmless and can be bus-accessible.

## 6. Backend architecture and packaging

### 6.1 Core + backends

The core package knows only policies, cgroups and nftables, plus the built-in
modes `direct` and `blocked` with allow-lists. Everything that needs a daemon
or a tunnel is a **backend**, found through an entry point group
(proposal: `ovos.netpolicy.backend`), in the same spirit as OPM plugins.

```bash
pip install ovos-PHAL-plugin-netpolicy              # core: direct / blocked / allow-lists
pip install ovos-PHAL-plugin-netpolicy[tor]         # + ovos-netpolicy-backend-tor
pip install ovos-PHAL-plugin-netpolicy[wireguard]   # + ovos-netpolicy-backend-wireguard
```

A backend provides:

- a mode name (`tor`, `vpn`, …) usable in `routes`
- `start()` / `stop()` / `status()` for its daemon or interface
- the rules it needs, as data the core turns into nftables / policy routing:
  either *redirect TCP/DNS to local ports* (Tor, a local SOCKS→transparent
  helper) or *mark with fwmark → routing table* (WireGuard, other VPNs)
- its own config section under the plugin config

The core keeps sole ownership of the nftables table, so a buggy backend
cannot open holes: it can only ask for redirects/marks inside the policy, and
fail-closed stays the core's job.

Third parties can then add `-i2p`, `-socks` (an external proxy), etc. without
changes to the core.

### 6.2 Where the tor binary comes from

pip cannot install a C program from the distro. The tor backend therefore
looks for a binary in this order (the same chain Unseenet uses):

1. explicit `tor.binary` in config
2. an environment variable (e.g. `OVOS_NETPOLICY_TOR`)
3. a **bundled** binary shipped inside a platform wheel
4. `tor` on `PATH` (distro package)

and fails closed with a clear status if none is found.

### 6.3 Bundling the Tor Expert Bundle — architecture coverage

The Tor Project publishes the *Tor Expert Bundle* specifically for apps that
bundle tor (tor + pluggable transports/lyrebird + bridge strings + geoip).
Linux coverage as checked on the download page/mirrors (October 2026):

| Linux arch | stable channel | alpha channel | relevant for OVOS |
|------------|----------------|---------------|-------------------|
| x86_64     | ✅             | ✅            | PCs, VMs, test boxes |
| i686       | ✅             | ❌ (dropped)  | hardly |
| aarch64    | ❌             | ✅            | Raspberry Pi 4/5 (64-bit), Mark II |
| armv7      | ❌             | ❌            | 32-bit Pi OS |

So: **aarch64 only exists in the alpha channel** of the Expert Bundle. On the
mirror checked, the alpha bundle carried the same tor version as stable
(0.4.9.5) — the "alpha" label is about the Tor Browser build, not necessarily
the daemon — but that has to be re-checked per release. **[verify]**
32-bit ARM has no bundle at all → distro `tor` only.

Proposal:

- `ovos-netpolicy-backend-tor` is a pure-Python package (stem, torrc
  generation, supervision) — always works with a distro `tor`.
- an optional extra, e.g. `[tor-bundled]`, pulls a platform wheel
  (`ovos-netpolicy-tor-bin`) with the Expert Bundle for `manylinux_x86_64`
  and `manylinux_aarch64`. No wheel for armv7 → pip falls back to nothing, and
  the distro binary is used.
- the bundle must be verified (signature) at wheel build time in CI, and a
  new wheel released for every Tor security release — that is the real cost
  of bundling. Distro `tor` remains the recommended path.

### 6.4 Lessons to reuse from Unseenet's tor handling

Unseenet already runs a bundled Expert Bundle tor in portable builds. Things
that apply directly here:

- the Expert Bundle ships its own libevent/OpenSSL next to `tor` → launch
  with `LD_LIBRARY_PATH` set to the binary's folder
- files unpacked from a wheel/zip may lose the exec bit → fall back to
  running it via the dynamic loader (`/lib64/ld-linux-x86-64.so.2`,
  `/lib/ld-linux-aarch64.so.1`), or fix permissions at first start
- built-in bridges come from the bundle's `pluggable_transports/pt_config.json`;
  bridges need `lyrebird` from the same bundle
- start tor with `__OwningControllerProcess` so it quits by itself if the
  plugin dies — no orphaned Tor holding the data directory
- never stop a Tor that another program is using; only clean up one whose
  owner is gone
- read bootstrap progress from the control port (`PROGRESS=` / `SUMMARY=`)
  for the status message

Worth considering later: a small shared "tor runtime" library used by both
projects instead of two copies of the same logic.

## 7. Beyond Tor

The plugin is a general per-service network policy; Tor is one mode. Other
uses the same machinery supports:

- `blocked` — e.g. a device in a child's room that may only reach the LAN
- an enforced "local only" setup: every service `blocked` + `lan_allow`
- later: `vpn:<interface>` — route a service out via WireGuard using an
  fwmark + policy routing table instead of a Tor redirect
- later: per-mode allow-lists (`direct` but only to the STT/TTS servers)

## 8. Open questions

- entry point group name for backends (`ovos.netpolicy.backend`?) and whether
  it belongs in OPM
- should STT/TTS optionally go via Tor too (privacy vs. latency)?
- interest from the OVOS maintainers in having this under OpenVoiceOS

## 9. Next steps

1. verify unit names and cgroup paths on the test machines (stable + alpha)
2. manual proof of concept: hand-written nftables rules + Tor on the alpha
   box, check with `ss -tnp` / tcpdump that nothing from ovos-core leaks
3. test the cgroup-restart behaviour (5.1)
4. only then write the plugin around the rules that proved to work
