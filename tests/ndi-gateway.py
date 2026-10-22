#!/usr/bin/env python3
"""Focused gateway timestamp, container and process tests (stdlib)."""
import importlib.util
import json
import ctypes as C
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('ndi_sender', ROOT/'gateway/ndi_sender.py')
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)
spec = importlib.util.spec_from_file_location('ssh_relay', ROOT/'gateway/ssh_relay.py')
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


class TimelineTests(unittest.TestCase):
    def test_common_origin_preserves_audio_offset(self):
        timeline = sender.Timeline(1_000_000)
        self.assertEqual(timeline.timestamp(1, 1_000_000), 0)
        self.assertEqual(timeline.timestamp(2, 1_100_000), 10_000)
        self.assertEqual(timeline.timestamp(1, 1_400_000), 40_000)

    def test_rejects_timestamp_reset_duplicate_and_large_jump(self):
        for bad in (999_999, 1_000_000, 22_000_000):
            with self.subTest(timecode=bad):
                timeline = sender.Timeline(1_000_000)
                timeline.timestamp(1, 1_000_000)
                with self.assertRaises(RuntimeError):
                    timeline.timestamp(1, bad)

    def test_missing_ndi_timecode_is_rejected(self):
        for origin in (-1, 2**63 - 1):
            with self.assertRaises(RuntimeError):
                sender.Timeline(origin)


class CaptureTests(unittest.TestCase):
    def test_backlog_has_byte_and_packet_limits(self):
        pending = sender.BoundedPackets(max_bytes=8, max_packets=2)
        pending.put_nowait(b'12345')
        with self.assertRaises(sender.queue.Full):
            pending.put_nowait(b'6789')
        self.assertEqual(pending.get(), b'12345')
        pending.put_nowait(b'a')
        pending.put_nowait(b'b')
        with self.assertRaises(sender.queue.Full):
            pending.put_nowait(b'c')
        self.assertEqual(pending.bytes, 2)

    def test_padded_video_rows_do_not_enter_the_picture(self):
        memory = C.create_string_buffer(b'1234pad!5678pad!')
        frame = sender.Video(width=2, height=2, fourcc=int.from_bytes(b'UYVY', 'little'),
            rate_n=25, rate_d=1, aspect=1, format=0, timecode=100,
            data=C.addressof(memory), stride=8)
        self.assertEqual(sender.copy_video(frame)[3], b'12345678')

    def test_planar_audio_is_interleaved_without_padding(self):
        memory = C.create_string_buffer(struct.pack('<fff', .25, .5, 999) + struct.pack('<fff', -.25, -.5, 999))
        frame = sender.Audio(rate=96000, channels=2, samples=2, timecode=100,
            data=C.addressof(memory), stride=12)
        self.assertEqual(struct.unpack('<ffff', sender.copy_audio(frame)[3]), (.25, -.25, .5, -.5))

    def test_unsupported_frames_are_rejected_before_reading_memory(self):
        with self.assertRaises(RuntimeError):
            sender.copy_video(sender.Video())
        with self.assertRaises(RuntimeError):
            sender.copy_audio(sender.Audio())


class RelayTests(unittest.TestCase):
    def test_windows_bootstrap_and_shell_boundary(self):
        args = SimpleNamespace(source='PLAYOUT (TEST)', ndi_runtime=r'C:\NDI\runtime.dll',
            ffmpeg=r'C:\ffmpeg\bin\ffmpeg.exe', field_order='tff', max_mbps=15, threads=2)
        command = relay.remote_command(args, 1024)
        self.assertNotIn('\n', command)
        bootstrap = command.split('-c "', 1)[1].split('" --source', 1)[0]
        compile(bootstrap, '<bootstrap-test>', 'exec')
        for value in ('source&whoami', 'source"', '%PATH%', 'source\nquit'):
            args.source = value
            with self.assertRaises(ValueError):
                relay.remote_command(args, 1024)

    def test_second_gateway_cannot_mix_into_the_same_destination(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(relay.tempfile, 'gettempdir', return_value=directory):
            first = relay.lock_destination('test', 12000)
            try:
                with self.assertRaises(RuntimeError):
                    relay.lock_destination('test', 12000)
            finally:
                os.close(first)
            second = relay.lock_destination('test', 12000)
            os.close(second)

    def test_arbitrary_chunks_keep_mpegts_packet_boundaries(self):
        data = b''.join(b'G'+bytes([i])*187 for i in range(20))
        parser, output = relay.TransportStream(), []
        for offset in range(0, len(data), 137):
            output.extend(parser.feed(data[offset:offset+137]))
        output.extend(parser.feed(b'', final=True))
        self.assertEqual(b''.join(output), data)
        self.assertTrue(all(len(packet) in (188, 1316) for packet in output))

    def test_truncated_packet_is_not_forwarded_and_banner_is_rejected(self):
        parser = relay.TransportStream()
        self.assertEqual(list(parser.feed(b'G'+b'x'*187+b'Gpartial', final=True)), [b'G'+b'x'*187])
        with self.assertRaises(RuntimeError):
            list(relay.TransportStream().feed(b'Unexpected text' + b'x'*1400))


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class MediaTests(unittest.TestCase):
    def test_real_mux_encode_preserves_fields_aspect_and_audio_rate(self):
        video = (64, 48, 25, 1, 16/9, True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'output.ts'
            data = sender.mkv_header(video, (96000, 2))
            for frame in range(25):
                data += sender.mkv_frame(1, frame*40000, bytes([128, 80+frame, 128, 80+frame])*(64*48//2))
                data += sender.mkv_frame(2, frame*40000+100000, struct.pack('<ff', .2, -.2)*3840)
            with path.open('wb') as output:
                run = subprocess.run(sender.ffmpeg_command('ffmpeg', video, 'tff'),
                                     input=data, stdout=output, stderr=subprocess.PIPE, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr.decode(errors='replace'))
            info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)]))
            v, a = info['streams']
            self.assertEqual((v['codec_name'], v['width'], v['height'], v['field_order']), ('h264', 64, 48, 'tt'))
            self.assertEqual(v['display_aspect_ratio'], '16:9')
            self.assertEqual(v['pix_fmt'], 'yuv422p')
            self.assertEqual((a['codec_name'], a['sample_rate'], a['channels']), ('aac', '48000', 2))
            frames = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_frames',
                 '-show_entries', 'frame=media_type,key_frame,best_effort_timestamp_time,interlaced_frame,top_field_first', '-of', 'json', str(path)]))['frames']
            video_frames = [f for f in frames if f['media_type'] == 'video']
            self.assertEqual(len(video_frames), 25)
            self.assertTrue(all(f['interlaced_frame'] and f['top_field_first'] for f in video_frames))
            keys = [float(f['best_effort_timestamp_time']) for f in video_frames if f['key_frame']]
            self.assertGreaterEqual(len(keys), 2)
            self.assertLessEqual(max(b-a for a,b in zip(keys,keys[1:])), .5)
            decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
                 '-map', '0:a:0', '-f', 'f32le', '-'])
            samples = sender.array.array('f')
            samples.frombytes(decoded)
            first_sound = next(i//2 for i,x in enumerate(samples) if abs(x) > .05)/48000
            video_start = float(video_frames[0]['best_effort_timestamp_time'])
            self.assertAlmostEqual(first_sound-video_start, .1, delta=.03,
                msg='Audio offset lost while resampling/muxing')
            for kind in ('video', 'audio'):
                pts = [float(f['best_effort_timestamp_time']) for f in frames if f['media_type'] == kind]
                self.assertTrue(all(b > a for a, b in zip(pts, pts[1:])))


if __name__ == '__main__':
    unittest.main()
