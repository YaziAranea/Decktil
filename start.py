#!/usr/bin/python3
"""Keep the keyboard manager available in the app menu after unpacking a release."""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import setup

SOURCE = Path(__file__).resolve().parent
LAUNCHER = "Decktil.desktop"
MENU_ID = "io.github.YaziAranea.SteamDeckPlasmaKeyboard.desktop"


def install_manager(source, data_dir):
    menu = data_dir / "applications" / MENU_ID
    store = data_dir / "deck-plasma-keyboard-manager"
    store.mkdir(parents=True, exist_ok=True)
    with (store / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if menu.is_symlink() or (menu.exists() and
                "X-Deck-Plasma-Manager=true" not in menu.read_text().splitlines()):
            raise RuntimeError(f"An existing menu entry belongs to another application: {menu}")
        files = ["manager.py", "setup.py", "steam_shortcuts.py", "LICENSE", "INSTALL.md",
                 *("src/" + name for name in setup.RUNTIME_FILES)]
        payload = {name: (source / name).read_bytes() for name in files}
        fingerprint = hashlib.sha256()
        for name, content in sorted(payload.items()):
            fingerprint.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
        version = store / fingerprint.hexdigest()
        if not version.is_dir():
            # Only a complete, immutable copy can become the menu target. A manager
            # that is already running keeps using the backend it started with.
            with tempfile.TemporaryDirectory(dir=store) as temp:
                staging = Path(temp) / "version"
                for name, content in payload.items():
                    destination = staging / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(content)
                staging.rename(version)
        manager = version / "manager.py"
        lines = (source / LAUNCHER).read_text().splitlines()
        entry = "\n".join("Exec=/usr/bin/python3 " + setup.quoted(manager)
                          if line.startswith("Exec=") else line for line in lines) + "\n"
        setup.save(menu, entry.encode(), 0o755)
        return manager, menu


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-only", action="store_true")
    args = parser.parse_args()
    try:
        manager, menu = install_manager(SOURCE, setup.DATA)
        if args.install_only:
            print(menu)
        else:
            os.execv("/usr/bin/python3", ["python3", str(manager)])
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        message = str(error)
        print(message, file=sys.stderr)
        if not args.install_only and shutil.which("kdialog"):
            subprocess.run(["kdialog", "--title", "Decktil", "--error", message])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
