#!/usr/bin/env python3
"""Contract tests for the web panel, using an isolated fake OBE executable."""
import http.client
import json
import os
from pathlib import Path
import re
import runpy
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'webpanel'))
FAKE = '''#!/usr/bin/env python3
import json, os, sys, threading, time
from pathlib import Path
def events():
    previous = None
    while True:
        marker = Path('event').read_text() if Path('event').exists() else ''
        if marker != previous:
            previous = marker
            if marker == 'reconnect': print('obecli> ....URL input: retrying in 1 seconds (attempt 1)', flush=True)
            if marker == 'recovered': print('obecli> ....URL input: reconnected; receiving video and stereo audio', flush=True)
            if marker == 'format-change': print('URL input: stopped (format changed)', flush=True)
        time.sleep(0.05)
threading.Thread(target=events, daemon=True).start()
print('Version 4.1.4 (panel-test)', flush=True)
frames = 0
is_srt = False
for line in sys.stdin:
    command = line.strip()
    if command.startswith('set input opts location='):
        is_srt = 'location=srt://' in command
    elif command == 'probe input':
        behavior = Path('behavior').read_text() if Path('behavior').exists() else ''
        if behavior == 'silent': continue
        if behavior == 'exit': sys.exit(3)
        print('Detected input streams:', flush=True)
        print('Input-stream-id: 0 - Video: RAW 1920x1080i 25/1fps SDR', flush=True)
        if behavior != 'video-only':
            print('Input-stream-id: 1 - Audio: PCM stereo 48kHz - SDI audio pair: 1', flush=True)
        print('Encoder outputs:', flush=True)
    elif command == 'start':
        if Path('behavior').exists() and Path('behavior').read_text() == 'no-start': continue
        print('Encoding started', flush=True)
        print('URL input: receiving video and stereo audio', flush=True)
        if os.getenv('OBE_SRT_PASSPHRASE'):
            print('error detail ' + os.environ['OBE_SRT_PASSPHRASE'], flush=True)
    elif command == 'show telemetry':
        if Path('behavior').exists() and Path('behavior').read_text() == 'no-telemetry': continue
        frames += 25
        print('obecli> ....OBE_TELEMETRY ' + json.dumps({
            'schema': 1, 'monotonic_ns': time.monotonic_ns(), 'video_frames': frames,
            'video_bytes': frames * 40000, 'output_bytes': frames * 50000,
            'output_datagrams': frames * 38, 'output_errors': 0, 'video_queue_frames': 2}), flush=True)
        if is_srt:
            print('OBE_SRT ' + json.dumps({'schema': 1, 'connection': 1, 'stats': {
                'sample_us': time.monotonic_ns() // 1000, 'rtt_us': 25000,
                'received_packets': frames * 10, 'received_bytes': frames * 13160,
                'lost_packets': 3, 'retransmitted_packets': 3, 'dropped_packets': 0,
                'buffer_bytes': 50000, 'buffer_ms': 100, 'buffer_available_bytes': 100000,
                'latency_ms': 200}}), flush=True)
    elif command == 'quit':
        Path('quit-received').write_text('yes')
        break
'''


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='obe-panel-test-')
        self.root = Path(self.temp.name)
        (self.root / 'run.sh').write_text(FAKE)
        (self.root / 'run.sh').chmod(0o700)
        self.server = None
        self.start_server()

    def start_server(self):
        self.server = subprocess.Popen(
            [sys.executable, '-u', str(ROOT/'webpanel/server.py'), '--root', str(self.root),
             '--state-dir', str(self.root/'state'), '--port', '0'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        import select
        self.assertTrue(select.select([self.server.stdout], [], [], 6)[0], 'Server failed to start')
        line = self.server.stdout.readline()
        match = re.search(r':(\d+)\s*$', line)
        self.assertIsNotNone(match, line)
        self.port = int(match[1])

    def stop_server(self):
        if self.server and self.server.poll() is None:
            self.server.terminate()
            self.server.communicate(timeout=12)

    def tearDown(self):
        self.stop_server()
        self.temp.cleanup()

    def request(self, method='GET', path='/api/state', payload=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=12)
        body = json.dumps(payload) if payload is not None else None
        request_headers = {'Content-Type': 'application/json'} if body is not None else {}
        request_headers.update(headers or {})
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            parsed = json.loads(data) if 'application/json' in response.getheader('Content-Type', '') else data
            return response.status, parsed
        finally:
            connection.close()

    def state(self):
        status, result = self.request()
        self.assertEqual(status, 200, result)
        return result

    def wait_phase(self, expected, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            state = self.state()
            if state['phase'] in expected:
                return state
            time.sleep(.05)
        self.fail(f'Expected {expected}, got {state}')

    def save(self, **changes):
        state = self.state()
        config = dict(state['config'], **changes)
        return self.request('PUT', '/api/config', {'revision': state['revision'], 'config': config})

    def start_encoder(self, **options):
        status, result = self.request('POST', '/api/start', dict(revision=self.state()['revision'], **options))
        self.assertEqual(status, 202, result)
        state = self.wait_phase({'running', 'error'})
        self.assertEqual(state['phase'], 'running', state)
        return state

    def crash(self, state):
        # Only the fake child belonging to this test's isolated server.
        os.kill(state['pid'], signal.SIGKILL)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            result = self.state()
            if (not result['process_alive'] and result['recovery']['last_exit']
                    and result['recovery']['last_exit']['pid'] == state['pid']):
                return result
            time.sleep(.02)
        self.fail('The fake child did not exit')

    def test_idle_and_revision_persistence(self):
        state = self.state()
        self.assertFalse(state['process_alive'])
        self.assertFalse(state['encoding_started'])
        self.assertIsNone(state['metrics']['cpu_percent'])
        status, saved = self.save(name='Collaudo locale')
        self.assertEqual(status, 200, saved)
        self.assertGreater(saved['revision'], state['revision'])
        self.assertEqual((self.root/'state/config.json').stat().st_mode & 0o777, 0o600)
        status, _ = self.request('PUT', '/api/config', {'revision': state['revision'], 'config': state['config']})
        self.assertEqual(status, 409)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.state()['config']['name'], 'Collaudo locale')
        self.assertEqual(self.state()['phase'], 'idle')

    def test_validation_rejects_injection_and_credentials(self):
        for value in ['udp://127.0.0.1:12000\nstart', 'udp://127.0.0.1:12000,evil=1',
                      'file:///etc/passwd', 'http://user:password@localhost/video',
                      'srt://localhost:12000?%70assphrase=dummy-test-key',
                      'srt://localhost:12000?pbkeylen=16', 'udp://127.0.0.1:70000']:
            with self.subTest(url=value):
                self.assertEqual(self.save(input_url=value)[0], 422)
        for changes in [{'video_bitrate_kbps': True}, {'mux_bitrate_kbps': 128},
                        {'threads': 0}, {'unknown_setting': 'value'}, {'preset': 'invalid'},
                        {'output_url': 'file:///tmp/output.ts'}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.save(**changes)[0], 422)

    def test_origin_host_and_static_paths(self):
        self.assertEqual(self.request(headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/stop', {}, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/stop', {}, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertIn(self.request('GET', '/../../run.sh')[0], (403, 404))
        self.assertIn(self.request('GET', '/%2e%2e/run.sh')[0], (403, 404))
        status, _ = self.request('POST', '/api/start', {'revision': 999})
        self.assertEqual(status, 409)

    def test_probe_does_not_start_encoding(self):
        status, result = self.request('POST', '/api/probe', {'revision': self.state()['revision']})
        self.assertEqual(status, 202, result)
        state = self.wait_phase({'idle', 'error'})
        self.assertEqual(state['phase'], 'idle', state)
        self.assertEqual(state['signal']['width'], 1920)
        self.assertFalse(state['process_alive'])
        self.assertFalse(state['encoding_started'])

    def test_start_reconnect_stop_and_busy_config(self):
        status, result = self.request('POST', '/api/start', {'revision': self.state()['revision']})
        self.assertEqual(status, 202, result)
        state = self.wait_phase({'running', 'error'})
        self.assertEqual(state['phase'], 'running', state)
        self.assertTrue(state['process_alive'])
        self.assertTrue(state['encoding_started'])
        self.assertEqual(self.save(name='Busy')[0], 409)
        self.assertEqual(self.request('POST', '/api/start', {'revision': state['revision']})[0], 409)
        (self.root/'event').write_text('reconnect')
        self.assertEqual(self.wait_phase({'reconnecting'})['signal_state'], 'reconnecting')
        (self.root/'event').write_text('recovered')
        self.wait_phase({'running'})
        self.assertIn(self.request('POST', '/api/stop', {})[0], (200, 202))
        state = self.wait_phase({'idle'})
        self.assertFalse(state['process_alive'])
        self.assertTrue((self.root/'quit-received').exists())
        self.assertIn(self.request('POST', '/api/stop', {})[0], (200, 202))

    def test_secrets_stay_out_of_config_and_events(self):
        self.assertEqual(self.save(input_url='srt://127.0.0.1:12000?mode=caller')[0], 200)
        key = 'ephemeral-panel-test-key'
        status, result = self.request('POST', '/api/start', {'revision': self.state()['revision'], 'passphrase': key})
        self.assertEqual(status, 202, result)
        state = self.wait_phase({'running', 'error'})
        self.assertEqual(state['phase'], 'running', state)
        time.sleep(.1)
        self.assertNotIn(key, json.dumps(self.state()))
        for path in (self.root/'state').rglob('*'):
            if path.is_file():
                self.assertNotIn(key.encode(), path.read_bytes())
        self.assertTrue(self.state()['encrypted'])

    def test_srt_history_export_and_persistence_without_browser_polling(self):
        self.assertEqual(self.save(name='=SUM(1,2)', input_url='srt://127.0.0.1:12000?mode=caller')[0], 200)
        key = 'test-history-ephemeral-key'
        self.request('POST', '/api/start', {'revision': self.state()['revision'], 'passphrase': key})
        self.wait_phase({'running'})
        before = self.request(path='/api/history')[1]['total_samples']
        time.sleep(3.2)  # No HTTP requests: the service must collect by itself.
        state = self.state()
        self.assertEqual(state['srt']['status'], 'live')
        self.assertEqual(state['srt']['rtt_ms'], 25)
        self.assertEqual(state['monitoring']['status'], 'live')
        self.assertFalse(state['monitoring']['active_alarms'])
        status, history = self.request(path='/api/history?limit=1800&format=json')
        self.assertEqual(status, 200)
        self.assertGreaterEqual(history['total_samples'], before + 2)
        self.assertTrue(any(s['srt']['status'] == 'live' for s in history['samples']))
        self.assertTrue(all('input_url' not in s['context'] and 'output_url' not in s['context'] for s in history['samples']))
        self.assertNotIn(key, json.dumps(history))
        csv_status, csv_body = self.request(path='/api/history?format=csv&limit=1800')
        self.assertEqual(csv_status, 200)
        self.assertIn(b"'=SUM(1,2)", csv_body)
        self.assertNotIn(key.encode(), csv_body)
        self.assertFalse(any('OBE_SRT ' in e['message'] for e in state['events']))
        self.request('POST', '/api/stop', {})
        self.stop_server()
        self.start_server()
        self.assertEqual(self.state()['phase'], 'idle')
        restored = self.request(path='/api/history?limit=1800')[1]
        self.assertGreaterEqual(restored['total_samples'], history['total_samples'])
        for path in (self.root/'state').rglob('*'):
            if path.is_file():
                self.assertNotIn(key.encode(), path.read_bytes())

    def test_history_validation_and_origin(self):
        for query in ('limit=0', 'limit=1801', 'limit=-1', 'limit=a', 'limit=1&limit=2',
                      'format=html', 'unknown=1', 'format=csv&limit=1&extra=2'):
            self.assertEqual(self.request(path='/api/history?' + query)[0], 422)
        self.assertEqual(self.request(path='/api/history', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request(path='/api/history', headers={'Origin': 'http://evil.example'})[0], 403)

    def test_child_exit_is_error_not_success(self):
        (self.root/'behavior').write_text('exit')
        status, _ = self.request('POST', '/api/start', {'revision': self.state()['revision']})
        self.assertEqual(status, 202)
        state = self.wait_phase({'error'})
        self.assertFalse(state['encoding_started'])
        self.assertFalse(state['process_alive'])

    def test_missing_start_confirmation_is_an_error(self):
        (self.root/'behavior').write_text('no-start')
        status, _ = self.request('POST', '/api/start', {'revision': self.state()['revision'], 'auto_restart': True})
        self.assertEqual(status, 202)
        state = self.wait_phase({'error'}, seconds=24)
        self.assertFalse(state['encoding_started'])
        self.assertFalse(state['process_alive'])
        self.assertFalse(state['recovery']['active'])
        self.assertEqual(state['recovery']['attempts'], 0)
        self.assertEqual(state['recovery']['last_exit']['reason'], 'start_timeout')

    def test_stop_during_probe_and_server_shutdown(self):
        (self.root/'behavior').write_text('silent')
        self.assertEqual(self.request('POST', '/api/start', {'revision': self.state()['revision']})[0], 202)
        time.sleep(.1)
        self.request('POST', '/api/stop', {})
        self.wait_phase({'idle'})
        (self.root/'behavior').write_text('video-only')
        self.assertEqual(self.request('POST', '/api/start', {'revision': self.state()['revision']})[0], 202)
        state = self.wait_phase({'running', 'error'})
        self.assertEqual(state['phase'], 'running', state)
        pid = state['pid']
        self.assertIsNone(state['signal']['audio'])
        self.stop_server()
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_pending_start_cannot_spawn_after_panel_close(self):
        module = runpy.run_path(str(ROOT / 'webpanel/server.py'))
        panel = module['Panel'](self.root, self.root / 'closing-state')
        panel.close()
        # A request already accepted by the HTTP server can reach action after close.
        with mock.patch('subprocess.Popen') as spawn:
            for action in ('probe', 'start'):
                with self.subTest(action=action):
                    with self.assertRaises(module['APIError']) as raised:
                        panel.action(action, {'revision': panel.revision})
                    self.assertEqual(raised.exception.status, 503)
            spawn.assert_not_called()

    def test_live_telemetry_and_older_encoder(self):
        self.request('POST', '/api/start', {'revision': self.state()['revision']})
        self.wait_phase({'running'})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            telemetry = self.state()['telemetry']
            if telemetry['video_fps'] is not None:
                break
            time.sleep(.05)
        self.assertEqual(telemetry['status'], 'live')
        self.assertGreater(telemetry['video_bitrate_kbps'], 0)
        self.assertGreater(telemetry['output_bitrate_kbps'], 0)
        self.assertEqual(telemetry['video_queue_frames'], 2)
        self.assertFalse(any('OBE_TELEMETRY' in event['message'] for event in self.state()['events']))
        self.request('POST', '/api/stop', {})
        self.assertEqual(self.state()['telemetry']['status'], 'idle')
        self.assertIsNone(self.state()['telemetry']['video_frames'])
        (self.root/'behavior').write_text('no-telemetry')
        self.request('POST', '/api/start', {'revision': self.state()['revision']})
        self.wait_phase({'running'})
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            state = self.state()
            if state['telemetry']['status'] == 'unavailable':
                break
            time.sleep(.05)
        self.assertEqual(state['telemetry']['status'], 'unavailable')
        self.assertTrue(state['encoding_started'])
        self.assertIsNone(state['telemetry']['output_bitrate_kbps'])

    def test_crash_recovery_is_disabled_by_default(self):
        running = self.start_encoder()
        crashed = self.crash(running)
        self.assertEqual(crashed['phase'], 'error')
        recovery = crashed['recovery']
        self.assertFalse(recovery['enabled'])
        self.assertFalse(recovery['active'])
        self.assertEqual(recovery['attempts'], 0)
        exit_info = recovery['last_exit']
        self.assertEqual((exit_info['pid'], exit_info['returncode'], exit_info['signal']),
                         (running['pid'], -9, 'SIGKILL'))
        self.assertEqual(exit_info['reason'], 'process_signal')
        self.assertFalse(exit_info['expected'])
        time.sleep(2.2)
        self.assertEqual(self.state()['phase'], 'error')
        self.assertFalse(self.state()['process_alive'])

    def test_recovery_budget_config_and_ephemeral_key(self):
        self.assertEqual(self.save(input_url='srt://127.0.0.1:12000?mode=caller', name='Recovery test')[0], 200)
        key = 'ephemeral-recovery-test-key'
        running = self.start_encoder(auto_restart=True, passphrase=key)
        original = running['config']
        original_revision = running['revision']
        (self.root/'event').write_text('reconnect')
        reconnecting = self.wait_phase({'reconnecting'})
        self.assertEqual(reconnecting['pid'], running['pid'])
        self.assertEqual(reconnecting['recovery']['attempts'], 0)
        (self.root/'event').write_text('recovered')
        self.wait_phase({'running'})
        pids = set()
        for attempt, delay in enumerate((2, 4, 8, None)):
            pids.add(running['pid'])
            self.assertEqual(running['recovery']['attempts'], attempt)
            self.assertEqual(running['config'], original)
            self.assertEqual(running['revision'], original_revision)
            self.assertTrue(running['encrypted'])
            # Each new fake child confirms it received the key; only redaction reaches the API.
            time.sleep(.1)
            state = self.state()
            self.assertEqual(sum('[chiave rimossa]' in e['message'] for e in state['events']), attempt + 1)
            self.assertNotIn(key, json.dumps(state))
            started = time.monotonic()
            crashed = self.crash(running)
            if delay is None:
                self.assertEqual(crashed['phase'], 'error')
                self.assertTrue(crashed['recovery']['exhausted'])
                self.assertFalse(crashed['recovery']['active'])
                self.assertIsNone(crashed['recovery']['next_retry_seconds'])
                break
            self.assertEqual(crashed['phase'], 'restart_wait')
            self.assertFalse(crashed['process_alive'])
            self.assertEqual(crashed['telemetry']['status'], 'idle')
            self.assertLessEqual(crashed['recovery']['next_retry_seconds'], delay)
            for action in ('probe', 'start'):
                self.assertEqual(self.request('POST', '/api/'+action, {'revision': original_revision})[0], 409)
            self.assertEqual(self.save(name='Blocked during recovery')[0], 409)
            running = self.wait_phase({'running', 'error'}, seconds=delay+4)
            self.assertEqual(running['phase'], 'running')
            self.assertGreaterEqual(time.monotonic()-started, delay-.1)
        self.assertEqual(len(pids), 4)
        self.assertEqual([e['restart_attempt'] for e in crashed['recovery']['history']], [0, 1, 2, 3])
        self.assertNotIn(key, (self.root/'state/config.json').read_text())
        # A manual start resets the budget and does not inherit the old key/policy.
        fresh = self.start_encoder()
        self.assertEqual(fresh['recovery']['attempts'], 0)
        self.assertFalse(fresh['recovery']['enabled'])
        self.assertFalse(fresh['encrypted'])

    def test_stop_cancels_backoff_before_new_session(self):
        self.assertEqual(self.crash(self.start_encoder(auto_restart=True))['phase'], 'restart_wait')
        self.request('POST', '/api/stop', {})
        stopped = self.state()
        self.assertEqual(stopped['phase'], 'idle')
        self.assertFalse(stopped['recovery']['active'])
        self.assertIsNone(stopped['recovery']['next_retry_seconds'])
        fresh = self.start_encoder()
        time.sleep(2.2)
        self.assertEqual(self.state()['pid'], fresh['pid'])
        self.assertEqual(self.state()['recovery']['attempts'], 0)
        self.request('POST', '/api/stop', {})
        exit_info = self.state()['recovery']['last_exit']
        self.assertEqual(exit_info['reason'], 'operator_stop')
        self.assertTrue(exit_info['expected'])
        self.assertEqual(exit_info['returncode'], 0)

    def test_shutdown_during_backoff_never_autostarts(self):
        self.assertEqual(self.crash(self.start_encoder(auto_restart=True))['phase'], 'restart_wait')
        self.stop_server()
        self.start_server()
        time.sleep(2.2)
        state = self.state()
        self.assertEqual(state['phase'], 'idle')
        self.assertFalse(state['process_alive'])
        self.assertFalse(state['recovery']['enabled'])
        self.assertEqual(state['recovery']['history'], [])

    def test_recovery_validation_and_initial_failure(self):
        revision = self.state()['revision']
        for value in (1, 'true', None, [], {}):
            self.assertEqual(self.request('POST', '/api/start', {'revision': revision, 'auto_restart': value})[0], 422)
        self.assertEqual(self.request('POST', '/api/probe', {'revision': revision, 'auto_restart': True})[0], 422)
        (self.root/'behavior').write_text('exit')
        self.assertEqual(self.request('POST', '/api/start', {'revision': revision, 'auto_restart': True})[0], 202)
        state = self.wait_phase({'error'})
        self.assertFalse(state['recovery']['active'])
        self.assertEqual(state['recovery']['attempts'], 0)
        self.assertEqual(state['recovery']['last_exit']['returncode'], 3)
        self.assertEqual(state['recovery']['last_exit']['reason'], 'process_exit')

    def test_format_change_disarms_recovery(self):
        running = self.start_encoder(auto_restart=True)
        (self.root/'event').write_text('format-change')
        state = self.wait_phase({'error'})
        self.assertFalse(state['recovery']['active'])
        self.crash(running)
        self.assertEqual(self.state()['recovery']['last_exit']['reason'], 'unsupported_input')
        self.assertIsNone(self.state()['recovery']['next_retry_seconds'])

    def test_bounded_diagnostics_and_failed_spawn(self):
        revision = self.state()['revision']
        for _ in range(11):
            self.assertEqual(self.request('POST', '/api/probe', {'revision': revision})[0], 202)
            self.wait_phase({'idle'})
        history = self.state()['recovery']['history']
        self.assertEqual(len(history), 10)
        self.assertTrue(all(e['action'] == 'probe' and e['reason'] == 'probe_complete'
                            and e['expected'] and e['returncode'] == 0 for e in history))
        (self.root/'run.sh').chmod(0o600)
        self.assertEqual(self.request('POST', '/api/probe', {'revision': revision})[0], 500)
        state = self.state()
        self.assertEqual(state['phase'], 'error')
        exit_info = state['recovery']['last_exit']
        self.assertEqual(exit_info['reason'], 'encoder_unavailable')
        self.assertEqual(exit_info['action'], 'probe')
        self.assertIsNone(exit_info['pid'])
        self.assertEqual(len(state['recovery']['history']), 10)


class RecoveryRaceTests(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(ROOT/'webpanel/server.py'))
        self.temp = tempfile.TemporaryDirectory(prefix='obe-recovery-test-')
        self.root = Path(self.temp.name)
        (self.root/'run.sh').write_text(FAKE)
        (self.root/'run.sh').chmod(0o700)
        self.panel = self.module['Panel'](self.root, self.root/'state')

    def tearDown(self):
        self.panel.close()
        self.temp.cleanup()

    def wait(self, phase, seconds=4):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            state = self.panel.state()
            if state['phase'] == phase:
                return state
            time.sleep(.01)
        self.fail(f'Expected {phase}, got {state}')

    def start(self):
        self.panel.action('start', {'revision': self.panel.revision, 'auto_restart': True})
        return self.wait('running')

    def test_stop_serializes_with_retry_spawn(self):
        running = self.start()
        os.kill(running['pid'], signal.SIGKILL)
        self.wait('restart_wait')
        session = self.panel.session
        spawning, release, stopping = threading.Event(), threading.Event(), threading.Event()
        launch = self.panel._launch

        def delayed_launch(*args):
            spawning.set()
            if not release.wait(3):
                raise AssertionError('Timed out releasing retry')
            return launch(*args)

        def stop():
            stopping.set()
            self.panel.stop()

        with mock.patch.object(self.panel, '_launch', side_effect=delayed_launch):
            retry = threading.Thread(target=self.panel._restart, args=(session, 0))
            retry.start()
            try:
                self.assertTrue(spawning.wait(2))
                stopper = threading.Thread(target=stop)
                stopper.start()
                self.assertTrue(stopping.wait(2))
            finally:
                release.set()
                retry.join(4)
            stopper.join(4)
            self.assertFalse(retry.is_alive())
            self.assertFalse(stopper.is_alive())
        self.assertEqual(self.panel.state()['phase'], 'idle')
        self.assertFalse(self.panel.state()['process_alive'])
        self.assertFalse(session.active)
        with mock.patch.object(self.panel, '_launch') as spawn:
            self.panel._restart(session, 0)
            spawn.assert_not_called()

    def test_late_completion_cannot_override_stop_or_next_run(self):
        for next_run in (False, True):
            with self.subTest(next_run=next_run):
                self.start()
                old = self.panel.run
                terminated, release = threading.Event(), threading.Event()
                terminate = self.panel._terminate

                def delayed_terminate(run):
                    terminate(run)
                    terminated.set()
                    release.wait(3)

                with mock.patch.object(self.panel, '_terminate', side_effect=delayed_terminate):
                    completion = threading.Thread(target=self.panel._fail, args=(old, 'Old failure', 'probe_timeout'))
                    completion.start()
                    try:
                        self.assertTrue(terminated.wait(2))
                        self.assertTrue(old.exited.is_set())
                        self.panel.stop()
                        if next_run:
                            fresh = self.start()
                    finally:
                        release.set()
                        completion.join(4)
                    self.assertFalse(completion.is_alive())
                state = self.panel.state()
                self.assertEqual(state['phase'], 'running' if next_run else 'idle')
                if next_run:
                    self.panel._line(old, 'URL input: stopped (old process)')
                    self.panel._fail(old, 'Late failure')
                    self.assertEqual(self.panel.state()['pid'], fresh['pid'])
                    self.assertEqual(self.panel.state()['phase'], 'running')
                self.panel.stop()

    def test_retry_probe_exit_and_spawn_failure_are_bounded(self):
        running = self.start()
        (self.root/'behavior').write_text('exit')
        os.kill(running['pid'], signal.SIGKILL)
        self.wait('restart_wait')
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            retry = self.panel.state()
            if len(retry['recovery']['history']) == 2:
                break
            time.sleep(.01)
        self.assertEqual(retry['phase'], 'restart_wait')
        self.assertEqual(retry['recovery']['attempts'], 1)
        # Remaining retries cannot spawn after the launcher loses execute permission.
        (self.root/'run.sh').chmod(0o600)
        state = self.wait('error', seconds=14)
        self.assertTrue(state['recovery']['exhausted'])
        self.assertEqual(state['recovery']['attempts'], 3)
        self.assertEqual([e['reason'] for e in state['recovery']['history']],
                         ['process_signal', 'process_exit', 'encoder_unavailable', 'encoder_unavailable'])
        self.assertEqual(self.panel.session.secret, '')

    def test_retry_probe_timeout_is_terminal_and_clears_key(self):
        self.panel.config['input_url'] = 'srt://127.0.0.1:12000?mode=caller'
        self.panel.action('start', {'revision': self.panel.revision, 'auto_restart': True,
                                    'passphrase': 'ephemeral-timeout-test-key'})
        running = self.wait('running')
        (self.root/'behavior').write_text('silent')
        os.kill(running['pid'], signal.SIGKILL)
        self.wait('restart_wait')
        # Exercise the timeout path without waiting 20 seconds in a second test.
        with mock.patch.object(self.panel, '_await', return_value=False):
            state = self.wait('error', seconds=4)
        self.assertEqual(state['recovery']['last_exit']['reason'], 'probe_timeout')
        self.assertFalse(state['recovery']['active'])
        self.assertFalse(state['recovery']['exhausted'])
        self.assertEqual(state['recovery']['attempts'], 1)
        self.assertEqual(self.panel.session.secret, '')
        self.assertEqual(self.panel.run.secret, '')


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(ROOT/'webpanel/server.py'))
        self.temp = tempfile.TemporaryDirectory(prefix='obe-telemetry-test-')
        self.panel = self.module['Panel'](ROOT, self.temp.name)
        self.run = self.module['Run'](mock.Mock(), '', 'start')
        self.run.process.poll.return_value = None
        self.panel.run = self.run
        self.run.started.set()
        self.sample = {'schema': 1, 'monotonic_ns': 10_000_000_000,
                       'video_frames': 100, 'video_bytes': 100_000, 'output_bytes': 150_000,
                       'output_datagrams': 120, 'output_errors': 0, 'video_queue_frames': 3}

    def tearDown(self):
        self.temp.cleanup()

    def feed(self, **changes):
        sample = dict(self.sample, **changes)
        self.panel._line(self.run, 'obecli> ....OBE_TELEMETRY ' + json.dumps(sample))

    def test_rates_use_encoder_clock_and_stale_values_are_hidden(self):
        self.feed()
        self.assertIsNone(self.panel.state()['telemetry']['video_fps'])
        self.feed(monotonic_ns=12_000_000_000, video_frames=150, video_bytes=120_000,
                  output_bytes=1_150_000, output_datagrams=880, video_queue_frames=1)
        telemetry = self.panel.state()['telemetry']
        self.assertEqual((telemetry['video_fps'], telemetry['video_bitrate_kbps'], telemetry['output_bitrate_kbps']), (25, 80, 4000))
        self.assertEqual(telemetry['interval_seconds'], 2)
        self.assertEqual(self.panel.state()['telemetry']['video_fps'], 25, 'HTTP reads must not alter the sample')
        self.run.telemetry_received -= 6
        telemetry = self.panel.state()['telemetry']
        self.assertEqual(telemetry['status'], 'stale')
        self.assertIsNone(telemetry['video_fps'])
        self.assertIsNone(telemetry['video_frames'])

    def test_invalid_samples_cannot_replace_good_data_or_flood_events(self):
        self.feed()
        for changes in ({'schema': True}, {'schema': 2}, {'video_frames': -1},
                        {'video_frames': 2**64}, {'monotonic_ns': 1}, {'video_frames': None},
                        {'output_errors': float('nan')}, {'monotonic_ns': 11_000_000_000, 'video_bytes': 1}):
            self.feed(**changes)
        self.panel._line(self.run, 'OBE_TELEMETRY {"schema":1,"schema":1}')
        self.assertEqual(self.run.telemetry_sample, self.sample)
        warnings = [event for event in self.panel.events if event['level'] == 'warning']
        self.assertEqual(len(warnings), 1)

    def test_unsupported_video_and_previous_process_samples(self):
        self.feed(video_frames=None, video_bytes=None, video_queue_frames=None)
        telemetry = self.panel.state()['telemetry']
        self.assertEqual(telemetry['status'], 'live')
        self.assertIsNone(telemetry['video_frames'])
        self.assertEqual(telemetry['output_bytes'], 150_000)
        other = self.module['Run'](mock.Mock(), '', 'start')
        other.process.poll.return_value = None
        self.panel.run = other
        self.feed(monotonic_ns=12_000_000_000)
        self.assertEqual(self.panel.state()['telemetry']['status'], 'waiting')
        self.assertIsNone(other.telemetry_sample)

    def test_srt_key_matching_schema_does_not_corrupt_machine_samples(self):
        self.run.secret = 'video_frames'
        self.feed()
        self.assertEqual(self.panel.state()['telemetry']['status'], 'live')
        self.assertEqual(self.panel.state()['telemetry']['video_frames'], 100)
        self.panel._line(self.run, 'error detail video_frames')
        self.assertNotIn('video_frames', json.dumps(list(self.panel.events)))

    def test_srt_machine_line_secret_collision_and_invalid_payload_not_logged(self):
        sample = runpy.run_path(str(ROOT / 'tests/webpanel-monitoring.py'))['srt_sample']
        self.panel.config['input_url'] = 'srt://127.0.0.1:12000'
        self.run.secret = 'received_packets'
        self.panel._line(self.run, 'OBE_SRT ' + json.dumps(sample()))
        self.assertEqual(self.panel.state()['srt']['status'], 'live')
        self.assertEqual(self.panel.state()['srt']['received_packets'], 100)
        for _ in range(3):
            self.panel._line(self.run, 'OBE_SRT ' + json.dumps(sample(received_packets='secret-value')))
        self.assertNotIn('secret-value', json.dumps(list(self.panel.events)))
        self.assertEqual(sum(e['level'] == 'warning' for e in self.panel.events), 1)


if __name__ == '__main__':
    unittest.main()
