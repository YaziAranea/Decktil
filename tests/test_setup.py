"""Offline packaging tests. No Steam process, controller or desktop is touched."""
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE))
import setup
from steam_shortcuts import lookup


def shortcut(target, name, appid):
    def field(kind,key,data):
        return bytes([kind])+key.encode()+b"\0"+data
    item = field(2, "appid",struct.pack("<I",appid))
    item += field(1,"AppName",name.encode()+b"\0")
    item += field(1,"Exe",f'"{target}"'.encode()+b"\0")
    return field(0,"shortcuts",field(0,"0",item+b'\x08')+b'\x08')+b'\x08'


class Packaging(unittest.TestCase):
    def test_prepare_enable_remove(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            base = Path(tmp)
            src = base / "package/src"
            src.mkdir(parents=True)
            for name in setup.RUNTIME_FILES:
                (src / name).parent.mkdir(parents=True, exist_ok=True)
                (src / name).write_text(f"fixture for {name}")
            (src / "other.py").write_text("unrelated")
            app = base / "share/deck-plasma-keyboard"
            unit = base / "config/systemd/user" / setup.UNIT_NAME
            manifest = base / "state/install.json"
            root = base / "steam"
            helper = app / "Deck Plasma Keyboard Controls"
            entry = base / "share/applications/local.deck.PlasmaKeyboard.desktop"
            template = root / "controller_base/templates/deck_plasma_keyboard.vdf"
            runtime = Mock(DESKTOP=413080)
            runtime.desktop_source.return_value = (0, base / "desktop.vdf")
            runtime.active_app.return_value = 413080
            runtime.running_game.return_value = False
            runtime.inherited_template.return_value = "template from desktop"
            stack.enter_context(patch.multiple(
                setup, ROOT=root, PACKAGE=src.parent, STATE_DIR=manifest.parent,
                APP=app, ENTRY=entry, UNIT=unit, MANIFEST=manifest, HELPER=helper,
                TEMPLATE=template,
            ))
            stack.enter_context(patch.object(setup, "preflight"))
            stack.enter_context(patch.object(setup, "module", return_value=runtime))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            commands = stack.enter_context(patch.object(setup, "cmd"))
            current = ["/usr/share/applications/previous.desktop"]
            previous = current[0]
            stack.enter_context(patch.object(setup, "method", side_effect=lambda: current[0]))
            stack.enter_context(patch.object(
                setup, "set_method", side_effect=lambda value: current.__setitem__(0, value)
            ))

            state = setup.load_manifest()
            setup.prepare(state)
            self.assertTrue(os.access(app / "keyboard.py", os.X_OK))
            self.assertTrue(os.access(helper, os.X_OK))
            self.assertTrue((app / "haptics.py").exists())
            self.assertFalse((app / "other.py").exists())
            commands.assert_any_call("steamos-add-to-steam", str(helper))
            self.assertEqual(current[0], previous)
            with self.assertRaisesRegex(RuntimeError, "not saved"):
                setup.enable(state)

            shortcuts = root / "userdata/123/config/shortcuts.vdf"
            shortcuts.parent.mkdir(parents=True)
            shortcuts.write_bytes(shortcut(helper, helper.name, 0x956789ab))
            setup.enable(state)
            self.assertTrue(setup.load_manifest()["enabled"])
            self.assertEqual(current[0], str(entry))
            commands.assert_any_call(sys.executable, str(app / "profile.py"), "enter")
            commands.assert_any_call(sys.executable, str(app / "profile.py"), "leave")
            commands.assert_any_call("systemctl", "--user", "enable", "--now", setup.UNIT_NAME)
            self.assertEqual(json.loads((app / "steam-shortcut.json").read_text())["appid"], 0x956789ab)

            # A later desktop change regenerates the owned template. A manual
            # edit to an installed program should still be kept on removal.
            template.write_bytes(b"newly derived desktop template")
            (manifest.parent / "template.sha256").write_text(setup.digest(template.read_bytes()))
            edited = app / "keyboard.py"
            edited.write_text("my edits")
            setup.remove(setup.load_manifest())
            self.assertEqual(current[0], previous)
            self.assertEqual(edited.read_text(), "my edits")
            for path in (template, helper, unit, manifest, entry, app / "haptics.py"):
                self.assertFalse(path.exists(), path)
            self.assertTrue(shortcuts.exists())

    def test_roundtrip_offline_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/"Steam"; helper=Path(tmp)/"my helper"
            name="Deck Plasma Keyboard Controls"; fake_id=0x956789ab
            cfg=root/"userdata/123/config/shortcuts.vdf"
            cfg.parent.mkdir(parents=True)
            cfg.write_bytes(shortcut(helper,name,fake_id))
            self.assertEqual(lookup(root,helper),fake_id)
            self.assertIsNone(lookup(root,helper.with_suffix(".different")))
            selection=root/"steamapps/common/Steam Controller Configs/123/config/configset_controller_neptune.vdf"
            selection.parent.mkdir(parents=True)
            original=b'"controller_config"\r\n{\r\n\t"413080"\r\n\t{\r\n\t\t"template" "existing"\r\n\t}\r\n}\r\n'
            selection.write_bytes(original)
            tm=Path(tmp)/"template"; tm.touch()
            st=Path(tmp)/"state"; st.write_text("{}")
            state={}
            with patch.multiple(setup,ROOT=root,HELPER=helper,TEMPLATE=tm,MANIFEST=st),patch.object(setup,"steam_is_closed",return_value=True),contextlib.redirect_stdout(io.StringIO()):
                setup.bind(state)
                after=selection.read_bytes()
                self.assertEqual(after.replace(setup.binding_block(name,b"\r\n"),b""),original)
                setup.bind(state)
                self.assertEqual(after,selection.read_bytes())
                setup.remove_offline_binding(state)
                self.assertEqual(selection.read_bytes(),original)
                selection.write_bytes(original.replace(b'"413080"',b'"deck plasma keyboard controls"'))
                with self.assertRaisesRegex(RuntimeError,"existing binding"): setup.bind(state)

    def test_profile_inheritance(self):
        profile=setup.module(BASE/"src/profile.py")
        original='"controller_mappings"\n{\n  "title" "Desktop"\n "controller_type" "controller_neptune"\n "group_source_bindings" { "0" "left_trigger active" "1" "right_trackpad active" "2" "switch active" }\n}\n'
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/"original"
            cfg.write_text(original)
            result=profile.inherited_template(cfg)
            self.assertIn('"left_trigger inactive"',result)
            self.assertIn('"right_trackpad inactive"',result)
            self.assertIn('"switch active"',result)
            self.assertEqual(cfg.read_text(),original)

    def test_does_not_replace_unmanaged_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/"src"
            src.write_bytes(b"personal")
            state={"files":{}}
            with self.assertRaisesRegex(RuntimeError,"unmanaged"):
                setup.managed_file(state,src,b"other",0o644)
            self.assertEqual(src.read_bytes(),b"personal")

if __name__=="__main__":
    unittest.main()
