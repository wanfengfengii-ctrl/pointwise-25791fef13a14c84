from dataclasses import dataclass

from objects.integrity import PayloadVerificationError
from objects.store import ObjectStore
from publish.catalog import Catalog
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
