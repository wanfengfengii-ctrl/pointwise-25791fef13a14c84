import sqlite3
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import check_project
from demo import emit, require_receipt
from intake.encoding import encode_samples
from objects.integrity import PayloadVerificationError
from objects.store import initialize_object_store
from publish.catalog import Catalog, initialize_catalog
from publish.submissions import submit
from retire.volumes import retire_volume
from review.selection import RetiredVolumeError
from tests.archive_fixture import ArchiveCase


RECORDS = (3, -8, 3, 8)
HITS = ((1, -8), (3, 8))


def expected(volume_id, threshold, hits, count, peak):
    return dict(volume_id=volume_id, threshold=threshold, hits=hits, record_count=count, peak=peak)


class V1ThresholdCases(ArchiveCase):
    def test_distinct_volumes_and_same_peak(self):
        first = self.sign("A", RECORDS)
        twin = self.sign("B", RECORDS)
        other = self.sign("C", (3, -7, 3, 8))
        self.reopen()
        self.assertNotEqual(first.volume_id, twin.volume_id)
        self.assertEqual(self.object_for(first), self.object_for(twin))
        self.assert_pickup(first, RECORDS, 4, 8)
        self.assert_pickup(twin, RECORDS, 4, 8)
        self.assert_pickup(other, (3, -7, 3, 8), 4, 8)


class RejectCatalogCommit(Catalog):
    @contextmanager
    def transaction(self, *, write=False):
        with super().transaction(write=write) as connection:
            yield connection
            if write and connection.total_changes:
                raise sqlite3.OperationalError("fixture rejected anchor commit")


class ThresholdCases(ArchiveCase):
    def test_offline_coverage_and_volume(self):
        from publish.receipts import issue_threshold_anchor
        from review.result import export_threshold, verify_threshold

        first = self.sign("A", RECORDS).volume_id
        twin = self.sign("B", RECORDS).volume_id
        anchor = issue_threshold_anchor(self.catalog, self.objects, first)
        issue_threshold_anchor(self.catalog, self.objects, twin)
        self.reopen()
        self.assertEqual(issue_threshold_anchor(self.catalog, self.objects, first), anchor)
        package = export_threshold(self.catalog, self.objects, first, 8)
        high = export_threshold(self.catalog, self.objects, first, 9)
        swapped = export_threshold(self.catalog, self.objects, twin, 8)
        retire_volume(self.catalog, first)
        with self.assertRaises(RetiredVolumeError):
            export_threshold(self.catalog, self.objects, first, 8)
        invalid = (
            dict(package, hits=((3, 8),)),
            dict(package, hits=((1, 8), (3, 8))),
            dict(package, hits=((0, -8), (3, 8))),
            dict(package, record_count=3),
            dict(package, peak=9),
            dict(package, threshold=9),
            {key: value for key, value in package.items() if key != "proof"},
            swapped,
        )
        with patch.object(sqlite3, "connect", side_effect=AssertionError("offline database access")):
            self.assertEqual(verify_threshold(anchor, first, 8, package), expected(first, 8, HITS, 4, 8))
            self.assertEqual(verify_threshold(anchor, first, 9, high), expected(first, 9, (), 4, 8))
            for changed in invalid:
                with self.assertRaises(ValueError):
                    verify_threshold(anchor, first, 8, changed)
            with self.assertRaises(ValueError):
                verify_threshold(anchor, first, 9, package)
            with self.assertRaises(ValueError):
                verify_threshold(anchor, first, 9, dict(package, threshold=9))

    def test_empty_zero_and_exact_integer(self):
        from publish.receipts import issue_threshold_anchor
        from review.result import export_threshold, verify_threshold

        large = 1 << 20000
        cases = (
            ("empty", (), 0, (), None),
            ("zero", (0, 0), 0, ((0, 0), (1, 0)), 0),
            ("wide", (large - 1, -large, -large), large, ((1, -large), (2, -large)), large),
        )
        for name, records, threshold, hits, peak in cases:
            with self.subTest(name=name):
                volume = self.sign(name, records).volume_id
                anchor = issue_threshold_anchor(self.catalog, self.objects, volume)
                package = export_threshold(self.catalog, self.objects, volume, threshold)
                self.assertEqual(verify_threshold(anchor, volume, threshold, package),
                                 expected(volume, threshold, hits, len(records), peak))
        for bad in (True, -1, 8.0):
            with self.assertRaises(ValueError):
                export_threshold(self.catalog, self.objects, volume, bad)
            with self.assertRaises(ValueError):
                verify_threshold(anchor, volume, bad, package)

    def test_first_issuance_can_retry_after_failure(self):
        from publish.receipts import issue_threshold_anchor
        from review.result import export_threshold, verify_threshold

        receipt = self.sign("A", RECORDS)
        volume = receipt.volume_id
        name = self.object_for(receipt)
        self.replace_object(name, encode_samples((3, -7, 3, 8)))
        with self.assertRaises(PayloadVerificationError):
            issue_threshold_anchor(self.catalog, self.objects, volume)
        self.replace_object(name, encode_samples(RECORDS))
        with self.assertRaises(LookupError):
            export_threshold(self.catalog, self.objects, volume, 8)
        with self.assertRaisesRegex(sqlite3.OperationalError, "fixture rejected anchor commit"):
            issue_threshold_anchor(RejectCatalogCommit(self.catalog.path), self.objects, volume)
        self.reopen()
        self.assert_pickup(receipt, RECORDS, 4, 8)
        with self.assertRaises(LookupError):
            export_threshold(self.catalog, self.objects, volume, 8)
        anchor = issue_threshold_anchor(self.catalog, self.objects, volume)
        package = export_threshold(self.catalog, self.objects, volume, 8)
        self.assertEqual(verify_threshold(anchor, volume, 8, package), expected(volume, 8, HITS, 4, 8))


def demonstrate():
    from publish.receipts import issue_threshold_anchor
    from review.result import export_threshold, verify_threshold

    with TemporaryDirectory() as directory:
        catalog_path = Path(directory) / "catalog.sqlite3"
        store_path = Path(directory) / "objects.sqlite3"
        catalog = initialize_catalog(catalog_path)
        store = initialize_object_store(store_path)
        volume = require_receipt(submit(catalog, store, "A", RECORDS)).volume_id
        anchor = issue_threshold_anchor(catalog, store, volume)
        catalog = initialize_catalog(catalog_path)
        store = initialize_object_store(store_path)
        package = export_threshold(catalog, store, volume, 8)
    actual = verify_threshold(anchor, volume, 8, package)
    try:
        verify_threshold(anchor, volume, 8, dict(package, hits=((3, 8),)))
    except ValueError:
        verdict = "rejected"
    else:
        verdict = "accepted"
    emit("threshold-offline", RECORDS, expected(volume, 8, HITS, 4, 8), actual,
         "rejected", verdict, ["publish/receipts.py", "review/result.py"],
         "Positions 1 and 3 have absolute value 8. Dropping position 1 leaves the count and peak unchanged.")


def run_cases(case_type):
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(case_type)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main(args):
    if args == ["baseline"]:
        return run_cases(V1ThresholdCases)
    if args == ["verify"]:
        return check_project.main() or run_cases(ThresholdCases)
    if args == ["demo"]:
        demonstrate()
        return 0
    raise SystemExit("usage: python3 -B threshold_cases.py {baseline|verify|demo}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
