from intake.encoding import PayloadFormatError, decode_samples, encode_samples
from objects.integrity import PayloadVerificationError
from objects.payloads import read_payload
from review.result import read
from review.selection import UnknownVolumeError
from tests.archive_fixture import ArchiveCase


class PickupTests(ArchiveCase):
    def test_original_order_and_peak(self):
        receipt = self.sign('shift-C', [3, -8, 3])
        self.reopen()
        self.assert_pickup(receipt, (3, -8, 3), 3, 8)

    def test_empty_volume(self):
        empty = self.sign('shift-E', [])
        zero = self.sign('shift-Z', [0])
        self.reopen()
        self.assert_pickup(empty, (), 0, None)
        self.assert_pickup(zero, (0,), 1, 0)

    def test_fixed_volume(self):
        original = self.sign('first', [3, -8, 3])
        later = self.sign('later', [1, 2])
        self.reopen()
        self.assert_pickup(original, (3, -8, 3), 3, 8)
        self.assert_pickup(later, (1, 2), 2, 2)

    def test_corrupt_payload_that_passes_object_checksum(self):
        receipt = self.sign('tampered', [3, -8, 3])
        name = self.object_for(receipt)
        replacement = encode_samples((3, -7, 3))
        self.replace_object(name, replacement)
        self.assertEqual(read_payload(self.objects, name), replacement)
        with self.assertRaises(PayloadVerificationError):
            read(self.catalog, self.objects, receipt.volume_id)

    def test_unknown_volume(self):
        with self.assertRaises(UnknownVolumeError):
            read(self.catalog, self.objects, 'absent')

    def test_exact_large_integers(self):
        large = 1 << 20000
        receipt = self.sign('wide', [large - 1, -large, 0])
        self.assert_pickup(receipt, (large - 1, -large, 0), 3, large)

    def test_strict_versioned_frame(self):
        empty_frame = bytes.fromhex('43 56 01 00 00 00 00')
        negative_frame = bytes.fromhex('43 56 01 00 00 00 01 02 00 00 00 01 08')
        self.assertEqual(encode_samples(()), empty_frame)
        self.assertEqual(encode_samples((-8,)), negative_frame)
        self.assertEqual(decode_samples(negative_frame), (-8,))
        malformed = (
            b'', b'XY' + empty_frame[2:], b'CV\x02' + empty_frame[3:],
            empty_frame + b'\x00', negative_frame[:-1],
            bytes.fromhex('43 56 01 00 00 00 02 02 00 00 00 01 08'),
            bytes.fromhex('43 56 01 00 00 00 01 02 00 00 00 02 00 08'),
            bytes.fromhex('43 56 01 00 00 00 01 01 00 00 00 00'),
            bytes.fromhex('43 56 01 00 00 00 01 00 00 00 00 01 00'),
        )
        for frame in malformed:
            with self.subTest(frame=frame.hex()), self.assertRaises(PayloadFormatError):
                decode_samples(frame)
