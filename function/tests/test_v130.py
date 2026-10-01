"""Authored for v1.3.0; not executed during the user's static-only workflow."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from stockreport.storage import Store
from stockreport.ui import App


class VersionDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name)
        strategy=self.store.get()[1]
        self.store.save({**strategy,'name':'附加策略一'})
        self.store.save({**strategy,'name':'附加策略二'})

    def test_only_one_id_removed_reports_untouched_and_restart_valid(self):
        before=self.store.versions(); target=before[1][0]
        self.store.set_default(target)
        report=self.store.reports/'keep.md'; report.write_text('keep',encoding='utf-8')
        loaded,_=self.store.delete_version(target)
        self.assertEqual({r[0] for r in self.store.versions()},{r[0] for r in before}-{target})
        self.assertEqual(Store(self.tmp.name).get()[0],loaded)
        self.assertEqual(report.read_text(encoding='utf-8'),'keep')
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT id FROM deleted_versions').fetchall(),[(target,)])

    def test_deleted_highest_id_is_not_reused(self):
        highest=self.store.versions()[0][0]; self.store.delete_version(highest)
        self.assertGreater(self.store.save(self.store.get()[1]),highest)

    def test_last_version_cannot_be_deleted(self):
        for version,_,_ in self.store.versions()[:-1]: self.store.delete_version(version)
        last=self.store.get()[0]
        with self.assertRaises(ValueError): self.store.delete_version(last)
        self.assertEqual(len(self.store.versions()),1)

    def test_ui_delete_uses_loaded_id_not_dropdown(self):
        loaded,strategy=self.store.get()
        app=SimpleNamespace(version=loaded,strategy=strategy,store=self.store,
                            strategy_action_allowed=lambda:True,invalidate_proposal=Mock(),
                            populate=Mock(),refresh_versions=Mock(),status=Mock(),version_choice=Mock())
        with patch('stockreport.ui.messagebox.askyesno',return_value=True): App.delete_strategy(app)
        with self.assertRaises(ValueError): self.store.get(loaded)
        app.version_choice.get.assert_not_called()


class EnterTests(unittest.TestCase):
    def test_enter_sends_and_suppresses_newline(self):
        app=SimpleNamespace(ime_composing=lambda:False,send=Mock())
        self.assertEqual(App.chat_enter(app,SimpleNamespace(state=0)),'break')
        app.send.assert_called_once()

    def test_shift_enter_is_newline_and_composition_never_sends(self):
        app=SimpleNamespace(ime_composing=lambda:True,send=Mock())
        self.assertIsNone(App.chat_enter(app,SimpleNamespace(state=1)))
        self.assertEqual(App.chat_enter(app,SimpleNamespace(state=0)),'break')
        app.send.assert_not_called()
