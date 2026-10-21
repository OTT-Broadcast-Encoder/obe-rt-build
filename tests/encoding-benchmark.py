#!/usr/bin/env python3
"""Measure real OBE over loopback HTTP/UDP on field-aligned 1080i frames.

Run in the existing builder image. Artifacts stay in tests/.build; the webpanel
service and its saved profile are never contacted or modified. No Python packages.
"""
import argparse
import csv
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'webpanel'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load('input_url', ROOT/'tests/input-url.py')
panel_api = load('panel_api', ROOT/'webpanel/server.py')
W, H, FPS, DURATION = 1920, 1080, 25, 30
FIRST, COUNT = 350, 200  # Identical 14–22 s window in every candidate.
MARKER_W, MARKER_H = 384, 32
FIXTURE_SPEC = 'testsrc2-1080i50-tff-noise6-seed42-from18s-field-id12-v1'
PRESETS = ('veryfast', 'faster', 'medium')
BITRATES = (4000, 6000, 8000, 10000)


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def run(args, timeout=150):
    result = subprocess.run(args, cwd=ROOT, env=base.ENV, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{args[0]} failed: {result.stderr.decode(errors="replace")[-2500:]}')
    return result


def probe(path, *options):
    return json.loads(run(['ffprobe', '-v', 'error', *options, '-of', 'json', str(path)]).stdout)


def decode_marker(data):
    """Read a 12-bit field identity from each parity of the decoded luma strip."""
    if len(data) != MARKER_W * MARKER_H:
        raise ValueError('Truncated marker frame')
    fields = []
    for parity in (0, 1):
        identity = 0
        for bit in range(12):
            values = [data[y * MARKER_W + bit * 32 + x]
                      for y in range(6 + parity, 26, 2) for x in range(10, 22)]
            mean = sum(values) / len(values)
            if 64 < mean < 192:
                raise ValueError('Ambiguous field marker')
            identity |= (mean >= 128) << bit
        fields.append(identity)
    return tuple(fields)


def identities(path):
    result = run(['ffmpeg', '-v', 'error', '-threads', '2', '-i', str(path),
                  '-an', '-vf', 'crop=384:32:64:32,format=gray', '-fps_mode', 'passthrough',
                  '-f', 'rawvideo', '-'])
    size = MARKER_W * MARKER_H
    if len(result.stdout) % size:
        raise ValueError('Incomplete marker decode')
    return [decode_marker(result.stdout[n:n+size]) for n in range(0, len(result.stdout), size)]


def aligned_window(ids, first=FIRST, count=COUNT):
    expected = [(2*n, 2*n+1) for n in range(first, first+count)]
    starts = [n for n, pair in enumerate(ids) if tuple(pair) == expected[0]]
    if len(starts) != 1:
        raise ValueError('Common window missing or duplicated')
    start = starts[0]
    if [tuple(pair) for pair in ids[start:start+count]] != expected:
        raise ValueError('Dropped, duplicated, swapped or out-of-order field in measurement window')
    return start


def fixture(directory):
    source = directory/'source.ts'
    manifest_path = directory/'source.json'
    if source.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest['spec'] != FIXTURE_SPEC or manifest['sha256'] != sha(source):
            raise ValueError('Existing fixture does not match its manifest')
        return source
    # Marker is added before weaving: consecutive 50 Hz instants become TFF fields.
    marker = "nullsrc=size=384x32:rate=50,geq=lum='16+219*bitand(floor(N/pow(2,floor(X/32))),1)':cb=128:cr=128"
    filters = ("[0:v]noise=alls=6:allf=t:all_seed=42:enable='gte(t,18)'[content];"
               "[content][1:v]overlay=64:32:shortest=1,interlace=scan=tff:lowpass=0,setsar=1/1,"
               "setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[v]")
    result = run(['ffmpeg', '-v', 'warning', '-filter_complex_threads', '1',
                  '-f', 'lavfi', '-i', f'testsrc2=size={W}x{H}:rate=50',
                  '-f', 'lavfi', '-i', marker, '-f', 'lavfi', '-i',
                  'sine=frequency=997:sample_rate=48000', '-filter_complex', filters,
                  '-map', '[v]', '-map', '2:a', '-t', str(DURATION), '-c:v', 'mpeg2video',
                  '-threads', '2', '-flags', '+ilme+ildct', '-field_order', 'tt', '-q:v', '2',
                  '-bf', '0', '-g', '12', '-pix_fmt', 'yuv420p', '-color_range', 'tv',
                  '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                  '-c:a', 'mp2', '-b:a', '192k', '-ac', '2', '-f', 'mpegts', str(source)], timeout=240)
    (directory/'fixture.log').write_bytes(result.stderr)
    ids = identities(source)
    aligned_window(ids)
    if len(ids) != DURATION*FPS or ids != [(n*2, n*2+1) for n in range(len(ids))]:
        raise ValueError('Source markers are not consecutive TFF fields')
    manifest = {'spec': FIXTURE_SPEC, 'sha256': sha(source), 'frames': len(ids),
                'reference': 'Decoded MPEG-2 contribution; not the pre-contribution generator',
                'window': {'first_frame_id': FIRST, 'frame_count': COUNT, 'fields': COUNT*2},
                'metric_roi': {'x': 0, 'y': 96, 'width': W, 'height': H-96},
                'streams': probe(source, '-show_streams')['streams']}
    write_json(manifest_path, manifest)
    return source


def quality(reference, output, ref_start, out_start, directory, degrade=False):
    """Both legs use the same crop and separatefields; marker area is excluded."""
    def leg(index, start, label):
        corruption = 'noise=alls=30:allf=t:all_seed=7,' if degrade and index == 1 else ''
        return (f'[{index}:v]trim=start_frame={start}:end_frame={start+COUNT},'
                f'setpts=PTS-STARTPTS,crop=iw:ih-96:0:96,{corruption}separatefields,'
                f'setpts=N/(50*TB),split=2[{label}p][{label}s]')
    # Filter filenames are fixed basenames; cwd is private to this measurement.
    graph = (leg(0, ref_start, 'r')+';'+leg(1, out_start, 'd')+';'
             '[dp][rp]psnr=stats_file=psnr.log[p];[ds][rs]ssim=stats_file=ssim.log[s]')
    result = subprocess.run(['ffmpeg', '-v', 'info', '-filter_complex_threads', '1',
                             '-threads', '2', '-i', str(reference.resolve()),
                             '-threads', '2', '-i', str(output.resolve()),
                             '-filter_complex', graph, '-map', '[p]', '-map', '[s]',
                             '-an', '-f', 'null', '-'], cwd=directory, env=base.ENV,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=150)
    (directory/'quality.log').write_bytes(result.stderr)
    if result.returncode:
        raise ValueError('Quality filter failed; see quality.log')
    log = result.stderr.decode(errors='replace')
    psnr = re.findall(r'PSNR .*?average:([0-9.]+|inf)', log)
    ssim = re.findall(r'SSIM .*?All:([0-9.]+)', log)
    if len(psnr) != 1 or len(ssim) != 1:
        raise ValueError('Missing PSNR/SSIM summary')
    for name in ('psnr.log', 'ssim.log'):
        if len((directory/name).read_text().splitlines()) != COUNT*2:
            raise ValueError('Metric did not compare the exact expected number of fields')
    return {'psnr_db': None if psnr[0] == 'inf' else float(psnr[0]), 'ssim': float(ssim[0]),
            'identical': psnr[0] == 'inf', 'fields': COUNT*2}


def metric_controls(source, directory):
    for name in ('identity-control', 'degraded-control'):
        (directory/name).mkdir(exist_ok=True)
    identical = quality(source, source, FIRST, FIRST, directory/'identity-control')
    degraded = quality(source, source, FIRST, FIRST, directory/'degraded-control', degrade=True)
    if not identical['identical'] or identical['ssim'] != 1 or degraded['ssim'] >= .99 or degraded['psnr_db'] is None:
        raise ValueError('Quality controls did not distinguish identity and degradation')
    write_json(directory/'metric-controls.json', {'identity': identical, 'degraded': degraded})


def transport_metrics(path):
    """Rate from packet distance/PCR, plus null packet ratio over that same span."""
    first = last = None
    nulls = 0
    pcr_pid = None
    with path.open('rb') as stream:
        index = 0
        while packet := stream.read(188):
            if len(packet) != 188 or packet[0] != 0x47:
                raise ValueError('Unaligned transport stream')
            pid = ((packet[1] & 31) << 8) | packet[2]
            if pid == 8191:
                nulls += 1
            if (packet[3] >> 4) & 2 and packet[4] >= 7 and packet[5] & 16:
                pcr = (((packet[6] << 25) | (packet[7] << 17) | (packet[8] << 9) |
                        (packet[9] << 1) | (packet[10] >> 7)) * 300 +
                       ((packet[10] & 1) << 8) + packet[11])
                if first is None:
                    pcr_pid, first = pid, (index, pcr, nulls)
                if pid == pcr_pid:
                    last = (index, pcr, nulls)
            index += 1
    if first is None or last[0] <= first[0]:
        raise ValueError('Insufficient PCR samples')
    seconds = ((last[1] - first[1]) % ((1 << 33)*300)) / 27e6
    if not 0 < seconds < DURATION+20:
        raise ValueError('Unexpected PCR discontinuity')
    packets = last[0] - first[0]
    return {'ts_bitrate_kbps': round(packets*188*8/seconds/1000, 3),
            'null_packet_percent': round((last[2]-first[2])/packets*100, 3),
            'pcr_measurement_seconds': round(seconds, 3)}


def validate_output(source, output, directory):
    ids = identities(output)
    start = aligned_window(ids)
    frames = probe(output, '-select_streams', 'v:0', '-show_frames', '-show_entries',
                   'frame=best_effort_timestamp_time,interlaced_frame,top_field_first')['frames']
    chosen = frames[start:start+COUNT]
    if len(chosen) != COUNT or not all(f['interlaced_frame'] and f['top_field_first'] for f in chosen):
        raise ValueError('Interlacing signalling differs from the verified TFF pixels')
    times = [float(frame['best_effort_timestamp_time']) for frame in chosen]
    if any(abs(b-a-1/FPS) > .0001 for a, b in zip(times, times[1:])):
        raise ValueError('Non-continuous frame PTS in quality window')
    streams = probe(output, '-show_streams')['streams']
    video = next(s for s in streams if s['codec_type'] == 'video')
    if (video['codec_name'], video['width'], video['height'], video['sample_aspect_ratio'], video['pix_fmt']) != ('h264', W, H, '1:1', 'yuv420p'):
        raise ValueError('Unexpected output format')
    if any(video.get(key) != 'bt709' for key in ('color_primaries', 'color_transfer', 'color_space')) or video.get('color_range') != 'tv':
        raise ValueError('Output color signalling changed')
    packets = probe(output, '-select_streams', 'v:0', '-show_packets', '-show_entries',
                    'packet=pts_time,size,flags')['packets']
    selected = [p for p in packets if times[0]-.0001 <= float(p['pts_time']) < times[-1]+1/FPS-.0001]
    if len(selected) != COUNT or any('C' in p['flags'] for p in selected):
        raise ValueError('Missing/corrupt encoded frame packets in measured window')
    elementary = run(['ffmpeg', '-v', 'error', '-i', str(output), '-map', '0:v:0',
                      '-frames:v', '1', '-c', 'copy', '-f', 'h264', '-']).stdout
    settings = re.search(rb'x264 - core [^\x00]{20,3000}', elementary)
    if not settings:
        raise ValueError('Effective initial x264 settings absent from SEI')
    (directory/'x264-settings.txt').write_text(settings[0].decode(errors='replace')+'\n')
    result = quality(source, output, FIRST, start, directory)
    result.update(transport_metrics(output))
    result.update({'video_bitrate_kbps': round(sum(int(p['size']) for p in selected)*8/(COUNT/FPS)/1000, 3),
                   'output_start_index': start, 'first_frame_id': FIRST, 'frame_count': COUNT,
                   'output_sha256': sha(output)})
    return result


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values)-1, math.ceil(len(values)*fraction)-1)]


def encode(source, directory, preset, bitrate):
    class SourceHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/source.ts':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp2t')
            self.send_header('Content-Length', str(source.stat().st_size))
            self.end_headers()
            try:
                with source.open('rb') as media:
                    while block := media.read(65536):
                        self.wfile.write(block)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Probe deliberately closes the first HTTP connection.

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(('127.0.0.1', 0), SourceHandler)
    serving = threading.Thread(target=http.serve_forever, daemon=True)
    serving.start()
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4*1024*1024)
    receiver.bind(('127.0.0.1', 0))
    receiver.settimeout(.2)
    output = directory/'output.ts'
    finished = threading.Event()
    capture_error = []

    def capture():
        try:
            with output.open('wb') as media:
                while not finished.is_set():
                    try:
                        media.write(receiver.recv(65536))
                    except socket.timeout:
                        pass
        except Exception as error:
            capture_error.append(str(error))

    thread = threading.Thread(target=capture)
    thread.start()
    panel = panel_api.Panel(ROOT, directory/'state')
    config = dict(panel_api.DEFAULT_CONFIG, name=f'{preset}-{bitrate}', preset=preset,
                  input_url=f'http://127.0.0.1:{http.server_port}/source.ts',
                  output_url=f'udp://127.0.0.1:{receiver.getsockname()[1]}',
                  video_bitrate_kbps=bitrate, mux_bitrate_kbps=bitrate+1500,
                  vbv_buffer_kbits=bitrate, threads=4, gop_frames=50, bframes=2, lookahead_frames=20)
    write_json(directory/'config.json', config)
    samples = []
    try:
        if hasattr(panel, 'start_monitoring'):
            panel.start_monitoring()
        state = panel.save_config({'revision': panel.revision, 'config': config})
        panel.action('start', {'revision': state['revision']})
        started = time.monotonic()
        while time.monotonic()-started < DURATION+10:
            state = panel.state()
            elapsed = time.monotonic()-started
            if state['phase'] == 'error':
                raise ValueError(state['message'])
            count = state['telemetry'].get('video_frames') or 0
            if FIRST-50 <= count < FIRST+COUNT+75:
                samples.append({'elapsed_seconds': round(elapsed, 3), 'phase': state['phase'],
                                'metrics': state['metrics'], 'telemetry': state['telemetry']})
            time.sleep(1)
    finally:
        panel.close()
        http.shutdown()
        http.server_close()
        serving.join(timeout=3)
        write_json(directory/'events.json', list(panel.events))
        write_json(directory/'samples.json', samples)
        finished.set()
        thread.join(timeout=3)
        receiver.close()
    if capture_error or thread.is_alive():
        raise ValueError('UDP capture failed')
    if len(samples) < 10 or any(s['telemetry']['status'] != 'live' or s['phase'] != 'running' for s in samples):
        raise ValueError('Missing live telemetry in steady measurement window')
    if any(s['telemetry']['output_errors'] for s in samples):
        raise ValueError('UDP send failure')
    cpu = [s['metrics']['cpu_percent'] for s in samples]
    memory = [s['metrics']['memory_mb'] for s in samples]
    fps = [s['telemetry']['video_fps'] for s in samples]
    queue = [s['telemetry']['video_queue_frames'] for s in samples]
    if any(value is None for value in cpu+memory+fps+queue):
        raise ValueError('Incomplete performance measurements')
    return {'cpu_median_percent': round(statistics.median(cpu), 1),
            'cpu_p95_percent': round(percentile(cpu, .95), 1), 'rss_max_mb': max(memory),
            'fps_median': round(statistics.median(fps), 2), 'queue_max_frames': max(queue),
            'realtime_candidate': statistics.median(fps) >= 24.5 and max(queue) <= 25 and percentile(cpu, .95) <= 480}


def summarize(directory):
    results = [json.loads(path.read_text()) for path in sorted(directory.glob('*/result.json'))]
    fields = ['case', 'preset', 'target_kbps', 'repeat', 'status', 'video_bitrate_kbps',
              'ts_bitrate_kbps', 'null_packet_percent', 'psnr_db', 'ssim', 'cpu_median_percent',
              'cpu_p95_percent', 'rss_max_mb', 'fps_median', 'queue_max_frames', 'realtime_candidate']
    with (directory/'results.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    lines = ['# OBE: confronto sintetico 1080i50', '',
             'Finestra comune: 200 frame / 400 campi, ID 350–549; PSNR/SSIM per campo, marker esclusi.',
             'CPU 100% = un core. Candidato real time: mediana ≥24,5 fps, coda ≤25, CPU p95 ≤480%.',
             'Soglie di laboratorio su 6 CPU; non certificazione della macchina finale o di 24 ore.', '',
             '| Caso | AVC kb/s reali | PSNR dB | SSIM | CPU mediana/p95 % | fps | Coda max | RT candidato |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for item in results:
        if item['status'] != 'PASS':
            lines.append(f'| {item["case"]} | INVALIDO | — | — | — | — | — | no |')
            continue
        lines.append(f'| {item["case"]} | {item["video_bitrate_kbps"]:.0f} | {item["psnr_db"]:.3f} | '
                     f'{item["ssim"]:.6f} | {item["cpu_median_percent"]}/{item["cpu_p95_percent"]} | '
                     f'{item["fps_median"]} | {item["queue_max_frames"]} | {"sì" if item["realtime_candidate"] else "no"} |')
    (directory/'report.md').write_text('\n'.join(lines)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--presets', nargs='+', choices=PRESETS, default=list(PRESETS))
    parser.add_argument('--bitrates', nargs='+', type=int, choices=BITRATES, default=list(BITRATES))
    parser.add_argument('--repeats', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--fixture-only', action='store_true')
    args = parser.parse_args()
    parent = ROOT/'tests/.build'
    parent.mkdir(exist_ok=True)
    directory = (args.directory or Path(tempfile.mkdtemp(prefix='encoding-benchmark-', dir=parent))).resolve()
    if not directory.is_relative_to(parent.resolve()):
        parser.error('Artifacts must stay under tests/.build')
    directory.mkdir(exist_ok=True)
    print(f'Artifacts: {directory}', flush=True)
    source = fixture(directory)
    cpu_info = Path('/proc/cpuinfo').read_text()
    cpu_model = re.search(r'model name\s*:\s*(.*)', cpu_info)[1]
    libraries = {str(path.relative_to(ROOT)): sha(path)
                 for base_dir in (ROOT/'target-root', ROOT/'NDI')
                 for path in sorted(base_dir.rglob('*.so*')) if path.is_file()}
    identity = {'source_sha256': sha(source), 'encoder_sha256': sha(ROOT/'obecli'),
                'runner_sha256': sha(__file__), 'panel_sha256': sha(ROOT/'webpanel/server.py'),
                'input_helper_sha256': sha(ROOT/'tests/input-url.py'),
                'launcher_sha256': sha(ROOT/'run.sh'),
                'ffmpeg_sha256': sha(ROOT/'target-root/usr/local/bin/ffmpeg'),
                'ffprobe_sha256': sha(ROOT/'target-root/usr/local/bin/ffprobe'),
                'libraries_sha256': libraries, 'cpu_model': cpu_model,
                'cpu_max': Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
                'memory_max': Path('/sys/fs/cgroup/memory.max').read_text().strip(),
                'fixture_spec': FIXTURE_SPEC}
    monitoring = ROOT/'webpanel/monitoring.py'
    if monitoring.exists():
        identity['monitoring_sha256'] = sha(monitoring)
    manifest_path = directory/'environment.json'
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        if existing['identity'] != identity:
            raise ValueError('Source, encoder or runner changed: use a new artifact directory')
    else:
        write_json(manifest_path, {'identity': identity, 'cpu_model': cpu_model,
                                  'created_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                  'ffmpeg': run(['ffmpeg', '-version']).stdout.decode().splitlines()[0],
                                  'runtime': json.loads((ROOT/'runtime.json').read_text()) if (ROOT/'runtime.json').exists() else None,
                                  'speedcontrol': 'OBE generic: enabled, adaptive; SEI records initial settings only'})
    if not (directory/'metric-controls.json').exists():
        metric_controls(source, directory)
    if args.fixture_only:
        return
    failures = 0
    for repeat in range(1, args.repeats+1):
        for bitrate in args.bitrates:
            for preset in args.presets:
                name = f'{preset}-{bitrate}-r{repeat}'
                case = directory/name
                case.mkdir(exist_ok=True)
                result_path = case/'result.json'
                if result_path.exists():
                    if args.resume and json.loads(result_path.read_text())['status'] == 'PASS':
                        print(f'SKIP {name}', flush=True)
                        continue
                    raise ValueError(f'{name} already exists; use --resume for completed cases')
                print(f'RUN {name}', flush=True)
                result = {'case': name, 'preset': preset, 'target_kbps': bitrate, 'repeat': repeat}
                try:
                    result.update(encode(source, case, preset, bitrate))
                    result.update(validate_output(source, case/'output.ts', case))
                    result['status'] = 'PASS'
                    print(f'PASS {name}: {result["psnr_db"]:.3f} dB / SSIM {result["ssim"]:.6f}; '
                          f'{result["video_bitrate_kbps"]:.0f} kb/s; CPU p95 {result["cpu_p95_percent"]}%', flush=True)
                except (ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                    result.update(status='INVALID', error=str(error))
                    failures += 1
                    print(f'INVALID {name}: {error}', flush=True)
                write_json(result_path, result)
                summarize(directory)
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
