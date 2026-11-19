from sqlite3 import Connection

from intake.request_store import StoredRequest
from objects.integrity import PayloadVerificationError
from objects.payloads import read_payload
from objects.store import ObjectStore
from publish.bindings import PayloadBinding, get_binding


def verify_readback(store: ObjectStore, object_name: str, expected: bytes) -> bool:
    try:
        return read_payload(store, object_name) == expected
    except PayloadVerificationError:
        return False


def binding_is_signable(
    connection: Connection, binding: PayloadBinding, request: StoredRequest
) -> bool:
    current = get_binding(connection, binding.object_name)
    return (
        current is not None
        and current.state == 'open'
        and current.origin_request_id == binding.origin_request_id
        and current.content_digest == request.content_digest
    )
