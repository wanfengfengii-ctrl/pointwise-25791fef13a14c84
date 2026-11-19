import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from objects.integrity import payload_checksum
from objects.store import ObjectStore, initialize_object_store
from publish.catalog import initialize_catalog
from publish.receipts import find_volume
from publish.submissions import submit
from review.result import read


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.reopen()

    def reopen(self):
        self.catalog = initialize_catalog(self.root / 'catalog.sqlite3')
        self.objects = initialize_object_store(self.root / 'objects.sqlite3')

    def unavailable_port(self):
        return ObjectStore(self.root / 'unavailable' / 'objects.sqlite3')

    def sign(self, request_id, records):
        result = submit(self.catalog, self.objects, request_id, records)
        self.assertEqual(result.state, 'signed')
        self.assertIsNotNone(result.receipt)
        return result.receipt

    def object_for(self, receipt):
        with self.catalog.transaction() as connection:
            volume = find_volume(connection, receipt.volume_id)
        self.assertIsNotNone(volume)
        return volume.object_name

    def has_object(self, name):
        with self.objects.transaction() as connection:
            row = connection.execute('SELECT 1 FROM payloads WHERE object_name = ?', (name,)).fetchone()
        return row is not None

    def replace_object(self, name, payload):
        with self.objects.transaction(write=True) as connection:
            update = connection.execute('''
                UPDATE payloads SET payload = ?, checksum = ? WHERE object_name = ?
            ''', (payload, payload_checksum(payload), name))
            self.assertEqual(update.rowcount, 1)

    def assert_pickup(self, receipt, records, count, peak):
        actual = read(self.catalog, self.objects, receipt.volume_id)
        self.assertEqual(actual.volume_id, receipt.volume_id)
        self.assertEqual(actual.records, records)
        self.assertEqual(actual.record_count, count)
        self.assertEqual(actual.peak, peak)
        return actual

    @contextmanager
    def reject_catalog_commit(self, table):
        if table not in ('volumes', 'retirements'):
            raise ValueError(table)
        with self.catalog.transaction(write=True) as connection:
            connection.execute('CREATE TABLE fixture_parent (id INTEGER PRIMARY KEY)')
            connection.execute('''
                CREATE TABLE fixture_child (
                    id INTEGER REFERENCES fixture_parent(id) DEFERRABLE INITIALLY DEFERRED
                )
            ''')
            connection.execute(f'''
                CREATE TRIGGER fixture_reject_commit AFTER INSERT ON {table}
                BEGIN
                    INSERT INTO fixture_child (id) VALUES (1);
                END
            ''')
        try:
            yield
        finally:
            with self.catalog.transaction(write=True) as connection:
                connection.execute('DROP TRIGGER fixture_reject_commit')
                connection.execute('DROP TABLE fixture_child')
                connection.execute('DROP TABLE fixture_parent')
