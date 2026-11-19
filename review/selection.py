from dataclasses import dataclass

from publish.catalog import Catalog
from publish.receipts import find_volume
from retire.task_store import find_retirement


class UnknownVolumeError(LookupError):
    pass


class RetiredVolumeError(LookupError):
    pass


@dataclass(frozen=True)
class VolumeSelection:
    volume_id: str
    object_name: str
    content_digest: str


def select_volume(catalog: Catalog, volume_id: str) -> VolumeSelection:
    with catalog.transaction() as connection:
        volume = find_volume(connection, volume_id)
        if volume is None:
            raise UnknownVolumeError(volume_id)
        if find_retirement(connection, volume_id) is not None:
            raise RetiredVolumeError(volume_id)
        return VolumeSelection(volume.volume_id, volume.object_name, volume.content_digest)
