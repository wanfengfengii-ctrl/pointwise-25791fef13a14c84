from objects.delete_queue import DeleteRecord, read_delete_record
from objects.store import ObjectStore


def execute_delete(store: ObjectStore, request_id: str) -> DeleteRecord:
    with store.transaction(write=True) as connection:
        record = read_delete_record(connection, request_id)
        if record is None:
            raise KeyError(request_id)
        if record.state == 'completed':
            return record
        connection.execute('DELETE FROM payloads WHERE object_name = ?', (record.target,))
        connection.execute('''
            UPDATE delete_requests SET state = 'completed' WHERE request_id = ?
        ''', (request_id,))
        return DeleteRecord(request_id, record.target, 'completed')


def run_pending_deletes(store: ObjectStore) -> tuple[DeleteRecord, ...]:
    with store.transaction() as connection:
        pending = connection.execute('''
            SELECT request_id FROM delete_requests WHERE state = 'accepted' ORDER BY request_id
        ''').fetchall()
    return tuple(execute_delete(store, request_id) for (request_id,) in pending)
