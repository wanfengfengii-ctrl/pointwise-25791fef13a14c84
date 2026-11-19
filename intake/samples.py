from collections.abc import Iterable, Mapping, Set


class InvalidRecordError(ValueError):
    pass


def freeze_samples(records: Iterable[int]) -> tuple[int, ...]:
    if isinstance(records, (str, bytes, bytearray, memoryview, Mapping, Set)):
        raise InvalidRecordError('records must be an ordered iterable of integers')
    try:
        iterator = iter(records)
    except TypeError as exc:
        raise InvalidRecordError('records must be an ordered iterable of integers') from exc

    frozen = []
    for position, value in enumerate(iterator):
        if not isinstance(value, int) or isinstance(value, bool):
            raise InvalidRecordError(f'record {position} is not an integer amplitude')
        frozen.append(int.__int__(value))
    return tuple(frozen)
