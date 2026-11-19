from dataclasses import dataclass
from sqlite3 import Connection

from intake.request_store import StoredRequest
from publish.bindings import PayloadBinding


@dataclass(frozen=True)
class Receipt:
    request_id: str
    volume_id: str


@dataclass(frozen=True)
class VolumeRecord:
    volume_id: str
    request_id: str
    object_name: str
    content_digest: str


def issue_receipt(
    connection: Connection, request: StoredRequest, binding: PayloadBinding
) -> Receipt:
    volume_id = 'volume:' + request.request_id
    connection.execute('''
        INSERT INTO volumes (volume_id, request_id, object_name, content_digest)
        VALUES (?, ?, ?, ?)
    ''', (volume_id, request.request_id, binding.object_name, request.content_digest))
    return Receipt(request.request_id, volume_id)


def get_receipt(connection: Connection, request_id: str) -> Receipt | None:
    row = connection.execute('''
        SELECT request_id, volume_id FROM volumes WHERE request_id = ?
    ''', (request_id,)).fetchone()
    return None if row is None else Receipt(*row)


def find_volume(connection: Connection, volume_id: str) -> VolumeRecord | None:
    row = connection.execute('''
        SELECT volume_id, request_id, object_name, content_digest
        FROM volumes WHERE volume_id = ?
    ''', (volume_id,)).fetchone()
    return None if row is None else VolumeRecord(*row)


def list_volume_references(connection: Connection, object_name: str) -> tuple[str, ...]:
    rows = connection.execute('''
        SELECT volume_id FROM volumes WHERE object_name = ? ORDER BY volume_id
    ''', (object_name,))
    return tuple(volume_id for (volume_id,) in rows)
