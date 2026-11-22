"""A fake cgroup tree: the folders ovos-installer's units make, user and system."""
import pytest

APP = "user.slice/user-1000.slice/user@1000.service/app.slice"
USER_UNITS = ("ovos-core.service", "ovos-audio.service", "ovos-listener.service",
              "ovos-messagebus.service", "ovos-control-panel.service", "some-app.service")
SYSTEM_UNITS = ("ovos-phal-admin.service", "sshd.service", "hivemind-core.service")


@pytest.fixture
def cgroups(tmp_path):
    root = tmp_path / "cgroup"
    for name in USER_UNITS:
        (root / APP / name).mkdir(parents=True)
    for name in SYSTEM_UNITS:
        (root / "system.slice" / name).mkdir(parents=True)
    return root


def restart(root, path):
    """What systemd does to a unit's cgroup on restart: a new folder, a new id."""
    folder = root / path
    folder.rmdir()
    (root / "tmp-keep-inode").mkdir()  # so the new folder cannot reuse the inode
    folder.mkdir()
    (root / "tmp-keep-inode").rmdir()
