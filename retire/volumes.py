from enum import StrEnum

from publish.catalog import Catalog
from publish.receipts import find_volume
from retire.task_store import find_retirement, record_retirement
from review.selection import UnknownVolumeError


class RetirementStatus(StrEnum):
    RETIRED = 'retired'


def retire_volume(catalog: Catalog, volume_id: str) -> RetirementStatus:
    with catalog.transaction(write=True) as connection:
        if find_volume(connection, volume_id) is None:
            raise UnknownVolumeError(volume_id)
        if find_retirement(connection, volume_id) is None:
            record_retirement(connection, volume_id)
    return RetirementStatus.RETIRED
