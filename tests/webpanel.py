#!/usr/bin/env python3
"""Contract tests for the web panel, using an isolated fake OBE executable."""
import http.client
import json
import os
from pathlib import Path
from urllib.parse import quote, urlencode
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
for field, variable in [('expected-source', 'OBE_INPUT_URL'), ('expected-passphrase', 'OBE_SRT_PASSPHRASE')]:
    if Path(field).exists() and os.environ.get(variable, '') != Path(field).read_text():
        sys.exit(7)
if os.environ.get('OBE_INPUT_URL'):
    print('Input debug ' + os.environ['OBE_INPUT_URL'], flush=True)
    if Path('echo-stream-token').exists():
        print('Stream token ' + os.environ['OBE_INPUT_URL'].rsplit('/', 1)[-1], flush=True)
frames = 0
is_srt = False
for line in sys.stdin:
    command = line.strip()
    if Path('forbid-cli-url').exists() and Path('forbid-cli-url').read_text() in command:
        sys.exit(8)
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

    def start_server(self, extra_args=()):
        self.server = subprocess.Popen(
            [sys.executable, '-u', str(ROOT/'webpanel/server.py'), '--root', str(self.root),
             '--state-dir', str(self.root/'state'), '--port', '0', *extra_args],
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

    def test_new_channels_default_to_local_multicast(self):
        self.assertEqual(self.state()['config']['output_url'], 'udp://239.255.0.1:13000')
        status, created = self.request('POST', '/api/channels', {'name': 'Multicast locale'})
        self.assertEqual(status, 201, created)
        self.assertEqual(created['state']['config']['output_url'], 'udp://239.255.0.1:13020')
        module = runpy.run_path(str(ROOT/'webpanel/server.py'))
        target = module['udp_output_target']
        self.assertEqual(target('udp://239.255.0.1:13000'), 'udp://239.255.0.1:13000?ttl=1')
        self.assertEqual(target('udp://239.255.0.1:13000?ttl=2'), 'udp://239.255.0.1:13000?ttl=2')
        self.assertEqual(target('udp://127.0.0.1:13000'), 'udp://127.0.0.1:13000')

    def test_validation_rejects_injection_and_credentials(self):
        for value in ['udp://127.0.0.1:12000\nstart', 'udp://127.0.0.1:12000,evil=1',
                      'file:///etc/passwd', 'http://user:password@localhost/video',
                      'srt://localhost:12000?%70assphrase=dummy-test-key',
                      'srt://localhost:12000?pbkeylen=16', 'udp://127.0.0.1:70000',
                      'udp://localhost:12000?localport=12000&localport=12020']:
            with self.subTest(url=value):
                self.assertEqual(self.save(input_url=value)[0], 422)
        for changes in [{'video_bitrate_kbps': True}, {'mux_bitrate_kbps': 128},
                        {'threads': 0}, {'unknown_setting': 'value'}, {'preset': 'invalid'},
                        {'output_url': 'file:///tmp/output.ts'}]:
            with self.subTest(changes=changes):
                self.assertEqual(self.save(**changes)[0], 422)

    def test_x264_settings_survive_save_reload_and_reject_invalid_combinations(self):
        advanced = {"x264_speedcontrol": "fixed", "motion_estimation": "umh",
                    "motion_range": 24, "subme": 7, "trellis": "2",
                    "psy": "1", "psy_rd": 0.8, "psy_trellis": 0.2,
                    "qcomp": 0.7, "deblock_alpha": 1, "deblock_beta": -1}
        status, saved = self.save(**advanced)
        self.assertEqual(status, 200, saved)
        for key, value in advanced.items():
            self.assertEqual(self.state()["config"][key], value)
        self.stop_server()
        self.start_server()
        for key, value in advanced.items():
            self.assertEqual(self.state()["config"][key], value)
        status, error = self.save(x264_speedcontrol="adaptive")
        self.assertEqual(status, 422, error)
        self.assertIn("motion_estimation", error["error"]["fields"])
        status, error = self.save(deblock_beta=None)
        self.assertEqual(status, 422, error)
        self.assertIn("deblock_beta", error["error"]["fields"])

    def test_rtmp_input_keeps_stream_url_out_of_cli_and_events(self):
        url = 'rtmp://stream.example:1935/live/fixture-private-stream-key'
        self.assertEqual(self.save(input_url=url)[0], 200)
        for invalid_url in ('rtmp://stream.example/live', 'rtmps://stream.example/live/stream',
                            'rtmp://user:pass@stream.example/live/stream',
                            'rtmp://stream.example/live/stream?token=fixture-token'):
            with self.subTest(url=invalid_url):
                self.assertEqual(self.save(input_url=invalid_url)[0], 422)
        (self.root/'expected-source').write_text(url)
        (self.root/'forbid-cli-url').write_text('fixture-private-stream-key')
        (self.root/'echo-stream-token').touch()
        running = self.start_encoder()
        events = json.dumps(running['events'])
        self.assertIn('Input debug', events)
        self.assertIn('Stream token', events)
        self.assertNotIn(url, events)
        self.assertNotIn('fixture-private-stream-key', events)

    def test_origin_host_and_static_paths(self):
        self.assertEqual(self.request(headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/stop', {}, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/stop', {}, {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertIn(self.request('GET', '/../../run.sh')[0], (403, 404))
        self.assertIn(self.request('GET', '/%2e%2e/run.sh')[0], (403, 404))
        status, _ = self.request('POST', '/api/start', {'revision': 999})
        self.assertEqual(status, 409)

    def test_explicit_lan_host_and_same_origin_writes(self):
        host = '192.168.1.10:18088'
        self.assertEqual(self.request(headers={'Host': host})[0], 403)
        self.stop_server()
        self.start_server(['--allow-host', '192.168.1.10'])
        status, state = self.request(headers={'Host': host})
        self.assertEqual(status, 200)
        payload = {'revision': state['revision'], 'config': state['config']}
        headers = {'Host': host, 'Origin': 'http://' + host, 'Sec-Fetch-Site': 'same-origin'}
        self.assertEqual(self.request('PUT', '/api/config', payload, headers)[0], 200)
        for changes in ({'Host': '192.168.1.11:18088'},
                        {'Origin': 'http://192.168.1.10:18089'},
                        {'Origin': 'http://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            with self.subTest(headers=changes):
                self.assertEqual(self.request('PUT', '/api/config', payload, dict(headers, **changes))[0], 403)
        self.assertEqual(self.request()[0], 200, 'Loopback must remain available')

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

    def test_saved_srt_credentials_survive_restart_stop_and_probe(self):
        self.assertEqual(self.save(input_url='srt://127.0.0.1:12000?mode=caller')[0], 200)
        state = self.state()
        key = 'saved-panel-passphrase'
        payload = {'revision': state['revision'], 'config': state['config'], 'credentials': {'passphrase': key}}
        status, saved = self.request('PUT', '/api/config', payload)
        self.assertEqual(status, 200)
        self.assertTrue(saved['credentials']['passphrase'])
        self.assertNotIn(key, json.dumps(saved))
        config_path = self.root/'state/config.json'
        self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root/'state').stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.request('PUT', '/api/config', payload)[0], 409)
        self.stop_server(); self.start_server()
        self.assertTrue(self.state()['credentials']['passphrase'])
        (self.root/'expected-passphrase').write_text(key)
        self.assertEqual(self.request('POST', '/api/probe', {'revision': self.state()['revision']})[0], 202)
        self.assertEqual(self.wait_phase({'idle', 'error'})['phase'], 'idle')
        for _ in range(2):
            self.assertTrue(self.start_encoder()['encrypted'])
            self.request('POST', '/api/stop', {})
            self.wait_phase({'idle'})
        self.assertTrue(self.state()['credentials']['passphrase'])
        self.assertNotIn(key, json.dumps(self.request(path='/api/history')[1]))
        state = self.state()
        self.assertEqual(self.request('PUT', '/api/config', {'revision': state['revision'], 'config': state['config'],
                         'credentials': {'passphrase': ''}})[0], 200)
        self.stop_server(); self.start_server()
        self.assertFalse(self.state()['credentials']['passphrase'])
        self.assertNotIn(key, config_path.read_text())
        (self.root/'expected-passphrase').write_text('')
        self.assertFalse(self.start_encoder()['encrypted'])

    def test_saved_source_auth_is_private_scoped_and_kept_on_normal_save(self):
        url = 'https://stream.example/live?format=ts'
        username, password, token = 'test-user', 'fake-p@ss:word/%+ with,space', 'fake-access-token'
        values = {'username': username, 'password': password, 'query': 'access=' + token}
        state = self.state()
        status, saved = self.request('PUT', '/api/config', {'revision': state['revision'],
                                  'config': dict(state['config'], input_url=url), 'credentials': values})
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved['config']['input_url'], url)
        self.assertTrue(all(saved['credentials'][key] for key in values))
        self.assertEqual(self.save(video_bitrate_kbps=7000)[0], 200)
        self.stop_server(); self.start_server()
        authenticated = 'https://' + quote(username, safe='') + ':' + quote(password, safe='') + '@stream.example/live?format=ts&' + urlencode({'access': token})
        (self.root/'expected-source').write_text(authenticated)
        self.start_encoder()
        outputs = json.dumps(self.state()) + json.dumps(self.request(path='/api/history')[1])
        for value in (password, quote(password, safe=''), token, authenticated):
            self.assertNotIn(value, outputs)
        self.request('POST', '/api/stop', {})
        self.wait_phase({'idle'})
        _, saved = self.save(input_url='https://stream.example:443/other')
        self.assertTrue(saved['credentials']['password'], 'Same origin retains credentials')
        _, saved = self.save(input_url='https://other.example/live')
        self.assertFalse(any(saved['credentials'].values()), 'Another origin must not receive old credentials')
        self.stop_server(); self.start_server()
        self.assertFalse(any(self.state()['credentials'].values()))

    def test_credentials_validation_and_channel_isolation(self):
        self.save(input_url='https://stream.example/live?format=ts')
        state = self.state()
        for values in [{'unknown': 'x'}, {'password': 'test-password'}, {'username': 'bad:name'},
                       {'passphrase': 'valid-srt-passphrase'}, {'query': 'format=secret'},
                       {'query': 'token=a&token=b'}, {'query': 'token=%0Astart'}, {'query': 'token=%invalid'},
                       {'password': True}, {'username': None}, {'query': ['token=x']}]:
            with self.subTest(fields=list(values)):
                self.assertEqual(self.request('PUT', '/api/config', {'revision': state['revision'],
                                 'config': state['config'], 'credentials': values})[0], 422)
                self.assertEqual(self.state()['revision'], state['revision'])
        values = {'username': 'test-user', 'password': 'channel-private-password'}
        self.assertEqual(self.request('PUT', '/api/config', {'revision': state['revision'],
                         'config': state['config'], 'credentials': values})[0], 200)
        status, created = self.request('POST', '/api/channels', {'name': 'Isolated credentials'})
        self.assertEqual(status, 201)
        self.assertFalse(any(created['state']['credentials'].values()))
        self.stop_server(); self.start_server()
        self.assertTrue(self.state()['credentials']['password'])
        other = self.request(path='/api/channels/'+created['id']+'/state')[1]
        self.assertFalse(any(other['credentials'].values()))

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

    def test_saved_recovery_preference_survives_reload_and_controls_start(self):
        self.assertFalse(self.state()['config']['auto_restart'])
        status, saved = self.save(auto_restart=True)
        self.assertEqual(status, 200, saved)
        self.stop_server()
        self.start_server()
        self.assertTrue(self.state()['config']['auto_restart'])
        _, other = self.request('POST', '/api/channels', {'name': 'Independent recovery'})
        self.assertFalse(other['state']['config']['auto_restart'])
        running = self.start_encoder()
        self.assertTrue(running['recovery']['enabled'])
        self.assertEqual(self.request('POST', '/api/stop', {})[0], 200)
        self.wait_phase({'idle'})
        overridden = self.start_encoder(auto_restart=False)
        self.assertFalse(overridden['recovery']['enabled'])
        self.assertTrue(overridden['config']['auto_restart'])

    def test_saved_recovery_preference_requires_boolean(self):
        original = self.state()
        for value in ('true', 'false', 0, 1, None, [], {}):
            with self.subTest(value=value):
                status, result = self.save(auto_restart=value)
                self.assertEqual(status, 422, result)
                self.assertIn('auto_restart', result['error']['fields'])
        self.assertEqual(self.state()['revision'], original['revision'])
        self.assertEqual(self.state()['config'], original['config'])

    def test_apply_updates_saved_recovery_preference(self):
        running = self.start_encoder()
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                status, saved = self.request('POST', '/api/apply',
                    self.apply_payload(running, auto_restart=enabled))
                self.assertEqual(status, 202, saved)
                running = self.wait_restarted(running['pid'])
                self.assertEqual(running['recovery']['enabled'], enabled)
                self.assertEqual(running['config']['auto_restart'], enabled)

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


    def test_independent_processes_and_persistence(self):
        status, created = self.request('POST', '/api/channels', {'name': 'Secondo canale'})
        self.assertEqual(status, 201, created)
        prefix = '/api/channels/' + created['id']
        self.assertEqual(self.request('GET', prefix+'/state')[1]['config']['name'], 'Secondo canale')
        self.request('POST', '/api/start', {'revision': self.state()['revision']})
        first = self.wait_phase({'running'})
        self.assertEqual(self.request('POST', prefix+'/start', {'revision': created['state']['revision']})[0], 202)
        deadline = time.monotonic()+4
        while time.monotonic() < deadline:
            second = self.request('GET', prefix+'/state')[1]
            if second['phase'] == 'running': break
            time.sleep(.05)
        self.assertEqual(second['phase'], 'running')
        self.assertNotEqual(first['pid'], second['pid'])
        self.request('POST', prefix+'/stop', {})
        self.assertTrue(self.state()['process_alive'])
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request('GET', prefix+'/state')[1]['config']['name'], 'Secondo canale')
        self.assertFalse(self.state()['process_alive'])
        self.assertFalse(self.request('GET', prefix+'/state')[1]['process_alive'])

    def test_preview_lifecycle_and_stale_image(self):
        self.assertEqual(self.request('POST', '/api/preview', {})[0], 409)
        self.assertEqual(self.request('GET', '/api/preview-frame')[0], 503)
        self.assertEqual(self.request('POST', '/api/preview', {'url': 'file:///tmp/x'})[0], 422)
        executable = self.root/'target-root/usr/local/bin/ffmpeg'
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/usr/bin/env python3\nimport os,time\nos.write(1,b'\\xff\\xd8test\\xff\\xd9')\ntime.sleep(30)\n")
        executable.chmod(0o700)
        self.request('POST', '/api/start', {'revision': self.state()['revision']})
        self.wait_phase({'running'})
        self.assertEqual(self.request('POST', '/api/preview', {})[0], 200)
        deadline = time.monotonic()+3
        while self.state()['preview']['status'] != 'live' and time.monotonic() < deadline: time.sleep(.05)
        preview = self.state()['preview']
        self.assertEqual(preview['status'], 'live')
        status, frame = self.request('GET', '/api/preview-frame')
        self.assertEqual((status, frame), (200, b'\xff\xd8test\xff\xd9'))
        time.sleep(3.1)
        self.assertEqual(self.state()['preview']['status'], 'stale')
        self.assertEqual(self.request('GET', '/api/preview-frame')[0], 503)
        self.request('POST', '/api/stop', {})
        self.assertEqual(self.state()['preview']['status'], 'idle')
        with self.assertRaises(ProcessLookupError): os.kill(preview['pid'], 0)

    def test_channel_ports_include_local_bindings_and_output_aliases(self):
        _, created = self.request('POST', '/api/channels', {'name': 'Port checks'})
        prefix = '/api/channels/'+created['id']
        self.request('POST', '/api/start', {'revision': self.state()['revision']})
        self.wait_phase({'running'})
        for changes, code in (
            ({'input_url': 'udp://127.0.0.1:12345?localport=12000'}, 'input_in_use'),
            ({'input_url': 'udp://127.0.0.1:12000?localrtpport=12345'}, 'input_in_use'),
            ({'input_url': 'rtp://127.0.0.1:12345?localrtcpport=12000'}, 'input_in_use'),
            ({'input_url': 'srt://127.0.0.1:12000?mode=listener'}, 'input_in_use'),
            ({'output_url': 'udp://239.255.0.1:13000?ttl=2'}, 'output_in_use')):
            state = self.request('GET', prefix+'/state')[1]
            config = dict(created['state']['config'], **changes)
            saved = self.request('PUT', prefix+'/config', {'revision': state['revision'], 'config': config})[1]
            status, result = self.request('POST', prefix+'/start', {'revision': saved['revision']})
            self.assertEqual(status, 409, result)
            self.assertEqual(result['error']['code'], code)

    def test_ephemeral_local_input_ports_can_start(self):
        for url in ('udp://127.0.0.1:12000?localport=0',
                    'rtp://127.0.0.1:12000?localrtpport=0'):
            with self.subTest(url=url):
                status, saved = self.save(input_url=url)
                self.assertEqual(status, 200, saved)
                self.assertEqual(self.state()['config']['input_url'], url)
                self.start_encoder()
                self.assertEqual(self.request('POST', '/api/stop', {})[0], 200)
                self.wait_phase({'idle'})

    def test_network_url_scheme_is_canonicalized(self):
        for url in ('UDP://127.0.0.1:12000', 'RTMP://example.net/live/stream'):
            with self.subTest(url=url):
                status, saved = self.save(input_url=url)
                self.assertEqual(status, 200, saved)
                self.assertEqual(saved['config']['input_url'], url.lower())

    def test_conflicts_limits_and_invalid_ids(self):
        status, created = self.request('POST', '/api/channels', {'name': 'Duplicato'})
        self.assertEqual(status, 201)
        prefix = '/api/channels/'+created['id']
        self.request('PUT', prefix+'/config', {'revision': created['state']['revision'], 'config': self.state()['config']})
        self.request('POST', '/api/start', {'revision': self.state()['revision']})
        self.wait_phase({'running'})
        revision = self.request('GET', prefix+'/state')[1]['revision']
        self.assertEqual(self.request('POST', prefix+'/start', {'revision': revision})[0], 409)
        for _ in range(2): self.assertEqual(self.request('POST', '/api/channels', {'name': 'Altro'})[0], 201)
        self.assertEqual(self.request('POST', '/api/channels', {'name': 'Quinto'})[0], 409)
        self.assertEqual(len(self.request('GET', '/api/channels')[1]['channels']), 4)
        self.assertEqual(self.request('GET', '/api/channels/../state')[0], 404)

    def test_channel_rename_preserves_running_process_and_profile(self):
        status, created = self.request('POST', '/api/channels', {'name': 'Prima'})
        self.assertEqual(status, 201)
        path = '/api/channels/' + created['id']
        config = dict(created['state']['config'], input_url='srt://127.0.0.1:12020')
        status, saved = self.request('PUT', path+'/config', {
            'revision': created['state']['revision'], 'config': config,
            'credentials': {'passphrase': 'rename-fixture-passphrase'}})
        self.assertEqual(status, 200, saved)
        profile_dir = self.root/'state/channels'/created['id']
        previous = (profile_dir/'previous.json').read_bytes()
        self.assertEqual(self.request('POST', path+'/start', {'revision': saved['revision']})[0], 202)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            running = self.request(path=path+'/state')[1]
            if running['phase'] == 'running':
                break
            time.sleep(.05)
        self.assertEqual(running['phase'], 'running', running)
        payload = {'name': '  Dopo  ', 'revision': running['revision'], 'instance_id': running['instance_id']}
        status, renamed = self.request('PATCH', path, payload)
        self.assertEqual(status, 200, renamed)
        self.assertEqual((renamed['config']['name'], renamed['phase'], renamed['pid']), ('Dopo', 'running', running['pid']))
        self.assertEqual(renamed['revision'], running['revision'] + 1)
        self.assertEqual(renamed['credentials'], saved['credentials'])
        self.assertEqual((profile_dir/'previous.json').read_bytes(), previous)
        self.assertEqual(json.loads((profile_dir/'config.json').read_text())['credentials']['passphrase'], 'rename-fixture-passphrase')
        channels = self.request(path='/api/channels')[1]['channels']
        self.assertEqual(next(item['name'] for item in channels if item['id'] == created['id']), 'Dopo')
        self.assertEqual(self.request('PATCH', path, dict(payload, revision=renamed['revision']))[1]['revision'], renamed['revision'])
        for body, code in ((dict(payload, revision=running['revision']), 'stale_revision'),
                           (dict(payload, revision=renamed['revision'], instance_id='old'), 'stale_channel')):
            status, result = self.request('PATCH', path, body)
            self.assertEqual((status, result['error']['code']), (409, code))
        for body in ({}, dict(payload, name=' '), dict(payload, name='x'*65), dict(payload, extra=True)):
            self.assertEqual(self.request('PATCH', path, body)[0], 422)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request(path=path+'/state')[1]['config']['name'], 'Dopo')

    def test_channel_history_is_isolated_and_survives_restart(self):
        self.assertEqual(self.save(name='Main history')[0], 200)
        _, created = self.request('POST', '/api/channels', {'name': 'Second history'})
        prefix = '/api/channels/' + created['id']
        self.start_encoder()
        self.assertEqual(self.request('POST', prefix+'/start', {'revision': created['state']['revision']})[0], 202)
        time.sleep(3.2)
        histories = {}
        for path, name in [('/api', 'Main history'), (prefix, 'Second history')]:
            status, history = self.request(path=path+'/history?limit=1800')
            self.assertEqual(status, 200)
            self.assertGreaterEqual(history['total_samples'], 2)
            self.assertEqual({sample['context']['name'] for sample in history['samples'] if sample['process_alive']}, {name})
            self.assertEqual(self.request(path=path+'/state')[1]['monitoring']['status'], 'live')
            csv_status, csv_data = self.request(path=path+'/history?format=csv')
            self.assertEqual(csv_status, 200)
            self.assertIn(name.encode(), csv_data)
            histories[path] = history['total_samples']
        self.request('POST', prefix+'/stop', {})
        self.assertTrue(self.state()['process_alive'])
        self.stop_server()
        self.start_server()
        for path, count in histories.items():
            self.assertGreaterEqual(self.request(path=path+'/history')[1]['total_samples'], count)
            self.assertFalse(self.request(path=path+'/state')[1]['process_alive'])
        revision = self.request(path=prefix+'/state')[1]['revision']
        self.request('POST', prefix+'/start', {'revision': revision})
        time.sleep(2.2)
        self.assertGreater(self.request(path=prefix+'/history')[1]['total_samples'], histories[prefix])

    def test_retry_reserves_channel_ports_and_stop_is_independent(self):
        _, created = self.request('POST', '/api/channels', {'name': 'Second retry'})
        prefix = '/api/channels/' + created['id']
        main = self.start_encoder(auto_restart=True)
        conflicting = dict(created['state']['config'], input_url=main['config']['input_url'])
        _, saved = self.request('PUT', prefix+'/config', {'revision': created['state']['revision'], 'config': conflicting})
        crashed = self.crash(main)
        self.assertEqual(crashed['phase'], 'restart_wait')
        status, result = self.request('POST', prefix+'/start', {'revision': saved['revision']})
        self.assertEqual((status, result['error']['code']), (409, 'input_in_use'))
        self.request('POST', prefix+'/stop', {})
        self.assertTrue(self.state()['recovery']['active'])
        self.request('POST', '/api/stop', {})
        self.assertFalse(self.state()['recovery']['active'])
        self.assertEqual(self.request('POST', prefix+'/start', {'revision': saved['revision']})[0], 202)

    def test_channel_delete_validation_and_request_guards(self):
        _, created = self.request('POST', '/api/channels', {'name': 'Delete guards'})
        path = '/api/channels/' + created['id']
        payload = {key: created['state'][key] for key in ('revision', 'instance_id')}
        for body in ({}, {'revision': payload['revision']}, dict(payload, extra=True),
                     dict(payload, revision=True), dict(payload, revision='2'), dict(payload, instance_id=None)):
            with self.subTest(body=body):
                self.assertEqual(self.request('DELETE', path, body)[0], 422)
        self.assertEqual(self.request('DELETE', path, [payload])[0], 400)
        for headers in ({'Host': 'evil.example'}, {'Origin': 'https://evil.example'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('DELETE', path, payload, headers)[0], 403)
        for target, body, code in (
                ('/api/channels/main', payload, 'main_channel_protected'),
                (path, dict(payload, revision=payload['revision'] - 1), 'stale_revision'),
                (path, dict(payload, instance_id='obsolete-instance'), 'stale_channel')):
            status, result = self.request('DELETE', target, body)
            self.assertEqual((status, result['error']['code']), (409, code))
        for target in ('/api/channels/channel-9', '/api/channels/../main', '/api/channels/channel-3'):
            self.assertEqual(self.request('DELETE', target, payload)[0], 404)
        self.assertEqual(self.request(path=path+'/state')[1]['instance_id'], payload['instance_id'])
        headers = {'Origin': f'http://127.0.0.1:{self.port}', 'Sec-Fetch-Site': 'same-origin'}
        self.assertEqual(self.request('DELETE', path, payload, headers)[0], 200)

    def test_channel_delete_restart_and_clean_slot_reuse(self):
        _, created = self.request('POST', '/api/channels', {'name': 'Deleted profile'})
        path = '/api/channels/' + created['id']
        config = dict(created['state']['config'], input_url='srt://127.0.0.1:12020')
        status, saved = self.request('PUT', path+'/config', {
            'revision': created['state']['revision'], 'config': config,
            'credentials': {'passphrase': 'delete-fixture-passphrase'}})
        self.assertEqual(status, 200)
        payload = {key: saved[key] for key in ('revision', 'instance_id')}
        directory = self.root/'state/channels'/created['id']
        self.assertTrue((directory/'config.json').is_file())
        self.assertTrue((directory/'monitoring.sqlite3').is_file())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            samples = self.request(path=path+'/history')[1]['total_samples']
            if samples:
                break
            time.sleep(.01)
        self.assertGreater(samples, 0)
        main = self.start_encoder()
        status, result = self.request('DELETE', path, payload)
        self.assertEqual(status, 200, result)
        self.assertEqual(set(result), {'deleted', 'limit', 'channels'})
        self.assertEqual((result['deleted'], result['limit']), (created['id'], 4))
        self.assertEqual([channel['id'] for channel in result['channels']], ['main'])
        self.assertEqual(self.state()['pid'], main['pid'])
        self.assertFalse(directory.exists())
        self.assertEqual(self.request(path=path+'/history')[0], 404)
        self.assertEqual(self.request('DELETE', path, payload)[0], 404)
        self.stop_server()
        self.start_server()
        self.assertEqual(self.request(path=path+'/state')[0], 404)
        status, replacement = self.request('POST', '/api/channels', {'name': 'Clean replacement'})
        self.assertEqual(status, 201)
        self.assertEqual(replacement['id'], created['id'])
        self.assertNotEqual(replacement['state']['instance_id'], saved['instance_id'])
        self.assertNotEqual(replacement['state']['revision'], saved['revision'])
        # Old browser tabs may still use the revision-only API after reuse.
        status, result = self.request('PUT', path+'/config', {
            'revision': saved['revision'], 'config': saved['config']})
        self.assertEqual((status, result['error']['code']), (409, 'stale_revision'))
        status, result = self.request('POST', path+'/start', {'revision': saved['revision']})
        self.assertEqual((status, result['error']['code']), (409, 'stale_revision'))
        self.assertFalse(any(replacement['state']['credentials'].values()))
        self.assertEqual(replacement['state']['config']['input_url'], created['state']['config']['input_url'])
        self.assertNotIn('delete-fixture-passphrase', (directory/'config.json').read_text())
        history = self.request(path=path+'/history')[1]
        self.assertNotIn('Deleted profile', json.dumps(history))
        stale = dict(payload, revision=replacement['state']['revision'])
        status, result = self.request('DELETE', path, stale)
        self.assertEqual((status, result['error']['code']), (409, 'stale_channel'))
        self.assertEqual(self.request(path=path+'/state')[1]['config']['name'], 'Clean replacement')

    def test_channel_delete_rejects_probe_running_and_recovery(self):
        _, created = self.request('POST', '/api/channels', {'name': 'Busy delete'})
        path = '/api/channels/' + created['id']
        payload = {key: created['state'][key] for key in ('revision', 'instance_id')}
        (self.root/'behavior').write_text('silent')
        self.assertEqual(self.request('POST', path+'/probe', {'revision': payload['revision']})[0], 202)
        status, result = self.request('DELETE', path, payload)
        self.assertEqual((status, result['error']['code']), (409, 'encoder_busy'))
        self.assertEqual(self.request('POST', path+'/stop', {})[0], 200)
        (self.root/'behavior').write_text('')
        self.assertEqual(self.request('POST', path+'/start', {'revision': payload['revision'], 'auto_restart': True})[0], 202)
        for phase in ('running', 'restart_wait'):
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                state = self.request(path=path+'/state')[1]
                if state['phase'] == phase:
                    break
                time.sleep(.01)
            self.assertEqual(state['phase'], phase)
            status, result = self.request('DELETE', path, payload)
            self.assertEqual((status, result['error']['code']), (409, 'encoder_busy'))
            if phase == 'running':
                os.kill(state['pid'], signal.SIGKILL)  # Isolated fake encoder only.
        self.assertTrue((self.root/'state/channels'/created['id']/'config.json').is_file())
        self.assertEqual(self.request('POST', path+'/stop', {})[0], 200)
        self.assertEqual(self.request('DELETE', path, payload)[0], 200)


    def apply_payload(self, state=None, **changes):
        state = state or self.state()
        return {'revision': state['revision'], 'instance_id': state['instance_id'],
                'config': dict(state['config'], **changes)}

    def wait_restarted(self, old_pid):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = self.state()
            if not state.get('applying') and state['phase'] == 'running' and state['pid'] != old_pid:
                return state
            time.sleep(.03)
        self.fail(f'Configuration did not restart the fake encoder: {state}')

    def test_apply_stopped_channel_saves_without_start(self):
        _, created = self.request('POST', '/api/channels', {'name': 'Apply idle'})
        path = '/api/channels/' + created['id']
        payload = self.apply_payload(created['state'], video_bitrate_kbps=7000)
        status, saved = self.request('POST', path+'/apply', payload)
        self.assertEqual(status, 200, saved)
        self.assertFalse(saved['applying'])
        self.assertFalse(saved['process_alive'])
        self.assertFalse(saved['recovery']['active'])
        self.assertEqual(saved['config']['video_bitrate_kbps'], 7000)
        self.assertGreater(saved['revision'], payload['revision'])
        self.assertEqual(self.state()['config']['video_bitrate_kbps'], 8000)
        self.stop_server()
        self.start_server()
        state = self.request(path=path+'/state')[1]
        self.assertEqual(state['config'], saved['config'])
        self.assertFalse(state['process_alive'])

    def test_apply_restarts_only_selected_encoder_and_keeps_recovery(self):
        first = self.start_encoder(auto_restart=True)
        _, created = self.request('POST', '/api/channels', {'name': 'Unchanged on apply'})
        path = '/api/channels/' + created['id']
        self.assertEqual(self.request('POST', path+'/start', {'revision': created['state']['revision']})[0], 202)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            other = self.request(path=path+'/state')[1]
            if other['phase'] == 'running':
                break
            time.sleep(.03)
        self.assertEqual(other['phase'], 'running')
        status, saved = self.request('POST', '/api/apply', self.apply_payload(first, video_bitrate_kbps=7000))
        self.assertEqual(status, 202, saved)
        self.assertEqual(saved['config']['video_bitrate_kbps'], 7000)
        self.assertGreater(saved['revision'], first['revision'])
        fresh = self.wait_restarted(first['pid'])
        self.assertEqual(fresh['config'], saved['config'])
        self.assertTrue(fresh['recovery']['enabled'])
        self.assertTrue(fresh['recovery']['active'])
        self.assertTrue((self.root/'quit-received').exists(), 'The replaced encoder must stop gracefully')
        unchanged = self.request(path=path+'/state')[1]
        self.assertEqual(unchanged['pid'], other['pid'])
        self.assertEqual(unchanged['config'], other['config'])
        self.assertEqual(unchanged['revision'], other['revision'])

    def test_apply_rejects_invalid_stale_and_conflicting_changes_before_stop(self):
        running = self.start_encoder()
        payload = self.apply_payload(running, name='Must remain a draft')
        cases = [
            (dict(payload, config=dict(payload['config'], threads=0)), 422),
            (dict(payload, credentials={'passphrase': 'short'}), 422),
            (dict(payload, revision=running['revision']-1), 409),
            (dict(payload, instance_id='an-older-instance'), 409),
            ({key: value for key, value in payload.items() if key != 'instance_id'}, 422),
        ]
        _, created = self.request('POST', '/api/channels', {'name': 'Reserved ports'})
        path = '/api/channels/' + created['id']
        self.assertEqual(self.request('POST', path+'/start', {'revision': created['state']['revision']})[0], 202)
        cases.extend((dict(payload, config=dict(payload['config'], **{field: created['state']['config'][field]})), 409)
                     for field in ('input_url', 'output_url'))
        before = (self.root/'state/config.json').read_bytes() if (self.root/'state/config.json').exists() else None
        for candidate, expected_status in cases:
            with self.subTest(expected_status=expected_status, fields=list(candidate)):
                status, response = self.request('POST', '/api/apply', candidate)
                self.assertEqual(status, expected_status, response)
                state = self.state()
                self.assertEqual(state['pid'], running['pid'])
                self.assertEqual(state['phase'], 'running')
                self.assertEqual(state['revision'], running['revision'])
                self.assertEqual(state['config'], running['config'])
        after = (self.root/'state/config.json').read_bytes() if (self.root/'state/config.json').exists() else None
        self.assertEqual(after, before)
        self.assertFalse((self.root/'quit-received').exists())
        self.assertEqual(self.request('POST', '/api/apply', payload, {'Origin': 'https://evil.example'})[0], 403)

    def test_apply_retains_saved_credentials_without_exposing_them(self):
        state = self.state()
        secret = 'apply-saved-fixture-passphrase'
        config = dict(state['config'], input_url='srt://127.0.0.1:12000?mode=caller')
        self.assertEqual(self.request('PUT', '/api/config', {
            'revision': state['revision'], 'config': config, 'credentials': {'passphrase': secret}})[0], 200)
        (self.root/'expected-passphrase').write_text(secret)
        running = self.start_encoder()
        status, result = self.request('POST', '/api/apply', self.apply_payload(running, preset='fast'))
        self.assertEqual(status, 202, result)
        fresh = self.wait_restarted(running['pid'])
        self.assertTrue(fresh['credentials']['passphrase'])
        self.assertTrue(fresh['encrypted'])
        self.assertNotIn(secret, json.dumps(fresh))
        self.assertNotIn(secret, json.dumps(result))


class ChannelDeleteTests(unittest.TestCase):
    def setUp(self):
        self.module = runpy.run_path(str(ROOT/'webpanel/server.py'))
        self.temp = tempfile.TemporaryDirectory(prefix='obe-channel-delete-')
        self.root = Path(self.temp.name)
        self.channels = self.module['Channels'](self.root, self.root/'state')
        created = self.channels.create({'name': 'Delete fixture'})
        self.channel = created['id']
        self.panel = self.channels.get(self.channel)
        self.panel.save_config({'revision': self.panel.revision,
                               'config': dict(self.panel.config, input_url='srt://127.0.0.1:12020'),
                               'credentials': {'passphrase': 'delete-unit-passphrase'}})
        self.payload = {'revision': self.panel.revision, 'instance_id': self.panel.instance_id}

    def tearDown(self):
        self.channels.close()
        self.temp.cleanup()

    def assert_delete_error(self, status, code):
        with self.assertRaises(self.module['APIError']) as caught:
            self.channels.delete(self.channel, self.payload)
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))

    def test_deleted_panel_cannot_save_into_reused_slot(self):
        old_panel = self.panel
        self.channels.delete(self.channel, self.payload)
        self.assertFalse(any(old_panel.credentials.values()))
        replacement = self.channels.create({'name': 'Replacement'})
        self.assertEqual(replacement['id'], self.channel)
        before = old_panel.config_path.read_bytes()
        with self.assertRaises(self.module['APIError']) as caught:
            old_panel.save_config({'revision': old_panel.revision, 'config': old_panel.config})
        self.assertEqual((caught.exception.status, caught.exception.code), (503, 'panel_closing'))
        self.assertEqual(old_panel.config_path.read_bytes(), before)

    def test_delete_rejects_symlinked_channel_parent_and_config(self):
        for path in (self.panel.config_path, self.panel.state_dir, self.panel.state_dir.parent):
            with self.subTest(path=path.name):
                moved = path.with_name(path.name + '-fixture-target')
                path.rename(moved)
                path.symlink_to(moved, target_is_directory=moved.is_dir())
                try:
                    self.assert_delete_error(409, 'unsafe_channel_path')
                    self.assertTrue(moved.exists())
                    self.assertFalse(self.panel.closing)
                    self.assertIs(self.channels.get(self.channel), self.panel)
                finally:
                    path.unlink()
                    moved.rename(path)

    def test_rename_failure_preserves_data_and_restarts_monitor(self):
        self.channels.start_monitoring()
        self.panel._sample_monitoring()
        before = self.panel.config_path.read_bytes()
        samples = self.panel.history.read(1800)['total_samples']
        monitor = self.panel.monitor_thread
        with mock.patch.object(Path, 'rename', side_effect=OSError('fixture rename failure')):
            self.assert_delete_error(500, 'delete_failed')
        self.assertFalse(monitor.is_alive())
        self.assertIsNot(self.panel.monitor_thread, monitor)
        self.assertTrue(self.panel.monitor_thread.is_alive())
        self.assertFalse(self.panel.monitor_cancel.is_set())
        self.assertFalse(self.panel.closing)
        self.assertEqual(self.panel.config_path.read_bytes(), before)
        self.assertTrue(self.panel.credentials['passphrase'])
        self.assertGreaterEqual(self.panel.history.read(1800)['total_samples'], samples)
        self.panel.save_config({'revision': self.panel.revision, 'config': dict(self.panel.config, name='Still usable')})
        self.panel._sample_monitoring()
        self.assertIn('Still usable', json.dumps(self.panel.history.read(1800)))

    def test_path_changed_during_close_is_not_reopened_on_shutdown(self):
        self.channels.start_monitoring()
        outside = self.root/'outside'
        outside.mkdir()
        close = self.panel.close

        def replace_closed_path():
            close()
            self.panel.state_dir.rename(self.root/'preserved')
            self.panel.state_dir.symlink_to(outside, target_is_directory=True)

        with mock.patch.object(self.panel, 'close', side_effect=replace_closed_path):
            self.assert_delete_error(409, 'unsafe_channel_path')
        self.assertTrue(self.panel.closing)
        self.assertIsNone(self.panel.history)
        self.channels.close()
        self.assertFalse(list(outside.iterdir()))
        self.assertTrue((self.root/'preserved/config.json').is_file())

    def test_cleanup_failure_removes_channel_and_tombstone_is_not_reloaded(self):
        self.channels.start_monitoring()
        self.panel._sample_monitoring()
        with mock.patch.object(self.module['shutil'], 'rmtree', side_effect=OSError('fixture cleanup failure')):
            self.assert_delete_error(500, 'channel_cleanup_failed')
        self.assertNotIn(self.channel, self.channels.panels)
        self.assertFalse(self.panel.state_dir.exists())
        self.assertFalse(self.panel.monitor_thread.is_alive())
        self.assertFalse(any(self.panel.credentials.values()))
        tombstones = list((self.root/'state/channels').glob('.deleted-*'))
        self.assertEqual(len(tombstones), 1)
        self.assertTrue((tombstones[0]/'config.json').is_file())
        self.assertTrue((tombstones[0]/'monitoring.sqlite3').is_file())
        self.channels.close()
        self.channels = self.module['Channels'](self.root, self.root/'state')
        self.assertEqual(list(self.channels.panels), ['main'])
        replacement = self.channels.create({'name': 'After cleanup failure'})
        self.assertEqual(replacement['id'], self.channel)
        self.assertFalse(any(replacement['state']['credentials'].values()))


class ApplyRaceTests(unittest.TestCase):
    """Control stop/start interleavings without launching or signalling processes."""
    def setUp(self):
        self.module = runpy.run_path(str(ROOT/'webpanel/server.py'))
        self.temp = tempfile.TemporaryDirectory(prefix='obe-apply-race-')
        self.root = Path(self.temp.name)
        self.channels = self.module['Channels'](self.root, self.root/'state')
        created = self.channels.create({'name': 'Apply fixture'})
        self.channel = created['id']
        self.panel = self.channels.get(self.channel)
        self.serial = 0
        self.workers = []
        self.releases = []
        self.terminate = mock.patch.object(self.panel, '_terminate', side_effect=self.finish_process)
        self.terminate_mock = self.terminate.start()
        self.launch = mock.patch.object(self.panel, '_launch', side_effect=self.launch_process)
        self.launch_mock = self.launch.start()

    def tearDown(self):
        for release in self.releases:
            release.set()
        for worker in self.workers:
            worker.join(4)
        self.channels.close()
        self.launch.stop()
        self.terminate.stop()
        self.temp.cleanup()

    def launch_process(self, kind, config, secret, session):
        self.serial += 1
        process = mock.Mock(pid=900000+self.serial, returncode=None)
        process.poll.return_value = None
        run = self.module['Run'](process, secret, kind, session)
        self.panel.run = run
        self.panel.phase = 'running' if kind == 'start' else 'probing'
        self.panel.encoding_started = kind == 'start'
        if kind == 'start':
            run.started.set()
            session.armed = True
        return run

    @staticmethod
    def finish_process(run):
        run.process.poll.return_value = 0
        run.process.returncode = 0
        run.exited.set()

    def start(self, **options):
        self.channels.action(self.channel, 'start', dict(revision=self.panel.revision, **options))
        self.launch_mock.reset_mock()
        return self.panel.run

    def payload(self, **changes):
        return {'revision': self.panel.revision, 'instance_id': self.panel.instance_id,
                'config': dict(self.panel.config, **changes)}

    def wait_done(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = self.panel.state()
            if not state['applying']:
                return state
            time.sleep(.005)
        self.fail('Apply worker did not complete')

    def barrier(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)

        def terminate(run):
            if not entered.is_set():
                self.workers.append(threading.current_thread())
                entered.set()
                if not release.wait(4):
                    raise AssertionError('Apply termination barrier timed out')
            self.finish_process(run)

        self.terminate_mock.side_effect = terminate
        return entered, release

    def test_failed_persistence_keeps_running_process_and_profile(self):
        old = self.start(auto_restart=True)
        original_session = self.panel.session
        before = self.panel.config_path.read_bytes()
        revision = self.panel.revision
        with mock.patch('os.replace', side_effect=OSError('fixture write failure')):
            with self.assertRaises(self.module['APIError']) as caught:
                self.channels.apply(self.channel, self.payload(name='Unsaved profile'))
        self.assertEqual(caught.exception.code, 'save_failed')
        self.assertEqual(self.panel.config_path.read_bytes(), before)
        self.assertEqual(self.panel.revision, revision)
        self.assertIs(self.panel.run, old)
        self.assertIs(self.panel.session, original_session)
        self.assertTrue(original_session.active)
        self.assertFalse(old.cancel.is_set())
        self.assertFalse(self.panel.state()['applying'])
        self.assertEqual(self.panel.state()['phase'], 'running')
        self.terminate_mock.assert_not_called()
        self.launch_mock.assert_not_called()

    def test_stop_during_apply_cancels_restart_but_preserves_saved_config(self):
        old = self.start(auto_restart=True)
        entered, release = self.barrier()
        payload = self.payload(video_bitrate_kbps=7000)
        self.channels.apply(self.channel, payload)
        self.assertTrue(entered.wait(2))
        self.assertTrue(self.panel.state()['applying'])
        self.assertEqual(self.panel.config['video_bitrate_kbps'], 7000)
        self.panel.stop()
        release.set()
        for worker in self.workers:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        state = self.wait_done()
        self.assertEqual(state['phase'], 'idle')
        self.assertFalse(state['process_alive'])
        self.assertFalse(state['recovery']['active'])
        self.assertEqual(state['config'], payload['config'])
        self.assertGreater(state['revision'], payload['revision'])
        self.assertTrue(old.exited.is_set())
        self.launch_mock.assert_not_called()
        self.channels.action(self.channel, 'start', {'revision': state['revision']})
        self.assertEqual(self.panel.phase, 'running')

    def test_pending_apply_rejects_other_mutations(self):
        self.start()
        entered, release = self.barrier()
        self.channels.apply(self.channel, self.payload(name='Saved while stopping'))
        self.assertTrue(entered.wait(2))
        state = self.panel.state()
        actions = [
            lambda: self.panel.save_config({'revision': state['revision'], 'config': state['config']}),
            lambda: self.channels.action(self.channel, 'start', {'revision': state['revision']}),
            lambda: self.channels.action(self.channel, 'probe', {'revision': state['revision']}),
            lambda: self.channels.apply(self.channel, self.payload()),
            lambda: self.channels.delete(self.channel, {'revision': state['revision'], 'instance_id': state['instance_id']}),
        ]
        for action in actions:
            with self.assertRaises(self.module['APIError']) as caught:
                action()
            self.assertEqual(caught.exception.status, 409)
        self.assertEqual(self.panel.revision, state['revision'])
        release.set()
        self.assertEqual(self.wait_done()['phase'], 'running')
        self.launch_mock.assert_called_once()

    def test_probe_and_stopping_cannot_be_applied(self):
        self.channels.action(self.channel, 'probe', {'revision': self.panel.revision})
        for phase in ('probing', 'stopping'):
            with self.subTest(phase=phase):
                self.panel.phase = phase
                with self.assertRaises(self.module['APIError']) as caught:
                    self.channels.apply(self.channel, self.payload(name='Busy draft'))
                self.assertEqual(caught.exception.status, 409)
        self.terminate_mock.assert_not_called()

    def test_pending_apply_reserves_old_and_new_input_and_output_ports(self):
        self.start()
        old_config = dict(self.panel.config)
        entered, release = self.barrier()
        self.channels.apply(self.channel, self.payload(
            input_url='udp://127.0.0.1:12400', output_url='udp://127.0.0.1:13400'))
        self.assertTrue(entered.wait(2))
        other = self.channels.get('main')
        original_other = dict(other.config)
        with mock.patch.object(other, '_launch') as spawn:
            for config in (old_config, self.panel.config):
                for field, expected in [('input_url', 'input_in_use'), ('output_url', 'output_in_use')]:
                    with self.subTest(field=field, old=config is old_config):
                        other.save_config({'revision': other.revision,
                                           'config': dict(original_other, **{field: config[field]})})
                        with self.assertRaises(self.module['APIError']) as caught:
                            self.channels.action('main', 'start', {'revision': other.revision})
                        self.assertEqual(caught.exception.code, expected)
            spawn.assert_not_called()
        release.set()
        self.assertEqual(self.wait_done()['phase'], 'running')

    def test_apply_during_recovery_cancels_old_retry_and_keeps_policy(self):
        old = self.start(auto_restart=True)
        old_session = self.panel.session
        self.finish_process(old)
        self.panel.phase = 'restart_wait'
        old_session.retry_at = time.monotonic() + 30
        payload = self.payload(preset='fast')
        self.channels.apply(self.channel, payload)
        state = self.wait_done()
        self.assertEqual(state['phase'], 'running')
        self.assertTrue(state['recovery']['enabled'])
        self.assertEqual(state['config'], payload['config'])
        self.assertFalse(old_session.active)
        self.assertTrue(old_session.cancel.is_set())
        self.assertNotEqual(state['pid'], old.process.pid)
        self.launch_mock.assert_called_once()
        self.panel._restart(old_session, 0)
        self.launch_mock.assert_called_once()

    def test_output_apply_during_recovery_keeps_retry_and_updates_session(self):
        old = self.start(auto_restart=True)
        session = self.panel.session
        self.finish_process(old)
        self.panel.phase = 'restart_wait'
        retry_at = session.retry_at = time.monotonic() + 30
        outputs = [{'id': 'mirror', 'name': 'Mirror', 'protocol': 'udp', 'url': 'udp://127.0.0.1:19993'}]
        result = self.channels.apply(self.channel, self.payload(outputs=outputs))
        self.assertEqual(result['apply_mode'], 'outputs')
        self.assertEqual(result['phase'], 'restart_wait')
        self.assertIs(self.panel.session, session)
        self.assertFalse(session.cancel.is_set())
        self.assertEqual(session.retry_at, retry_at)
        self.assertEqual(session.config['outputs'][0]['id'], 'mirror')
        self.terminate_mock.assert_not_called()
        self.launch_mock.assert_not_called()
        self.panel._restart(session, 0)
        self.assertEqual(self.launch_mock.call_args.args[1]['outputs'], self.panel.config['outputs'])

    def test_output_apply_save_failure_keeps_runtime_and_profile(self):
        old = self.start()
        gateway = self.module['transport'].Transport(self.root)
        old.transport = gateway
        gateway.start([], primary_url=self.panel.config['output_url'])
        worker = gateway.outputs[0]
        before, revision = dict(self.panel.config), self.panel.revision
        try:
            error = self.module['APIError'](500, 'save_failed', 'Fixture disk failure')
            with mock.patch.object(self.panel, '_persist_config', side_effect=error):
                with self.assertRaises(self.module['APIError']):
                    self.channels.apply(self.channel, self.payload(outputs=[
                        {'id': 'mirror', 'name': 'Mirror', 'protocol': 'udp', 'url': 'udp://127.0.0.1:19993'}]))
            self.assertEqual(self.panel.config, before)
            self.assertEqual(self.panel.revision, revision)
            self.assertEqual(self.panel.session.config, before)
            self.assertEqual(gateway.outputs, [worker])
            self.assertTrue(worker.thread.is_alive())
            self.terminate_mock.assert_not_called()
            self.launch_mock.assert_not_called()
        finally:
            gateway.stop()

    def test_restart_failure_is_visible_and_allows_manual_start(self):
        old = self.start(auto_restart=True)
        self.launch_mock.side_effect = self.module['APIError'](500, 'spawn_failed', 'Fixture launch failed')
        payload = self.payload(name='Saved despite failed restart')
        self.channels.apply(self.channel, payload)
        state = self.wait_done()
        self.assertEqual(state['phase'], 'error')
        self.assertIn('salvat', state['message'].lower())
        self.assertFalse(state['process_alive'])
        self.assertFalse(state['recovery']['active'])
        self.assertEqual(state['config'], payload['config'])
        self.assertTrue(old.exited.is_set())
        self.assertEqual(json.loads(self.panel.config_path.read_text())['config'], payload['config'])
        self.launch_mock.side_effect = self.launch_process
        self.channels.action(self.channel, 'start', {'revision': state['revision']})
        self.assertEqual(self.panel.phase, 'running')

    def test_failed_stop_keeps_old_live_ports_reserved_after_apply_finishes(self):
        old = self.start()
        old_config = dict(self.panel.config)
        payload = self.payload(input_url='udp://127.0.0.1:12400', output_url='udp://127.0.0.1:13400')
        self.terminate_mock.side_effect = OSError('fixture stop failure')
        try:
            self.channels.apply(self.channel, payload)
            state = self.wait_done()
            self.assertEqual(state['phase'], 'error')
            self.assertTrue(state['process_alive'])
            self.assertEqual(state['pid'], old.process.pid)
            self.assertFalse(old.exited.is_set())
            self.assertEqual(state['config'], payload['config'])
            self.assertEqual(json.loads(self.panel.config_path.read_text())['config'], payload['config'])
            self.launch_mock.assert_not_called()
            other = self.channels.get('main')
            original_other = dict(other.config)
            with mock.patch.object(other, '_launch') as spawn:
                for field, expected in [('input_url', 'input_in_use'), ('output_url', 'output_in_use')]:
                    with self.subTest(field=field):
                        other.save_config({'revision': other.revision,
                                           'config': dict(original_other, **{field: old_config[field]})})
                        with self.assertRaises(self.module['APIError']) as caught:
                            self.channels.action('main', 'start', {'revision': other.revision})
                        self.assertEqual(caught.exception.code, expected)
                spawn.assert_not_called()
        finally:
            self.terminate_mock.side_effect = self.finish_process

    def test_session_key_override_is_kept_only_for_unchanged_origin(self):
        config = dict(self.panel.config, input_url='srt://127.0.0.1:12000?mode=caller')
        self.panel.save_config({'revision': self.panel.revision, 'config': config,
                                'credentials': {'passphrase': 'saved-fixture-passphrase'}})
        override = 'session-fixture-passphrase'
        for changes, credential_update, expected in [
                ({'preset': 'fast'}, None, override),
                ({'preset': 'medium'}, {'passphrase': 'new-fixture-passphrase'}, 'new-fixture-passphrase'),
                ({'preset': 'slow'}, {'passphrase': ''}, ''),
                ({'input_url': 'srt://127.0.0.2:12000?mode=caller'}, None, '')]:
            with self.subTest(changes=changes):
                self.start(passphrase=override, auto_restart=True)
                payload = self.payload(**changes)
                if credential_update is not None:
                    payload['credentials'] = credential_update
                self.channels.apply(self.channel, payload)
                state = self.wait_done()
                self.assertEqual(state['phase'], 'running')
                self.assertEqual(self.panel.session.secret, expected)
                self.assertTrue(self.panel.session.enabled)
                self.assertEqual(self.launch_mock.call_args.args[2], expected)
                self.assertNotIn(override, json.dumps(state))
                self.panel.stop()


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

    def test_qp_after_aq_supports_both_schemas_and_expiry(self):
        self.feed(schema=2, video_pvmaf=None, video_qp_aq=36.25)
        self.assertEqual(self.panel.state()['telemetry']['video_qp_aq'], 36.25)
        self.run.telemetry_received -= 6
        self.assertIsNone(self.panel.state()['telemetry']['video_qp_aq'])
        self.feed(schema=2, video_pvmaf=None, monotonic_ns=12_000_000_000, video_qp_aq=None)
        self.assertIsNone(self.panel.state()['telemetry']['video_qp_aq'])
        self.feed(monotonic_ns=14_000_000_000)
        self.assertEqual(self.panel.state()['telemetry']['status'], 'live')
        self.assertIsNone(self.panel.state()['telemetry']['video_qp_aq'])

    def test_progressive_pvmaf_is_optional_and_bounded(self):
        self.feed(schema=2, video_qp_aq=35, video_pvmaf=85.75)
        self.assertEqual(self.panel.state()['telemetry']['video_pvmaf'], 85.75)
        good = dict(self.run.telemetry_sample)
        for value in (-1, 100.01, True, '85', float('nan'), float('inf')):
            self.feed(schema=2, monotonic_ns=12_000_000_000, video_qp_aq=35, video_pvmaf=value)
            self.assertEqual(self.run.telemetry_sample, good)

    def test_invalid_qp_does_not_replace_valid_sample(self):
        self.feed(schema=2, video_pvmaf=None, video_qp_aq=51)
        good = dict(self.run.telemetry_sample)
        for value in (-1, 51.01, True, '36', float('nan'), float('inf')):
            self.feed(schema=2, video_pvmaf=None, monotonic_ns=12_000_000_000, video_qp_aq=value)
            self.assertEqual(self.run.telemetry_sample, good)
        self.feed(schema=2, video_pvmaf=None, monotonic_ns=12_000_000_000, video_qp_aq=30,
                  video_frames=None, video_bytes=None, video_queue_frames=None)
        self.assertEqual(self.run.telemetry_sample, good)

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


class CredentialPersistenceTests(unittest.TestCase):
    def test_basic_auth_header_is_redacted(self):
        import base64
        module = runpy.run_path(str(ROOT/'webpanel/server.py'))
        values = {'username': 'fixture-user', 'password': 'fixture-password', 'query': '', 'passphrase': ''}
        basic = base64.b64encode(b'fixture-user:fixture-password').decode('ascii')
        secrets = module['credentials'].redactions(values)
        self.assertNotIn(basic, module['clean_log']('Authorization: Basic ' + basic, secrets))

    def test_failed_write_preserves_profile_credentials_and_revision(self):
        module = runpy.run_path(str(ROOT/'webpanel/server.py'))
        with tempfile.TemporaryDirectory(prefix='obe-credential-write-') as temporary:
            root = Path(temporary)
            panel = module['Panel'](root, root/'state')
            try:
                config = dict(panel.config, input_url='srt://127.0.0.1:12000')
                panel.save_config({'revision': 1, 'config': config,
                                   'credentials': {'passphrase': 'original-fixture-passphrase'}})
                before = panel.config_path.read_bytes()
                with mock.patch('os.replace', side_effect=OSError('fixture write failure')):
                    with self.assertRaises(module['APIError']) as caught:
                        panel.save_config({'revision': 2, 'config': dict(config, name='Unsaved'),
                                           'credentials': {'passphrase': 'replacement-fixture-passphrase'}})
                self.assertEqual(caught.exception.code, 'save_failed')
                self.assertEqual(panel.revision, 2)
                self.assertEqual(panel.config, config)
                self.assertEqual(panel.credentials['passphrase'], 'original-fixture-passphrase')
                self.assertEqual(panel.config_path.read_bytes(), before)
                self.assertFalse(list(panel.state_dir.glob('.config-*')))
            finally:
                panel.close()


if __name__ == '__main__':
    unittest.main()
