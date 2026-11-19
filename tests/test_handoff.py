from intake.encoding import encode_samples
from intake.requests import RequestConflictError
from objects.delete_queue import DeleteRequestConflictError, accept_delete, query_delete
from objects.execution import execute_delete
from objects.integrity import PayloadVerificationError
from objects.payloads import put_payload
from publish.submissions import submit
from retire.dispatch import collect
from retire.task_store import get_delete_task
from retire.volumes import retire_volume
from review.result import read
from review.selection import RetiredVolumeError
from tests.archive_fixture import ArchiveCase


class HandoffTests(ArchiveCase):
    def retire_a(self, *, dispatch=True):
        original = self.sign('A', [3, -8])
        old_name = self.object_for(original)
        retire_volume(self.catalog, original.volume_id)
        port = self.objects if dispatch else self.unavailable_port()
        task, = collect(self.catalog, port)
        self.assertEqual(task.target, old_name)
        self.assertEqual(task.state, 'awaiting_confirmation' if dispatch else 'awaiting_dispatch')
        self.assertTrue(self.has_object(old_name))
        return original, task

    def test_unrelated_content_progresses(self):
        _, task = self.retire_a()
        receipt = self.sign('unrelated', [0, -5])
        self.assert_pickup(receipt, (0, -5), 2, 5)
        self.assertEqual(query_delete(self.objects, task.request_id).state, 'accepted')
        self.assertTrue(self.has_object(task.target))

    def test_same_content_after_accepted_delete(self):
        original, task = self.retire_a()
        attempt = submit(self.catalog, self.objects, 'B', [3, -8])
        self.reopen()
        if attempt.receipt is not None:
            self.assertEqual(attempt.state, 'signed')
            self.assert_pickup(attempt.receipt, (3, -8), 2, 8)
        else:
            self.assertEqual(attempt.state, 'pending')
        execute_delete(self.objects, task.request_id)
        self.reopen()
        completed, = collect(self.catalog, self.objects)
        self.assertEqual(completed.request_id, task.request_id)
        self.assertEqual(completed.state, 'completed')
        self.assertFalse(self.has_object(task.target))
        receipt = self.sign('B', [3, -8])
        self.assertNotEqual(receipt.volume_id, original.volume_id)
        if attempt.receipt is not None:
            self.assertEqual(receipt, attempt.receipt)
        self.assertEqual(self.sign('B', [3, -8]), receipt)
        self.assert_pickup(receipt, (3, -8), 2, 8)
        with self.assertRaises(RetiredVolumeError):
            read(self.catalog, self.objects, original.volume_id)

    def test_fixed_target_when_first_delivery_resumes(self):
        _, task = self.retire_a(dispatch=False)
        self.assertIsNone(query_delete(self.objects, task.request_id))
        before = submit(self.catalog, self.objects, 'B', [3, -8])
        if before.receipt is not None:
            self.assertNotEqual(self.object_for(before.receipt), task.target)
            self.assert_pickup(before.receipt, (3, -8), 2, 8)
        self.reopen()
        resumed, = collect(self.catalog, self.objects)
        self.assertEqual(resumed.request_id, task.request_id)
        self.assertEqual(resumed.state, 'awaiting_confirmation')
        self.assertEqual(query_delete(self.objects, task.request_id).target, task.target)
        execute_delete(self.objects, task.request_id)
        completed, = collect(self.catalog, self.objects)
        self.assertEqual(completed.state, 'completed')
        self.assertFalse(self.has_object(task.target))
        receipt = self.sign('B', [3, -8])
        if before.receipt is not None:
            self.assertEqual(receipt, before.receipt)
        self.assert_pickup(receipt, (3, -8), 2, 8)

    def test_lost_delete_completion(self):
        original, task = self.retire_a()
        first_b = submit(self.catalog, self.objects, 'B', [3, -8])
        execute_delete(self.objects, task.request_id)
        self.reopen()
        with self.catalog.transaction() as connection:
            self.assertEqual(get_delete_task(connection, task.request_id).state, 'awaiting_confirmation')
        result = query_delete(self.objects, task.request_id)
        self.assertEqual((result.target, result.state), (task.target, 'completed'))
        self.assertFalse(self.has_object(task.target))
        other = self.sign('other', [11])
        self.assert_pickup(other, (11,), 1, 11)
        completion, = collect(self.catalog, self.objects)
        self.assertEqual(completion.request_id, task.request_id)
        self.assertEqual(completion.state, 'completed')
        self.reopen()
        receipt = self.sign('B', [3, -8])
        if first_b.receipt is not None:
            self.assertEqual(receipt, first_b.receipt)
        self.assertEqual(self.sign('B', [3, -8]), receipt)
        with self.assertRaises(RequestConflictError):
            submit(self.catalog, self.objects, 'B', [3, -7])
        self.assert_pickup(receipt, (3, -8), 2, 8)
        with self.assertRaises(RetiredVolumeError):
            read(self.catalog, self.objects, original.volume_id)
        self.assertEqual(collect(self.catalog, self.objects), ())

    def test_original_delete_delivery_is_idempotent(self):
        _, task = self.retire_a()
        accepted = query_delete(self.objects, task.request_id)
        self.assertEqual(accept_delete(self.objects, task.request_id, task.target), accepted)
        with self.assertRaises(DeleteRequestConflictError):
            accept_delete(self.objects, task.request_id, 'different-target')
        finished = execute_delete(self.objects, task.request_id)
        self.reopen()
        self.assertEqual(accept_delete(self.objects, task.request_id, task.target), finished)
        self.assertEqual(execute_delete(self.objects, task.request_id), finished)
        self.assertEqual(query_delete(self.objects, task.request_id), finished)
        self.assertFalse(self.has_object(task.target))

    def test_repeat_initialization_preserves_state_and_user_changes(self):
        old, task = self.retire_a()
        execute_delete(self.objects, task.request_id)
        done, = collect(self.catalog, self.objects)
        current = self.sign('readable-until-replaced', [5])
        replacement = encode_samples((9,))
        self.replace_object(self.object_for(current), replacement)
        with self.catalog.transaction(write=True) as connection:
            connection.execute('CREATE TABLE operator_note (value TEXT)')
            connection.execute('INSERT INTO operator_note VALUES (?)', ('reviewed',))
        self.reopen()
        self.reopen()
        self.assertEqual(self.sign('readable-until-replaced', [5]), current)
        with self.catalog.transaction() as connection:
            self.assertEqual(connection.execute('SELECT value FROM operator_note').fetchone(), ('reviewed',))
            self.assertEqual(get_delete_task(connection, task.request_id).state, done.state)
        self.assertEqual(query_delete(self.objects, task.request_id).state, 'completed')
        self.assertFalse(self.has_object(task.target))
        with self.assertRaises(PayloadVerificationError):
            read(self.catalog, self.objects, current.volume_id)
        with self.assertRaises(RetiredVolumeError):
            read(self.catalog, self.objects, old.volume_id)

    def test_delayed_put_cannot_resurrect_an_old_name(self):
        _, task = self.retire_a()
        original_bytes = encode_samples((3, -8))
        with self.assertRaises(PayloadVerificationError):
            put_payload(self.objects, task.target, original_bytes)
        execute_delete(self.objects, task.request_id)
        self.reopen()
        with self.assertRaises(PayloadVerificationError):
            put_payload(self.objects, task.target, original_bytes)
        self.assertFalse(self.has_object(task.target))
