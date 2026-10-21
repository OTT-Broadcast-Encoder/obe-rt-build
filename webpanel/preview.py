"""On-demand, bounded JPEG snapshots of a local/multicast MPEG-TS output."""
import ipaddress
import os
from pathlib import Path
import subprocess
import threading
import time
from urllib.parse import urlsplit

MAX_FRAME = 1024 * 1024
MAX_AGE = 3


def receiver_url(output):
    parts = urlsplit(output)
    host = '127.0.0.1' if parts.hostname == 'localhost' else parts.hostname
    try:
        address = ipaddress.ip_address(host)
        if parts.scheme != 'udp' or not (address.is_loopback or address.is_multicast):
            raise ValueError()
        port = parts.port
        if not port or not 1 <= port <= 65535: raise ValueError()
    except ValueError:
        raise ValueError('La preview richiede un’uscita UDP su loopback o multicast. Per l’unicast remoto serve un monitor a destinazione.') from None
    host = f'[{address}]' if address.version == 6 else str(address)
    return (f'udp://{host}:{port}?reuse={int(address.is_multicast)}&fifo_size=4096'
            '&overrun_nonfatal=1&timeout=5000000')


class Preview:
    def __init__(self, root):
        self.root = Path(root)
        self.lock, self.lifecycle = threading.RLock(), threading.Lock()
        self.process = self.worker = None
        self.frame = self.received = None
        self.sequence = 0

    def start(self, output):
        url = receiver_url(output)
        with self.lifecycle:
            if self.process and self.process.poll() is None: return
            self._stop()
            prefix = self.root/'target-root/usr/local'
            env = dict(os.environ)
            env['LD_LIBRARY_PATH'] = str(prefix/'lib') + ':' + env.get('LD_LIBRARY_PATH', '')
            command = [str(prefix/'bin/ffmpeg'), '-nostdin', '-v', 'error', '-threads', '1',
                       '-probesize', '2000000', '-analyzeduration', '2000000', '-i', url,
                       '-map', '0:v:0', '-an', '-sn', '-dn', '-filter_threads', '1',
                       '-vf', 'yadif=mode=send_frame:parity=auto:deint=interlaced,fps=1,scale=640:trunc(640/dar/2)*2:flags=fast_bilinear,setsar=1',
                       '-c:v', 'mjpeg', '-threads', '1', '-q:v', '5', '-f', 'image2pipe', 'pipe:1']
            process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            with self.lock:
                self.process, self.sequence = process, 0
            self.worker = threading.Thread(target=self._read, args=(process,), daemon=True)
            self.worker.start()

    def _read(self, process):
        pending = b''
        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk: break
                pending += chunk
                while pending:
                    start = pending.find(b'\xff\xd8')
                    if start < 0:
                        pending = pending[-1:]; break
                    pending = pending[start:]
                    end = pending.find(b'\xff\xd9', 2)
                    if end < 0:
                        if len(pending) > MAX_FRAME: pending = pending[-1:]
                        break
                    frame, pending = pending[:end+2], pending[end+2:]
                    if len(frame) > MAX_FRAME: continue
                    with self.lock:
                        if self.process is not process: return
                        self.frame, self.received = frame, time.monotonic()
                        self.sequence += 1
        finally:
            process.stdout.close()
            process.wait()
            with self.lock:
                if self.process is process:
                    self.frame = self.received = None

    def state(self):
        with self.lock:
            alive = self.process is not None and self.process.poll() is None
            age = None if self.received is None else max(0, time.monotonic()-self.received)
            status = ('idle' if self.process is None else 'error' if not alive else
                      'waiting' if age is None else 'stale' if age > MAX_AGE else 'live')
            return {'status': status, 'age_seconds': None if age is None else round(age, 2),
                    'frame_id': self.sequence if status == 'live' else None,
                    'pid': self.process.pid if alive else None}

    def jpeg(self):
        with self.lock:
            return self.frame if self.state()['status'] == 'live' else None

    def _stop(self):
        with self.lock:
            process, self.process = self.process, None
            self.frame = self.received = None
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=2)
        if self.worker:
            self.worker.join(timeout=2)
            self.worker = None

    def stop(self):
        with self.lifecycle: self._stop()
