from intake.identity import content_digest


class PayloadVerificationError(RuntimeError):
    pass


def payload_checksum(payload: bytes) -> str:
    return content_digest(payload)


def verify_payload(payload: bytes, expected_checksum: str, *, subject: str) -> None:
    if not isinstance(payload, bytes) or payload_checksum(payload) != expected_checksum:
        raise PayloadVerificationError(f'checksum mismatch for {subject}')
