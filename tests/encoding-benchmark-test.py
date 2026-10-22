#!/usr/bin/env python3
"""Guard alignment and bitrate accounting without starting FFmpeg or OBE."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('benchmark', Path(__file__).with_name('encoding-benchmark.py'))
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def marker(top, bottom):
    return bytes(235 if (top if y % 2 == 0 else bottom) & (1 << (x // 32)) else 16
                 for y in range(32) for x in range(384))


def packet(pid=256, pcr=None):
    data = bytearray(b'\xff' * 188)
    data[:4] = bytes((0x47, pid >> 8, pid & 255, 0x10 if pcr is None else 0x30))
    if pcr is not None:
        base, extension = divmod(pcr, 300)
        data[4:12] = bytes((7, 0x10, base >> 25, (base >> 17) & 255,
                           (base >> 9) & 255, (base >> 1) & 255,
                           ((base & 1) << 7) | 0x7e | (extension >> 8), extension & 255))
    return data


class AlignmentTests(unittest.TestCase):
    def test_field_ids_survive_a_nonzero_capture_offset(self):
        frames = [(2, 3), (4, 5), (6, 7), (8, 9), (10, 11)]
        decoded = [benchmark.decode_marker(marker(*frame)) for frame in frames]
        self.assertEqual(decoded, frames)
        self.assertEqual(benchmark.aligned_window(decoded, first=2, count=3), 1)

    def test_marker_rejects_uncertain_or_incomplete_pixels(self):
        for pixels in (bytes([128]) * 12288, marker(700, 701)[:-1]):
            with self.subTest(length=len(pixels)), self.assertRaises(ValueError):
                benchmark.decode_marker(pixels)

    def test_rejects_missing_duplicate_swapped_or_reordered_fields(self):
        for frames in (
            [(6, 7), (8, 9)],
            [(4, 5), (6, 7)],
            [(4, 5), (6, 7), (6, 7)],
            [(4, 5), (7, 6), (8, 9)],
            [(4, 5), (8, 9), (6, 7)],
            [(4, 5), (6, 7), (8, 9), (4, 5)],
        ):
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                benchmark.aligned_window(frames, first=2, count=3)


class TransportTests(unittest.TestCase):
    def measure(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sample.ts'
            path.write_bytes(content)
            return benchmark.transport_metrics(path)

    def test_rate_and_stuffing_use_the_same_pcr_interval(self):
        # 1000 packet distances in one second, including 100 null packets.
        content = packet(8191)*7 + packet(pcr=27000000)
        content += packet(8191)*100 + packet()*899 + packet(pcr=54000000)
        content += packet(8191)*11
        result = self.measure(content)
        self.assertEqual(result['ts_bitrate_kbps'], 1504)
        self.assertEqual(result['null_packet_percent'], 10)
        self.assertEqual(result['pcr_measurement_seconds'], 1)

    def test_pcr_wrap_and_other_pids_do_not_change_the_rate(self):
        content = packet(pcr=(1 << 33)*300 - 13500000)
        content += packet(pid=257, pcr=123456)*999 + packet(pcr=13500000)
        self.assertEqual(self.measure(content)['ts_bitrate_kbps'], 1504)

    def test_rejects_truncation_no_clock_and_discontinuity(self):
        for content in (packet()[:-1], packet()*3, packet(pcr=0),
                        packet(pcr=0)+packet(pcr=0),
                        packet(pcr=27000000)+packet(pcr=13500000)):
            with self.subTest(size=len(content)), self.assertRaises(ValueError):
                self.measure(content)


if __name__ == '__main__':
    unittest.main()
