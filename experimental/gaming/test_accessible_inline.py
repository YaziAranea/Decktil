import unittest
from unittest.mock import Mock, patch

import focus
from accessible_inline import AccessibleInlineState


class AccessibleInlineTests(unittest.TestCase):
    def setUp(self):
        self.reader = AccessibleInlineState()
        self.tree = Mock()
        self.field = focus.Editor(12, 40, ':1.2', '/doc', '/field', True)
        self.wrapper = (self.field.bus, '/keyboard')
        self.tree.editor.return_value = self.field
        self.tree.inline.return_value = (True, self.wrapper)
        self.tree.clear_to_wait.return_value = True
        self.tree.call.return_value = {'class': 'inline keys'}
        self.tree.matches.side_effect = lambda doc, **opts: [] if 'role' in opts else [self.wrapper]
        self.enterContext(patch.object(focus, '_props', return_value={'_NET_WM_PID': '40'}))
        self.trusted = self.enterContext(patch.object(focus, '_trusted', return_value=True))
        self.windows = self.enterContext(patch.object(focus, '_command', return_value='12\n'))
        self.skin = focus.Skin('search', 'wrapper', 'keys', 'buffer', 'modal', 'standalone')
        self.enterContext(patch.object(focus, '_known_skin', return_value=self.skin))
        self.connect = self.enterContext(patch.object(focus, '_Tree', return_value=self.tree))

    def test_inline_and_same_editor_after_close_have_same_identity(self):
        state, field = self.reader.sample(12)
        self.assertEqual(state, 'inline')
        self.assertEqual(self.tree.call.call_args.args[2], 'GetAttributes')
        self.tree.inline.return_value = (None, self.wrapper)
        self.assertIsNone(self.reader.sample(12))
        self.tree.clear_to_wait.assert_not_called()
        self.tree.inline.return_value = (False, None)
        self.assertEqual(self.reader.sample(12), ('editor', field))
        self.tree.inline.assert_called_with(self.field, self.wrapper, self.skin)

    def test_no_claim_on_shared_window_process_or_foreign_dialog(self):
        self.windows.return_value = '12\n14\n'
        self.assertIsNone(self.reader.sample(12))
        self.connect.assert_not_called()
        self.windows.return_value = '12\n'
        self.tree.call.return_value = {'class': 'chrome standalone positioned'}
        self.assertIsNone(self.reader.sample(12))
        self.tree.matches.side_effect = lambda doc, **opts: [self.wrapper] if opts.get('role') == focus.ROLE.DIALOG else []
        self.assertIsNone(self.reader.sample(12))

    def test_unknown_search_or_incomplete_obstruction_evidence_returns_none(self):
        self.tree.editor.return_value = None
        self.assertIsNone(self.reader.sample(12))
        self.tree.editor.return_value = self.field
        self.tree.inline.return_value = (None, None)
        self.tree.clear_to_wait.return_value = False
        self.assertIsNone(self.reader.sample(12))
        self.tree.inline.side_effect = TimeoutError
        self.assertIsNone(self.reader.sample(12))
        self.assertIsNone(self.reader.tree)
