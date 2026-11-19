from dataclasses import dataclass
from sqlite3 import Connection
from uuid import uuid4

from intake.request_store import StoredRequest


@dataclass(frozen=True)
class PayloadBinding:
    object_name: str
    content_digest: str
    origin_request_id: str
    state: str


def find_reusable_bindings(
    connection: Connection, request: StoredRequest
) -> tuple[PayloadBinding, ...]:
    rows = connection.execute('''
        SELECT binding.object_name, binding.content_digest, binding.origin_request_id, binding.state
        FROM payload_bindings AS binding
        JOIN submissions AS origin ON origin.request_id = binding.origin_request_id
        WHERE binding.content_digest = ? AND binding.state = 'open' AND origin.payload = ?
        ORDER BY binding.object_name
    ''', (request.content_digest, request.payload))
    return tuple(PayloadBinding(*row) for row in rows)


def reserve_binding(connection: Connection, request: StoredRequest) -> PayloadBinding:
    object_name = 'payload-' + uuid4().hex
    connection.execute('''
        INSERT INTO payload_bindings (object_name, content_digest, origin_request_id, state)
        VALUES (?, ?, ?, 'open')
    ''', (object_name, request.content_digest, request.request_id))
    return PayloadBinding(object_name, request.content_digest, request.request_id, 'open')


def get_binding(connection: Connection, object_name: str) -> PayloadBinding | None:
    row = connection.execute('''
        SELECT object_name, content_digest, origin_request_id, state
        FROM payload_bindings WHERE object_name = ?
    ''', (object_name,)).fetchone()
    return None if row is None else PayloadBinding(*row)


def close_binding(connection: Connection, object_name: str) -> bool:
    changed = connection.execute('''
        UPDATE payload_bindings SET state = 'closing' WHERE object_name = ? AND state = 'open'
    ''', (object_name,))
    return changed.rowcount == 1


def mark_binding_reclaimed(connection: Connection, object_name: str) -> None:
    connection.execute('''
        UPDATE payload_bindings SET state = 'reclaimed'
        WHERE object_name = ? AND state = 'closing'
    ''', (object_name,))
