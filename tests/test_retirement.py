import sqlite3

from objects.delete_queue import query_delete
from objects.execution import execute_delete, run_pending_deletes
from publish.bindings import get_binding
from retire.dispatch import collect
from retire.task_store import find_retirement, list_open_delete_tasks
from retire.volumes import RetirementStatus, retire_volume
from review.result import read
from review.selection import RetiredVolumeError, UnknownVolumeError
from tests.archive_fixture import ArchiveCase


class RetirementTests(ArchiveCase):
    def test_retire_and_collect(self):
        receipt = self.sign('ordinary', [6, -1])
        old = self.object_for(receipt)
        self.assertEqual(retire_volume(self.catalog, receipt.volume_id), RetirementStatus.RETIRED)
        with self.assertRaises(RetiredVolumeError):
            read(self.catalog, self.objects, receipt.volume_id)
        pending, = collect(self.catalog, self.objects)
        executed, = run_pending_deletes(self.objects)
        self.assertEqual(executed.request_id, pending.request_id)
        completed, = collect(self.catalog, self.objects)
        self.assertEqual(completed.state, 'completed')
        self.assertFalse(self.has_object(old))
        with self.catalog.transaction() as connection:
            self.assertEqual(get_binding(connection, old).state, 'reclaimed')

    def test_shared_payload(self):
        first = self.sign('first', [7, -2])
        second = self.sign('second', [7, -2])
        name = self.object_for(first)
        self.assertEqual(name, self.object_for(second))
        retire_volume(self.catalog, first.volume_id)
        self.assertEqual(collect(self.catalog, self.objects), ())
        self.assertTrue(self.has_object(name))
        self.assert_pickup(second, (7, -2), 2, 7)
        retire_volume(self.catalog, second.volume_id)
        pending, = collect(self.catalog, self.objects)
        self.assertEqual(pending.target, name)
        execute_delete(self.objects, pending.request_id)
        completed, = collect(self.catalog, self.objects)
        self.assertEqual(completed.state, 'completed')
        self.assertFalse(self.has_object(name))

    def test_retire_rollback(self):
        receipt = self.sign('shift', [5])
        with self.reject_catalog_commit('retirements'):
            with self.assertRaises(sqlite3.IntegrityError):
                retire_volume(self.catalog, receipt.volume_id)
            self.reopen()
            self.assert_pickup(receipt, (5,), 1, 5)
            with self.catalog.transaction() as connection:
                self.assertIsNone(find_retirement(connection, receipt.volume_id))
            self.assertEqual(collect(self.catalog, self.objects), ())
            self.assertEqual(run_pending_deletes(self.objects), ())
        self.assertEqual(retire_volume(self.catalog, receipt.volume_id), RetirementStatus.RETIRED)

    def test_retire_replay(self):
        receipt = self.sign('replay', [5])
        first = retire_volume(self.catalog, receipt.volume_id)
        pending, = collect(self.catalog, self.objects)
        self.reopen()
        self.assertEqual(retire_volume(self.catalog, receipt.volume_id), first)
        again, = collect(self.catalog, self.objects)
        self.assertEqual(again, pending)
        execute_delete(self.objects, pending.request_id)
        collect(self.catalog, self.objects)
        self.assertEqual(retire_volume(self.catalog, receipt.volume_id), first)
        self.assertEqual(collect(self.catalog, self.objects), ())
        self.assertEqual(self.sign('replay', [5]), receipt)
        with self.assertRaises(UnknownVolumeError):
            retire_volume(self.catalog, 'absent')

    def test_pending_is_not_complete(self):
        receipt = self.sign('paused', [2])
        retire_volume(self.catalog, receipt.volume_id)
        pending, = collect(self.catalog, self.objects)
        self.assertEqual(pending.state, 'awaiting_confirmation')
        self.assertEqual(query_delete(self.objects, pending.request_id).state, 'accepted')
        self.assertTrue(self.has_object(pending.target))
        self.assertEqual(collect(self.catalog, self.objects), (pending,))
        execute_delete(self.objects, pending.request_id)
        complete, = collect(self.catalog, self.objects)
        self.assertEqual(complete.state, 'completed')
        self.assertFalse(self.has_object(pending.target))

    def test_port_unavailable(self):
        receipt = self.sign('offline', [2])
        retire_volume(self.catalog, receipt.volume_id)
        pending, = collect(self.catalog, self.unavailable_port())
        self.assertEqual(pending.state, 'awaiting_dispatch')
        self.assertIsNone(query_delete(self.objects, pending.request_id))
        with self.catalog.transaction() as connection:
            task, = list_open_delete_tasks(connection)
            self.assertEqual((task.request_id, task.target), (pending.request_id, pending.target))
        self.reopen()
        accepted, = collect(self.catalog, self.objects)
        self.assertEqual(accepted.request_id, pending.request_id)
        self.assertEqual(accepted.state, 'awaiting_confirmation')
        execute_delete(self.objects, accepted.request_id)
        complete, = collect(self.catalog, self.objects)
        self.assertEqual(complete.state, 'completed')
