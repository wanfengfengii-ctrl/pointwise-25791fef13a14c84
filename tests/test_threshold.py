import sqlite3
from contextlib import contextmanager
from unittest.mock import patch

from intake.encoding import encode_samples
from objects.integrity import PayloadVerificationError, payload_checksum
from publish.catalog import Catalog
from publish.receipts import find_threshold_anchor, issue_threshold_anchor
from retire.volumes import retire_volume
from review.result import export_threshold, verify_threshold
from review.selection import RetiredVolumeError, UnknownVolumeError
from tests.archive_fixture import ArchiveCase


RECORDS = (3, -8, 3, 8)
HITS_8 = ((1, -8), (3, 8))


class RejectCatalogCommit(Catalog):
    @contextmanager
    def transaction(self, *, write=False):
        with super().transaction(write=write) as connection:
            yield connection
            if write and connection.total_changes:
                raise sqlite3.OperationalError('fixture rejected anchor commit')


def _block_databases():
    return patch.object(sqlite3, 'connect',
                        side_effect=AssertionError('offline database access'))


class ThresholdAnchorTests(ArchiveCase):
    def _anchor(self, request_id='A', records=RECORDS):
        receipt = self.sign(request_id, records)
        return receipt.volume_id, issue_threshold_anchor(self.catalog, self.objects, receipt.volume_id)

    def test_anchor_shape_and_idempotent_refetch(self):
        volume, anchor = self._anchor()
        self.assertEqual(set(anchor), {'volume_id', 'version', 'root'})
        self.assertEqual(anchor['volume_id'], volume)
        self.assertIsInstance(anchor['version'], int)
        self.assertGreater(anchor['version'], 0)
        self.assertEqual(len(anchor['root']), 64)
        self.assertEqual(anchor['root'], anchor['root'].lower())
        self.assertEqual(issue_threshold_anchor(self.catalog, self.objects, volume), anchor)
        self.reopen()
        self.assertEqual(issue_threshold_anchor(self.catalog, self.objects, volume), anchor)

    def test_shared_object_gets_distinct_volume_anchors(self):
        first = self.sign('A', RECORDS)
        twin = self.sign('B', RECORDS)
        self.assertEqual(self.object_for(first), self.object_for(twin))
        anchor_a = issue_threshold_anchor(self.catalog, self.objects, first.volume_id)
        anchor_b = issue_threshold_anchor(self.catalog, self.objects, twin.volume_id)
        self.assertNotEqual(anchor_a['root'], anchor_b['root'])
        package_a = export_threshold(self.catalog, self.objects, first.volume_id, 8)
        package_b = export_threshold(self.catalog, self.objects, twin.volume_id, 8)
        with _block_databases():
            self.assertEqual(
                verify_threshold(anchor_a, first.volume_id, 8, package_a)['hits'], HITS_8
            )
            with self.assertRaises(ValueError):
                verify_threshold(anchor_a, twin.volume_id, 8, package_b)
            with self.assertRaises(ValueError):
                verify_threshold(anchor_b, first.volume_id, 8, package_a)

    def test_all_and_only_hits_with_signed_values_and_zero_threshold(self):
        records = (3, -8, 0, 8, 0)
        volume, anchor = self._anchor('Z', records)
        package = export_threshold(self.catalog, self.objects, volume, 0)
        self.assertEqual(package['hits'], tuple(enumerate(records)))
        self.assertEqual(package['record_count'], 5)
        self.assertEqual(package['peak'], 8)
        with _block_databases():
            self.assertEqual(
                verify_threshold(anchor, volume, 0, package),
                {'volume_id': volume, 'threshold': 0,
                 'hits': tuple(enumerate(records)), 'record_count': 5, 'peak': 8},
            )

    def test_exact_threshold_matches_and_ascending_positions(self):
        records = (11, -11, 10, -12)
        volume, anchor = self._anchor('E', records)
        package = export_threshold(self.catalog, self.objects, volume, 11)
        self.assertEqual(package['hits'], ((0, 11), (1, -11), (3, -12)))
        with _block_databases():
            self.assertEqual(
                verify_threshold(anchor, volume, 11, package)['hits'], package['hits']
            )

    def test_empty_volume_has_empty_peak(self):
        volume, anchor = self._anchor('empty', ())
        package = export_threshold(self.catalog, self.objects, volume, 0)
        self.assertEqual(package['hits'], ())
        self.assertEqual(package['record_count'], 0)
        self.assertIsNone(package['peak'])
        with _block_databases():
            result = verify_threshold(anchor, volume, 0, package)
        self.assertIsNone(result['peak'])

    def test_invalid_thresholds_rejected_online_and_offline(self):
        volume, anchor = self._anchor()
        package = export_threshold(self.catalog, self.objects, volume, 8)
        for bad in (True, False, -1, 8.0, '8', None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    export_threshold(self.catalog, self.objects, volume, bad)
                with self.assertRaises(ValueError):
                    verify_threshold(anchor, volume, bad, package)

    def test_dropped_or_altered_hit_is_rejected_even_when_stats_hold(self):
        volume, anchor = self._anchor()
        package = export_threshold(self.catalog, self.objects, volume, 8)
        tampered = (
            dict(package, hits=((3, 8),)),
            dict(package, hits=((1, 8), (3, 8))),
            dict(package, hits=((0, -8), (3, 8))),
            dict(package, hits=((1, -8), (2, 8), (3, 8))),
            dict(package, record_count=3),
            dict(package, peak=9),
            dict(package, threshold=9),
            dict(package, proof=package['proof'][:-1]),
            dict(package, proof=package['proof'] + b'\x00'),
            dict(package, proof=b''),
            {key: value for key, value in package.items() if key != 'proof'},
        )
        with _block_databases():
            for changed in tampered:
                with self.subTest(changed=changed):
                    with self.assertRaises(ValueError):
                        verify_threshold(anchor, volume, 8, changed)

    def test_non_hit_values_are_not_disclosed(self):
        # 9 = 1001 is below 12 = 1100; only the shared prefix bits may appear.
        volume, anchor = self._anchor('S', (9,))
        package = export_threshold(self.catalog, self.objects, volume, 12)
        self.assertEqual(package['hits'], ())
        with _block_databases():
            self.assertEqual(
                verify_threshold(anchor, volume, 12, package)['hits'], ()
            )

    def test_unknown_and_retired_volumes(self):
        with self.assertRaises(UnknownVolumeError):
            issue_threshold_anchor(self.catalog, self.objects, 'volume:absent')
        volume, anchor = self._anchor()
        package = export_threshold(self.catalog, self.objects, volume, 8)
        retire_volume(self.catalog, volume)
        # An existing anchor remains retrievable by volume id after retirement;
        # only new threshold exports are refused.
        self.assertEqual(
            issue_threshold_anchor(self.catalog, self.objects, volume), anchor
        )
        with self.assertRaises(RetiredVolumeError):
            export_threshold(self.catalog, self.objects, volume, 8)
        # Delivered evidence remains verifiable fully offline after retirement.
        with _block_databases():
            self.assertEqual(
                verify_threshold(anchor, volume, 8, package)['hits'], HITS_8
            )

    def test_corrupt_payload_blocks_first_issuance_but_retries_after_repair(self):
        receipt = self.sign('A', RECORDS)
        volume = receipt.volume_id
        name = self.object_for(receipt)
        self.replace_object(name, encode_samples((3, -7, 3, 8)))
        with self.assertRaises(PayloadVerificationError):
            issue_threshold_anchor(self.catalog, self.objects, volume)
        with self.catalog.transaction() as connection:
            self.assertIsNone(find_threshold_anchor(connection, volume))
        with self.assertRaises(LookupError):
            export_threshold(self.catalog, self.objects, volume, 8)
        self.replace_object(name, encode_samples(RECORDS))
        anchor = issue_threshold_anchor(self.catalog, self.objects, volume)
        package = export_threshold(self.catalog, self.objects, volume, 8)
        with _block_databases():
            self.assertEqual(
                verify_threshold(anchor, volume, 8, package)['hits'], HITS_8
            )

    def test_failed_commit_leaves_no_anchor_and_same_volume_retries(self):
        receipt = self.sign('A', RECORDS)
        volume = receipt.volume_id
        with self.assertRaises(sqlite3.OperationalError):
            issue_threshold_anchor(RejectCatalogCommit(self.catalog.path),
                                   self.objects, volume)
        self.reopen()
        with self.catalog.transaction() as connection:
            self.assertIsNone(find_threshold_anchor(connection, volume))
        with self.assertRaises(LookupError):
            export_threshold(self.catalog, self.objects, volume, 8)
        anchor = issue_threshold_anchor(self.catalog, self.objects, volume)
        self.assertEqual(anchor['volume_id'], volume)
        package = export_threshold(self.catalog, self.objects, volume, 8)
        with _block_databases():
            self.assertEqual(
                verify_threshold(anchor, volume, 8, package),
                {'volume_id': volume, 'threshold': 8, 'hits': HITS_8,
                 'record_count': 4, 'peak': 8},
            )

    def test_large_integers_remain_exact(self):
        large = 1 << 20000
        records = (large - 1, -large, -large)
        volume, anchor = self._anchor('wide', records)
        package = export_threshold(self.catalog, self.objects, volume, large)
        self.assertEqual(package['hits'], ((1, -large), (2, -large)))
        self.assertEqual(package['peak'], large)
        with _block_databases():
            result = verify_threshold(anchor, volume, large, package)
        self.assertEqual(result['hits'], ((1, -large), (2, -large)))
        self.assertEqual(result['peak'], large)

    def test_proof_uses_committed_anchor_root(self):
        volume, anchor = self._anchor()
        package = export_threshold(self.catalog, self.objects, volume, 8)
        # A foreign anchor with the same volume id but a different root cannot verify.
        foreign = {'volume_id': volume, 'version': anchor['version'],
                   'root': '0' * 64}
        with self.assertRaises(ValueError):
            verify_threshold(foreign, volume, 8, package)
