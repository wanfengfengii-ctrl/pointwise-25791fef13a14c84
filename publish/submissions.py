from collections.abc import Iterable
from dataclasses import dataclass

from intake.requests import freeze_request
from objects.integrity import PayloadVerificationError
from objects.payloads import put_payload
from objects.store import ObjectStore, ObjectStoreUnavailableError
from publish.bindings import find_reusable_bindings, reserve_binding
from publish.catalog import Catalog
from publish.readiness import binding_is_signable, verify_readback
from publish.receipts import Receipt, get_receipt, issue_receipt


@dataclass(frozen=True)
class SubmissionResult:
    request_id: str
    receipt: Receipt | None = None

    @property
    def state(self) -> str:
        return 'pending' if self.receipt is None else 'signed'


def _materialize(store: ObjectStore, object_name: str, payload: bytes) -> bool:
    try:
        if verify_readback(store, object_name, payload):
            return True
        put_payload(store, object_name, payload)
        return verify_readback(store, object_name, payload)
    except (PayloadVerificationError, ObjectStoreUnavailableError):
        return False


def submit(
    catalog: Catalog, store: ObjectStore, request_id: str, records: Iterable[int]
) -> SubmissionResult:
    with catalog.transaction(write=True) as connection:
        request = freeze_request(connection, request_id, records)
        receipt = get_receipt(connection, request_id)
        if receipt is not None:
            return SubmissionResult(request_id, receipt)
        candidates = find_reusable_bindings(connection, request)
        binding = candidates[0] if candidates else reserve_binding(connection, request)

    ready = _materialize(store, binding.object_name, request.payload)
    with catalog.transaction(write=ready) as connection:
        receipt = get_receipt(connection, request_id)
        if receipt is None and ready and binding_is_signable(connection, binding, request):
            receipt = issue_receipt(connection, request, binding)
    return SubmissionResult(request_id, receipt)
