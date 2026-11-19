from hashlib import sha256


def content_digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()
