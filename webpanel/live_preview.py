"""Bounded fragmented MP4 preview. Separate FFmpeg process, no output backpressure."""
from collections import deque
import os
from pathlib import Path
import struct
import subprocess
import threading
import time
import uuid
from preview import receiver_url

MAX_BOX = 2 * 1024 * 1024
MAX_FRAGMENT = 3 * 1024 * 1024
MAX_TOTAL = 8 * 1024 * 1024
MAX_AGE = 3


class Fragments:
    def __init__(self):
        self.pending = bytearray()
        self.init = bytearray()
        self.fragment = bytearray()
        self.ready = False

    def feed(self, data):
        self.pending.extend(data)
        output = []
        while len(self.pending) >= 8:
            size, kind = struct.unpack('!I4s', self.pending[:8])
            if size < 8 or size > MAX_BOX:
                raise ValueError('Invalid MP4 box size')
            if len(self.pending) < size:
                break
            box = bytes(self.pending[:size]); del self.pending[:size]
            if kind in (b'ftyp', b'moov'):
                if self.ready or len(self.init) + size > MAX_BOX:
                    raise ValueError('Invalid MP4 init')
                self.init.extend(box)
                if kind == b'moov':
                    self.ready = True
            elif kind == b'moof':
                if not self.ready or self.fragment:
                    raise ValueError('Invalid fragment order')
                self.fragment.extend(box)
            elif kind == b'mdat':
                if not self.fragment or len(self.fragment) + size > MAX_FRAGMENT:
                    raise ValueError('Invalid MP4 media')
                self.fragment.extend(box)
                output.append(bytes(self.fragment)); self.fragment.clear()
        return output


class LivePreview:
    def __init__(self, root):
        self.root = Path(root)
        self.lock, self.lifecycle = threading.RLock(), threading.Lock()
        self.process = self.worker = None
        self.session = None
        self.fragments = deque(maxlen=8)
        self.init = None
        self.received = None
        self.sequence = 0
        self.mime = 'video/mp4; codecs="avc1.42C01F, mp4a.40.2"'

    def start(self, output, audio=True):
        url = receiver_url(output)
        with self.lifecycle:
            if self.process and self.process.poll() is None:
                return
            self._stop()
            prefix = self.root / 'target-root/usr/local'
            env = dict(os.environ)
            env['LD_LIBRARY_PATH'] = str(prefix/'lib') + ':' + env.get('LD_LIBRARY_PATH', '')
            command = [str(prefix/'bin/ffmpeg'), '-nostdin', '-v', 'error', '-threads', '1',
                       '-probesize', '2000000', '-analyzeduration', '2000000', '-i', url,
                       '-map', '0:v:0', '-map', '0:a:0?', '-sn', '-dn', '-filter_threads', '1',
                       '-vf', 'yadif=mode=send_field:parity=auto:deint=interlaced,scale=640:trunc(640/dar/2)*2:flags=fast_bilinear,setsar=1,fps=50',
                       '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
                       '-profile:v', 'baseline', '-level:v', '3.1', '-pix_fmt', 'yuv420p',
                       '-threads', '1', '-b:v', '800k', '-maxrate', '1000k', '-bufsize', '1000k',
                       '-g', '25', '-keyint_min', '25', '-sc_threshold', '0', '-bf', '0',
                       '-c:a', 'aac', '-b:a', '96k', '-ar', '48000', '-ac', '2',
                       '-movflags', '+empty_moov+default_base_moof+frag_keyframe',
                       '-f', 'mp4', 'pipe:1']
            if not audio:
                command[command.index('-sn'):command.index('-sn')] = ['-an']
            process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            with self.lock:
                self.process = process
                self.session = uuid.uuid4().hex
                self.sequence = 0
                self.mime = 'video/mp4; codecs="avc1.42C01F' + (', mp4a.40.2' if audio else '') + '"'
            self.worker = threading.Thread(target=self._read, args=(process,), daemon=True)
            self.worker.start()

    def _read(self, process):
        parser = Fragments()
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                for fragment in parser.feed(chunk):
                    with self.lock:
                        if self.process is not process:
                            return
                        self.init = bytes(parser.init)
                        self.sequence += 1
                        self.fragments.append((self.sequence, fragment))
                        while sum(len(data) for _, data in self.fragments) > MAX_TOTAL:
                            self.fragments.popleft()
                        self.received = time.monotonic()
        except (OSError, ValueError):
            if process.poll() is None:
                process.terminate()
        finally:
            process.stdout.close()
            process.wait()
            with self.lock:
                if self.process is process:
                    self.init = self.received = None
                    self.fragments.clear()

    def state(self):
        with self.lock:
            alive = self.process is not None and self.process.poll() is None
            age = None if self.received is None else max(0, time.monotonic() - self.received)
            status = ('idle' if self.process is None else 'error' if not alive else
                      'waiting' if age is None else 'stale' if age > MAX_AGE else 'live')
            return {'mode': 'video', 'status': status, 'age_seconds': round(age, 2) if age is not None else None,
                    'pid': self.process.pid if alive else None, 'session': self.session, 'mime': self.mime,
                    'first_sequence': self.fragments[0][0] if self.fragments else None,
                    'last_sequence': self.sequence, 'frame_id': None}

    def media(self, session, sequence=None):
        with self.lock:
            if session != self.session or self.state()['status'] != 'live':
                raise ValueError('Preview session unavailable')
            if sequence is None:
                return self.init
            for number, data in self.fragments:
                if number == sequence:
                    return data
            if self.fragments and sequence < self.fragments[0][0]:
                raise ValueError('Preview fragment expired')
            return None

    def _stop(self):
        with self.lock:
            process, self.process = self.process, None
            self.session = self.init = self.received = None
            self.fragments.clear()
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=2)
        if self.worker:
            self.worker.join(timeout=2)
            self.worker = None

    def stop(self):
        with self.lifecycle:
            self._stop()
