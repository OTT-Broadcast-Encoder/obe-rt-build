#!/usr/bin/env python3
"""Exercise the gateway's real encrypted SRT listener without Windows or NDI."""
import importlib.util
import json
from pathlib import Path
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('ndi_sender', Path(__file__).resolve().parents[1]/'gateway/ndi_sender.py')
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)
VIDEO = (64, 48, 25, 1, 16/9, True)


class ListenerTests(unittest.TestCase):
    def test_srt_uses_a_near_lossless_profile_without_changing_pipe_defaults(self):
        key = secrets.token_hex(16)
        srt = sender.ffmpeg_command('ffmpeg', (1920, 1080, 25, 1, 16/9, True), 'tff',
                                    srt_port=20879, srt_passphrase=key)
        pipe = sender.ffmpeg_command('ffmpeg', VIDEO, 'tff')
        def value(command, option):
            return command[command.index(option)+1]
        self.assertEqual((value(srt, '-preset'), value(srt, '-crf'), value(srt, '-maxrate'),
                          value(srt, '-threads'), value(srt, '-pix_fmt')),
                         ('veryfast', '8', '200M', '4', 'yuv422p'))
        self.assertEqual((value(pipe, '-preset'), value(pipe, '-crf'), value(pipe, '-maxrate')),
                         ('veryfast', '16', '15M'))
        self.assertEqual(value(srt, '-flags'), '+ilme+ildct')
        self.assertIn('tff=1', value(srt, '-x264-params'))
        self.assertEqual(value(srt, '-queue_size'), '60')
        self.assertIn('listen_timeout=2000000', srt[-1])
        self.assertNotIn('-minrate', srt)
        self.assertNotIn('-b:v', srt)

    def test_public_listener_requires_a_valid_key(self):
        for key in (None, '', 'short', 'a'*80, 'unsafe:key&value', 'secret\nvalue'):
            with self.assertRaises(ValueError):
                sender.ffmpeg_command('ffmpeg', VIDEO, 'tff', srt_port=20879, srt_passphrase=key)
        with self.assertRaises(ValueError):
            sender.ffmpeg_command('ffmpeg', VIDEO, 'tff', srt_port=0, srt_passphrase=secrets.token_hex(16))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_idle_wrong_key_and_client_reconnection(self):
        key = secrets.token_hex(16)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        command = sender.ffmpeg_command('ffmpeg', VIDEO, 'tff', srt_port=port, srt_passphrase=key)
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE)
        stop, logs = threading.Event(), []
        def collect():
            for line in process.stderr:
                logs.append(line.decode(errors='replace'))
        def feed():
            try:
                process.stdin.write(sender.mkv_header(VIDEO, (96000, 2)))
                frame = 0
                while not stop.is_set():
                    process.stdin.write(sender.mkv_frame(1, frame*40000, bytes([128, 80+frame%80, 128, 80])*(64*48//2)))
                    process.stdin.write(sender.mkv_frame(2, frame*40000, struct.pack('<ff', .2, -.2)*3840))
                    process.stdin.flush()
                    frame += 1
                    stop.wait(.04)
            except (OSError, ValueError):
                pass
            finally:
                process.stdin.close()
        reader, writer = threading.Thread(target=collect), threading.Thread(target=feed)
        reader.start()
        writer.start()
        url = (f'srt://127.0.0.1:{port}?mode=caller&latency=200000&pbkeylen=32'
               '&enforced_encryption=1&connect_timeout=1000&timeout=2000000')
        def probe(secret, retry):
            deadline = time.monotonic()+(12 if retry else 0)
            while True:
                try:
                    result = subprocess.run(['ffprobe', '-v', 'error', '-passphrase', secret,
                        '-analyzeduration', '1000000', '-probesize', '2000000', '-read_intervals', '%+1',
                        '-show_streams', '-show_packets', '-of', 'json', url],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6)
                    if result.returncode == 0:
                        return json.loads(result.stdout)
                except subprocess.TimeoutExpired:
                    pass
                if time.monotonic() >= deadline:
                    return None
                time.sleep(.3)
        try:
            time.sleep(12)  # Join after several listener retries; capture must keep draining.
            self.assertIsNone(process.poll(), 'Listener exited while idle')
            first = probe(key, True)
            self.assertIsNotNone(first, 'Encrypted listener did not deliver media')
            self.assertIsNone(probe(secrets.token_hex(16), False), 'Wrong key was accepted')
            second = probe(key, True)
            self.assertIsNotNone(second, 'Listener failed to accept a new client')
            for result in (first, second):
                v, a = result['streams']
                self.assertEqual((v['field_order'], v['pix_fmt'], v['display_aspect_ratio']), ('tt', 'yuv422p', '16:9'))
                self.assertEqual((a['sample_rate'], a['channels']), ('48000', 2))
            def start(result):
                return min(float(p['pts_time']) for p in result['packets'] if 'pts_time' in p)
            self.assertGreater(start(first), 1, 'Listener delivered stale queued media')
            self.assertGreater(start(second), start(first)+1, 'Reconnect did not resume current media')
        finally:
            stop.set()
            writer.join(timeout=3)
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            writer.join(timeout=3)
            reader.join(timeout=3)
            process.stderr.close()
        self.assertNotIn(key, ''.join(logs), 'FFmpeg exposed the passphrase')
        self.assertFalse(writer.is_alive() or reader.is_alive(), 'Gateway did not stop')


if __name__ == '__main__':
    unittest.main()
