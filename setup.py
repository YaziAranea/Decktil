#!/usr/bin/env python3
"""Set up the Plasma Keyboard adapter for one SteamOS Desktop user."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from steam_shortcuts import helper_records, lookup

PACKAGE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home()/".local/share")
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home()/".config")
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home()/".local/state")/"deck-plasma-keyboard"
MANIFEST = STATE_DIR/"install.json"
APP = DATA/"deck-plasma-keyboard"
HELPER = APP/"Deck Plasma Keyboard Controls"
ENTRY = DATA/"applications/local.deck.PlasmaKeyboard.desktop"
UNIT_NAME = "deck-plasma-keyboard.service"
UNIT = CONFIG/"systemd/user"/UNIT_NAME
ROOT = Path.home()/".steam/steam"
TEMPLATE = ROOT/"controller_base/templates/deck_plasma_keyboard.vdf"
ABSENT = "__deck_plasma_unset__"
RUNTIME_FILES = ("bridge.py", "chord.py", "profile.py", "keyboard.py", "haptics.py", "DeckSelector.qml")


def cmd(*args, **kwargs):
    return subprocess.run(args,check=True,**kwargs)


def method():
    value=cmd("kreadconfig6","--file","kwinrc","--group","Wayland","--key","InputMethod",
              "--default",ABSENT,capture_output=True,text=True).stdout.rstrip("\n")
    return None if value == ABSENT else value


def set_method(value):
    cmd("kwriteconfig6","--notify","--file","kwinrc","--group","Wayland","--key","InputMethod",
        "--delete" if value is None else value)


def save(path, data, mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as file:
        temp=Path(file.name)
    try:
        temp.write_bytes(data)
        temp.chmod(mode)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {"format":1,"files":{},"enabled":False,"shortcut_requested":False}


def record(state):
    save(MANIFEST,json.dumps(state,indent=2).encode())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def managed_file(state, path, data, mode):
    key=str(path)
    old=state["files"].get(key)
    # Record planned files first, so an interrupted prepare can be repeated.
    if path.is_symlink() or (path.exists() and (old is None or
            digest(path.read_bytes()) not in {old, digest(data)})):
        raise RuntimeError(f"An unmanaged or modified file exists: {path}")
    # Reserve new paths before creating them. For a managed update keep the
    # previous checksum until the replacement succeeds, allowing a retry
    # after a crash on either side of the atomic rename.
    if old is None:
        state["files"][key] = digest(data)
        record(state)
    save(path,data,mode)
    if old is not None and old != digest(data):
        state["files"][key] = digest(data)
        record(state)


def module(path):
    spec=importlib.util.spec_from_file_location("deck_profile",path)
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def preflight():
    if os.geteuid()==0 or "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP","").split(":") or os.environ.get("XDG_SESSION_TYPE")!="wayland":
        raise RuntimeError("Run this as your Desktop user in a KDE Plasma Wayland session")
    for binary in ("python3","steam","steamos-add-to-steam","plasma-keyboard","qdbus6","kreadconfig6",
                   "kwriteconfig6","systemctl","xdotool"):
        if not shutil.which(binary):
            raise RuntimeError(f"Missing prerequisite: {binary}")
    cmd("/usr/bin/python3","-c","import dbus; from gi.repository import GLib, GLibUnix")
    if not (Path("/usr/share/plasma/keyboard/layouts/ru_RU/main.qml").is_file() and
            Path("/usr/share/plasma/keyboard/layouts/fallback/main.qml").is_file()):
        raise RuntimeError("Plasma Keyboard with filesystem RU/EN layouts is required")


def quoted(path):
    # Systemd and desktop entry argument syntax both support these quoted
    # paths. Avoid variable/specifier expansion on nonstandard home names.
    text=str(path)
    if any(char in text for char in ('\n','\r','%','$','`','"','\\')):
        raise RuntimeError(f"Unsupported path character in {path}")
    return '"'+text+'"'


def prepare(state):
    preflight()
    if state["enabled"]:
        raise RuntimeError("Already enabled. To install a new version, remove first and keep the Steam shortcut.")
    profile=module(PACKAGE/"src/profile.py")
    index,source=profile.desktop_source()
    if profile.running_game() or profile.active_app(index)!=profile.DESKTOP:
        raise RuntimeError("Focus the Plasma desktop and close running games before preparing")
    for name in RUNTIME_FILES:
        data = (PACKAGE / "src" / name).read_bytes()
        managed_file(state, APP / name, data, 0o755 if name == "keyboard.py" else 0o644)
    helper=b'#!/bin/sh\nexec qdbus6 org.kde.KWin /VirtualKeyboard org.kde.kwin.VirtualKeyboard.forceActivate\n'
    managed_file(state,HELPER,helper,0o755)
    desktop=f"""[Desktop Entry]
Type=Application
Name=Plasma Keyboard (Steam Deck)
Name[ru]=Клавиатура Plasma (Steam Deck)
Exec={quoted(APP/'keyboard.py')}
X-KDE-Wayland-VirtualKeyboard=true
Icon=input-keyboard-virtual
NoDisplay=true
""".encode()
    managed_file(state,ENTRY,desktop,0o644)
    unit=f"""[Unit]
Description=Steam Deck trackpads and triggers for Plasma Keyboard
PartOf=plasma-workspace.target
After=plasma-kwin_wayland.service
ConditionEnvironment=XDG_SESSION_TYPE=wayland
[Service]
Type=simple
ExecStart=/usr/bin/python3 {quoted(APP/'bridge.py')}
ExecStopPost=/usr/bin/python3 {quoted(APP/'profile.py')} leave
Restart=on-failure
RestartSec=2
[Install]
WantedBy=plasma-workspace.target
""".encode()
    managed_file(state,UNIT,unit,0o644)
    managed_file(state,TEMPLATE,profile.inherited_template(source).encode(),0o600)
    if not lookup(ROOT,HELPER) and not state["shortcut_requested"]:
        cmd("steamos-add-to-steam",str(HELPER))
        state["shortcut_requested"]=True
        record(state)
    print("In the Steam Library, open Deck Plasma Keyboard Controls → Controller Layout.")
    print("Select the local template ‘Deck Plasma Keyboard’ under Templates and apply it.")
    print("Then focus the desktop and run: python3 setup.py enable")
    print(f"If Steam has not added the shortcut, add this executable as a non-Steam game: {HELPER}")



def binding_block(name, newline):
    if not name or any(ch in name for ch in ('"', "\\", "\n", "\r")):
        raise RuntimeError("Unsupported shortcut name; use Steam's template picker")
    return newline.join((f'\t"{name.lower()}"'.encode(),
                         b'\t{', b'\t\t"template"\t\t"LOCAL_controller_base/templates/deck_plasma_keyboard.vdf"',
                         b'\t}', b''))


def steam_is_closed():
    return subprocess.run(["pgrep","-u",str(os.getuid()),"-x","steam"],
                          stdout=subprocess.DEVNULL).returncode == 1


def bind(state):
    if not MANIFEST.exists() or not TEMPLATE.is_file():
        raise RuntimeError("Run prepare with Steam open first")
    if not steam_is_closed():
        raise RuntimeError("Choose Steam > Exit and allow Steam to finish before offline binding")
    helpers=list(helper_records(ROOT,HELPER))
    if len(helpers)!=1:
        raise RuntimeError("A single registered helper is required for offline binding. Use Steam's template picker for multiple accounts.")
    account,name,_=helpers[0]
    path=ROOT/"steamapps/common/Steam Controller Configs"/account/"config/configset_controller_neptune.vdf"
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("Cannot locate Steam's Neptune selection file. Use the Steam template picker.")
    source=path.read_bytes()
    newline=b"\r\n" if b"\r\n" in source else b"\n"
    block=binding_block(name,newline)
    if block in source:
        print("Helper template already selected.")
        return
    if re.search(rb'^\s*"'+re.escape(name.lower().encode())+rb'"\s*$',source,re.M|re.I):
        raise RuntimeError("The helper has an existing binding. Select Deck Plasma Keyboard in Steam's template picker.")
    end=re.search(rb'(?m)^}\s*\Z',source)
    if not source.lstrip().startswith(b'"controller_config"') or not end:
        raise RuntimeError("Unexpected Steam controller selection file format")
    state["offline_binding"]={"path":str(path),"name":name}
    record(state)
    if not steam_is_closed() or path.read_bytes()!=source:
        raise RuntimeError("Steam started or the config changed; rerun bind after Steam exits")
    save(path,source[:end.start()]+block+source[end.start():],path.stat().st_mode & 0o777)
    print("Helper binding installed. Start Steam normally, focus the desktop, then run python3 setup.py enable.")


def remove_offline_binding(state):
    binding=state.get("offline_binding")
    if not binding:
        return
    if not steam_is_closed():
        print("Steam is running: remove its helper library entry using Manage → Remove non-Steam game.")
        return
    path=Path(binding["path"])
    if not path.is_file() or path.is_symlink(): return
    source=path.read_bytes()
    block=binding_block(binding["name"], b"\r\n" if b"\r\n" in source else b"\n")
    if source.count(block)==1 and steam_is_closed() and path.read_bytes()==source:
        save(path,source.replace(block,b"",1),path.stat().st_mode & 0o777)

def enable(state):
    preflight()
    if not MANIFEST.is_file() or not UNIT.is_file():
        raise RuntimeError("Run python3 setup.py prepare first")
    appid=lookup(ROOT,HELPER)
    if not appid:
        raise RuntimeError(f"Steam has not saved the helper shortcut yet. Add {HELPER} to Steam and confirm its library entry.")
    managed_file(state,APP/"steam-shortcut.json",json.dumps({"appid":appid}).encode(),0o600)
    if state["enabled"] and method()==str(ENTRY) and subprocess.run(
        ["systemctl","--user","is-active","--quiet",UNIT_NAME]).returncode==0:
        print("Already enabled.")
        return
    # Prove Steam selects the expected helper VDF and returns to automatic
    # selection before changing the user's input method or enabling the bridge.
    try:
        cmd(sys.executable,str(APP/"profile.py"),"enter")
    finally:
        cmd(sys.executable,str(APP/"profile.py"),"leave")
    before=method()
    if "previous_method" not in state:
        state["previous_method"]=before
    record(state)
    try:
        cmd("systemctl","--user","daemon-reload")
        cmd("systemctl","--user","enable","--now",UNIT_NAME)
        set_method(str(ENTRY))
        cmd("systemctl","--user","is-active","--quiet",UNIT_NAME)
        state["enabled"]=True
        record(state)
    except BaseException:
        if method()==str(ENTRY):
            set_method(before)
        subprocess.run(["systemctl","--user","disable","--now",UNIT_NAME])
        raise
    print("Enabled. In Plasma Keyboard settings select English (US) and Russian to use both layouts.")


def remove(state):
    if not MANIFEST.exists():
        print("No managed installation.")
        return
    if "previous_method" in state and method()==str(ENTRY):
        set_method(state["previous_method"])
    if str(UNIT) in state["files"] and UNIT.exists():
        cmd("systemctl","--user","disable","--now",UNIT_NAME)
    remove_offline_binding(state)
    # The runtime regenerates this template when desktop bindings change.
    # Its most recent generated hash distinguishes that from a user's edit.
    digest_file = STATE_DIR / "template.sha256"
    generated = digest_file.read_text().strip() if digest_file.is_file() else None
    for key,checksum in list(state["files"].items()):
        path=Path(key)
        if not path.exists() and not path.is_symlink():
            continue
        accepted = {checksum, generated} if path == TEMPLATE else {checksum}
        if not path.is_symlink() and path.is_file() and digest(path.read_bytes()) in accepted:
            path.unlink()
        else:
            print(f"Keeping locally modified file: {path}")
    cmd("systemctl","--user","daemon-reload")
    digest_file.unlink(missing_ok=True)
    MANIFEST.unlink()
    print("Removed the adapter. In Steam, remove the non-Steam shortcut ‘Deck Plasma Keyboard Controls’.")
    print("Use Manage → Remove non-Steam game. Your desktop controller layout was not edited.")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("prepare","bind","enable","remove"))
    args=parser.parse_args()
    STATE_DIR.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (STATE_DIR/"setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state=load_manifest()
        {"prepare":prepare,"bind":bind,"enable":enable,"remove":remove}[args.action](state)


if __name__=="__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as err:
        sys.exit(str(err))
