from dataclasses import dataclass

from objects.integrity import PayloadVerificationError
from objects.store import ObjectStore
from publish.catalog import Catalog
from publish.receipts import find_threshold_anchor
from review.commitment import (
    build_threshold_proof,
    build_volume_root,
    verify_threshold_package,
)
from review.records import read_records
from review.selection import select_volume
from review.statistics import calculate_statistics


@dataclass(frozen=True)
class PickupResult:
    volume_id: str
    records: tuple[int, ...]
    record_count: int
    peak: int | None


def read(catalog: Catalog, store: ObjectStore, volume_id: str) -> PickupResult:
    selection = select_volume(catalog, volume_id)
    try:
        records = read_records(store, selection)
    except PayloadVerificationError:
        # A retirement may have committed and cleared the object after selection.
        select_volume(catalog, volume_id)
        raise
    statistics = calculate_statistics(records)
    return PickupResult(selection.volume_id, records, statistics.record_count, statistics.peak)


def _validate_threshold(threshold) -> int:
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
        raise ValueError('threshold must be a non-negative integer')
    return threshold


def export_threshold(
    catalog: Catalog, store: ObjectStore, volume_id: str, threshold: int
) -> dict:
    """Build a threshold delivery package from a committed anchor and V1 payload.

    The complete volume may be rescanned for every export.  Live volumes
    without a previously issued anchor raise ``LookupError``; retired volumes
    raise the shared ``RetiredVolumeError`` and payload problems raise
    ``PayloadVerificationError``.
    """
    _validate_threshold(threshold)
    selection = select_volume(catalog, volume_id)
    with catalog.transaction() as connection:
        anchor = find_threshold_anchor(connection, volume_id)
    if anchor is None:
        raise LookupError(f'no threshold anchor has been issued for {volume_id!r}')
    records = read_records(store, selection)
    if build_volume_root(volume_id, records) != anchor['root']:
        raise PayloadVerificationError(
            f'payload cannot be reconciled with the anchor for {volume_id!r}'
        )
    statistics = calculate_statistics(records)
    hits = tuple(
        (index, value)
        for index, value in enumerate(records)
        if abs(value) >= threshold
    )
    proof = build_threshold_proof(volume_id, threshold, records)
    return {
        'volume_id': volume_id,
        'threshold': threshold,
        'hits': hits,
        'record_count': statistics.record_count,
        'peak': statistics.peak,
        'proof': proof,
    }


def verify_threshold(anchor, volume_id, threshold, package) -> dict:
    """Verify a threshold package using only the passed values.

    No database, portal or issuing-process state is consulted.  Any mismatch
    in volume, threshold, format, version, hits or statistics raises
    ``ValueError`` and no partial result is returned.
    """
    return verify_threshold_package(anchor, volume_id, threshold, package)
