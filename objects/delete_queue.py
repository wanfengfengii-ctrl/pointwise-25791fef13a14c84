from dataclasses import dataclass
from sqlite3 import Connection

from objects.store import ObjectStore


@dataclass(frozen=True)
class DeleteRecord:
    request_id: str
    target: str
    state: str


class DeleteRequestConflictError(ValueError):
    pass


def read_delete_record(connection: Connection, request_id: str) -> DeleteRecord | None:
    row = connection.execute('''
        SELECT request_id, target, state FROM delete_requests WHERE request_id = ?
    ''', (request_id,)).fetchone()
    return None if row is None else DeleteRecord(*row)


def accept_delete(store: ObjectStore, request_id: str, target: str) -> DeleteRecord:
    with store.transaction(write=True) as connection:
        original = read_delete_record(connection, request_id)
        if original is not None:
            if original.target != target:
                raise DeleteRequestConflictError(f'{request_id!r} already has a different target')
            return original
        connection.execute('''
            INSERT INTO delete_requests (request_id, target, state) VALUES (?, ?, 'accepted')
        ''', (request_id, target))
        return DeleteRecord(request_id, target, 'accepted')


def query_delete(store: ObjectStore, request_id: str) -> DeleteRecord | None:
    with store.transaction() as connection:
        return read_delete_record(connection, request_id)


def has_delete_for_payload(connection: Connection, object_name: str) -> bool:
    row = connection.execute('''
        SELECT 1 FROM delete_requests WHERE target = ? LIMIT 1
    ''', (object_name,)).fetchone()
    return row is not None
