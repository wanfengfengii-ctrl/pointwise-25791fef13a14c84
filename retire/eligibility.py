from sqlite3 import Connection
from uuid import uuid4

from publish.bindings import close_binding, get_binding
from publish.catalog import Catalog
from publish.receipts import list_volume_references
from retire.task_store import DeleteTask, find_retirement, list_retired_payloads, save_delete_task


def has_live_references(connection: Connection, object_name: str) -> bool:
    return any(
        find_retirement(connection, volume_id) is None
        for volume_id in list_volume_references(connection, object_name)
    )


def prepare_collection(catalog: Catalog) -> None:
    with catalog.transaction(write=True) as connection:
        for object_name in list_retired_payloads(connection):
            binding = get_binding(connection, object_name)
            if binding is None or binding.state != 'open':
                continue
            if has_live_references(connection, object_name):
                continue
            if close_binding(connection, object_name):
                task = DeleteTask('delete-' + uuid4().hex, object_name, 'awaiting_dispatch')
                save_delete_task(connection, task)
