from dataclasses import dataclass
from sqlite3 import Connection


@dataclass(frozen=True)
class RetirementRecord:
    volume_id: str


@dataclass(frozen=True)
class DeleteTask:
    request_id: str
    target: str
    state: str


def initialize_retirement_tables(connection: Connection) -> None:
    connection.execute('''
        CREATE TABLE IF NOT EXISTS retirements (
            volume_id TEXT PRIMARY KEY NOT NULL REFERENCES volumes(volume_id)
        )
    ''')
    connection.execute('''
        CREATE TABLE IF NOT EXISTS collection_tasks (
            request_id TEXT PRIMARY KEY NOT NULL,
            target TEXT NOT NULL UNIQUE REFERENCES payload_bindings(object_name),
            state TEXT NOT NULL CHECK (
                state IN ('awaiting_dispatch', 'awaiting_confirmation', 'completed')
            )
        )
    ''')


def record_retirement(connection: Connection, volume_id: str) -> RetirementRecord:
    connection.execute('''
        INSERT INTO retirements (volume_id) VALUES (?) ON CONFLICT(volume_id) DO NOTHING
    ''', (volume_id,))
    return RetirementRecord(volume_id)


def find_retirement(connection: Connection, volume_id: str) -> RetirementRecord | None:
    row = connection.execute('SELECT volume_id FROM retirements WHERE volume_id = ?',
                             (volume_id,)).fetchone()
    return None if row is None else RetirementRecord(*row)


def list_retired_payloads(connection: Connection) -> tuple[str, ...]:
    rows = connection.execute('''
        SELECT DISTINCT volume.object_name
        FROM retirements AS retirement
        JOIN volumes AS volume ON volume.volume_id = retirement.volume_id
        ORDER BY volume.object_name
    ''')
    return tuple(object_name for (object_name,) in rows)


def save_delete_task(connection: Connection, task: DeleteTask) -> None:
    connection.execute('''
        INSERT INTO collection_tasks (request_id, target, state) VALUES (?, ?, ?)
    ''', (task.request_id, task.target, task.state))


def get_delete_task(connection: Connection, request_id: str) -> DeleteTask | None:
    row = connection.execute('''
        SELECT request_id, target, state FROM collection_tasks WHERE request_id = ?
    ''', (request_id,)).fetchone()
    return None if row is None else DeleteTask(*row)


def list_open_delete_tasks(connection: Connection) -> tuple[DeleteTask, ...]:
    rows = connection.execute('''
        SELECT request_id, target, state FROM collection_tasks
        WHERE state <> 'completed' ORDER BY target
    ''')
    return tuple(DeleteTask(*row) for row in rows)


def mark_delete_accepted(connection: Connection, request_id: str) -> None:
    connection.execute('''
        UPDATE collection_tasks SET state = 'awaiting_confirmation'
        WHERE request_id = ? AND state = 'awaiting_dispatch'
    ''', (request_id,))


def mark_delete_completed(connection: Connection, request_id: str) -> None:
    connection.execute('''
        UPDATE collection_tasks SET state = 'completed'
        WHERE request_id = ? AND state <> 'completed'
    ''', (request_id,))
