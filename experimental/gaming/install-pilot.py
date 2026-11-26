#!/usr/bin/python3
"""Install or stage the opt-in Decktil keyboard for the SteamOS Gaming Mode session."""
import argparse
import base64
import fcntl
import hashlib
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UNIT = "decktil-gaming-pilot.service"
BIG_PICTURE_UNIT = "decktil-bigpicture.service"
PACKAGES = ("python-pyqt6", "python-pyqt6-sip")


DROP_IN = Path("steam-osk-layout-sync.service.d/50-decktil-layout.conf")
DROP_IN_CONTENT = ("[Service]\nExecStart=\n"
                   "ExecStart=/usr/bin/flock --nonblock --conflict-exit-code=0 "
                   "%t/decktil-bigpicture-layout.lock %h/.local/bin/steam-osk-layout-sync\n").encode()
UNITS = (UNIT, BIG_PICTURE_UNIT)


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
    if dest.exists() or dest.is_symlink():
        return
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, delete=False) as staged:
        staged.write(DROP_IN_CONTENT)
        os.fchmod(staged.fileno(), 0o644)
    os.replace(staged.name, dest)


def repository_archive(packages, filename, checksum, location, scratch):
    if Path(filename).name != filename or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Invalid Qt package metadata in the host pacman database")
    if packages:
        return (packages / filename).read_bytes()
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "decktil/packages"
    for folder in (Path("/var/cache/pacman/pkg"), cache):
        candidate = folder / filename
        if candidate.is_file():
            data = candidate.read_bytes()
            if hashlib.sha256(data).hexdigest() == checksum:
                return data
    if not location.startswith("https://"):
        raise ValueError("The configured SteamOS package mirror must use HTTPS")
    target = scratch / filename
    subprocess.run(["curl", "--fail", "--silent", "--show-error", "--location",
                    "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "10",
                    "--max-time", "120", "--retry", "1", "--output", str(target), location], check=True)
    data = target.read_bytes()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError(f"SHA-256 differs from the pacman database for {filename}")
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as cached:
        cached.write(data)
    os.replace(cached.name, cache / filename)
    return data


def package_rows(*options):
    result = subprocess.run(["pacman", "-Spdd", *options, "--print-format", "%n %f %h %g %l", *PACKAGES],
                            capture_output=True, text=True)
    rows = result.stdout.splitlines()
    if (result.returncode == 0 and len(rows) == len(PACKAGES)
            and {line.split(maxsplit=1)[0] for line in rows if line.strip()} == set(PACKAGES)):
        return rows
    return []


def steamos_mirror(server):
    url = urlsplit(server)
    return (url.scheme == "https" and url.hostname == "steamdeck-packages.steamos.cloud"
            and not url.username and not url.password and url.port in (None, 443)
            and not url.query and not url.fragment and not any(char.isspace() for char in server))


def package_metadata(scratch):
    rows = package_rows()
    if rows:
        return rows

    # A factory image can lack the sync databases. Ask pacman-conf to expand the
    # configured release branch and architecture. Keep repo order and use only
    # Valve's configured HTTPS mirror. The private configuration never installs
    # packages, updates the system's databases or needs a pacman signing key.
    repos = subprocess.check_output(["pacman-conf", "--repo-list"], text=True).splitlines()
    db = scratch / "pacman-db"
    sync = db / "sync"
    sync.mkdir(parents=True)
    sections = ["[options]", "Architecture = auto", "SigLevel = Required DatabaseOptional"]
    for repo in repos:
        if not re.fullmatch(r"(jupiter|holo|core|extra|multilib)(-[A-Za-z0-9_.-]+)?", repo):
            continue
        servers = subprocess.check_output(["pacman-conf", "-r", repo, "Server"], text=True).splitlines()
        servers = [server.rstrip("/") for server in servers if steamos_mirror(server)]
        if not servers:
            continue
        for server in servers:
            location = f"{server}/{repo}.db"
            result = subprocess.run(["curl", "--fail", "--silent", "--show-error", "--proto", "=https",
                                     "--connect-timeout", "10", "--max-time", "90", "--retry", "1",
                                     "--output", str(sync / (repo + ".db")), location])
            if result.returncode == 0:
                break
        else:
            raise RuntimeError(f"SteamOS package metadata could not be downloaded for {repo}")
        sections.extend(["[" + repo + "]", "Server = " + server])
    config = scratch / "pacman.conf"
    config.write_text("\n".join(sections) + "\n")
    rows = package_rows("--config", str(config), "--dbpath", str(db))
    if not rows:
        raise RuntimeError("The configured SteamOS repositories lack the two Qt binding packages")
    return rows


def install_bindings(packages, snapshot, scratch):
    """Verify the selected host repository packages, then copy just the panel's bindings."""
    rows = package_metadata(scratch)
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
        name, filename, checksum, signature, location = row.split()
        archive = repository_archive(packages, filename, checksum, location, scratch)
        if hashlib.sha256(archive).hexdigest() != checksum:
            raise ValueError(f"SHA-256 differs from the pacman database for {filename}")
        sigfile = scratch / (name + ".sig")
        sigfile.write_bytes(base64.b64decode(signature, validate=True))
        subprocess.run(["gpgv", "--homedir", str(scratch), *rings, str(sigfile), "-"],
                       input=archive, check=True)
        # Some SteamOS Python versions predate tarfile's native zstd support.
        unpacked = scratch / (name + ".tar")
        with unpacked.open("wb") as output:
            subprocess.run(["zstd", "--decompress", "--stdout", "--quiet"], input=archive,
                           stdout=output, check=True)
        with tarfile.open(unpacked, mode="r:") as package:
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


def base_directory():
    # These paths also appear as %h in the existing user services.
    return Path.home() / ".local/share/decktil-gaming"


def prepare_release(packages=None):
    base = base_directory()
    releases = base / "releases"
    releases.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        scratch = Path(tmp)
        snapshot = scratch / "snapshot"
        snapshot.mkdir()
        build(snapshot)
        qt_import = "from PyQt6 import QtCore, QtGui, QtNetwork, QtQml, QtQuick"
        if packages or subprocess.run(["/usr/bin/python3", "-IB", "-c", qt_import],
                                      stderr=subprocess.DEVNULL).returncode:
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
        release = releases / snapshot_digest(snapshot)
        if not release.exists():
            snapshot.rename(release)
        elif release.is_symlink() or snapshot_digest(release) != release.name:
            raise RuntimeError(f"An installed Decktil snapshot was modified: {release}")
        return str(release.relative_to(base))


def snapshot_digest(folder):
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            return None
        if path.is_file():
            digest.update(str(path.relative_to(folder)).encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def current_release():
    current = base_directory() / "current"
    if current.is_symlink():
        target = os.readlink(current)
        if re.fullmatch(r"releases/[a-f0-9]{64}", target):
            return target
    elif not current.exists():
        return None
    raise RuntimeError(f"Unrecognized Decktil snapshot link: {current}")


def select_release(target):
    base = base_directory()
    if current_release() == target:
        return
    if target is None:
        (base / "current").unlink()
        return
    if not re.fullmatch(r"releases/[a-f0-9]{64}", target) or not (base / target).is_dir():
        raise RuntimeError("Decktil snapshot is missing")
    with tempfile.TemporaryDirectory(dir=base) as tmp:
        link = Path(tmp) / "current"
        link.symlink_to(target, target_is_directory=True)
        link.replace(base / "current")


def unit_kind(units, name):
    unit = units / name
    if unit.is_symlink():
        return "foreign"
    if not unit.exists():
        # A different system unit with this name must also remain untouched.
        if any((parent / name).exists() for parent in (Path("/etc/systemd/user"), Path("/usr/lib/systemd/user"))):
            return "foreign"
        return "missing"
    if not unit.is_file() or unit.read_bytes() != (HERE / name).read_bytes():
        return "foreign"
    return "owned"


def systemctl(*args, **kwargs):
    return subprocess.run(["systemctl", "--user", *args], check=True, **kwargs)


def unit_flags(name):
    result = systemctl("show", name, "--property=ActiveState,UnitFileState", capture_output=True, text=True)
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    state = values.get("ActiveState")
    return {"active": state in {"active", "activating", "reloading"},
            "enabled": values.get("UnitFileState") == "enabled", "failed": state == "failed"}


def status(units):
    result = {}
    for key, name in (("gaming_mode", UNIT), ("big_picture", BIG_PICTURE_UNIT)):
        kind = unit_kind(units, name)
        if kind == "owned":
            try:
                flags = unit_flags(name)
            except (OSError, subprocess.CalledProcessError):
                result[key] = "mixed"
                continue
            try:
                current = current_release()
            except RuntimeError:
                result[key] = "foreign"
                continue
            ready = current and (base_directory() / current / "experimental/gaming/session.py").is_file()
            if flags["failed"]:
                kind = "mixed"
            elif flags["enabled"]:
                kind = "enabled" if ready else "mixed"
            else:
                kind = "mixed" if flags["active"] else "disabled"
        result[key] = kind
    return result


def capture(units):
    states = {}
    for name in UNITS:
        kind = unit_kind(units, name)
        if kind == "foreign":
            raise RuntimeError(f"An unmanaged or modified file exists: {units / name}")
        states[name] = unit_flags(name) if kind == "owned" else None
    drop = units / DROP_IN
    return {"units": states, "release": current_release(), "drop_in": drop.exists() or drop.is_symlink()}


def install_files(units, release, *, prepare_only=False):
    units.mkdir(parents=True, exist_ok=True)
    for name in UNITS:
        kind = unit_kind(units, name)
        if kind == "foreign":
            raise RuntimeError(f"An unmanaged or modified file exists: {units / name}")
        if kind == "missing":
            with tempfile.NamedTemporaryFile(dir=units, delete=False) as new_unit:
                new_unit.write((HERE / name).read_bytes())
                os.fchmod(new_unit.fileno(), 0o644)
            os.replace(new_unit.name, units / name)
    select_release(release)
    if not prepare_only:
        coordinate_legacy_layout_sync(units)
        systemctl("daemon-reload")


def activate():
    systemctl("enable", *UNITS)
    systemctl("try-restart", UNIT)
    systemctl("restart", BIG_PICTURE_UNIT)
    systemctl("is-active", "--quiet", BIG_PICTURE_UNIT)
    for name in UNITS:
        systemctl("is-enabled", "--quiet", name)


def remove_layout_coordination(units):
    drop = units / DROP_IN
    if drop.is_file() and not drop.is_symlink() and drop.read_bytes() == DROP_IN_CONTENT:
        drop.unlink()
        return True
    return False


def disable(units):
    owned = [name for name in UNITS if unit_kind(units, name) == "owned"]
    if owned:
        systemctl("disable", "--now", *owned)
    if remove_layout_coordination(units):
        systemctl("daemon-reload")


def uninstall(units):
    for name in UNITS:
        if unit_kind(units, name) == "owned":
            (units / name).unlink()
    remove_layout_coordination(units)
    # Preserve any locally edited snapshot. Only remove directories whose contents
    # still match their original, installer-generated name.
    select_release(None)
    releases = base_directory() / "releases"
    for release in releases.glob("*"):
        if (re.fullmatch(r"[0-9a-f]{64}", release.name) and release.is_dir()
                and not release.is_symlink() and snapshot_digest(release) == release.name):
            shutil.rmtree(release)
    systemctl("daemon-reload")


def restore(units, before):
    for name, flags in before["units"].items():
        kind = unit_kind(units, name)
        if kind == "foreign":
            raise RuntimeError(f"An unmanaged or modified file exists: {units / name}")
        if kind == "owned" and (not flags or not flags["active"]):
            systemctl("stop", name)
        if kind == "owned":
            systemctl("enable" if flags and flags["enabled"] else "disable", name)
            if flags is None:
                (units / name).unlink()
    if before["drop_in"]:
        coordinate_legacy_layout_sync(units)
    else:
        remove_layout_coordination(units)
    select_release(before["release"])
    systemctl("daemon-reload")
    for name, flags in before["units"].items():
        if flags and flags["active"]:
            systemctl("restart", name)


def install(packages, prepare):
    units = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user"
    base = base_directory()
    base.mkdir(parents=True, exist_ok=True)
    with (base / ".install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = capture(units)
        release = prepare_release(packages)
        try:
            install_files(units, release, prepare_only=prepare)
            if not prepare:
                systemctl("enable", *UNITS)
                systemctl("try-restart", UNIT)
                if subprocess.run(["systemctl", "--user", "is-active", "--quiet", "deck-plasma-keyboard.service"]).returncode == 0:
                    systemctl("restart", BIG_PICTURE_UNIT)
        except Exception:
            restore(units, before)
            raise
    print(f"Decktil snapshot: {release}")
    print("Prepared without enabling services." if prepare else "Enabled for Gaming Mode and KDE Big Picture.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, help="directory containing the two original SteamOS pacman archives")
    parser.add_argument("--prepare-only", action="store_true", help="copy and verify files without enabling the service")
    args = parser.parse_args()
    try:
        install(args.packages, args.prepare_only)
    except (OSError, ValueError, RuntimeError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Install failed: {error}\n")
