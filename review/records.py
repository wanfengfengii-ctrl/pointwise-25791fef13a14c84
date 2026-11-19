from intake.encoding import PayloadFormatError, decode_samples
from objects.integrity import PayloadVerificationError, verify_payload
from objects.payloads import read_payload
from objects.store import ObjectStore, ObjectStoreUnavailableError
from review.selection import VolumeSelection


def read_records(store: ObjectStore, selection: VolumeSelection) -> tuple[int, ...]:
    try:
        payload = read_payload(store, selection.object_name)
    except ObjectStoreUnavailableError as exc:
        raise PayloadVerificationError(f'cannot read volume {selection.volume_id!r}') from exc
    verify_payload(payload, selection.content_digest, subject=selection.volume_id)
    try:
        return decode_samples(payload)
    except PayloadFormatError as exc:
        raise PayloadVerificationError(f'invalid frame for {selection.volume_id!r}') from exc
