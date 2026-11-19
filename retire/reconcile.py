from dataclasses import dataclass

from objects.delete_queue import query_delete
from objects.store import ObjectStore, ObjectStoreUnavailableError
from publish.bindings import mark_binding_reclaimed
from publish.catalog import Catalog
from retire.task_store import (
    DeleteTask,
    get_delete_task,
    mark_delete_accepted,
    mark_delete_completed,
)


@dataclass(frozen=True)
class CollectionStatus:
    request_id: str
    target: str
    state: str


def reconcile_delete(catalog: Catalog, store: ObjectStore, task: DeleteTask) -> CollectionStatus:
    try:
        result = query_delete(store, task.request_id)
    except ObjectStoreUnavailableError:
        result = None

    with catalog.transaction(write=True) as connection:
        current = get_delete_task(connection, task.request_id)
        if current is None:
            raise KeyError(task.request_id)
        state = current.state
        if result is not None and result.request_id == current.request_id and result.target == current.target:
            if result.state == 'completed' and current.state != 'completed':
                mark_delete_completed(connection, current.request_id)
                mark_binding_reclaimed(connection, current.target)
                state = 'completed'
            elif result.state == 'accepted' and current.state == 'awaiting_dispatch':
                mark_delete_accepted(connection, current.request_id)
                state = 'awaiting_confirmation'
    return CollectionStatus(current.request_id, current.target, state)
