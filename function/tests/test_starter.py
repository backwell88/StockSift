"""Offline regression cases, retained for later execution at the user's choice."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from stockreport.storage import Store,data_root
from stockreport.bootstrap import REPORT,load_starter

class StarterTests(unittest.TestCase):
    def test_default_data_directory_is_inside_project(self):
        with patch.dict(os.environ,{'STOCKREPORT_HOME':''}):
            self.assertEqual(data_root(),Path(__file__).resolve().parents[2]/'data')

    def test_assets_and_first_use(self):
        strategy,files=load_starter()
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            self.assertEqual(store.get()[1],strategy)
            self.assertEqual(strategy['name'],'Jayson缩量回调策略')
            self.assertEqual(len(store.versions()),1)
            self.assertEqual(store.get_api_key(),'')
            for name,raw in files.items():
                self.assertEqual((store.reports/name).read_bytes(),raw)

    def test_existing_user_data_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            strategy=store.get()[1]; strategy['name']='用户自己的策略'
            version=store.save(strategy)
            report=store.reports/(REPORT+'.md')
            report.write_text('用户修改',encoding='utf-8')
            again=Store(folder)
            self.assertEqual(again.get()[0],version)
            self.assertEqual(report.read_text(encoding='utf-8'),'用户修改')
            self.assertEqual(len(again.versions()),2)

    def test_old_strategy_rows_archived_once_without_changing_reports(self):
        starter,_=load_starter()
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            original_id=store.get()[0]
            extra=copy.deepcopy(starter); extra['name']='用户添加的策略'
            extra_id=store.save(extra)
            report=store.reports/'own.md'; report.write_text('历史报告',encoding='utf-8')
            store.set_api_key('sk-local')
            with store.connect() as c:
                c.execute("DELETE FROM settings WHERE key='single_starter_strategy'")
            migrated=Store(folder)
            self.assertEqual([row[0] for row in migrated.versions()],[original_id])
            self.assertEqual(migrated.get()[1],starter)
            self.assertEqual(report.read_text(encoding='utf-8'),'历史报告')
            self.assertEqual(migrated.get_api_key(),'sk-local')
            with migrated.connect() as c:
                archived=c.execute('SELECT body FROM deleted_versions WHERE id=?',(extra_id,)).fetchone()
            self.assertEqual(json.loads(archived[0]),extra)
            next_id=migrated.save(extra)
            self.assertGreater(next_id,extra_id)
            self.assertEqual(len(Store(folder).versions()),2)

    def test_rename_updates_only_saved_name_in_same_row(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            version,before=store.get()
            created=store.versions()[0][1]
            renamed_version,after=store.rename_version(version,'新名称')
            self.assertEqual(renamed_version,version)
            self.assertEqual(len(store.versions()),1)
            self.assertEqual(store.versions()[0][1],created)
            self.assertEqual(after['name'],'新名称')
            self.assertEqual(after['rule'],before['rule'])
            with self.assertRaises(ValueError): store.rename_version(version,'')
            self.assertEqual(store.get(),(version,after))

    def test_migration_keeps_edited_jayson_when_default_is_another_strategy(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            edited=store.get()[1]
            edited['rule']['all'][1]['value']=0.55
            edited_id=store.save(edited)
            other=copy.deepcopy(edited); other['name']='其他策略'
            other_id=store.save(other)
            with store.connect() as c:
                c.execute("DELETE FROM settings WHERE key='single_starter_strategy'")
            migrated=Store(folder)
            self.assertEqual(migrated.get(),(edited_id,edited))
            self.assertEqual(len(migrated.versions()),1)
            with migrated.connect() as c:
                archived={row[0] for row in c.execute('SELECT id FROM deleted_versions')}
            self.assertIn(other_id,archived)

    def test_migration_prefers_default_among_named_jayson_strategies(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            original_id,original=store.get()
            newer=copy.deepcopy(original)
            newer['rule']['all'][1]['value']=0.55
            newer_id=store.save(newer)
            store.set_default(original_id)
            with store.connect() as c:
                c.execute("DELETE FROM settings WHERE key='single_starter_strategy'")
            migrated=Store(folder)
            self.assertEqual(migrated.get(),(original_id,original))
            with migrated.connect() as c:
                self.assertIsNotNone(c.execute('SELECT id FROM deleted_versions WHERE id=?',(newer_id,)).fetchone())

    def test_migration_restores_starter_when_old_db_has_no_matching_strategy(self):
        starter,_=load_starter()
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            old_id=store.get()[0]
            store.rename_version(old_id,'旧的自定义策略')
            with store.connect() as c:
                c.execute("DELETE FROM settings WHERE key='single_starter_strategy'")
            migrated=Store(folder)
            self.assertEqual(len(migrated.versions()),1)
            new_id,loaded=migrated.get()
            self.assertGreater(new_id,old_id)
            self.assertEqual(loaded,starter)
            with migrated.connect() as c:
                archived=c.execute('SELECT body FROM deleted_versions WHERE id=?',(old_id,)).fetchone()
            self.assertEqual(json.loads(archived[0])['name'],'旧的自定义策略')

    def test_same_named_report_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            reports=Path(folder)/'reports'; reports.mkdir()
            path=reports/(REPORT+'.md'); path.write_text('原文件',encoding='utf-8')
            Store(folder)
            self.assertEqual(path.read_text(encoding='utf-8'),'原文件')

    def test_deleted_sample_not_reimported(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            path=store.reports/(REPORT+'.md'); path.unlink()
            Store(folder)
            self.assertFalse(path.exists())
