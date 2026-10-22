#!/usr/bin/env python3
"""Preview destination and bounded stream regression checks."""
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'webpanel'))
from preview import Preview, receiver_url, MAX_FRAME


class PreviewTests(unittest.TestCase):
    def test_destinations_do_not_follow_arbitrary_urls_or_reuse_local_socket(self):
        self.assertIn('reuse=0', receiver_url('udp://localhost:13000?ttl=2'))
        self.assertIn('reuse=1', receiver_url('udp://239.1.1.1:13000'))
        self.assertNotIn('ttl=2', receiver_url('udp://localhost:13000?ttl=2'))
        for url in ('udp://192.0.2.1:13000', 'udp://example.com:13000', 'http://localhost:13000', 'file:///tmp/x'):
            with self.subTest(url=url), self.assertRaises(ValueError): receiver_url(url)

    def test_oversized_frame_is_discarded_and_exit_clears_last_image(self):
        preview = Preview('.')
        process = mock.Mock()
        process.poll.return_value = None
        preview.process = process
        chunks = iter([b'\xff\xd8'+b'x'*(MAX_FRAME+1), b'\xff\xd9', b'\xff\xd8ok', b'\xff\xd9', b''])
        seen = []
        def read(*args):
            seen.append(preview.jpeg())
            return next(chunks)
        with mock.patch('preview.os.read', side_effect=read): preview._read(process)
        self.assertIn(b'\xff\xd8ok\xff\xd9', seen)
        self.assertTrue(all(frame is None or len(frame) <= MAX_FRAME for frame in seen))
        self.assertIsNone(preview.jpeg())


if __name__ == '__main__': unittest.main()
