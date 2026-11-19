import sqlite3

from intake.encoding import encode_samples
from intake.request_store import get_request
from intake.requests import RequestConflictError
from intake.samples import InvalidRecordError
from objects.integrity import PayloadVerificationError
from objects.payloads import read_payload
from publish.receipts import get_receipt
from publish.submissions import submit
from review.result import read
from review.selection import UnknownVolumeError
from tests.archive_fixture import ArchiveCase


class ReportTests(ArchiveCase):
    def test_request_conflict(self):
        original = self.sign('station', [3])
        with self.assertRaises(RequestConflictError):
            submit(self.catalog, self.objects, 'station', [4])
        self.assertEqual(self.sign('station', [3]), original)
        self.assert_pickup(original, (3,), 1, 3)

    def test_reordered_input_conflicts(self):
        original = self.sign('ordered', [3, -8])
        for different in ([-8, 3], [3, -8, 3]):
            with self.subTest(different=different), self.assertRaises(RequestConflictError):
                submit(self.catalog, self.objects, 'ordered', different)
        self.assert_pickup(original, (3, -8), 2, 8)

    def test_invalid_records_are_not_registered(self):
        invalid = (None, [True], [3, 1.0], ['-8'], '38', b'38', {3, -8}, {0: 3})
        for records in invalid:
            with self.subTest(records=records), self.assertRaises(InvalidRecordError):
                submit(self.catalog, self.objects, 'bad', records)
        with self.catalog.transaction() as connection:
            self.assertIsNone(get_request(connection, 'bad'))
        good = self.sign('bad', iter((3, 0)))
        self.assert_pickup(good, (3, 0), 2, 3)

    def test_caller_mutation_does_not_change_frozen_input(self):
        caller = [3, -8, 3]
        pending = submit(self.catalog, self.unavailable_port(), 'mutable', caller)
        self.assertEqual(pending.state, 'pending')
        self.assertIsNone(pending.receipt)
        caller[:] = [9]
        self.reopen()
        with self.assertRaises(RequestConflictError):
            submit(self.catalog, self.objects, 'mutable', caller)
        original = self.sign('mutable', [3, -8, 3])
        self.assert_pickup(original, (3, -8, 3), 3, 8)

    def test_readback_gate(self):
        with self.objects.transaction(write=True) as connection:
            connection.execute('''
                CREATE TRIGGER fixture_corrupt_after_put AFTER INSERT ON payloads
                BEGIN
                    UPDATE payloads SET checksum = 'damaged' WHERE object_name = NEW.object_name;
                END
            ''')
        result = submit(self.catalog, self.objects, 'readback', [1, -4])
        self.assertEqual(result.state, 'pending')
        self.assertIsNone(result.receipt)
        with self.catalog.transaction() as connection:
            self.assertIsNone(get_receipt(connection, 'readback'))
            name, = connection.execute('''
                SELECT object_name FROM payload_bindings WHERE origin_request_id = 'readback'
            ''').fetchone()
        self.assertTrue(self.has_object(name))
        with self.assertRaises(PayloadVerificationError):
            read_payload(self.objects, name)
        with self.objects.transaction(write=True) as connection:
            connection.execute('DROP TRIGGER fixture_corrupt_after_put')
        self.replace_object(name, encode_samples((1, -4)))
        receipt = self.sign('readback', [1, -4])
        self.assertEqual(self.object_for(receipt), name)
        self.assert_pickup(receipt, (1, -4), 2, 4)

    def test_sign_commit_failure(self):
        with self.reject_catalog_commit('volumes'):
            with self.assertRaises(sqlite3.IntegrityError):
                submit(self.catalog, self.objects, 'resumable', [3, -8])
            self.reopen()
            with self.catalog.transaction() as connection:
                self.assertIsNotNone(get_request(connection, 'resumable'))
                self.assertIsNone(get_receipt(connection, 'resumable'))
                name, = connection.execute('SELECT object_name FROM payload_bindings').fetchone()
            self.assertEqual(read_payload(self.objects, name), encode_samples((3, -8)))
            with self.assertRaises(UnknownVolumeError):
                read(self.catalog, self.objects, 'volume:resumable')
        original = self.sign('resumable', [3, -8])
        self.assertEqual(self.sign('resumable', [3, -8]), original)
        self.assertEqual(self.object_for(original), name)
        self.assert_pickup(original, (3, -8), 2, 8)
        with self.catalog.transaction() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM volumes').fetchone(), (1,))

    def test_completed_replay_after_reopen(self):
        first = self.sign('original', [4, 0])
        self.reopen()
        replay = submit(self.catalog, self.unavailable_port(), 'original', [4, 0])
        self.assertEqual(replay.receipt, first)
        self.assert_pickup(first, (4, 0), 2, 4)

    def test_live_reuse(self):
        first = self.sign('first', [3, -8])
        second = self.sign('second', [3, -8])
        self.assertNotEqual(first.volume_id, second.volume_id)
        self.assertEqual(self.object_for(first), self.object_for(second))
        with self.objects.transaction() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM payloads').fetchone(), (1,))
        self.assert_pickup(first, (3, -8), 2, 8)
        self.assert_pickup(second, (3, -8), 2, 8)
