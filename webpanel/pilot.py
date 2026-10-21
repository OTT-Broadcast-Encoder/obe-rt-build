#!/usr/bin/env python3
"""Read-only 1080i50 telemetry soak; never starts, stops or reconfigures an encoder."""
import argparse
import csv
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

COLUMNS = ('elapsed_seconds', 'phase', 'pid', 'cpu_percent', 'memory_mb', 'video_fps',
           'video_bitrate_kbps', 'output_bitrate_kbps', 'video_queue_frames',
           'video_frames', 'output_bytes', 'output_errors', 'age_seconds', 'issues')
LIMITS = {'max_cpu': 600, 'max_rss_mb': 2048, 'max_queue': 50,
          'min_fps': 22, 'max_fps': 28, 'mux_tolerance': .2}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def assess(state, initial, previous, limits):
    failures = []
    signal, metrics, telemetry = state.get('signal') or {}, state.get('metrics', {}), state.get('telemetry', {})
    if not state.get('process_alive') or state.get('phase') != 'running': failures.append('encoder_not_running')
    if state.get('signal_state') != 'receiving': failures.append('source_not_receiving')
    if state.get('pid') != initial.get('pid'): failures.append('process_changed')
    if state.get('revision') != initial.get('revision'): failures.append('profile_changed')
    try: fps = Fraction(signal.get('fps', '0'))
    except (ValueError, ZeroDivisionError): fps = 0
    if (signal.get('width'), signal.get('height'), signal.get('scan'), fps) != (1920, 1080, 'i', 25):
        failures.append('not_1080i50')
    audio = signal.get('audio') or ''
    if 'stereo' not in audio.lower() or '48khz' not in audio.lower(): failures.append('audio_not_stereo_48khz')
    if telemetry.get('status') != 'live': failures.append('telemetry_unavailable')
    for key, low, high, source in (
        ('cpu_percent', 0, limits['max_cpu'], metrics), ('memory_mb', 1, limits['max_rss_mb'], metrics),
        ('video_queue_frames', 0, limits['max_queue'], telemetry),
        ('video_fps', limits['min_fps'], limits['max_fps'], telemetry),
        ('output_errors', 0, 0, telemetry), ('age_seconds', 0, 5, telemetry),
        ('output_bitrate_kbps', initial['config']['mux_bitrate_kbps']*(1-limits['mux_tolerance']),
         initial['config']['mux_bitrate_kbps']*(1+limits['mux_tolerance']), telemetry)):
        value = source.get(key)
        if not finite(value): failures.append(key+'_missing')
        elif not low <= value <= high: failures.append(key+'_outside_limit')
    if previous:
        for key in ('video_frames', 'output_bytes'):
            before, after = previous.get('telemetry', {}).get(key), telemetry.get(key)
            if finite(before) and finite(after) and after <= before: failures.append(key+'_not_advancing')
    return failures


def fetch(endpoint, channel):
    req = Request(endpoint+f'/api/channels/{channel}/state', headers={'Accept': 'application/json'})
    with urlopen(req, timeout=5) as response:
        raw = response.read(1048577)
    if len(raw) > 1048576: raise ValueError('Oversized API response')
    return json.loads(raw)


def observe(endpoint, channel, seconds, interval, output, limits):
    initial = fetch(endpoint, channel)
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    root = Path(__file__).resolve().parent.parent
    revision = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip() or None
    status = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--', 'webpanel'], capture_output=True)
    dirty = bool(status.stdout) or status.returncode != 0
    report = {'schema': 1, 'status': 'running', 'scope': '1080i50 encoder telemetry only',
              'production_qualified': False, 'channel': channel, 'requested_seconds': seconds,
              'interval_seconds': interval, 'started_utc': datetime.now(timezone.utc).isoformat(),
              'revision': initial['revision'], 'versions': initial.get('version'),
              'observer_commit': revision, 'observer_dirty': dirty,
              'observer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'config': {k: v for k, v in initial['config'].items() if k not in ('input_url', 'output_url')},
              'limits': limits, 'samples': 0, 'failures': {}, 'first_failures': [], 'metrics': {},
              'remaining_checks': ['receiver decode', 'PCR/PTS and continuity', 'A/V sync', 'rollback to PAL']}
    started, previous, interrupted = time.monotonic(), None, False
    previous_at = None

    def save():
        temporary = output/'report.tmp'
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
        temporary.replace(output/'report.json')

    try:
        with (output/'samples.csv').open('x', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            while True:
                elapsed = time.monotonic()-started
                try:
                    state = fetch(endpoint, channel)
                    failures = assess(state, initial, previous if previous_at is not None and elapsed-previous_at >= 2 else None, limits)
                    previous = state
                    previous_at = elapsed
                    row = dict(state.get('metrics', {}), **state.get('telemetry', {}),
                               phase=state.get('phase'), pid=state.get('pid'))
                except (OSError, ValueError, KeyError, TypeError):
                    row, failures, previous = {}, ['api_unavailable'], None
                row = {key: row.get(key) for key in COLUMNS}
                row.update(elapsed_seconds=round(elapsed, 3), issues=';'.join(failures))
                writer.writerow(row); handle.flush()
                report['samples'] += 1
                report['elapsed_seconds'] = round(time.monotonic()-started, 3)
                for code in failures:
                    report['failures'][code] = report['failures'].get(code, 0)+1
                if failures and len(report['first_failures']) < 20:
                    report['first_failures'].append({'elapsed_seconds': round(elapsed, 3), 'issues': failures})
                for key in COLUMNS[3:-1]:
                    value = row[key]
                    if finite(value):
                        bucket = report['metrics'].setdefault(key, {'min': value, 'max': value, 'sum': 0, 'count': 0})
                        bucket.update(min=min(bucket['min'], value), max=max(bucket['max'], value),
                                      sum=bucket['sum']+value, count=bucket['count']+1)
                save()
                remaining = seconds-(time.monotonic()-started)
                if remaining <= 0: break
                time.sleep(min(interval, remaining))
    except KeyboardInterrupt:
        interrupted = True
    report['elapsed_seconds'] = round(time.monotonic()-started, 3)
    report['completed_duration'] = not interrupted and report['elapsed_seconds'] >= seconds
    report['status'] = ('interrupted' if interrupted else 'fail' if report['failures'] else
                        'short_pass' if seconds < 86400 else 'telemetry_pass')
    for bucket in report['metrics'].values():
        bucket['mean'] = round(bucket.pop('sum')/bucket['count'], 3)
    save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel', default='http://127.0.0.1:18089')
    parser.add_argument('--channel', default='main')
    parser.add_argument('--seconds', type=int, default=86400)
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--output', type=Path, required=True)
    for name in ('max_cpu', 'max_rss_mb', 'max_queue'):
        parser.add_argument('--'+name.replace('_', '-'), type=int, default=LIMITS[name])
    args = parser.parse_args()
    parts = urlsplit(args.panel)
    if (parts.scheme != 'http' or parts.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parts.username or parts.path.strip('/') or parts.query or parts.fragment):
        parser.error('Use the local panel or a loopback SSH tunnel URL, without credentials')
    if not re.fullmatch('main|channel-[2-4]', args.channel): parser.error('Invalid channel')
    if not 10 <= args.seconds <= 86400 or not 1 <= args.interval <= 60: parser.error('Use 10–86400 seconds, interval 1–60')
    limits = dict(LIMITS, **{key: getattr(args, key) for key in ('max_cpu', 'max_rss_mb', 'max_queue')})
    if min(limits[key] for key in ('max_cpu', 'max_rss_mb', 'max_queue')) <= 0: parser.error('Limits must be positive')
    try: report = observe(args.panel.rstrip('/'), args.channel, args.seconds, args.interval, args.output, limits)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, 'Cannot start observation: check the API and use a new output directory.\n')
    print(json.dumps({key: report[key] for key in ('status', 'elapsed_seconds', 'samples', 'failures', 'production_qualified')}))
    print(f'Report: {args.output}/report.json')
    return 130 if report['status'] == 'interrupted' else 1 if report['status'] == 'fail' else 0


if __name__ == '__main__': raise SystemExit(main())
