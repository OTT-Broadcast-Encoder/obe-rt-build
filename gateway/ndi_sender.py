#!/usr/bin/env python3
"""NDI program receiver -> timestamped Matroska -> MPEG-TS on stdout or SRT.

Only Python stdlib, an installed NDI runtime and FFmpeg are required.
Diagnostics go to stderr; stdout is reserved for the contribution stream.
"""
import array
import argparse
from collections import deque
import ctypes as C
from fractions import Fraction
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import queue
import shutil
import struct
import subprocess
import sys
import threading
import time

LOGGER = logging.getLogger('ndi-gateway')


def size_vint(value):
    for width in range(1, 9):
        if 0 <= value < (1 << (7 * width)) - 1:
            return ((1 << (7 * width)) | value).to_bytes(width, 'big')
    raise ValueError('Matroska element too large')


def element(tag, data):
    return tag.to_bytes((tag.bit_length() + 7) // 8, 'big') + size_vint(len(data)) + data


def uint(tag, value):
    return element(tag, value.to_bytes(max(1, (value.bit_length() + 7) // 8), 'big'))


def mkv_header(video, audio, field_order='tff'):
    """Raw UYVY + interleaved float PCM; one common microsecond timebase."""
    width, height, rate_n, rate_d, aspect, interlaced = video
    sample_rate, channels = audio
    dar = Fraction(aspect).limit_denominator(1000)
    ebml = element(0x1A45DFA3, b''.join((uint(0x4286, 1), uint(0x42F7, 1),
        uint(0x42F2, 4), uint(0x42F3, 8), element(0x4282, b'matroska'),
        uint(0x4287, 4), uint(0x4285, 2))))
    info = element(0x1549A966, uint(0x2AD7B1, 1000)
                   + element(0x4D80, b'OBE NDI gateway') + element(0x5741, b'OBE NDI gateway'))
    bitmap = struct.pack('<IiiHH4sIiiII', 40, width, height, 1, 16, b'UYVY', width*height*2, 0, 0, 0, 0)
    vformat = element(0xE0, uint(0xB0, width) + uint(0xBA, height)
        + uint(0x54B0, dar.numerator) + uint(0x54BA, dar.denominator) + uint(0x54B2, 3)
        + uint(0x9A, 1 if interlaced else 2)
        + uint(0x9D, (1 if field_order == 'tff' else 6) if interlaced else 0))
    vtrack = element(0xAE, uint(0xD7, 1) + uint(0x73C5, 1) + uint(0x83, 1)
        + uint(0x9C, 0) + uint(0x23E383, round(1_000_000_000 * rate_d / rate_n))
        + element(0x86, b'V_MS/VFW/FOURCC') + element(0x63A2, bitmap) + vformat)
    atrack = element(0xAE, uint(0xD7, 2) + uint(0x73C5, 2) + uint(0x83, 2)
        + uint(0x9C, 0) + element(0x86, b'A_PCM/FLOAT/IEEE')
        + element(0xE1, element(0xB5, struct.pack('>d', sample_rate))
                  + uint(0x9F, channels) + uint(0x6264, 32)))
    return ebml + bytes.fromhex('1853806701ffffffffffffff') + info + element(0x1654AE6B, vtrack + atrack)


def mkv_frame(track, pts_us, payload):
    if track not in (1, 2) or pts_us < 0:
        raise ValueError('Invalid track or negative timestamp')
    return element(0x1F43B675, uint(0xE7, pts_us)
                   + element(0xA3, bytes([0x80 | track, 0, 0, 0x80]) + payload))


class Timeline:
    def __init__(self, origin):
        if not 0 <= origin < 2**63 - 1:
            raise RuntimeError('NDI source has no valid timecode')
        self.origin, self.last = origin, {}

    def timestamp(self, track, timecode):
        if not 0 <= timecode < 2**63 - 1:
            raise RuntimeError('NDI source has no valid timecode')
        pts = (timecode - self.origin) // 10  # NDI 100 ns -> Matroska microseconds.
        if pts < 0 or (track in self.last and not 0 < pts - self.last[track] <= 2_000_000):
            raise RuntimeError('NDI timestamp discontinuity; reconnect required')
        self.last[track] = pts
        return pts


class BoundedPackets:
    def __init__(self, max_bytes=32*1024*1024, max_packets=64):
        self.queue = queue.Queue(maxsize=max_packets)
        self.lock = threading.Lock()
        self.bytes, self.max_bytes = 0, max_bytes

    def put_nowait(self, packet):
        size = len(packet) if packet is not None else 0
        with self.lock:
            if self.bytes + size > self.max_bytes:
                raise queue.Full
            self.queue.put_nowait(packet)
            self.bytes += size

    def get(self):
        packet = self.queue.get()
        with self.lock:
            self.bytes -= len(packet) if packet is not None else 0
        return packet


def ffmpeg_command(executable, video, field_order, max_mbps=15, threads=2, *,
                   srt_port=None, srt_passphrase=None, srt_latency_ms=200):
    if srt_port is not None:
        if not 1 <= srt_port <= 65535 or not 20 <= srt_latency_ms <= 8000:
            raise ValueError('Invalid SRT port or latency')
        if not srt_passphrase or not 10 <= len(srt_passphrase) <= 79 or any(
                c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in srt_passphrase):
            raise ValueError('SRT requires a 10-79 character URL-safe passphrase')
    width, height, rate_n, rate_d, aspect, interlaced = video
    sar = (Fraction(aspect).limit_denominator(1000) * height / width).limit_denominator(1000)
    vf = f'format=yuv422p,setsar={sar.numerator}/{sar.denominator}'
    if interlaced:
        vf = f'setfield={field_order},' + vf
    command = [executable, '-nostdin', '-hide_banner', '-loglevel', 'warning',
        '-threads', str(threads), '-filter_threads', '1', '-probesize', '2000000',
        '-analyzeduration', '500000', '-f', 'matroska', '-i', 'pipe:0',
        '-map', '0:v:0', '-map', '0:a:0', '-vf', vf,
        '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
        '-crf', '16', '-maxrate', f'{max_mbps}M', '-bufsize', f'{max_mbps}M',
        '-threads', str(threads), '-g', str(max(1, round(rate_n / rate_d / 2))), '-bf', '0',
        '-pix_fmt', 'yuv422p', '-fps_mode', 'passthrough']
    x264 = 'repeat-headers=1'
    if interlaced:
        command += ['-flags', '+ilme+ildct']
        x264 += f':{field_order}=1:weightp=0'
    command += ['-x264-params', x264]
    matrix = 'bt470bg' if height <= 576 else 'bt709'
    command += ['-colorspace', matrix, '-color_primaries', matrix, '-color_trc', 'bt709',
        '-c:a', 'aac', '-b:a', '256k', '-ar', '48000', '-ac', '2',
        '-af', 'aresample=48000:async=100:first_pts=0',
        '-streamid', '0:256', '-streamid', '1:257', '-muxdelay', '0', '-flush_packets', '1']
    if srt_port is None:
        command += ['-mpegts_service_id', '1', '-mpegts_pmt_start_pid', '4096',
            '-mpegts_flags', '+resend_headers+initial_discontinuity', '-f', 'mpegts', 'pipe:1']
    else:
        # A bounded encoded-packet queue keeps capture live while no client is connected.
        # The underlying muxer passes protocol options to SRT; keep the secret out of URLs.
        options = ('mpegts_service_id=1:mpegts_pmt_start_pid=4096:'
                   'mpegts_flags=+resend_headers+initial_discontinuity:flush_packets=1:'
                   f'passphrase={srt_passphrase}')
        command += ['-f', 'fifo', '-fifo_format', 'mpegts', '-format_opts', options,
            '-queue_size', '60', '-drop_pkts_on_overflow', '1', '-restart_with_keyframe', '1',
            '-attempt_recovery', '1', '-recover_any_error', '1', '-recovery_wait_time', '1',
            f'srt://0.0.0.0:{srt_port}?mode=listener&transtype=live&pkt_size=1316'
            f'&latency={srt_latency_ms*1000}&pbkeylen=32&enforced_encryption=1'
            '&listen_timeout=2000000&timeout=3000000']
    return command


class Source(C.Structure):
    _fields_ = [('name', C.c_char_p), ('url', C.c_char_p)]


class Find(C.Structure):
    _fields_ = [('local', C.c_bool), ('groups', C.c_char_p), ('ips', C.c_char_p)]


class Receive(C.Structure):
    _fields_ = [('source', Source), ('color', C.c_int), ('bandwidth', C.c_int),
               ('fields', C.c_bool), ('name', C.c_char_p)]


class Video(C.Structure):
    _fields_ = [('width', C.c_int), ('height', C.c_int), ('fourcc', C.c_uint32),
        ('rate_n', C.c_int), ('rate_d', C.c_int), ('aspect', C.c_float),
        ('format', C.c_int), ('timecode', C.c_int64), ('data', C.c_void_p),
        ('stride', C.c_int), ('metadata', C.c_char_p), ('timestamp', C.c_int64)]


class Audio(C.Structure):
    _fields_ = [('rate', C.c_int), ('channels', C.c_int), ('samples', C.c_int),
        ('timecode', C.c_int64), ('data', C.c_void_p), ('stride', C.c_int),
        ('metadata', C.c_char_p), ('timestamp', C.c_int64)]


class Metadata(C.Structure):
    _fields_ = [('length', C.c_int), ('timecode', C.c_int64), ('data', C.c_void_p)]


def copy_video(frame):
    if (frame.fourcc != int.from_bytes(b'UYVY', 'little') or frame.format not in (0, 1)
        or not frame.data or not 2 <= frame.width <= 1920 or frame.width % 2
        or not 2 <= frame.height <= 1080 or frame.rate_d <= 0
        or not 1 <= frame.rate_n / frame.rate_d <= 60):
        raise RuntimeError('NDI requires UYVY full frames, SDR up to 1920x1080/60')
    aspect = frame.aspect or frame.width / frame.height
    if not math.isfinite(aspect) or not .1 <= aspect <= 10:
        raise RuntimeError('Invalid NDI aspect ratio')
    row = frame.width * 2
    stride = frame.stride or row
    if not row <= stride <= row + 4096:
        raise RuntimeError('Invalid NDI video stride')
    if stride == row:
        payload = C.string_at(frame.data, row * frame.height)
    else:
        payload = b''.join(C.string_at(frame.data + y*stride, row) for y in range(frame.height))
    fmt = (frame.width, frame.height, frame.rate_n, frame.rate_d, aspect, frame.format == 0)
    return 1, frame.timecode, fmt, payload


def copy_audio(frame):
    if (frame.channels != 2 or not 32000 <= frame.rate <= 192000
        or not 1 <= frame.samples <= frame.rate or not frame.data
        or not frame.samples*4 <= frame.stride <= frame.samples*4+4096):
        raise RuntimeError('NDI requires stereo float PCM, 32-192 kHz')
    output = array.array('f', [0]) * (frame.samples * 2)
    for channel in range(2):
        samples = array.array('f')
        samples.frombytes(C.string_at(frame.data + channel*frame.stride, frame.samples*4))
        output[channel::2] = samples
    return 2, frame.timecode, (frame.rate, frame.channels), output.tobytes()


class NDI:
    def __init__(self, runtime):
        self.finder = self.receiver = self.directory = None
        self.initialized = False
        if os.name == 'nt':
            self.directory = os.add_dll_directory(str(Path(runtime).resolve().parent))
        self.lib = C.CDLL(runtime)
        definitions = {
            'initialize': ([], C.c_bool), 'destroy': ([], None),
            'find_create_v2': ([C.POINTER(Find)], C.c_void_p),
            'find_destroy': ([C.c_void_p], None),
            'find_wait_for_sources': ([C.c_void_p, C.c_uint32], C.c_bool),
            'find_get_current_sources': ([C.c_void_p, C.POINTER(C.c_uint32)], C.POINTER(Source)),
            'recv_create_v3': ([C.POINTER(Receive)], C.c_void_p),
            'recv_destroy': ([C.c_void_p], None),
            'recv_capture_v2': ([C.c_void_p, C.POINTER(Video), C.POINTER(Audio), C.POINTER(Metadata), C.c_uint32], C.c_int),
            'recv_free_video_v2': ([C.c_void_p, C.POINTER(Video)], None),
            'recv_free_audio_v2': ([C.c_void_p, C.POINTER(Audio)], None),
            'recv_free_metadata': ([C.c_void_p, C.POINTER(Metadata)], None),
        }
        for name, (arguments, returns) in definitions.items():
            fn = getattr(self.lib, 'NDIlib_' + name)
            fn.argtypes, fn.restype = arguments, returns
            setattr(self, name, fn)
        self.initialized = bool(self.initialize())
        if not self.initialized:
            raise RuntimeError('NDI initialization failed')

    def connect(self, name, stop):
        self.finder = self.find_create_v2(C.byref(Find(True, None, None)))
        if not self.finder:
            raise RuntimeError('NDI discovery failed')
        deadline = time.monotonic() + 10
        while not stop.is_set() and time.monotonic() < deadline:
            self.find_wait_for_sources(self.finder, 250)
            count = C.c_uint32()
            found = self.find_get_current_sources(self.finder, C.byref(count))
            for i in range(count.value):
                if found[i].name == name.encode('utf-8'):
                    config = Receive(found[i], 1, 100, True, b'OBE contribution gateway')
                    self.receiver = self.recv_create_v3(C.byref(config))
                    if not self.receiver:
                        raise RuntimeError('NDI receiver creation failed')
                    return
        raise RuntimeError('NDI source unavailable')

    def read(self):
        video, audio, metadata = Video(), Audio(), Metadata()
        kind = self.recv_capture_v2(self.receiver, C.byref(video), C.byref(audio), C.byref(metadata), 250)
        if kind == 1:
            try:
                return copy_video(video)
            finally:
                self.recv_free_video_v2(self.receiver, C.byref(video))
        if kind == 2:
            try:
                return copy_audio(audio)
            finally:
                self.recv_free_audio_v2(self.receiver, C.byref(audio))
        if kind == 3:
            self.recv_free_metadata(self.receiver, C.byref(metadata))
        if kind == 4:
            raise RuntimeError('NDI receiver disconnected')
        return None

    def close(self):
        if self.receiver:
            self.recv_destroy(self.receiver)
        if self.finder:
            self.find_destroy(self.finder)
        if self.initialized:
            self.destroy()
        if self.directory:
            self.directory.close()


def log(event, **details):
    message = json.dumps({'event': event, **details})
    if LOGGER.handlers:
        LOGGER.info(message)
    else:
        print(message, file=sys.stderr, flush=True)


def watch_control(stop, heartbeat):
    def reader():
        pending = b''
        try:
            while not stop.is_set():
                chunk = os.read(sys.stdin.fileno(), 64)
                if not chunk:
                    break
                pending += chunk
                if len(pending) > 128:
                    break
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    if line != b'alive':
                        stop.set()
                        return
                    heartbeat[0] = time.monotonic()
        except OSError:
            pass
        finally:
            stop.set()
        # Raw os.read avoids BufferedReader's fatal daemon-thread lock at Windows exit.
    threading.Thread(target=reader, daemon=True).start()


def stop_requested(path):
    return bool(path and path.exists() and path.read_text(encoding='ascii').strip() == 'stop')


def run(args):
    stop = threading.Event()
    heartbeat = [time.monotonic()]
    if args.heartbeat_timeout:
        watch_control(stop, heartbeat)
    # Independent watchdog remains responsive even if FFmpeg's input pipe stalls.
    def watchdog():
        while not stop.wait(.2):
            if stop_requested(args.stop_file):
                stop.set()
            if args.heartbeat_timeout and time.monotonic() - heartbeat[0] > args.heartbeat_timeout:
                log('heartbeat_timeout')
                stop.set()
    threading.Thread(target=watchdog, daemon=True).start()
    ndi, process, writer, reader = None, None, None, None
    packets, errors = BoundedPackets(), []
    counts = {1: 0, 2: 0}
    try:
        ndi = NDI(args.ndi_runtime)
        ndi.connect(args.source, stop)
        initial, formats = deque(maxlen=16), {}
        deadline = time.monotonic() + 5
        while len(formats) < 2 and not stop.is_set():
            if time.monotonic() >= deadline:
                raise RuntimeError('NDI probe requires both video and stereo audio')
            frame = ndi.read()
            if frame:
                initial.append(frame)
                formats[frame[0]] = frame[2]
        if stop.is_set():
            return
        timeline = Timeline(min(frame[1] for frame in initial))
        process = subprocess.Popen(ffmpeg_command(args.ffmpeg, formats[1], args.field_order,
            args.max_mbps, args.threads, srt_port=args.srt_port,
            srt_passphrase=args.srt_passphrase, srt_latency_ms=args.srt_latency_ms),
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL if args.srt_port else sys.stdout.buffer,
            stderr=subprocess.PIPE if args.srt_port else None,
            creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0)
        if args.srt_port:
            def collect():
                for line in process.stderr:
                    log('ffmpeg', message=line.decode(errors='replace').strip().replace(args.srt_passphrase, '[redacted]'))
            reader = threading.Thread(target=collect, daemon=True)
            reader.start()
        def write_frames():
            try:
                process.stdin.write(mkv_header(formats[1], formats[2], args.field_order))
                while True:
                    packet = packets.get()
                    if packet is None:
                        break
                    process.stdin.write(packet)
                    process.stdin.flush()
                process.stdin.close()
            except (OSError, ValueError) as error:
                errors.append(type(error).__name__)
                stop.set()
        writer = threading.Thread(target=write_frames, daemon=True)
        writer.start()
        log('connected', source=args.source, video=formats[1], audio=formats[2], field_order=args.field_order,
            ffmpeg_pid=process.pid, max_mbps=args.max_mbps)
        start = time.monotonic()
        last = {1: start, 2: start}
        while not stop.is_set() and (not args.seconds or time.monotonic() - start < args.seconds):
            if process.poll() is not None:
                raise RuntimeError(f'FFmpeg exited ({process.returncode})')
            frame = initial.popleft() if initial else ndi.read()
            if frame:
                track, timecode, fmt, payload = frame
                if fmt != formats[track]:
                    raise RuntimeError('NDI format changed; reconnect required')
                packet = mkv_frame(track, timeline.timestamp(track, timecode), payload)
                try:
                    packets.put_nowait(packet)
                except queue.Full:
                    raise RuntimeError('Encoding/transport backlog; restarting instead of accumulating latency')
                counts[track] += 1
                last[track] = time.monotonic()
            if time.monotonic() - min(last.values()) > 3:
                raise RuntimeError('NDI video or audio stalled')
        if errors:
            raise RuntimeError('FFmpeg pipe failed')
    finally:
        stop.set()
        if process:
            try:
                packets.put_nowait(None)
            except queue.Full:
                pass
            if writer:
                writer.join(timeout=2)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            if reader:
                reader.join(timeout=2)
                process.stderr.close()
            if writer:
                writer.join(timeout=2)
        if ndi:
            ndi.close()
        log('stopped', video_frames=counts[1], audio_blocks=counts[2])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Exact NDI source name')
    parser.add_argument('--ndi-runtime', default=r'C:\OndaMultimedia\x64\NDI\Processing.NDI.Lib.x64.dll')
    parser.add_argument('--ffmpeg', default=shutil.which('ffmpeg') or r'C:\ffmpeg\bin\ffmpeg.exe')
    parser.add_argument('--field-order', choices=('tff', 'bff'), default='tff')
    parser.add_argument('--max-mbps', type=int, choices=range(1, 51), default=15, metavar='1..50')
    parser.add_argument('--threads', type=int, choices=range(1, 5), default=2)
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--heartbeat-timeout', type=float, default=12)
    parser.add_argument('--srt-port', type=int, help='Serve encrypted MPEG-TS directly on this UDP port')
    parser.add_argument('--srt-passphrase-file', type=Path, help='Restricted file containing a URL-safe SRT key')
    parser.add_argument('--srt-latency-ms', type=int, default=200)
    parser.add_argument('--stop-file', type=Path, help='Exit when this file contains stop (for the Windows task)')
    parser.add_argument('--log-file', type=Path, help='Rotate diagnostic logs (2 MiB, two backups)')
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds < 0 or not 0 <= args.heartbeat_timeout <= 60:
        parser.error('Invalid duration or heartbeat timeout')
    if sys.byteorder != 'little':
        parser.error('Only little-endian hosts are supported')
    args.srt_passphrase = None
    if args.srt_port is not None:
        if not args.srt_passphrase_file:
            parser.error('--srt-passphrase-file is required for a public listener')
        try:
            args.srt_passphrase = args.srt_passphrase_file.read_text(encoding='ascii').strip()
            ffmpeg_command(args.ffmpeg, (720, 576, 25, 1, 16/9, True), args.field_order,
                srt_port=args.srt_port, srt_passphrase=args.srt_passphrase, srt_latency_ms=args.srt_latency_ms)
        except (OSError, UnicodeError, ValueError):
            parser.error('Invalid SRT port, latency or passphrase file (10-79 URL-safe characters required)')
        args.heartbeat_timeout = 0  # Standalone listener: no SSH control pipe.
    elif args.srt_passphrase_file:
        parser.error('--srt-passphrase-file requires --srt-port')
    if args.log_file:
        LOGGER.setLevel(logging.INFO)
        LOGGER.addHandler(RotatingFileHandler(args.log_file, maxBytes=2*1024*1024, backupCount=2, encoding='utf-8'))
    if os.name == 'nt' and not args.srt_port:
        import msvcrt
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    while not stop_requested(args.stop_file):
        try:
            run(args)
            break
        except KeyboardInterrupt:
            break
        except Exception as error:
            message = str(error)
            if args.srt_passphrase:
                message = message.replace(args.srt_passphrase, '[redacted]')
            log('error', message=message)
            if not args.srt_port or args.seconds:
                return 1
            if stop_requested(args.stop_file):
                break
            log('retry', seconds=2)
            try:
                time.sleep(2)
            except KeyboardInterrupt:
                break
    return 0


if __name__ == '__main__':
    sys.exit(main())
