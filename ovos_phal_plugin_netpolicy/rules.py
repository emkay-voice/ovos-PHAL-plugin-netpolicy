"""The nftables table, written from a policy.

The shape is the one the proof of concept found to hold (poc/README.md):

- **user units** (ovos-installer's default): everything a user's
  ``app.slice`` with OVOS in it sends out goes through the ``ovos_user``
  chain, where only the units whose mode is ``direct`` are accepted. Anything
  else there – a blocked unit, but also a unit that was just restarted and
  has a new cgroup id the table does not know yet – is refused. A restart
  can therefore only ever close a door until the rules are re-applied, never
  open one. The same user's shells, ssh and pip are not in ``app.slice`` and
  are left alone.
- **system units**: system.slice has much more than OVOS in it, so a
  blocked system unit is matched by its own cgroup. After a restart it is
  not matched until the rules are re-applied (the plugin watches for that,
  every quarter of a second).

Refused means: localhost and the LAN stay open, DNS (port 53) and the rest
are refused with a reject (OVOS learns at once instead of waiting for a
timeout), and each refusal is logged to NFLOG with the unit's name as the
prefix, so the plugin can say who tried to reach what.
"""
import re

TABLE = "ovos_netpolicy"

LAN4 = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "224.0.0.0/4"]
LAN6 = ["::1", "fe80::/10", "fc00::/7", "ff00::/8"]

MODES = ("direct", "blocked")

_SAFE = re.compile(r"^[A-Za-z0-9@._:/\\-]+$")
_NET = re.compile(r"^[0-9A-Fa-f.:]+(/[0-9]{1,3})?$")


def _q(path: str) -> str:
    if not _SAFE.match(path):
        raise ValueError(f"not a cgroup path: {path!r}")
    return f'"{path}"'


def _nets(nets, what) -> str:
    for n in nets:
        if not _NET.match(n):
            raise ValueError(f"not an address or network in {what}: {n!r}")
    return ", ".join(nets)


def mode_of(unit, routes: dict, default: str, local_only: bool) -> str:
    """``direct`` or ``blocked`` for one unit."""
    if local_only:
        return "blocked"
    mode = routes.get(unit.name, routes.get(unit.short, default))
    return mode if mode in MODES else "blocked"  # an unknown mode fails closed


def _refuse_chain(name: str, prefix: str, lan4, lan6, group: int) -> list:
    log = f'log group {group} prefix "{prefix}"'
    lines = [f"  chain {name} {{",
             '    oif "lo" accept',
             f"    udp dport 53 {log} reject",
             f"    tcp dport 53 {log} reject with tcp reset"]
    if lan4:
        lines.append(f"    ip daddr {{ {_nets(lan4, 'lan_allow')} }} accept")
    if lan6:
        lines.append(f"    ip6 daddr {{ {_nets(lan6, 'lan_allow6')} }} accept")
    lines += [f"    {log}",
              "    meta l4proto tcp reject with tcp reset",
              "    reject",
              "  }"]
    return lines


def build(units, slices, routes=None, default="direct", local_only=False,
          lan4=None, lan6=None, group=4711) -> str:
    """The whole table, for ``nft -f``. ``units`` from units.find_units(),
    ``slices`` from units.app_slices()."""
    routes = routes or {}
    lan4 = LAN4 if lan4 is None else lan4
    lan6 = LAN6 if lan6 is None else lan6
    user = [u for u in units if u.scope == "user"]
    system = [u for u in units if u.scope == "system"]
    refuse = []  # (chain, prefix)

    def chain_for(unit):
        name = f"refuse_{len(refuse)}"
        refuse.append((name, unit.short))
        return name

    out = [f"table inet {TABLE} {{",
           "  chain output {",
           "    type filter hook output priority 0; policy accept;"]
    for path, _ in slices:
        out.append(f"    socket cgroupv2 level {path.count('/') + 1} {_q(path)} jump ovos_user")
    for u in system:
        if mode_of(u, routes, default, local_only) == "blocked":
            out.append(f"    socket cgroupv2 level {u.level} {_q(u.path)} goto {chain_for(u)}")
    out.append("  }")

    out += ["  chain ovos_user {"]
    for u in user:
        if mode_of(u, routes, default, local_only) == "direct":
            out.append(f"    socket cgroupv2 level {u.level} {_q(u.path)} accept")
    for u in user:
        if mode_of(u, routes, default, local_only) == "blocked":
            out.append(f"    socket cgroupv2 level {u.level} {_q(u.path)} goto {chain_for(u)}")
    # not known (yet): a unit that just started or restarted
    out += ["    goto refuse_unknown", "  }"]

    for name, prefix in refuse:
        out += _refuse_chain(name, prefix, lan4, lan6, group)
    out += _refuse_chain("refuse_unknown", "ovos", lan4, lan6, group)
    out.append("}")
    return "\n".join(out) + "\n"
