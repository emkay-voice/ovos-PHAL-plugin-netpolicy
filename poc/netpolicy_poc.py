#!/usr/bin/env python3
"""netpolicy PoC: fail-closed per-service policy for OVOS user units.

Everything OVOS' services (the ovos user's app.slice) send out is blocked by default (LAN + localhost
allowed). Services listed as direct get normal access. Because a restarted
service gets a new cgroup id, the rules are re-applied when a cgroup's inode
changes; until then the restarted service falls into "blocked" (fail closed),
never into "direct".

  netpolicy_poc.py apply|watch|bus|off [--uid 1000] [--direct svc ...] [--offline]

"bus" is "watch" plus the messagebus: it answers ovos.local_only[.status] the
way ovos-skill-guardian asks (local only on = every service blocked, off =
back to the --direct baseline; the bus can never open more than that), and
sends each refused connection as an ovos.network.request entry with
"blocked": true, in the network log's format. Needs ovos-bus-client, so run
it with the OVOS venv's python.
"""
import argparse, os, re, subprocess, time

TABLE = "ovos_netpolicy"
LAN = "10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 224.0.0.0/4"


def app_slice(uid):
    return f"user.slice/user-{uid}.slice/user@{uid}.service/app.slice"


def cg_inode(path):
    try:
        return os.stat("/sys/fs/cgroup/" + path).st_ino
    except FileNotFoundError:
        return None


def ruleset(uid, direct, offline):
    # only OVOS' own services (the user manager's app.slice): the ovos user's
    # shells, ssh and pip are not OVOS and are left alone
    app = app_slice(uid)
    lines = [f"table inet {TABLE} {{",
             "  chain output {",
             "    type filter hook output priority 0; policy accept;",
             f'    socket cgroupv2 level {app.count("/") + 1} "{app}" jump ovos',
             "  }",
             "  chain ovos {",
             '    oif "lo" accept']
    if not offline:
        for svc in direct:
            path = f"{app_slice(uid)}/{svc}"
            if cg_inode(path):  # a service that is not running cannot be matched
                lvl = path.count("/") + 1
                lines.append(f'    socket cgroupv2 level {lvl} "{path}" accept')
    lines += [
        '    udp dport 53 counter log prefix "netpolicy-dns: " reject',
        '    tcp dport 53 counter log prefix "netpolicy-dns: " reject with tcp reset',
        f"    ip daddr {{ {LAN} }} accept",
        "    ip6 daddr { ::1, fe80::/10, ff00::/8 } accept",
        '    meta l4proto tcp counter log prefix "netpolicy-drop: " reject with tcp reset',
        '    counter log prefix "netpolicy-drop: " reject',
        "  }", "}"]
    return "\n".join(lines) + "\n"


def apply(uid, direct, offline):
    subprocess.run(["nft", "delete", "table", "inet", TABLE], capture_output=True)
    subprocess.run(["nft", "-f", "-"], input=ruleset(uid, direct, offline), text=True, check=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["apply", "watch", "bus", "off", "show"])
    p.add_argument("--uid", type=int, default=1000)
    p.add_argument("--direct", nargs="*", default=[])
    p.add_argument("--offline", action="store_true", help="ignore --direct: whole device local only")
    a = p.parse_args()
    if a.cmd == "off":
        subprocess.run(["nft", "delete", "table", "inet", TABLE])
        return
    if a.cmd == "show":
        print(ruleset(a.uid, a.direct, a.offline))
        return
    apply(a.uid, a.direct, a.offline)
    if a.cmd == "bus":
        return run_bus(a)
    if a.cmd == "watch":
        seen = {s: cg_inode(f"{app_slice(a.uid)}/{s}") for s in a.direct}
        while True:
            time.sleep(0.5)
            now = {s: cg_inode(f"{app_slice(a.uid)}/{s}") for s in a.direct}
            if now != seen:
                apply(a.uid, a.direct, a.offline)
                print("re-applied:", [s for s in now if now[s] != seen[s]], flush=True)
                seen = now


def refused_count():
    """Packets the blocked rules refused so far (all counters in the table)."""
    out = subprocess.run(["nft", "list", "table", "inet", TABLE], capture_output=True, text=True).stdout
    return sum(int(w) for w in re.findall(r"packets (\d+)", out))


def run_bus(a):
    from ovos_bus_client import MessageBusClient, Message
    state = {"offline": a.offline}
    bus = MessageBusClient()
    bus.run_in_thread()
    bus.connected_event.wait(30)

    def answer(message, changed=False):
        data = {"enabled": state["offline"], "stt": "", "tts": ""}
        bus.emit(message.response(data))
        if changed:
            bus.emit(Message("ovos.local_only.changed", data))

    def switch(message):
        enabled = bool(message.data.get("enabled"))
        changed = enabled != state["offline"]
        state["offline"] = enabled
        apply(a.uid, a.direct, enabled)
        state["reset"] = True  # a new table starts its counters at 0
        print("local only", "on" if enabled else "off", flush=True)
        answer(message, changed)

    bus.on("ovos.local_only", switch)
    bus.on("ovos.local_only.status", answer)
    seen = {s: cg_inode(f"{app_slice(a.uid)}/{s}") for s in a.direct}
    last = refused_count()
    while True:
        time.sleep(0.2)
        now = {s: cg_inode(f"{app_slice(a.uid)}/{s}") for s in a.direct}
        if now != seen:
            apply(a.uid, a.direct, state["offline"])
            state["reset"] = True
            seen = now
        if state.pop("reset", False):
            last = 0
        count = refused_count()
        if count > last:
            bus.emit(Message("ovos.network.request", {
                "t": int(time.time() * 1000), "host": "?", "method": "CONNECT", "status": 0,
                "bytes": 0, "who": "ovos", "kind": "refused", "service": "refused by netpolicy",
                "blocked": True, "count": count - last}))
        last = count


if __name__ == "__main__":
    main()
