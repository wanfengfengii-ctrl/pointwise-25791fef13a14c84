from collections.abc import Iterable
from sqlite3 import Connection

from intake.encoding import encode_samples
from intake.identity import content_digest
from intake.request_store import StoredRequest, insert_or_get_request
from intake.samples import freeze_samples


class RequestConflictError(ValueError):
    pass


def freeze_request(
    connection: Connection, request_id: str, records: Iterable[int]
) -> StoredRequest:
    if not isinstance(request_id, str) or not request_id:
        raise ValueError('request_id must be a nonempty string')
    payload = encode_samples(freeze_samples(records))
    candidate = StoredRequest(request_id, payload, content_digest(payload))
    original = insert_or_get_request(connection, candidate)
    if original.payload != candidate.payload:
        raise RequestConflictError(f'{request_id!r} already has different records')
    return original
