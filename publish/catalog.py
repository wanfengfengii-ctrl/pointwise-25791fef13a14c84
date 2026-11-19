import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from intake.request_store import initialize_request_table
from retire.task_store import initialize_retirement_tables


class Catalog:
    def __init__(self, path: str | Path):
        self.path = path

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, isolation_level=None)
        try:
            connection.execute('PRAGMA foreign_keys = ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def initialize_catalog(path: str | Path) -> Catalog:
    catalog = Catalog(path)
    with catalog.transaction(write=True) as connection:
        initialize_request_table(connection)
        connection.execute('''
            CREATE TABLE IF NOT EXISTS payload_bindings (
                object_name TEXT PRIMARY KEY NOT NULL,
                content_digest TEXT NOT NULL,
                origin_request_id TEXT NOT NULL REFERENCES submissions(request_id),
                state TEXT NOT NULL CHECK (state IN ('open', 'closing', 'reclaimed'))
            )
        ''')
        connection.execute('''
            CREATE INDEX IF NOT EXISTS bindings_content
            ON payload_bindings(content_digest, state)
        ''')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS volumes (
                volume_id TEXT PRIMARY KEY NOT NULL,
                request_id TEXT NOT NULL UNIQUE REFERENCES submissions(request_id),
                object_name TEXT NOT NULL REFERENCES payload_bindings(object_name),
                content_digest TEXT NOT NULL
            )
        ''')
        connection.execute('''
            CREATE INDEX IF NOT EXISTS volumes_object ON volumes(object_name)
        ''')
        initialize_retirement_tables(connection)
    return catalog
