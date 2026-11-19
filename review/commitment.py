"""Version 1 threshold commitments and offline proof codec.

This module is deliberately self-contained: it only uses the Python standard
library and never opens a database.  The same construction is used when an
anchor is issued, when a threshold package is exported and when an external
reviewer verifies a package offline.

Commitment overview
-------------------
For every zero-based position ``i`` holding signed amplitude ``v``:

* ``sign`` is 0 (zero), 1 (positive) or 2 (negative), matching the V1 frame.
* ``magnitude = abs(v)`` is written as its canonical big-endian bit string
  ``bits`` (MSB first); the zero magnitude uses an empty bit string.
* A position-seeded hash chain follows the bits::

      n0 = H(b"CVT1.mag0" || enc_uint(i))
      nd = H(b"CVT1.bit" || n(d-1) || bit_d)

* A suffix-hiding cover chain runs back from the chain end::

      mk_cover = H(b"CVT1.end" || nk)
      md_cover = H(b"CVT1.cover" || m(d+1)_cover)

* The position leaf binds the index, bit length, sign, chain seed and the
  cover root ``m0``::

      leaf_i = H(b"CVT1.leaf" || enc_uint(i) || enc_uint(k) || sign || n0 || m0)

The leaves are placed in a power-of-two padded binary Merkle tree (padding
leaves are the fixed digest ``H(b"CVT1.pad")``; an empty volume uses
``H(b"CVT1.empty")``).  The anchor root additionally binds the official
volume id, the full record count and the full-volume peak::

      root = H(b"CVT1.root" || enc_text(volume_id) || merkle_root
               || enc_uint(record_count) || peak_enc)

Proof entries
-------------
A hit reveals the exact sign and magnitude bits, so the leaf is rebuilt in
full and the signed original value is checked.

A non-hit never reveals its magnitude.  It proves ``magnitude < threshold``
with one of two bounded openings:

* ``j == 0`` and the true bit length ``k < bit_length(threshold)``: the
  opaque cover root ``m0`` is presented.
* ``j > 0`` with ``k == bit_length(threshold)``: the canonical bit prefix up
  to the first position where the magnitude bit is 0 and the threshold bit is
  1 is shown, together with the opaque cover digest ``mj``; ``m0`` is
  recovered by applying ``j`` cover steps.  The revealed prefix equals the
  threshold's own prefix, so no non-hit amplitude is disclosed.

Non-hit original values, the complete V1 frame and any reversible encoding of
it are therefore never part of a package.  Every entry carries a Merkle
authentication path, so all and only the declared positions can reconcile
with the anchor root.

All integers use a canonical length-prefixed big-endian encoding::

      enc_uint(n) = uint32_be(byte_length(n)) || shortest_be_bytes(n)

The proof blob starts with the magic ``b"CVT1P"`` and the one-byte commitment
version ``1``.  Any malformed, truncated, non-canonical or inconsistent input
raises ``ValueError``.

Per-entry wire layout
---------------------
``tag`` 1 (hit):    index, sign, k, k raw bit bytes, Merkle path
``tag`` 2 (bound):  index, sign, k, j, j raw prefix bit bytes, 32-byte cover
                    digest, Merkle path
"""
from hashlib import sha256
from typing import Any

ANCHOR_VERSION = 1
_PROOF_MAGIC = b'CVT1P'
_DIGEST_BYTES = 32
_ENTRY_HIT = 1
_ENTRY_BOUND = 2


def _hash(*parts: bytes) -> bytes:
    digest = sha256()
    for part in parts:
        digest.update(part)
    return digest.digest()


def enc_uint(value: int) -> bytes:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError('value must be a non-negative integer')
    raw = value.to_bytes((value.bit_length() + 7) // 8, 'big')
    return len(raw).to_bytes(4, 'big') + raw


def enc_text(value: str) -> bytes:
    raw = value.encode('utf-8')
    return len(raw).to_bytes(4, 'big') + raw


def _bits_of(magnitude: int) -> tuple[int, ...]:
    if magnitude == 0:
        return ()
    width = magnitude.bit_length()
    return tuple((magnitude >> (width - 1 - offset)) & 1 for offset in range(width))


def _sign_of(value: int) -> int:
    if value == 0:
        return 0
    return 1 if value > 0 else 2


class _Reader:
    def __init__(self, data: bytes):
        if not isinstance(data, bytes):
            raise ValueError('proof must be bytes')
        self.data = data
        self.position = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.position + size > len(self.data):
            raise ValueError('truncated threshold proof')
        chunk = self.data[self.position:self.position + size]
        self.position += size
        return chunk

    def byte(self) -> int:
        return self.take(1)[0]

    def uint(self) -> int:
        length = int.from_bytes(self.take(4), 'big')
        raw = self.take(length)
        if raw and raw[0] == 0:
            raise ValueError('non-canonical integer in threshold proof')
        return int.from_bytes(raw, 'big')

    def prefix(self) -> int:
        """Read a fixed four-byte big-endian length/count prefix."""
        return int.from_bytes(self.take(4), 'big')

    def text(self) -> str:
        try:
            return self.take(self.prefix()).decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError('invalid UTF-8 text in threshold proof') from exc

    def digest(self) -> bytes:
        return self.take(_DIGEST_BYTES)

    def finish(self) -> None:
        if self.position != len(self.data):
            raise ValueError('trailing bytes after threshold proof')


def _commit_position(index: int, value: int) -> dict[str, Any]:
    sign = _sign_of(value)
    magnitude = abs(value)
    bits = _bits_of(magnitude)
    k = len(bits)
    nodes = [_hash(b'CVT1.mag0', enc_uint(index))]
    for bit in bits:
        nodes.append(_hash(b'CVT1.bit', nodes[-1], bytes((bit,))))
    covers: list[bytes] = [b''] * (k + 1)
    covers[k] = _hash(b'CVT1.end', nodes[k])
    for depth in range(k - 1, -1, -1):
        covers[depth] = _hash(b'CVT1.cover', covers[depth + 1])
    leaf = _hash(
        b'CVT1.leaf', enc_uint(index), enc_uint(k), bytes((sign,)),
        nodes[0], covers[0],
    )
    return {
        'sign': sign,
        'magnitude': magnitude,
        'bits': bits,
        'k': k,
        'nodes': nodes,
        'covers': covers,
        'leaf': leaf,
    }


def _pad_digest() -> bytes:
    return _hash(b'CVT1.pad')


def _merkle_layers(leaves: list[bytes]) -> list[list[bytes]]:
    size = 1
    while size < len(leaves):
        size <<= 1
    layer = list(leaves) + [_pad_digest()] * (size - len(leaves))
    layers = [layer]
    while len(layer) > 1:
        layer = [
            _hash(b'CVT1.node', layer[2 * offset], layer[2 * offset + 1])
            for offset in range(len(layer) // 2)
        ]
        layers.append(layer)
    return layers


def _merkle_root(leaves: list[bytes]) -> bytes:
    if not leaves:
        return _hash(b'CVT1.empty')
    return _merkle_layers(leaves)[-1][0]


def _authentication_path(
    layers: list[list[bytes]], index: int
) -> tuple[tuple[int, bytes], ...]:
    path = []
    current = index
    for layer in layers[:-1]:
        sibling_index = current ^ 1
        direction = current & 1
        path.append((direction, layer[sibling_index]))
        current >>= 1
    return tuple(path)


def _tree_depth(entry_count: int) -> int:
    depth = 0
    size = 1
    while size < entry_count:
        size <<= 1
        depth += 1
    return depth


def _encode_path(path: tuple[tuple[int, bytes], ...]) -> bytes:
    chunks = [enc_uint(len(path))]
    for direction, sibling in path:
        chunks.append(bytes((direction,)))
        chunks.append(sibling)
    return b''.join(chunks)


def _decode_path(reader: _Reader, expected_depth: int) -> tuple[tuple[int, bytes], ...]:
    depth = reader.uint()
    if depth != expected_depth:
        raise ValueError('Merkle path depth does not match the record count')
    path = []
    for _ in range(depth):
        direction = reader.byte()
        if direction not in (0, 1):
            raise ValueError('invalid Merkle path direction')
        path.append((direction, reader.digest()))
    return tuple(path)


def _walk_path(
    leaf: bytes, index: int, path: tuple[tuple[int, bytes], ...]
) -> bytes:
    node = leaf
    current = index
    for direction, sibling in path:
        if direction == 0:
            if current & 1:
                raise ValueError('Merkle path direction does not match position')
            node = _hash(b'CVT1.node', node, sibling)
        else:
            if not (current & 1):
                raise ValueError('Merkle path direction does not match position')
            node = _hash(b'CVT1.node', sibling, node)
        current >>= 1
    return node


def _peak_encoding(peak: int | None) -> bytes:
    if peak is None:
        return b'\x00'
    return b'\x01' + enc_uint(peak)


def build_volume_root(volume_id: str, records: tuple[int, ...]) -> str:
    """Return the lowercase hex anchor root for an ordered tuple of records."""
    leaves = [_commit_position(index, value)['leaf']
              for index, value in enumerate(records)]
    peak = max((abs(value) for value in records), default=None)
    root = _hash(
        b'CVT1.root', enc_text(volume_id), _merkle_root(leaves),
        enc_uint(len(records)), _peak_encoding(peak),
    )
    return root.hex()


def build_threshold_proof(
    volume_id: str, threshold: int, records: tuple[int, ...]
) -> bytes:
    """Build the savable proof blob for one threshold export."""
    count = len(records)
    peak = max((abs(value) for value in records), default=None)
    commitments = [_commit_position(index, value)
                   for index, value in enumerate(records)]
    leaves = [item['leaf'] for item in commitments]
    layers = _merkle_layers(leaves) if leaves else []
    threshold_bits = _bits_of(threshold)
    threshold_length = len(threshold_bits)

    chunks = [
        _PROOF_MAGIC,
        bytes((ANCHOR_VERSION,)),
        enc_text(volume_id),
        enc_uint(threshold),
        enc_uint(count),
        _peak_encoding(peak),
        enc_uint(count),
    ]
    # Header order matches verify_threshold_package:
    # magic, version, volume_id, threshold, record_count, peak, entry_count.
    for index, (value, commitment) in enumerate(zip(records, commitments)):
        path = _authentication_path(layers, index) if layers else ()
        k = commitment['k']
        sign = commitment['sign']
        if abs(value) >= threshold:
            chunks += [
                bytes((_ENTRY_HIT,)),
                enc_uint(index),
                bytes((sign,)),
                enc_uint(k),
                bytes(commitment['bits']),
                _encode_path(path),
            ]
            continue

        if k < threshold_length:
            j = 0
            prefix = b''
            cover_digest = commitment['covers'][0]
        else:
            # abs(value) < threshold with k == threshold_length finds the first
            # bit where the magnitude is 0 and the threshold is 1.
            if k != threshold_length:
                raise ValueError('bounded opening is not below the threshold')
            j = 1
            while commitment['bits'][j - 1] == threshold_bits[j - 1]:
                j += 1
            if commitment['bits'][j - 1] != 0 or threshold_bits[j - 1] != 1:
                raise ValueError('bounded opening does not prove the threshold bound')
            prefix = bytes(commitment['bits'][:j])
            cover_digest = commitment['covers'][j]
        chunks += [
            bytes((_ENTRY_BOUND,)),
            enc_uint(index),
            bytes((sign,)),
            enc_uint(k),
            enc_uint(j),
            prefix,
            cover_digest,
            _encode_path(path),
        ]
    return b''.join(chunks)


def _rebuild_exact_leaf(
    index: int, sign: int, k: int, bits: tuple[int, ...]
) -> tuple[bytes, int]:
    if k == 0:
        if bits or sign != 0:
            raise ValueError('invalid zero amplitude commitment')
    else:
        if len(bits) != k or bits[0] != 1 or sign not in (1, 2):
            raise ValueError('non-canonical amplitude commitment')
    if any(bit not in (0, 1) for bit in bits):
        raise ValueError('invalid amplitude bit in commitment')
    seed = _hash(b'CVT1.mag0', enc_uint(index))
    node = seed
    for bit in bits:
        node = _hash(b'CVT1.bit', node, bytes((bit,)))
    cover = _hash(b'CVT1.end', node)
    for _ in range(k):
        cover = _hash(b'CVT1.cover', cover)
    leaf = _hash(
        b'CVT1.leaf', enc_uint(index), enc_uint(k), bytes((sign,)), seed, cover
    )
    magnitude = 0
    for bit in bits:
        magnitude = (magnitude << 1) | bit
    return leaf, magnitude


def _rebuild_bounded_leaf(
    index: int,
    sign: int,
    k: int,
    j: int,
    prefix: tuple[int, ...],
    cover_digest: bytes,
    threshold: int,
) -> bytes:
    if not (0 <= j <= k):
        raise ValueError('invalid bounded opening length')
    if len(prefix) != j or any(bit not in (0, 1) for bit in prefix):
        raise ValueError('invalid bounded opening prefix')
    if k == 0:
        if sign != 0:
            raise ValueError('zero amplitude must use the zero sign')
    elif sign not in (1, 2):
        raise ValueError('invalid sign in bounded opening')
    threshold_bits = _bits_of(threshold)
    threshold_length = len(threshold_bits)
    seed = _hash(b'CVT1.mag0', enc_uint(index))
    node = seed
    for bit in prefix:
        node = _hash(b'CVT1.bit', node, bytes((bit,)))
    if j == 0:
        if threshold == 0 or not (k < threshold_length):
            raise ValueError('bounded opening fails the threshold comparison')
        cover_root = cover_digest
    else:
        if k != threshold_length:
            raise ValueError('bounded opening length does not match the threshold')
        if prefix[0] != 1:
            raise ValueError('non-canonical magnitude in bounded opening')
        for offset in range(j - 1):
            if prefix[offset] != threshold_bits[offset]:
                raise ValueError('bounded opening prefix diverges before the bound bit')
        if prefix[j - 1] != 0 or threshold_bits[j - 1] != 1:
            raise ValueError('bounded opening does not prove the threshold bound')
        cover_root = cover_digest
        for _ in range(j):
            cover_root = _hash(b'CVT1.cover', cover_root)
    return _hash(
        b'CVT1.leaf', enc_uint(index), enc_uint(k), bytes((sign,)), seed, cover_root
    )


def _decode_peak(reader: _Reader) -> int | None:
    tag = reader.byte()
    if tag == 0:
        return None
    if tag != 1:
        raise ValueError('invalid peak encoding')
    return reader.uint()


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _require_non_negative_int(value: Any, subject: str) -> int:
    if not _is_non_negative_int(value):
        raise ValueError(f'{subject} must be a non-negative integer')
    return value


def verify_threshold_package(
    anchor: Any, volume_id: Any, threshold: Any, package: Any
) -> dict[str, Any]:
    """Verify a threshold package using only the passed values."""
    if not isinstance(anchor, dict):
        raise ValueError('anchor must be a dictionary')
    if set(anchor) != {'volume_id', 'version', 'root'}:
        raise ValueError('anchor has invalid fields')
    anchor_volume = anchor['volume_id']
    anchor_version = anchor['version']
    anchor_root = anchor['root']
    if not isinstance(anchor_volume, str) or not anchor_volume:
        raise ValueError('anchor volume_id must be a nonempty string')
    if not isinstance(anchor_version, int) or isinstance(anchor_version, bool):
        raise ValueError('anchor version must be an integer')
    if anchor_version != ANCHOR_VERSION:
        raise ValueError('unsupported threshold commitment version')
    if not isinstance(anchor_root, str) or len(anchor_root) != 2 * _DIGEST_BYTES:
        raise ValueError('anchor root must be a SHA-256 hex digest')
    if any(char not in '0123456789abcdef' for char in anchor_root):
        raise ValueError('anchor root must be lowercase hexadecimal')

    if not isinstance(volume_id, str) or not volume_id:
        raise ValueError('volume_id must be a nonempty string')
    if volume_id != anchor_volume:
        raise ValueError('volume_id does not match the anchor')
    threshold = _require_non_negative_int(threshold, 'threshold')

    if not isinstance(package, dict):
        raise ValueError('package must be a dictionary')
    if set(package) != {
        'volume_id', 'threshold', 'hits', 'record_count', 'peak', 'proof',
    }:
        raise ValueError('package has invalid fields')
    if package['volume_id'] != volume_id:
        raise ValueError('package volume_id does not match the request')
    if package['threshold'] != threshold:
        raise ValueError('package threshold does not match the request')

    hits = package['hits']
    if not isinstance(hits, tuple):
        raise ValueError('hits must be a tuple')
    declared_hits = []
    previous_position = -1
    for hit in hits:
        if not isinstance(hit, tuple) or len(hit) != 2:
            raise ValueError('each hit must be a (position, value) tuple')
        position, value = hit
        if not _is_non_negative_int(position):
            raise ValueError('hit position must be a non-negative integer')
        if position <= previous_position:
            raise ValueError('hit positions must be strictly ascending')
        previous_position = position
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError('hit value must be an integer')
        if abs(value) < threshold:
            raise ValueError('hit value does not reach the threshold')
        declared_hits.append((position, value))

    record_count = _require_non_negative_int(
        package['record_count'], 'record_count'
    )
    peak = package['peak']
    if peak is not None:
        peak = _require_non_negative_int(peak, 'peak')
    proof = package['proof']
    if not isinstance(proof, bytes):
        raise ValueError('proof must be bytes')

    reader = _Reader(proof)
    if reader.take(len(_PROOF_MAGIC)) != _PROOF_MAGIC:
        raise ValueError('invalid threshold proof magic')
    if reader.byte() != ANCHOR_VERSION:
        raise ValueError('unsupported threshold proof version')
    if reader.text() != volume_id:
        raise ValueError('proof volume_id does not match the request')
    if reader.uint() != threshold:
        raise ValueError('proof threshold does not match the request')
    if reader.uint() != record_count:
        raise ValueError('proof record count does not match the package')
    if _decode_peak(reader) != peak:
        raise ValueError('proof peak does not match the package')
    entry_count = reader.uint()
    if entry_count != record_count:
        raise ValueError('proof entry count does not match the record count')

    tree_depth = _tree_depth(entry_count)
    rebuilt_leaves: list[bytes] = []
    proof_hits: list[tuple[int, int]] = []
    for expected_index in range(entry_count):
        tag = reader.byte()
        index = reader.uint()
        if index != expected_index:
            raise ValueError('proof entries are not in original position order')
        sign = reader.byte()
        if sign not in (0, 1, 2):
            raise ValueError('invalid sign in threshold proof')
        k = reader.uint()
        if tag == _ENTRY_HIT:
            bits = tuple(reader.take(k))
            path = _decode_path(reader, tree_depth)
            leaf, magnitude = _rebuild_exact_leaf(index, sign, k, bits)
            value = 0 if sign == 0 else (magnitude if sign == 1 else -magnitude)
            if abs(value) < threshold:
                raise ValueError('proof entry fails its own threshold comparison')
            proof_hits.append((index, value))
        elif tag == _ENTRY_BOUND:
            j = reader.uint()
            prefix = tuple(reader.take(j))
            cover_digest = reader.digest()
            path = _decode_path(reader, tree_depth)
            leaf = _rebuild_bounded_leaf(
                index, sign, k, j, prefix, cover_digest, threshold
            )
        else:
            raise ValueError('unknown threshold proof entry type')
        # Walking the authentication path confirms the leaf belongs to one
        # fixed Merkle tree; the tree root is compared against the anchor below.
        _walk_path(leaf, index, path)
        rebuilt_leaves.append(leaf)
    reader.finish()

    if tuple(proof_hits) != tuple(declared_hits):
        raise ValueError('package hits do not match the committed proof entries')

    expected_root = bytes.fromhex(anchor_root)
    rebuilt_root = _hash(
        b'CVT1.root', enc_text(volume_id), _merkle_root(rebuilt_leaves),
        enc_uint(record_count), _peak_encoding(peak),
    )
    if rebuilt_root != expected_root:
        raise ValueError('package cannot be reconciled with the trusted anchor')

    return {
        'volume_id': volume_id,
        'threshold': threshold,
        'hits': tuple(declared_hits),
        'record_count': record_count,
        'peak': peak,
    }
