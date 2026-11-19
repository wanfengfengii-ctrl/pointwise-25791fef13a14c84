from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class StoredRequest:
    request_id: str
    payload: bytes
    content_digest: str


def initialize_request_table(connection: Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            request_id TEXT PRIMARY KEY NOT NULL,
            payload BLOB NOT NULL,
            content_digest TEXT NOT NULL
        )
    ''')


def get_request(connection: Connection, request_id: str) -> StoredRequest | None:
    row = connection.execute('''
        SELECT request_id, payload, content_digest
        FROM submissions WHERE request_id = ?
    ''', (request_id,)).fetchone()
    return None if row is None else StoredRequest(*row)


def insert_or_get_request(connection: Connection, request: StoredRequest) -> StoredRequest:
    connection.execute('''
        INSERT INTO submissions (request_id, payload, content_digest) VALUES (?, ?, ?)
        ON CONFLICT(request_id) DO NOTHING
    ''', (request.request_id, request.payload, request.content_digest))
    stored = get_request(connection, request.request_id)
    if stored is None:
        raise RuntimeError('submission insert did not leave a stored request')
    return stored
