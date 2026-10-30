# ovos-PHAL-plugin-netpolicy

Per-service network policy for OpenVoiceOS — including enforced Tor routing.

> **Status: idea / design stage.** Nothing here routes traffic yet. This repo
> describes what the plugin should do, why, and what is hard about it.
> See [docs/DESIGN.md](docs/DESIGN.md) for the full design notes.

A PHAL **admin** plugin for [OpenVoiceOS](https://openvoiceos.org) that lets you
decide, per OVOS service, how that service may reach the network:

| mode     | meaning                                                        |
|----------|----------------------------------------------------------------|
| `tor`    | all TCP is forced through Tor, DNS through Tor, everything else dropped |
| `direct` | normal network access (optionally limited to an allow-list)    |
| `blocked`| no outbound network at all (LAN/localhost still allowed)       |

The typical use case: a privacy-focused assistant where speech (STT/TTS) goes
directly to your own or the OVOS servers, but every *lookup* a skill makes
(Wikipedia, weather, news, search…) leaves the house through Tor.

```
 ovos-dinkum-listener  ──────── direct ────────▶  STT server
 ovos-audio            ──────── direct ────────▶  TTS server
 ovos-core (skills)    ── nftables redirect ──▶  Tor TransPort ──▶ internet
                       ── UDP/53 redirect ────▶  Tor DNSPort
```

## Why enforced routing, not just a proxy?

Setting `HTTPS_PROXY=socks5h://…` on `ovos-core` works for skills using
`requests`, but it is voluntary: `aiohttp` without `trust_env`, raw sockets and
other libraries silently ignore it and go straight out. This plugin instead
enforces the policy in the kernel (nftables), so a skill **cannot** bypass it,
and it **fails closed**: if Tor is down, the `tor` services get no network,
rather than falling back to a direct connection.

## Why a PHAL admin plugin?

Enforcing routing needs root (nftables, managing the Tor daemon). The
`ovos_PHAL_admin` service already runs as root and loads plugins from the
`opm.phal.admin` entry point, with config from `mycroft.conf` → `PHAL.admin`.
So the plugin installs, configures and talks over the bus like any other OVOS
plugin.

## Planned configuration

```json
{
  "PHAL": {
    "admin": {
      "ovos-PHAL-plugin-netpolicy": {
        "enabled": true,
        "fail_closed": true,
        "routes": {
          "ovos-core.service": "tor",
          "ovos-dinkum-listener.service": "direct",
          "ovos-audio.service": "direct",
          "ovos-phal.service": "direct"
        },
        "lan_allow": ["127.0.0.0/8", "192.168.0.0/16", "10.0.0.0/8"],
        "direct_allow": [],
        "block_ipv6": true,
        "tor": {
          "binary": "tor",
          "install_tor": false,
          "trans_port": 9040,
          "dns_port": 5353,
          "control_port": 9051
        }
      }
    }
  }
}
```

## Tor

The plugin runs **its own Tor instance** with a torrc generated from
`mycroft.conf` (own data directory and ports, never touching
`/etc/tor/torrc`). It does not install Tor by default — install the `tor`
package with your distro (`apt install tor` / `dnf install tor`), or set
`"install_tor": true` to opt in.

Modes beyond `direct`/`blocked` come from **backend packages** pulled in as
extras (`pip install ovos-PHAL-plugin-netpolicy[tor]`), so others can add
their own (WireGuard, I2P, …). A bundled tor binary (Tor Expert Bundle) is
planned as an optional extra for x86_64 and aarch64 — see the design notes.

Tor is just one mode: `blocked` and allow-lists are useful on their own (a
LAN-only device, a child's room), and a WireGuard `vpn` mode could follow.

## Planned bus API

| message                     | purpose                                         |
|-----------------------------|-------------------------------------------------|
| `ovos.netpolicy.status`           | is Tor up, bootstrapped, which rules are active |
| `ovos.netpolicy.new_identity`     | request a new Tor circuit (NEWNYM via control port) |
| `ovos.netpolicy.reload`           | re-read config and re-apply firewall rules       |

A small companion skill could answer "are you using Tor?" / "get a new
identity", and a GUI/control-panel page could show the status.

## Scope of a first version

- systemd installs (ovos-installer, user or system units) on Linux with nftables
- per-*service* routing (all skills in `ovos-core` share one policy)
- IPv4 routed, IPv6 blocked for `tor` services
- Docker installs, per-skill routing and Tor onion services are **out of scope**
  for v1 — see the design notes

## License

Apache-2.0
