#!/usr/bin/env python3
"""Set up the Plasma Keyboard adapter for one SteamOS Desktop user."""
import argparse
import ast
import configparser
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shlex
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

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
RUNTIME_FILES = ("bridge.py", "chord.py", "profile.py", "keyboard.py", "haptics.py",
                 "popup.py", "suppress-steam-osk.js", "DeckSelector.qml", "DeckWordRow.qml",
                 "DeckPrediction.js", "words/ru.txt.gz", "words/en.txt.gz",
                 "words/ru-next.json.gz", "words/en-next.json.gz", "words/NOTICE.md")


def cmd(*args, **kwargs):
    return subprocess.run(args,check=True,**kwargs)


def kwin_setting(key):
    value = cmd("kreadconfig6", "--file", "kwinrc", "--group", "Wayland", "--key", key,
                "--default", ABSENT, capture_output=True, text=True).stdout.rstrip("\n")
    return None if value == ABSENT else value


def set_kwin_setting(key, value):
    cmd("kwriteconfig6", "--notify", "--file", "kwinrc", "--group", "Wayland", "--key", key,
        "--delete" if value is None else str(value))


def method():
    return kwin_setting("InputMethod")


def set_method(value):
    set_kwin_setting("InputMethod", value)


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
Name=Decktil (Plasma Keyboard)
Name[ru]=Decktil (Клавиатура Plasma)
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


MODE_POLICY = "VirtualKeyboardMode"
VK = "org.kde.kwin.VirtualKeyboard"
POPUP_SCRIPT = "deck-plasma-keyboard-popup"


def mode_file():
    # Keep mode preferences independent of installer ownership. A recognized
    # manual installation must never acquire an install manifest implicitly.
    return STATE_DIR / "mode.json"


def load_mode():
    path = mode_file()
    return json.loads(path.read_text()) if path.exists() else {}


def store_mode(settings):
    if settings:
        save(mode_file(), json.dumps(settings, indent=2).encode())
    else:
        mode_file().unlink(missing_ok=True)


def desktop_session():
    return (os.environ.get("XDG_SESSION_TYPE") == "wayland"
            and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":"))


def configured_shortcut():
    saved = APP / "steam-shortcut.json"
    try:
        if saved.is_file():
            value = json.loads(saved.read_text())["appid"]
        else:
            # Early manual installations used a numeric constant in profile.py.
            # Inspect the declaration without executing the installed program.
            declarations = ast.parse((APP / "profile.py").read_text()).body
            value = next((n.value.value for n in declarations if isinstance(n, ast.Assign)
                          and isinstance(n.value, ast.Constant) and any(isinstance(t, ast.Name)
                          and t.id == "KEYBOARD" for t in n.targets)), None)
        if type(value) is int and 0x80000000 <= value <= 0xffffffff:
            return value
    except (OSError, SyntaxError, ValueError, KeyError, TypeError):
        pass
    return None


def service_files_match():
    """Recognize the shipped service and the earlier %h desktop installation."""
    if any(not p.is_file() or p.is_symlink() for p in (ENTRY, UNIT, APP / "profile.py")):
        return False
    values = []
    for path, section, keys in ((UNIT, "Service", ("ExecStart", "ExecStopPost")),
                                (ENTRY, "Desktop Entry", ("Exec",))):
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(path.read_text())
            for key in keys:
                command = parser.get(section, key).replace("%h/", str(Path.home()) + "/")
                values.append(shlex.split(command))
            if path == ENTRY and not parser.getboolean(section, "X-KDE-Wayland-VirtualKeyboard"):
                return False
        except (OSError, configparser.Error, ValueError):
            return False
    return values == [["/usr/bin/python3", str(APP / "bridge.py")],
                      ["/usr/bin/python3", str(APP / "profile.py"), "leave"],
                      [str(APP / "keyboard.py")]]


def installation(state):
    claimed = state.get("files", {})
    core = ("bridge.py", "profile.py", "keyboard.py", "chord.py", "DeckSelector.qml")
    prepared = (service_files_match() and os.access(APP / "keyboard.py", os.X_OK)
                and all((APP / name).is_file() for name in core))
    appid = configured_shortcut() if prepared else None
    if MANIFEST.is_file():
        # The manifest is written before each file. A partial prepare remains
        # resumable, and an older installed release keeps its own runtime list.
        owned = (state.get("format") == 1 and isinstance(claimed, dict)
                 and all(isinstance(k, str) for k in claimed))
        required = (ENTRY, UNIT, TEMPLATE, *(APP / name for name in core))
        complete = (prepared and all(str(path) in claimed and path.is_file() for path in required)
                    and all(Path(name).is_file() for name in claimed if Path(name).is_relative_to(APP)))
        return ("managed", bool(complete), appid) if owned else ("unmanaged", False, None)
    if prepared and appid is not None:
        return "manual", True, appid
    # Removal can legitimately leave empty directories or bytecode. Only
    # paths that would collide with a new installation need manual attention.
    reserved = (ENTRY, UNIT, HELPER, TEMPLATE, *(APP / name for name in RUNTIME_FILES))
    if APP.is_symlink() or any(path.exists() or path.is_symlink() for path in reserved):
        return "unmanaged", False, None
    return "missing", False, None


def unit_state():
    result = cmd("systemctl", "--user", "show", UNIT_NAME, "--property=ActiveState,UnitFileState",
                 capture_output=True, text=True)
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return values.get("ActiveState") == "active", values.get("UnitFileState") == "enabled"


def panel_properties():
    import dbus
    bus = dbus.SessionBus()
    return dbus.Interface(bus.get_object("org.kde.KWin", "/VirtualKeyboard", introspect=False),
                          "org.freedesktop.DBus.Properties")


def panel_state():
    values = panel_properties().GetAll(VK, timeout=2)
    return {"mode": int(values["mode"]), "visible": bool(values["visible"]),
            "available": bool(values["available"])}


def set_policy(policy):
    import dbus
    if panel_state()["mode"] != policy["mode"]:
        panel_properties().Set(VK, "mode", dbus.Int32(policy["mode"], variant_level=1), timeout=2)
    # KWin persists the D-Bus value. Put back the original representation,
    # including an absent key for the implicit automatic visibility policy.
    if kwin_setting(MODE_POLICY) != policy["raw"]:
        set_kwin_setting(MODE_POLICY, policy["raw"])


def policy_now():
    return {"raw": kwin_setting(MODE_POLICY), "mode": panel_state()["mode"]}


def unload_popup_guard():
    import dbus
    bus = dbus.SessionBus()
    scripting = dbus.Interface(bus.get_object("org.kde.KWin", "/Scripting", introspect=False),
                               "org.kde.kwin.Scripting")
    if scripting.isScriptLoaded(POPUP_SCRIPT, timeout=2):
        scripting.unloadScript(POPUP_SCRIPT, timeout=2)
        if scripting.isScriptLoaded(POPUP_SCRIPT, timeout=2):
            raise RuntimeError("KWin still has the Steam keyboard filter loaded")


def snapshot():
    active, enabled = unit_state()
    return {"active": active, "enabled": enabled, "method": method(), "policy": policy_now()}


def effective_mode(actual):
    if actual["method"] == str(ENTRY) and actual["active"] and actual["enabled"] and actual["policy"]["mode"]:
        return "plasma"
    if not actual["active"] and not actual["enabled"] and not actual["policy"]["mode"]:
        return "steam"
    return "mixed"


def status(state):
    kind, prepared, appid = installation(state)
    session = desktop_session()
    mode = "steam" if kind == "missing" else "mixed"
    recovery = "pending" in load_mode()
    first_setup = kind == "managed" and not state.get("enabled", False)
    if first_setup and not recovery:
        mode = "steam"
    elif prepared and session and not recovery:
        mode = effective_mode(snapshot())
    if kind == "managed" and prepared and appid is None:
        try:
            appid = lookup(ROOT, HELPER)
        except (OSError, ValueError, RuntimeError):
            pass
    return {"installation": kind, "mode": mode, "prepared": prepared,
            "setup_complete": kind == "manual" or (kind == "managed" and not first_setup),
            "shortcut_appid": appid, "can_switch": prepared and session,
            "supported_session": session, "recovery_pending": recovery}


def restore_snapshot(previous):
    # Always stop first if the previous session was inactive. ExecStopPost
    # releases the temporary controller profile and the bridge's popup guard.
    if not previous["active"]:
        cmd("systemctl", "--user", "stop", UNIT_NAME)
    cmd("systemctl", "--user", "enable" if previous["enabled"] else "disable", UNIT_NAME)
    set_method(previous["method"])
    set_policy(previous["policy"])
    if previous["active"]:
        cmd("systemctl", "--user", "start", UNIT_NAME)
    else:
        unload_popup_guard()
    verify_settings(previous)


def verify_settings(expected, available=False):
    until = time.monotonic() + 3
    while True:
        actual = snapshot()
        panel = panel_state()
        if actual == expected and (not available or panel["available"]) and (actual["policy"]["mode"] or not panel["visible"]):
            return
        if time.monotonic() >= until:
            raise RuntimeError("KWin or the user service did not apply the selected keyboard settings")
        time.sleep(.08)


def resume_interrupted(settings):
    if "pending" in settings:
        restore_snapshot(settings["pending"])
        settings = {k: v for k, v in settings.items() if k != "pending"}
        store_mode(settings)
    return settings


def switch_mode(state, choice):
    if not desktop_session():
        raise RuntimeError("Use the keyboard switcher in KDE Plasma Desktop Mode on Wayland")
    kind, prepared, _ = installation(state)
    if kind not in ("managed", "manual") or not prepared:
        raise RuntimeError("The keyboard adapter is missing or its service belongs to another installation")
    settings = resume_interrupted(load_mode())
    first_setup = kind == "managed" and not state.get("enabled", False)
    if choice == "steam" and first_setup:
        # prepare only installed files. Keep the preexisting input method and
        # visibility policy until the first explicitly requested activation.
        return
    before = snapshot()
    if effective_mode(before) == choice and not first_setup:
        if choice == "steam":
            cmd("/usr/bin/python3", str(APP / "profile.py"), "leave")
            unload_popup_guard()
        elif not panel_state()["available"]:
            raise RuntimeError("KWin has not loaded the Plasma keyboard input method")
        return
    store_mode({**settings, "pending": before})
    original_policy = before["policy"]
    try:
        if choice == "steam":
            # Stop the bridge before disabling its input method. An in-flight
            # chord restores its temporary KWin policy as the service exits.
            cmd("systemctl", "--user", "disable", "--now", UNIT_NAME)
            cmd("/usr/bin/python3", str(APP / "profile.py"), "leave")
            unload_popup_guard()
            expected_policy = {"raw": "0", "mode": 0}
            plasma_policy = settings.get("plasma_policy", before["policy"])
            if effective_mode(before) == "plasma":
                plasma_policy = policy_now()
                original_policy = plasma_policy
            if not plasma_policy["mode"]:
                plasma_policy = {"raw": None, "mode": 1}
            target_method = before["method"]
            if target_method == str(ENTRY):
                target_method = settings.get("steam_method", state.get("previous_method"))
                if target_method == str(ENTRY):
                    target_method = None
            set_policy(expected_policy)
            set_method(target_method)
            expected = {"active": False, "enabled": False, "method": target_method, "policy": expected_policy}
            verify_settings(expected)
            settings["plasma_policy"] = plasma_policy
            settings["steam_method"] = target_method
        else:
            if before["method"] != str(ENTRY):
                settings["steam_method"] = before["method"]
            if first_setup:
                enable(state)
            else:
                cmd("systemctl", "--user", "enable", "--now", UNIT_NAME)
                set_method(str(ENTRY))
            restored = settings.get("plasma_policy", before["policy"])
            if not restored["mode"]:
                restored = {"raw": None, "mode": 1}
            set_policy(restored)
            expected = {"active": True, "enabled": True, "method": str(ENTRY), "policy": restored}
            verify_settings(expected, available=True)
        settings.setdefault("original_policy", original_policy)
        settings["applied_policy"] = expected["policy"]
        settings["mode"] = choice
        store_mode(settings)
    except Exception:
        # Leave the on-disk snapshot in place until complete rollback. A later
        # operation can recover after interruption of either half of a switch.
        if snapshot() != before:
            restore_snapshot(before)
        store_mode({k: v for k, v in load_mode().items() if k != "pending"})
        raise


def remove(state):
    if not MANIFEST.exists():
        print("No managed installation.")
        return
    settings = resume_interrupted(load_mode())
    if str(UNIT) in state["files"] and UNIT.exists():
        cmd("systemctl","--user","disable","--now",UNIT_NAME)
    if "original_policy" in settings and policy_now() == settings.get("applied_policy"):
        set_policy(settings["original_policy"])
    if "previous_method" in state and method()==str(ENTRY):
        set_method(settings.get("steam_method", state["previous_method"]))
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
    store_mode({})
    print("Removed the adapter. In Steam, remove the non-Steam shortcut ‘Deck Plasma Keyboard Controls’.")
    print("Use Manage → Remove non-Steam game. Your desktop controller layout was not edited.")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "bind", "enable", "remove", "status", "mode", "disable"))
    parser.add_argument("selection", nargs="?", choices=("plasma", "steam"))
    args=parser.parse_args()
    if (args.action == "mode") != bool(args.selection):
        parser.error("mode requires plasma or steam; other commands take no selection")
    if args.action == "status":
        print(json.dumps(status(load_manifest()), ensure_ascii=False))
        return
    STATE_DIR.mkdir(mode=0o700,parents=True,exist_ok=True)
    with (STATE_DIR/"setup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state=load_manifest()
        if args.action in ("mode", "disable"):
            switch_mode(state, args.selection or "steam")
        elif args.action == "enable" and mode_file().exists():
            switch_mode(state, "plasma")
        else:
            {"prepare":prepare,"bind":bind,"enable":enable,"remove":remove}[args.action](state)


if __name__=="__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as err:
        sys.exit(str(err))
