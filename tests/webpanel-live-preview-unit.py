#!/usr/bin/env python3
import struct
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'webpanel'))
from live_preview import Fragments, LivePreview, MAX_BOX


def box(kind, data=b'x'):return struct.pack('!I4s',8+len(data),kind)+data


class PreviewTests(unittest.TestCase):
    def test_fragmented_reads_and_bounded_sequence(self):
        parser=Fragments(); header=box(b'ftyp')+box(b'moov'); fragment=box(b'moof')+box(b'mdat',b'video')
        out=[]
        for byte in header+fragment+fragment:out+=parser.feed(bytes([byte]))
        self.assertEqual(bytes(parser.init),header);self.assertEqual(out,[fragment,fragment]);self.assertFalse(parser.pending)

    def test_rejects_oversized_or_malformed_media(self):
        for data in (struct.pack('!I4s',MAX_BOX+1,b'mdat'),box(b'mdat'),box(b'moof'),struct.pack('!I4s',0,b'free')):
            with self.subTest(data=data),self.assertRaises(ValueError): Fragments().feed(data)
        parser=Fragments();parser.feed(box(b'ftyp')+box(b'moov')+box(b'moof'))
        with self.assertRaises(ValueError): parser.feed(box(b'moof'))

    def test_idle_or_old_session_never_returns_media(self):
        preview=LivePreview('.')
        self.assertEqual(preview.state()['status'],'idle')
        with self.assertRaises(ValueError):preview.media('old')
        preview.stop();self.assertEqual(preview.state()['status'],'idle')


if __name__=='__main__':unittest.main()
