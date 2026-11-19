import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class ObjectStoreUnavailableError(RuntimeError):
    pass


class ObjectStore:
    def __init__(self, path: str | Path):
        self.path = path

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        try:
            connection = sqlite3.connect(self.path, isolation_level=None)
            try:
                connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
        except sqlite3.OperationalError as exc:
            raise ObjectStoreUnavailableError(str(exc)) from exc


def initialize_object_store(path: str | Path) -> ObjectStore:
    store = ObjectStore(path)
    with store.transaction(write=True) as connection:
        connection.execute('''
            CREATE TABLE IF NOT EXISTS payloads (
                object_name TEXT PRIMARY KEY NOT NULL,
                payload BLOB NOT NULL,
                checksum TEXT NOT NULL
            )
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS delete_requests (
                request_id TEXT PRIMARY KEY NOT NULL,
                target TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('accepted', 'completed'))
            )
        ''')
        connection.execute('''
            CREATE INDEX IF NOT EXISTS delete_requests_target ON delete_requests(target)
        ''')
    return store
