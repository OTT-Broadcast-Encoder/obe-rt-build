#!/usr/bin/env python3
"""Contract tests for the web panel, using an isolated fake OBE executable."""
import http.client
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FAKE = '''#!/usr/bin/env python3
import os, sys, threading, time
from pathlib import Path
def events():
    previous = None
    while True:
        marker = Path('event').read_text() if Path('event').exists() else ''
        if marker != previous:
            previous = marker
            if marker == 'reconnect': print('obecli> ....URL input: retrying in 1 seconds (attempt 1)', flush=True)
            if marker == 'recovered': print('obecli> ....URL input: reconnected; receiving video and stereo audio', flush=True)
        time.sleep(0.05)
threading.Thread(target=events, daemon=True).start()
print('Version 4.1.4 (panel-test)', flush=True)
for line in sys.stdin:
    command = line.strip()
    if command == 'probe input':
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
                self.assertNotIn(key, path.read_text())
        self.assertTrue(self.state()['encrypted'])

    def test_child_exit_is_error_not_success(self):
        (self.root/'behavior').write_text('exit')
        status, _ = self.request('POST', '/api/start', {'revision': self.state()['revision']})
        self.assertEqual(status, 202)
        state = self.wait_phase({'error'})
        self.assertFalse(state['encoding_started'])
        self.assertFalse(state['process_alive'])

    def test_missing_start_confirmation_is_an_error(self):
        (self.root/'behavior').write_text('no-start')
        status, _ = self.request('POST', '/api/start', {'revision': self.state()['revision']})
        self.assertEqual(status, 202)
        state = self.wait_phase({'error'}, seconds=24)
        self.assertFalse(state['encoding_started'])
        self.assertFalse(state['process_alive'])

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


if __name__ == '__main__':
    unittest.main()
