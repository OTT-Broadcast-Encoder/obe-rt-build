#!/usr/bin/env python3
"""HTTP integration tests: isolated process, no production state or sources."""
import http.client
import json
import os
from pathlib import Path
import runpy
import ssl
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'webpanel'))
from auth import setup_auth
from recovery import DesiredState
base = runpy.run_path(str(Path(__file__).with_name('webpanel.py')))['PanelTests']


class ParityTests(unittest.TestCase):
    setUp, tearDown = base.setUp, base.tearDown
    start_server, stop_server = base.start_server, base.stop_server
    request, state, save = base.request, base.state, base.save
    wait_phase, start_encoder, crash = base.wait_phase, base.start_encoder, base.crash

    def test_output_keys_persistence_and_scoping(self):
        outputs = [{'id': 'main', 'name': 'MAIN', 'enabled': False, 'protocol': 'srt', 'url': 'srt://127.0.0.1:19002'}]
        state = self.state()
        payload = {'revision': state['revision'], 'config': dict(state['config'], outputs=outputs),
                   'output_credentials': {'main': 'fixture-output-secret'}}
        status, state = self.request('PUT', '/api/config', payload)
        self.assertEqual(status, 200, state)
        self.assertEqual(state['output_credentials'], {'main': True})
        self.assertNotIn('fixture-output-secret', json.dumps(state))
        self.stop_server(); self.start_server()
        self.assertEqual(self.state()['output_credentials'], {'main': True})
        changed = self.state()['config']['outputs']; changed[0]['url'] = 'srt://127.0.0.1:19003'
        status, state = self.save(outputs=changed)
        self.assertEqual(status, 200, state)
        self.assertFalse(state['output_credentials']['main'])

    def test_output_parameter_contract_and_protocol_switch(self):
        state = self.state()
        parameters = state['capabilities']['outputs']['srt_parameters']
        self.assertEqual(parameters['srt_connect_timeout_ms'],
            {'type':'integer', 'default':1000, 'min':100, 'max':60000})
        configured = {'id':'mirror', 'name':'Mirror', 'enabled':False, 'protocol':'srt',
            'url':'srt://127.0.0.1:19993', 'srt_connect_timeout_ms':3000}
        status, state = self.request('PUT', '/api/config', {'revision':state['revision'],
            'config':dict(state['config'], outputs=[configured]),
            'output_credentials':{'mirror':'fixture-protocol-key'}})
        self.assertEqual(status, 200)
        for protocol in ('udp', 'rtp', 'srt'):
            changed = dict(state['config']['outputs'][0], protocol=protocol, url=f'{protocol}://127.0.0.1:19993')
            status, state = self.request('PUT', '/api/config', {'revision':state['revision'],
                'config':dict(state['config'], outputs=[changed])})
            self.assertEqual(status, 200)
            self.assertEqual(state['config']['outputs'][0]['srt_connect_timeout_ms'], 3000)
            self.assertFalse(state['output_credentials'].get('mirror'))
            invalid = dict(changed, srt_connect_timeout_ms=50)
            status, error = self.request('PUT', '/api/config', {'revision':state['revision'],
                'config':dict(state['config'], outputs=[invalid])})
            self.assertEqual(status, 422)
            self.assertEqual(self.state()['revision'], state['revision'])

    def test_profiles_validate_export_and_restore_private_snapshot(self):
        self.save(input_url='http://127.0.0.1/source')
        state = self.state()
        status, state = self.request('PUT', '/api/config', {'revision': state['revision'], 'config': state['config'], 'credentials': {'username': 'fixture-user', 'password': 'fixture-private-value'}})
        self.assertEqual(status, 200, state)
        self.save(video_bitrate_kbps=7000)
        status, document = self.request(path='/api/profile')
        self.assertEqual(status, 200)
        self.assertNotIn('fixture-private-value', json.dumps(document))
        self.assertNotIn('credentials', document)
        status, valid = self.request('POST', '/api/profile-validate', document)
        self.assertEqual(status, 200, valid)
        state = self.state()
        status, state = self.request('POST', '/api/restore', {'revision': state['revision'], 'instance_id': state['instance_id']})
        self.assertEqual(status, 200, state)
        self.assertEqual(state['config']['video_bitrate_kbps'], 8000)
        self.assertTrue(state['credentials']['password'])
        self.assertFalse(state['process_alive'])
        self.assertEqual((self.root/'state/previous.json').stat().st_mode & 0o777, 0o600)
        status, _ = self.request('POST', '/api/profile-validate', dict(document, version=2))
        self.assertEqual(status, 422)

    def test_resume_opt_in_and_explicit_stop(self):
        self.assertEqual(self.save(resume_on_restart=True, restart_attempts=2, restart_delay_seconds=1)[0], 200)
        first = self.start_encoder(auto_restart=True)
        self.stop_server(); self.start_server()
        resumed = self.wait_phase({'running'})
        self.assertNotEqual(first['pid'], resumed['pid'])
        self.assertEqual(resumed['recovery']['max_attempts'], 2)
        self.assertTrue(resumed['recovery']['enabled'])
        self.request('POST', '/api/stop', {})
        self.stop_server(); self.start_server()
        self.assertFalse(self.state()['process_alive'])
        self.assertFalse(DesiredState(self.root/'state').read()['running'])

    def test_corrupt_desired_state_never_starts(self):
        self.save(resume_on_restart=True)
        self.stop_server()
        (self.root/'state/desired.json').write_text('{"running":true}')
        (self.root/'state/desired.json').chmod(0o600)
        self.start_server()
        self.assertFalse(self.state()['process_alive'])

    def test_udp_transport_stop_and_apply(self):
        self.assertEqual(self.save(outputs=[{'id':'mirror','name':'Mirror','protocol':'udp','url':'udp://127.0.0.1:19993'}])[0], 200)
        first = self.start_encoder()
        self.assertTrue(first['transport']['running'])
        status, state = self.request('POST', '/api/apply', {'revision': first['revision'], 'instance_id': first['instance_id'], 'config': dict(first['config'], video_bitrate_kbps=7000)})
        self.assertEqual(status, 202, state)
        state = self.wait_phase({'running'})
        self.assertNotEqual(first['pid'], state['pid'])
        self.assertTrue(state['transport']['running'])
        status, state = self.request('POST', '/api/stop', {})
        self.assertEqual(status, 200)
        self.assertFalse(state['transport']['running'])

    def test_output_only_apply_keeps_encoder_and_continuous_ts(self):
        # Extend only this isolated fake encoder with numbered TS datagrams.
        launcher = self.root / 'run.sh'
        script = launcher.read_text().replace('import json, os, sys, threading, time',
            'import json, os, sys, threading, time, socket, struct\nfrom urllib.parse import urlsplit')
        script = script.replace('frames = 0', r'''
target = None
def emit():
    serial = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        while True:
            packet = (b'\x47\x1f\xff\x10' + struct.pack('!I', serial) + bytes(180)) * 7
            sender.sendto(packet, target)
            serial += 1
            time.sleep(.01)
frames = 0''', 1)
        script = script.replace("    elif command == 'start':", """    elif command.startswith('set output opts 0:'):
        parsed = urlsplit(command.split('target=', 1)[1])
        target = (parsed.hostname, parsed.port)
    elif command == 'start':
        threading.Thread(target=emit, daemon=True).start()""")
        launcher.write_text(script)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as primary, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as extra:
            for receiver in (primary, extra):
                receiver.bind(('127.0.0.1', 0))
                receiver.settimeout(3)
            primary_url = f'udp://127.0.0.1:{primary.getsockname()[1]}'
            self.assertEqual(self.save(output_url=primary_url)[0], 200)
            first = current = self.start_encoder(auto_restart=True)
            first_packet = primary.recv(2048)
            sequences = [struct.unpack('!I', first_packet[4:8])[0]]
            output = {'id': 'mirror', 'name': 'Mirror', 'protocol': 'udp',
                      'url': f'udp://127.0.0.1:{extra.getsockname()[1]}'}
            for outputs in ([output], [{**output, 'name': 'Renamed'}], [{**output, 'enabled': False}], []):
                status, current = self.request('POST', '/api/apply', {'revision': current['revision'],
                    'instance_id': current['instance_id'], 'config': dict(current['config'], outputs=outputs)})
                self.assertEqual(status, 200, current)
                self.assertEqual(current['apply_mode'], 'outputs')
                self.assertEqual(current['pid'], first['pid'])
                self.assertFalse(current['applying'])
                self.assertTrue(current['recovery']['enabled'])
                self.assertEqual(current['phase'], 'running')
                if outputs and outputs[0].get('enabled', True):
                    self.assertEqual(len(extra.recv(2048)), 1316)
            while len(sequences) < 80:
                packet = primary.recv(2048)
                self.assertEqual(len(packet), 1316)
                sequences.append(struct.unpack('!I', packet[4:8])[0])
            self.assertEqual(sequences, list(range(sequences[0], sequences[0] + len(sequences))))
            self.assertFalse((self.root / 'quit-received').exists())
            saved = json.loads((self.root / 'state/config.json').read_text())
            self.assertEqual(saved['config']['outputs'], [])

    def test_output_secret_update_and_restore_keep_encoder(self):
        target = {'id': 'mirror', 'name': 'Mirror', 'enabled': False, 'protocol': 'srt', 'url': 'srt://127.0.0.1:19002'}
        first = self.start_encoder()
        status, current = self.request('POST', '/api/apply', {'revision': first['revision'],
            'instance_id': first['instance_id'], 'config': dict(first['config'], outputs=[target]),
            'output_credentials': {'mirror': 'fixture-output-key'}})
        self.assertEqual(status, 200, current)
        self.assertEqual(current['pid'], first['pid'])
        self.assertTrue(current['output_credentials']['mirror'])
        self.assertNotIn('fixture-output-key', json.dumps(current))
        status, restored = self.request('POST', '/api/restore', {'revision': current['revision'], 'instance_id': current['instance_id']})
        self.assertEqual(status, 200, restored)
        self.assertEqual(restored['apply_mode'], 'outputs')
        self.assertEqual(restored['pid'], first['pid'])
        self.assertEqual(restored['config']['outputs'], [])

    def test_auth_api_cookie_restart_logout_and_origin(self):
        path = self.root/'state/auth.json'
        setup_auth(path, 'tester', 'fixture-long-password')
        self.assertEqual(self.request()[0], 401)
        self.assertEqual(self.request(path='/api/preview-init?session='+'a'*32)[0], 401)
        self.assertEqual(self.request(path='/api/preview-segment?session='+'a'*32+'&sequence=1')[0], 401)
        self.assertEqual(self.request('POST', '/api/start', {'revision': 1})[0], 401)
        self.assertEqual(self.request(path='/api/session')[1]['auth_required'], True)
        connection = http.client.HTTPConnection('127.0.0.1', self.port)
        connection.request('POST', '/api/login', json.dumps({'username':'tester','password':'fixture-long-password'}), {'Content-Type':'application/json'})
        response = connection.getresponse(); response.read()
        self.assertEqual(response.status, 200)
        header = response.getheader('Set-Cookie'); cookie = header.split(';')[0]
        self.assertIn('HttpOnly', header); self.assertIn('SameSite=Strict', header)
        connection.close()
        self.assertEqual(self.request(headers={'Cookie': cookie})[0], 200)
        self.stop_server(); self.start_server()
        self.assertEqual(self.request(headers={'Cookie': cookie})[0], 200)
        self.assertEqual(self.request('POST', '/api/logout', {}, {'Cookie':cookie,'Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/logout', {}, {'Cookie':cookie})[0], 200)
        self.assertEqual(self.request(headers={'Cookie':cookie})[0], 401)

    def test_incomplete_gateway_stop_remains_busy_and_retryable(self):
        module = runpy.run_path(str(ROOT/'webpanel/server.py'))
        with tempfile.TemporaryDirectory() as folder:
            panel = module['Panel'](folder, folder)
            process = mock.Mock(pid=900001, returncode=0)
            process.poll.return_value = 0
            run = module['Run'](process, '', 'start')
            run.transport = mock.Mock()
            run.transport.state.return_value = {'running': False, 'outputs': []}
            run.transport.stop.side_effect = RuntimeError('pending')
            panel.run = run
            try:
                with self.assertRaises(module['APIError']): panel.stop()
                self.assertTrue(panel.state()['cleanup_pending'])
                self.assertTrue(panel._busy())
                run.transport.stop.side_effect = None
                result = panel.stop()
                self.assertFalse(result['cleanup_pending'])
                self.assertFalse(panel._busy())
            finally:
                run.exited.set()
                panel.close()

    def test_duplicate_monitor_destination_rejected(self):
        status, result = self.save(outputs=[{'id':'mirror','name':'Mirror','protocol':'udp','url':'udp://239.255.0.1:13000'}])
        self.assertEqual(status, 422, result)
        self.assertFalse(self.state()['process_alive'])

    def test_auxiliary_destination_conflict_and_invalid_apply_keep_process(self):
        self.save(outputs=[{'id':'mirror','name':'Mirror','protocol':'udp','url':'udp://127.0.0.1:19993'}])
        first = self.start_encoder()
        status, created = self.request('POST', '/api/channels', {'name': 'Second'})
        self.assertEqual(status, 201)
        second = created['state']
        config = dict(second['config'], input_url='udp://127.0.0.1:12001', output_url='udp://127.0.0.1:19993')
        status, second = self.request('PUT', '/api/channels/channel-2/config', {'revision':second['revision'],'config':config})
        self.assertEqual(status, 200)
        status, _ = self.request('POST', '/api/channels/channel-2/start', {'revision':second['revision']})
        self.assertEqual(status, 409)
        status, _ = self.request('POST', '/api/apply', {'revision':first['revision'],'instance_id':first['instance_id'],
                                'config':dict(first['config'], video_pid=200, audio_pid=200)})
        self.assertEqual(status, 422)
        self.assertEqual(self.state()['pid'], first['pid'])

    def test_restore_removes_new_keys_and_restarts_active_channel(self):
        self.save(input_url='srt://127.0.0.1:12000', outputs=[{'id':'mirror','name':'Mirror','enabled':False,'protocol':'srt','url':'srt://127.0.0.1:19993'}])
        state = self.state()
        status, _ = self.request('PUT', '/api/config', {'revision':state['revision'], 'config':state['config'],
            'credentials':{'passphrase':'fixture-input-key'}, 'output_credentials':{'mirror':'fixture-output-key'}})
        self.assertEqual(status, 200)
        running = self.start_encoder()
        status, _ = self.request('POST', '/api/restore', {'revision':running['revision'], 'instance_id':running['instance_id']})
        self.assertEqual(status, 202)
        restored = self.wait_phase({'running'})
        self.assertNotEqual(restored['pid'], running['pid'])
        self.assertFalse(restored['credentials']['passphrase'])
        self.assertFalse(restored['output_credentials']['mirror'])

    def test_tls_secure_cookie_and_https_origin(self):
        self.stop_server()
        cert, key = self.root/'cert.pem', self.root/'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        setup_auth(self.root/'state/auth.json', 'tester', 'fixture-long-password')
        self.start_server(['--tls-cert', str(cert), '--tls-key', str(key)])
        pending = socket.create_connection(('127.0.0.1', self.port), timeout=2)
        connection = http.client.HTTPSConnection('127.0.0.1', self.port, timeout=2, context=ssl._create_unverified_context())
        try:
            connection.request('POST', '/api/login', json.dumps({'username':'tester','password':'fixture-long-password'}),
                               {'Content-Type':'application/json', 'Origin':f'https://127.0.0.1:{self.port}'})
            response = connection.getresponse(); response.read()
            self.assertEqual(response.status, 200)
            self.assertIn('; Secure', response.getheader('Set-Cookie'))
        finally:
            connection.close()
            pending.close()
        connection = http.client.HTTPSConnection('127.0.0.1', self.port, context=ssl._create_unverified_context())
        try:
            connection.request('GET', '/api/session', headers={'Origin':f'http://127.0.0.1:{self.port}'})
            response = connection.getresponse(); response.read()
            self.assertEqual(response.status, 403)
        finally:
            connection.close()

    def test_srt_passphrase_reveal_requires_login_and_current_channel(self):
        secret = 'fixture-encoded-source-key'
        self.assertTrue(self.state()['capabilities']['passphrase_reveal'])
        self.assertEqual(self.request('POST', '/api/reveal-passphrase',
            {'revision':self.state()['revision'], 'instance_id':self.state()['instance_id']})[0], 403)
        state = self.state()
        status, state = self.request('PUT', '/api/config', {'revision':state['revision'],
            'config':dict(state['config'], input_url='srt://127.0.0.1:12000'),
            'credentials':{'passphrase':secret}})
        self.assertEqual(status, 200)
        self.assertTrue(state['credentials']['passphrase'])
        self.assertNotIn(secret, json.dumps(state))
        setup_auth(self.root/'state/auth.json', 'tester', 'fixture-long-password')
        body = {'revision':state['revision'], 'instance_id':state['instance_id']}
        self.assertEqual(self.request('POST', '/api/reveal-passphrase', body)[0], 401)
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            connection.request('POST', '/api/login', json.dumps({'username':'tester', 'password':'fixture-long-password'}),
                {'Content-Type':'application/json'})
            response = connection.getresponse(); response.read()
            self.assertEqual(response.status, 200)
            cookie = response.getheader('Set-Cookie').split(';', 1)[0]
        finally:
            connection.close()
        headers = {'Cookie':cookie, 'Origin':f'http://127.0.0.1:{self.port}'}
        self.assertEqual(self.request('POST', '/api/reveal-passphrase', body, headers),
            (200, {'passphrase':secret}))
        self.assertEqual(self.request('POST', '/api/reveal-passphrase', body,
            dict(headers, Origin='http://evil.example'))[0], 403)
        self.assertEqual(self.request('POST', '/api/reveal-passphrase', dict(body, revision=0), headers)[0], 409)
        status, created = self.request('POST', '/api/channels', {'name':'Second'}, headers)
        self.assertEqual(status, 201)
        second = created['state']
        self.assertEqual(self.request('POST', '/api/channels/channel-2/reveal-passphrase',
            {'revision':second['revision'], 'instance_id':second['instance_id']}, headers)[0], 404)

    def test_output_passphrase_reveal_is_authenticated_and_bound_to_output(self):
        secret = 'fixture-output-reveal-key'
        state = self.state()
        output = {'id':'mirror', 'name':'Mirror', 'enabled':False, 'protocol':'srt',
                  'url':'srt://127.0.0.1:19993'}
        status, state = self.request('PUT', '/api/config', {'revision':state['revision'],
            'config':dict(state['config'], outputs=[output]), 'output_credentials':{'mirror':secret}})
        self.assertEqual(status, 200)
        self.assertTrue(state['capabilities']['output_passphrase_reveal'])
        self.assertNotIn(secret, json.dumps(state))
        body = {'revision':state['revision'], 'instance_id':state['instance_id'], 'output_id':'mirror'}
        self.assertEqual(self.request('POST', '/api/reveal-output-passphrase', body)[0], 403)
        setup_auth(self.root/'state/auth.json', 'tester', 'fixture-long-password')
        self.assertEqual(self.request('POST', '/api/reveal-output-passphrase', body)[0], 401)
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            connection.request('POST', '/api/login', json.dumps({'username':'tester','password':'fixture-long-password'}),
                {'Content-Type':'application/json'})
            response = connection.getresponse(); response.read()
            self.assertEqual(response.status, 200)
            cookie = response.getheader('Set-Cookie').split(';', 1)[0]
        finally:
            connection.close()
        headers = {'Cookie':cookie, 'Origin':f'http://127.0.0.1:{self.port}'}
        self.assertEqual(self.request('POST', '/api/reveal-output-passphrase', body, headers),
            (200, {'passphrase':secret}))
        self.assertEqual(self.request('POST', '/api/reveal-output-passphrase', dict(body, output_id='other'), headers)[0], 404)
        self.assertEqual(self.request('POST', '/api/reveal-output-passphrase', dict(body, revision=0), headers)[0], 409)

    def test_diagnostics_and_unknown_signal_details(self):
        self.start_encoder()
        deadline = time.monotonic()+4
        while time.monotonic()<deadline:
            state = self.state()
            if state['system']['status']=='live': break
            time.sleep(.1)
        self.assertEqual(state['system']['status'], 'live')
        self.assertEqual(state['signal_details']['field_rate'], 50)
        self.assertIsNone(state['signal_details']['field_order'])
        self.assertFalse(state['signal_details']['field_motion_verified'])
        self.assertNotIn('mac', json.dumps(state['system']).lower())


del base
if __name__ == '__main__': unittest.main()
