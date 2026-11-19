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


def find_threshold_anchor(connection: Connection, volume_id: str):
    row = connection.execute('''
        SELECT volume_id, commitment_version, root_digest
        FROM threshold_anchors WHERE volume_id = ?
    ''', (volume_id,)).fetchone()
    if row is None:
        return None
    stored_volume, commitment_version, root_digest = row
    return {'volume_id': stored_volume, 'version': commitment_version,
            'root': root_digest}


def insert_threshold_anchor(connection: Connection, anchor: dict) -> None:
    connection.execute('''
        INSERT INTO threshold_anchors (volume_id, commitment_version, root_digest)
        VALUES (?, ?, ?)
    ''', (anchor['volume_id'], anchor['version'], anchor['root']))


def issue_threshold_anchor(catalog, store, volume_id: str) -> dict:
    """Issue (or re-fetch) the trusted threshold anchor for one live volume.

    First issuance only accepts a live volume whose object-store payload
    verifies against the V1 fixed volume digest.  A previously committed
    anchor is returned unchanged by volume id, including after both databases
    are reopened.  The first write commits through ``Catalog.transaction``;
    on commit failure no anchor is left behind and the same volume can retry.
    """
    from review.commitment import ANCHOR_VERSION, build_volume_root
    from review.records import read_records
    from review.selection import select_volume

    with catalog.transaction() as connection:
        existing = find_threshold_anchor(connection, volume_id)
    if existing is not None:
        return existing

    # Liveness is enforced for first issuance; an unknown or retired volume
    # raises the shared V1 selection errors before any write is attempted.
    selection = select_volume(catalog, volume_id)
    records = read_records(store, selection)
    root = build_volume_root(volume_id, records)
    anchor = {'volume_id': volume_id, 'version': ANCHOR_VERSION, 'root': root}
    with catalog.transaction(write=True) as connection:
        # Another process may have committed first; the committed anchor wins.
        current = find_threshold_anchor(connection, volume_id)
        if current is not None:
            return current
        insert_threshold_anchor(connection, anchor)
    return anchor
