#!/usr/bin/python3
"""Install or stage the opt-in Decktil keyboard for the SteamOS Gaming Mode session."""
import argparse
import base64
import fcntl
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UNIT = "decktil-gaming-pilot.service"
BIG_PICTURE_UNIT = "decktil-bigpicture.service"
PACKAGES = ("python-pyqt6", "python-pyqt6-sip")


def coordinate_legacy_layout_sync(units):
    # Earlier Deck setups align KDE to Steam's OSK language every two seconds.
    # Preserve that helper and its timer. Let it take turns with this panel.
    name = "steam-osk-layout-sync.service"
    unit = units / name
    helper = Path.home() / ".local/bin/steam-osk-layout-sync"
    if not unit.is_file() or not helper.is_file():
        return
    commands = [line.strip() for line in unit.read_text().splitlines() if line.lstrip().startswith("ExecStart=")]
    if commands != [f"ExecStart={helper}"]:
        return
    folder = units / (name + ".d")
    dest = folder / "50-decktil-layout.conf"
    if any("ExecStart=" in p.read_text() for p in folder.glob("*.conf") if p != dest):
        return
    contents = ("[Service]\nExecStart=\n"
                "ExecStart=/usr/bin/flock --nonblock --conflict-exit-code=0 "
                "%t/decktil-bigpicture-layout.lock %h/.local/bin/steam-osk-layout-sync\n")
    if dest.exists():
        return
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, delete=False) as staged:
        staged.write(contents.encode())
        os.fchmod(staged.fileno(), 0o644)
    os.replace(staged.name, dest)


def install_bindings(packages, snapshot, scratch):
    """Verify the selected host repository packages, then copy just the panel's bindings."""
    rows = subprocess.check_output(
        ["pacman", "-Spdd", "--print-format", "%n %f %h %g", *PACKAGES], text=True).splitlines()
    if {row.split()[0] for row in rows} != set(PACKAGES) or len(rows) != len(PACKAGES):
        raise ValueError("The two Qt binding packages are missing from the host pacman database.")
    rings = []
    for name in ("archlinux", "holo"):
        ring = Path("/usr/share/pacman/keyrings") / (name + ".gpg")
        if ring.read_bytes().startswith(b"-----BEGIN"):
            binary = scratch / ring.name
            subprocess.run(["gpg", "--no-options", "--homedir", str(scratch), "--batch",
                            "--dearmor", "--output", str(binary), str(ring)], check=True)
            ring = binary
        rings.extend(["--keyring", str(ring)])
    module_dir = snapshot / "bindings" / "PyQt6"
    module_dir.mkdir(parents=True)
    licenses = snapshot / "bindings" / "licenses"
    licenses.mkdir()
    site = f"usr/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/"
    wanted = {"__init__.py", "QtCore.abi3.so", "QtGui.abi3.so", "QtNetwork.abi3.so",
              "QtQml.abi3.so", "QtQuick.abi3.so", "sip" + sysconfig.get_config_var("EXT_SUFFIX")}
    copied = set()
    for row in rows:
        name, filename, checksum, signature = row.split()
        archive = (packages / filename).read_bytes()
        if hashlib.sha256(archive).hexdigest() != checksum:
            raise ValueError(f"SHA-256 differs from the pacman database for {filename}")
        sigfile = scratch / (name + ".sig")
        sigfile.write_bytes(base64.b64decode(signature, validate=True))
        subprocess.run(["gpgv", "--homedir", str(scratch), *rings, str(sigfile), "-"],
                       input=archive, check=True)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as package:
            for member in package:
                if not member.isfile():
                    continue
                file = Path(member.name)
                if str(file.parent) == site + "PyQt6" and file.name in wanted:
                    output = module_dir / file.name
                    copied.add(file.name)
                elif member.name.startswith(site) and member.name.endswith(".dist-info/licenses/LICENSE"):
                    output = licenses / (name + "-LICENSE")
                else:
                    continue
                output.write_bytes(package.extractfile(member).read())
    if copied != wanted:
        raise ValueError("These packages lack bindings for the current system Python.")


def build(snapshot):
    src = snapshot / "src"
    src.mkdir()
    for path in (ROOT / "src").iterdir():
        if path.suffix in {".py", ".qml", ".js"}:
            shutil.copyfile(path, src / path.name)
    words = src / "words"
    words.mkdir()
    for name in ("en.txt.gz", "ru.txt.gz", "en-next.json.gz", "ru-next.json.gz", "ru-morph.json.gz", "NOTICE.md"):
        shutil.copyfile(ROOT / "src/words" / name, words / name)
    gaming = snapshot / "experimental" / "gaming"
    gaming.mkdir(parents=True)
    for name in ("controller.py", "focus.py", "output.py", "overlay.py", "session.py", "panel.py", "panel.qml",
                 "manual.py", "bigpicture_focus.py", "evdev_output.py", "kde_layout.py",
                 "auto_keyboard.py", "inline_state.py", "accessible_inline.py", "typed_context.py", "search_space.py"):
        shutil.copyfile(HERE / name, gaming / name)
    shutil.copyfile(ROOT / "LICENSE", snapshot / "LICENSE")


def install(packages, prepare):
    base = Path.home() / ".local/share/decktil-gaming"
    releases = base / "releases"
    releases.mkdir(mode=0o700, parents=True, exist_ok=True)
    user_config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    units = user_config / "systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    with (base / ".install.lock").open("a") as lock, tempfile.TemporaryDirectory(dir=base) as tmp:
        fcntl.flock(lock, fcntl.LOCK_EX)
        scratch = Path(tmp)
        snapshot = scratch / "snapshot"
        snapshot.mkdir()
        build(snapshot)
        if packages:
            install_bindings(packages, snapshot, scratch)
        # Check the copied modules against the host's actual Python and Qt ABI.
        code = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'bindings'))
from PyQt6 import QtCore, QtGui, QtQml, QtQuick
sys.path.insert(0, str(Path(sys.argv[1]) / 'experimental/gaming'))
import session, focus, overlay, manual, bigpicture_focus, evdev_output, kde_layout
import auto_keyboard, inline_state, accessible_inline
"""
        subprocess.run(["/usr/bin/python3", "-IB", "-c", code, str(snapshot)], check=True)
        digest = hashlib.sha256()
        for path in sorted(snapshot.rglob("*")):
            if path.is_file():
                digest.update(str(path.relative_to(snapshot)).encode() + b"\0")
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        release = releases / digest.hexdigest()
        if not release.exists():
            snapshot.rename(release)
        new_link = scratch / "current"
        new_link.symlink_to(release.relative_to(base), target_is_directory=True)
        # Replace each file atomically. Existing processes keep their resolved snapshot.
        for unit in (UNIT, BIG_PICTURE_UNIT):
            with tempfile.NamedTemporaryFile(dir=units, delete=False) as new_unit:
                new_unit.write((HERE / unit).read_bytes())
                os.fchmod(new_unit.fileno(), 0o644)
            os.replace(new_unit.name, units / unit)
        new_link.replace(base / "current")
        if not prepare:
            coordinate_legacy_layout_sync(units)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "--user", "enable", UNIT, BIG_PICTURE_UNIT], check=True)
            subprocess.run(["systemctl", "--user", "try-restart", UNIT], check=True)
            if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "deck-plasma-keyboard.service"]).returncode == 0:
                subprocess.run(["systemctl", "--user", "restart", BIG_PICTURE_UNIT], check=True)
    print(f"Decktil snapshot: {release}")
    print("Prepared without enabling services." if prepare else "Enabled for Gaming Mode and KDE Big Picture.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, help="directory containing the two original SteamOS pacman archives")
    parser.add_argument("--prepare-only", action="store_true", help="copy and verify files without enabling the service")
    args = parser.parse_args()
    try:
        install(args.packages, args.prepare_only)
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Install failed: {error}\n")
