# Proof of concept: `blocked` and local only

Hand-written nftables rules around OVOS' services, tried on a real OVOS
(ovos-installer, user units, Fedora 44 in an **unprivileged** Proxmox LXC,
kernel 7.0, nftables 1.1.6), before the plugin itself is written
(DESIGN.md §9, steps 1–3).

| file | what |
|------|------|
| `netpolicy_poc.py` | the rules: `apply`, `watch` (re-apply on service restart), `bus` (answers `ovos.local_only` like a PHAL plugin), `off` |
| `probe.sh` | `curl` as the ovos user from inside one service's cgroup |
| `ask2.py` | ask OVOS on the bus, answer a yes/no question, print what is said |
| `scenario.sh` | the Guardian scenario: go offline, a question that needs the internet (yes and no), one answered locally, go online |

```bash
# as root; every OVOS service direct, local only switchable from the bus
/home/ovos/.venvs/ovos/bin/python netpolicy_poc.py bus --direct \
  ovos-core.service ovos-audio.service ovos-listener.service \
  ovos-phal.service ovos-control-panel.service ovos-messagebus.service
```

## What the rules look like

Everything OVOS' services send out is **blocked unless the service is listed
as direct**. OVOS' services are what runs in the ovos user's `app.slice`;
the same user's shells, ssh and `pip` are not OVOS and are left alone (a
first version matched the whole user, and `pip install` in an ssh session
was refused). Blocked means: localhost and the LAN are allowed, DNS (port
53) and the internet are refused (reject, so OVOS learns at once rather
than after a timeout).

```
socket cgroupv2 level 4 ".../user@1000.service/app.slice" jump ovos
# chain ovos:
oif "lo" accept
socket cgroupv2 level 5 "user.slice/user-1000.slice/user@1000.service/app.slice/ovos-audio.service" accept
...                                    (one line per direct service)
udp/tcp dport 53 reject
ip daddr { 10/8, 172.16/12, 192.168/16, 169.254/16, 224/4 } accept
reject
```

Local only = the same table with no direct services.

## Results

**Per-service blocking works, also in an unprivileged LXC.** nftables in the
container's own network namespace can match the container's cgroup paths;
the level counts from the container's cgroup root (`level 5` for a user
unit). `ovos-core` blocked: `curl` from its cgroup fails, while
`ovos-audio`, `ovos-listener` and the panel get out, and the LAN still
answers.

**OVOS with skills blocked** (`ovos-core` blocked, the rest direct):

| asked | answer |
|-------|--------|
| what time is it | answered, nothing needed |
| who was Lise Meitner | answered by `ovos-skill-wiki-offline` (one connection was refused meanwhile) |
| what is the weather | weather skill matched, then *"I can't get weather forecasts"* or *"An error occurred …"* |

**5.1 confirmed: a restarted service falls out of its rule.** Matching by
cgroup path gives the kernel a cgroup id; after `systemctl --user restart`
the service has a new one. With a rule that *blocks* a service, the
restarted service went straight out (fail open). Hence the shape above:
blocked is the default for the whole OVOS user and the rules only *allow*.
A restarted direct service is then blocked until the rules are re-applied,
never open. `watch` re-applies when a service's cgroup changes (polling the
cgroup's inode; the plugin should follow systemd's unit events instead) —
tried with `ovos-audio` and `ovos-core` restarted: back to direct within a
second.

**5.4 confirmed: names leak through systemd-resolved.** With port 53 refused,
lookups still worked: glibc asks `systemd-resolved` over its local socket
(`hosts: ... resolve ...`), and resolved asks the network as itself. In
blocked mode nothing connects, but the names asked for still leave the
house. Not solved here. Options: for system units a private
`/etc/nsswitch.conf` without `resolve` (`BindReadOnlyPaths=`); for user
units, an environment for the service that makes it use a stub resolver the
plugin controls; or accept it for `blocked` and solve it properly for `tor`.

**Local only from the bus works.** `bus` answers `ovos.local_only` and
`ovos.local_only.status` as ovos-skill-guardian asks, so "go offline" /
"go online" and the Guardian page in the control panel switch the whole
device. The bus can only switch between the configured baseline and
everything blocked — it can never open more than root's config (§5.12).
Each refused connection is sent as an `ovos.network.request` entry with
`"blocked": true`, which Guardian already understands. Found with Guardian:
the refusal must be reported quickly — the first try polled counters every
0.5 s and the report came *after* `ovos.utterance.handled`. The plugin should
read refusals from NFLOG (immediate, and it gives the address too) instead of
counters.

**Local only must move speech too.** On the test box STT and TTS are
`ovos-stt-plugin-server` / `ovos-tts-plugin-server`. With everything
blocked the device can neither hear nor speak — it went on fine over the
bus, but `get_response` waited ~13 s for speech that never played. Emkay
moves speech into the device when it goes local only; the plugin must do
the same (switch to installed on-device plugins, or refuse local only and
say why), and report the modules in its `ovos.local_only` answer as
Guardian expects (`stt`, `tts`).

**With Guardian: online for one question** (`scenario.sh`, ovos-skill-guardian
branch `retry-online`):

```
"go offline"                    -> local only on, "I'm offline now …"
"what is the weather in Copenhagen"
                                -> refused, "I can't access weather reports as of now."
                                -> "That needs the internet, and I'm offline.
                                    Shall I go online for a moment and try again?"
"yes"                           -> local only off, the question asked again:
                                   "It's currently overcast and 3.8 degrees …"
                                -> local only on, "I'm offline again."
"who was Lise Meitner"          -> answered by the offline wiki, no offer
```

## Not tried yet

- system units (`/system.slice/...`), Docker
- Tor and the DNS redirect
- IPv6 to the LAN (only link-local is allowed now)
