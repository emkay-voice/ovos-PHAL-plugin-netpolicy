"""Finding OVOS' services in the cgroup tree, without asking systemd.

OVOS' services are the units named ovos-…, hivemind-…, mycroft-… or neon-…
(ovos-installer's names). They run either as a user's units, in
``user.slice/user-<uid>.slice/user@<uid>.service/app.slice/``, or as system
units in ``system.slice/``. A unit's cgroup is a folder; its inode is the
cgroup id nftables matches on, and it changes when the unit is restarted.
"""
import os
from dataclasses import dataclass
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")

#: what counts as OVOS
PREFIXES = ("ovos-", "hivemind-", "mycroft-", "neon-")
#: OVOS units that are never policed: the admin service is this plugin itself
EXCLUDED = ("ovos-phal-admin.service",)


@dataclass(frozen=True)
class Unit:
    name: str      # ovos-core.service
    path: str      # cgroup path below the root, no leading slash
    scope: str     # "user" or "system"
    inode: int     # the cgroup id; a restart gives a new one

    @property
    def level(self) -> int:
        """The depth nftables' ``socket cgroupv2 level`` matches at."""
        return self.path.count("/") + 1

    @property
    def short(self) -> str:
        """ovos-core.service -> ovos-core"""
        return self.name[:-len(".service")] if self.name.endswith(".service") else self.name


def is_ovos(name: str) -> bool:
    return name.endswith(".service") and name.startswith(PREFIXES) and name not in EXCLUDED


def _inode(path: Path):
    try:
        return os.stat(path).st_ino
    except OSError:
        return None


def app_slices(root: Path = CGROUP_ROOT) -> list:
    """The app.slice of every user manager that runs an OVOS unit, as
    (path below the root, inode)."""
    out = []
    for user in sorted((root / "user.slice").glob("user-*.slice/user@*.service/app.slice")):
        if any(is_ovos(p.name) for p in user.iterdir() if p.is_dir()):
            ino = _inode(user)
            if ino:
                out.append((str(user.relative_to(root)), ino))
    return out


def find_units(root: Path = CGROUP_ROOT) -> list:
    """Every running OVOS unit, user and system."""
    units = []
    for app, _ in app_slices(root):
        for p in sorted((root / app).iterdir()):
            if p.is_dir() and is_ovos(p.name):
                ino = _inode(p)
                if ino:
                    units.append(Unit(p.name, str(p.relative_to(root)), "user", ino))
    system = root / "system.slice"
    if system.is_dir():
        for p in sorted(system.iterdir()):
            if p.is_dir() and is_ovos(p.name):
                ino = _inode(p)
                if ino:
                    units.append(Unit(p.name, str(p.relative_to(root)), "system", ino))
    return units


def fingerprint(root: Path = CGROUP_ROOT) -> tuple:
    """Changes whenever a unit starts, stops or restarts: re-apply then."""
    return tuple(app_slices(root)) + tuple((u.path, u.inode) for u in find_units(root))
