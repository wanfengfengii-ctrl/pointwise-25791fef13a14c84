from objects.delete_queue import has_delete_for_payload
from objects.integrity import PayloadVerificationError, payload_checksum, verify_payload
from objects.store import ObjectStore


def put_payload(store: ObjectStore, object_name: str, payload: bytes) -> None:
    with store.transaction(write=True) as connection:
        if has_delete_for_payload(connection, object_name):
            raise PayloadVerificationError(f'{object_name!r} is closed to writes')
        row = connection.execute('''
            SELECT payload, checksum FROM payloads WHERE object_name = ?
        ''', (object_name,)).fetchone()
        if row is None:
            connection.execute('''
                INSERT INTO payloads (object_name, payload, checksum) VALUES (?, ?, ?)
            ''', (object_name, payload, payload_checksum(payload)))
            return
        stored, checksum = row
        verify_payload(stored, checksum, subject=object_name)
        if stored != payload:
            raise PayloadVerificationError(f'{object_name!r} already has different bytes')


def read_payload(store: ObjectStore, object_name: str) -> bytes:
    with store.transaction() as connection:
        row = connection.execute('''
            SELECT payload, checksum FROM payloads WHERE object_name = ?
        ''', (object_name,)).fetchone()
    if row is None:
        raise PayloadVerificationError(f'{object_name!r} is missing')
    payload, checksum = row
    verify_payload(payload, checksum, subject=object_name)
    return payload
