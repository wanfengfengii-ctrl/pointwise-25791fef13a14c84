from objects.delete_queue import DeleteRequestConflictError, accept_delete
from objects.store import ObjectStore, ObjectStoreUnavailableError
from publish.catalog import Catalog
from retire.eligibility import prepare_collection
from retire.reconcile import CollectionStatus, reconcile_delete
from retire.task_store import DeleteTask, list_open_delete_tasks


def dispatch_delete(catalog: Catalog, store: ObjectStore, task: DeleteTask) -> CollectionStatus:
    try:
        accept_delete(store, task.request_id, task.target)
    except (ObjectStoreUnavailableError, DeleteRequestConflictError):
        # An absent or conflicting result cannot advance the directory task.
        pass
    return reconcile_delete(catalog, store, task)


def collect(catalog: Catalog, store: ObjectStore) -> tuple[CollectionStatus, ...]:
    prepare_collection(catalog)
    with catalog.transaction() as connection:
        tasks = list_open_delete_tasks(connection)
    return tuple(dispatch_delete(catalog, store, task) for task in tasks)
