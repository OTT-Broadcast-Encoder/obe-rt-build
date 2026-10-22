#!/usr/bin/env python3
"""Framing, retry and cleanup checks for the SSH contribution gateway."""
from pathlib import Path
import socket
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'gateway'))
from ts_ssh import Packetizer, source_url, relay, DATAGRAM

PACKET = b'\x47'+bytes(187)


class GatewayTests(unittest.TestCase):
    def test_partial_reads_and_resynchronization(self):
        packetizer = Packetizer()
        source = b'invalid header'+PACKET*14
        result = []
        for offset in range(0, len(source), 17): result.extend(packetizer.feed(source[offset:offset+17]))
        self.assertEqual(result, [PACKET*7, PACKET*7])
        self.assertEqual(packetizer.discarded, len(b'invalid header'))
        self.assertLess(len(packetizer.pending), DATAGRAM)
        result = list(packetizer.feed(b'x'*100000))
        self.assertEqual(result, [])
        self.assertLess(len(packetizer.pending), DATAGRAM)

    def test_source_rejects_shell_and_credentials(self):
        self.assertEqual(source_url('udp://239.255.197.106:2079'), 'udp://239.255.197.106:2079')
        for url in ('udp://239.1.1.1:12;id', 'udp://user:pw@239.1.1.1:12',
                    'udp://239.1.1.1:12?x=1', 'udp://239.1.1.1:12/path', 'http://localhost:80'):
            with self.subTest(url=url), self.assertRaises(ValueError): source_url(url)

    def test_forwarding_and_bounded_retries(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(('127.0.0.1', 0)); receiver.settimeout(1)
            command = [sys.executable, '-u', '-c', 'import os,time;os.write(1,(b"\\x47"+bytes(187))*7);time.sleep(2)']
            result = relay(command, receiver.getsockname(), .25, 0, 20)
            self.assertEqual(result['status'], 'pass')
            self.assertEqual(receiver.recv(2048), PACKET*7)
            self.assertEqual(result['datagrams'], 1)
            self.assertIsNone(result['udp_packet_loss'])
            result = relay([sys.executable, '-c', 'pass'], receiver.getsockname(), 3, 1, 20)
            self.assertEqual(result['status'], 'fail')
            self.assertEqual(result['connections'], 2)
            self.assertEqual(result['retries'], 1)


if __name__ == '__main__': unittest.main()
