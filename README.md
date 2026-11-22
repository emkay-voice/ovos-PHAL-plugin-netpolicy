# <img src='res/icon.png' width='50' height='50' style='vertical-align:bottom'/> ovos-PHAL-plugin-netpolicy

Which of OpenVoiceOS' services may reach the internet – enforced in the
device's own firewall, so a skill cannot go around it – and **local only**,
switched by voice or from the control panel.

> **Status: first alpha.** `direct` and `blocked` per service, local only,
> refusals reported on the bus, a page in the control panel. Tor and other
> routes are designed ([docs/DESIGN.md](docs/DESIGN.md)) but not built.

Made for [Emkay](https://github.com/emkay-voice/emkay) – for Emkay Home and
any OpenVoiceOS on Linux – and works with
[ovos-skill-guardian](https://github.com/emkay-voice/ovos-skill-guardian), which
does the talking: *"go offline"*, *"what did you send out today?"*, and, when a
question needs the internet while the device is offline, *"shall I go online
for a moment and try again?"*.

## What it does

A PHAL **admin** plugin (it needs root for nftables). For each OVOS service –
`ovos-core` with all the skills, `ovos-audio`, the listener, the messagebus,
the control panel, `hivemind-core` … – the mode is:

| mode | means |
|------|-------|
| `direct` | normal network access (the default) |
| `blocked` | localhost and your own network only; DNS and the internet are refused |

**Local only** blocks every service. It is switched with the messages
Guardian and its Privacy page already send, so "go offline" works the same on
Emkay in a browser and on a Linux device:

```
ovos.local_only          {"enabled": bool}
ovos.local_only.status   {}
-> .response             {"enabled", "stt", "tts"}  (+ "error" when it said no)
ovos.local_only.changed  {"enabled", "stt", "tts"}
```

Local only is **refused** (`"error": "speech_not_local"`) while speech
recognition or the voice is a server plugin: the device would neither hear
nor speak. Choose on-device plugins first, or set `require_local_speech` to
false if that is really what you want. It is remembered across restarts.

Every refused connection is sent on the bus as an `ovos.network.request`
entry with `"blocked": true` – the [network log's format](https://github.com/emkay-voice/emkay/blob/main/docs/network-log.md) –
with the service and the address it tried, read from the firewall as it
happens (NFLOG).

```
ovos.netpolicy.status    {} -> the services and their modes, local only, today's refusals
ovos.netpolicy.reload    {} -> read the policy again and re-apply it
```

## How it holds

- **In the kernel.** nftables matches each service by its cgroup. A skill
  using `aiohttp`, raw sockets or a subprocess is caught like any other.
- **Closed when in doubt.** For ovos-installer's user services, everything in
  the OVOS user's `app.slice` is refused unless its service is allowed. A
  service that was just restarted (and has a new cgroup the rules do not
  know yet) is refused for the quarter of a second until the plugin has
  re-applied the rules – never let out. The same user's shells, ssh and
  `pip` are not OVOS and are left alone.
- **It stays closed.** While anything is blocked, stopping the plugin leaves
  its table in place, so a restart of the admin service does not open a gap.
- **The bus cannot open more than the policy.** Local only off returns to the
  configured routes, not to "everything direct".
- **Its own table only** (`inet ovos_netpolicy`); firewalld, ufw and Docker's
  rules are left alone.

Tried on ovos-installer user services in an unprivileged Proxmox LXC (Fedora
44). System units are handled too, but a restarted blocked *system* unit is
caught by the re-apply rather than by the slice. Docker is not covered.
**Known gap:** with systemd-resolved, names are looked up by resolved itself,
so the names a blocked service asks for still leave the device even though
the connections do not ([poc/README.md](poc/README.md)).

## Install

```bash
sudo dnf install nftables      # or: sudo apt install nftables
pip install ovos-PHAL-plugin-netpolicy   # in OVOS' environment
```

and enable it for the PHAL admin service (`ovos-phal-admin`) in mycroft.conf:

```json
{
  "PHAL": {
    "admin": {
      "ovos-PHAL-plugin-netpolicy": {
        "enabled": true,
        "routes": {"ovos-core": "blocked"}
      }
    }
  }
}
```

| setting | default | |
|---|---|---|
| `enabled` | `false` | nothing happens until it is true |
| `default` | `"direct"` | the mode of a service not in `routes` |
| `routes` | `{}` | mode per service: `"ovos-core"` or `"ovos-core.service"` |
| `lan_allow` | private IPv4 ranges | what stays open when blocked |
| `lan_allow6` | `::1`, `fe80::/10`, `fc00::/7`, `ff00::/8` | the same for IPv6 |
| `require_local_speech` | `true` | refuse local only while STT or TTS is a server |
| `local_speech` | `[]` | more on-device STT/TTS modules than the known ones |
| `nflog_group` | `4711` | where refusals are read from |
| `state_file` | `/var/lib/ovos-netpolicy/state.json` | remembers local only |

mycroft.conf belongs to the OVOS user, so OVOS itself could change it. Where
that matters, put the policy (`default`, `routes`, `lan_allow`, `lan_allow6`,
`require_local_speech`, `local_speech`) in `/etc/ovos-netpolicy/policy.json`:
it is read only when root owns it and nobody else may write it, and it wins
over mycroft.conf. The status and the panel page say which one is in force.

To take everything away: `sudo nft delete table inet ovos_netpolicy`.

## In the control panel

With [ovos-control-panel](https://github.com/OpenVoiceOS/ovos-control-panel)
the plugin brings a page, **Network policy**, under Device: whether the policy
is enforced, local only, where the policy comes from, every OVOS service with
its mode, and what was refused today – in all twelve languages Emkay ships. The
page is described in `controlpanel.json` and its words are in
`locale/<lang>/controlpanel.json` (generated from `tools/translations.py`); no
code of the plugin runs in the panel. Local only itself is switched on
Guardian's Privacy page, as by voice.

## License

Apache-2.0
