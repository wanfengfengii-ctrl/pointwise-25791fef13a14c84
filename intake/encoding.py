from io import BytesIO


FORMAT_VERSION = 1
_MAGIC = b'CV'
_COUNT_BYTES = 4
_RECORD_HEADER_BYTES = 1 + _COUNT_BYTES


class PayloadFormatError(ValueError):
    pass


def encode_samples(samples: tuple[int, ...]) -> bytes:
    frame = BytesIO()
    frame.write(_MAGIC)
    frame.write(bytes((FORMAT_VERSION,)))
    frame.write(len(samples).to_bytes(_COUNT_BYTES, 'big'))
    for sample in samples:
        magnitude = abs(sample)
        size = (magnitude.bit_length() + 7) // 8
        if sample == 0:
            sign = 0
        elif sample > 0:
            sign = 1
        else:
            sign = 2
        frame.write(bytes((sign,)))
        frame.write(size.to_bytes(_COUNT_BYTES, 'big'))
        frame.write(magnitude.to_bytes(size, 'big'))
    return frame.getvalue()


def _read_exact(frame: BytesIO, size: int) -> bytes:
    data = frame.read(size)
    if len(data) != size:
        raise PayloadFormatError('truncated vibration frame')
    return data


def decode_samples(payload: bytes) -> tuple[int, ...]:
    frame = BytesIO(payload)
    if _read_exact(frame, len(_MAGIC)) != _MAGIC:
        raise PayloadFormatError('invalid vibration frame header')
    if _read_exact(frame, 1)[0] != FORMAT_VERSION:
        raise PayloadFormatError('unsupported vibration frame version')
    count = int.from_bytes(_read_exact(frame, _COUNT_BYTES), 'big')
    if count > (len(payload) - frame.tell()) // _RECORD_HEADER_BYTES:
        raise PayloadFormatError('record count exceeds frame length')

    samples = []
    for _ in range(count):
        sign = _read_exact(frame, 1)[0]
        size = int.from_bytes(_read_exact(frame, _COUNT_BYTES), 'big')
        if sign == 0 and size == 0:
            samples.append(0)
            continue
        if sign not in (1, 2) or size == 0:
            raise PayloadFormatError('invalid sign or magnitude length')
        if size > len(payload) - frame.tell():
            raise PayloadFormatError('truncated vibration magnitude')
        magnitude_bytes = _read_exact(frame, size)
        if magnitude_bytes[0] == 0:
            raise PayloadFormatError('non-canonical leading zero')
        magnitude = int.from_bytes(magnitude_bytes, 'big')
        samples.append(magnitude if sign == 1 else -magnitude)
    if frame.read(1):
        raise PayloadFormatError('trailing data after vibration records')
    return tuple(samples)
