#!/usr/bin/env python3
"""SRT contracts, bounded persistence and alarm transitions; no network required."""
import copy
import csv
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'webpanel'))
from monitoring import Alarms, History, SRTStats, history_csv
from server import Panel


def srt_sample(connection=1, **changes):
    stats = dict(sample_us=10_000_000, rtt_us=25000, received_packets=100,
                 received_bytes=131600, lost_packets=5, retransmitted_packets=5,
                 dropped_packets=0, buffer_bytes=30000, buffer_ms=120,
                 buffer_available_bytes=100000, latency_ms=200)
    stats.update(changes)
    return dict(schema=1, connection=connection, stats=stats)


def snapshot(**changes):
    sample = dict(time='2026-10-21T12:00:00Z', run_id='run-a', session_id='session-a',
                  revision=3, phase='running', process_alive=True, encoding_started=True,
                  signal_state='receiving', source_fps=25, context={'name': 'Banco'},
                  version={'panel': 'test', 'encoder': 'test'}, metrics={},
                  telemetry={'status': 'live', 'output_errors': 0, 'video_frames': 100, 'video_queue_frames': 0},
                  srt={'status': 'live', 'connection': 1, 'dropped_packets': 0,
                       'lost_packets': 5, 'retransmitted_packets': 5, 'rtt_ms': 25, 'latency_ms': 200})
    sample.update(changes)
    return sample


class SRTTests(unittest.TestCase):
    def setUp(self):
        self.srt = SRTStats()

    def feed(self, now=100, **changes):
        self.srt.accept(json.dumps(srt_sample(**changes)), now)

    def test_real_clock_rates_duplicate_cache_and_expiry(self):
        self.feed()
        self.feed(now=102, sample_us=12_000_000, received_bytes=331600)
        state = self.srt.view(True, True, 102, 90)
        self.assertEqual(state['rtt_ms'], 25)
        self.assertEqual(state['receive_bitrate_kbps'], 800)
        self.feed(now=106, sample_us=12_000_000, received_bytes=331600)
        self.assertEqual(self.srt.received, 102)
        stale = self.srt.view(True, True, 108, 90)
        self.assertEqual(stale['status'], 'stale')
        self.assertIsNone(stale['received_packets'])
        self.assertIsNone(stale['rtt_ms'])

    def test_connection_resets_rates_and_rejects_previous_connection(self):
        self.feed()
        self.feed(now=102, connection=2, sample_us=1, received_bytes=5, received_packets=1,
                  lost_packets=0, retransmitted_packets=0)
        state = self.srt.view(True, True, 102, 90)
        self.assertEqual(state['connection'], 2)
        self.assertIsNone(state['receive_bitrate_kbps'])
        with self.assertRaises(ValueError):
            self.feed(now=103, connection=1)

    def test_invalid_envelopes_cannot_replace_good_data(self):
        self.feed()
        valid = copy.deepcopy(self.srt.sample)
        cases = [dict(schema=True), dict(connection=True), dict(connection=0), dict(extra='secret')]
        envelopes = [dict(srt_sample(), **case) for case in cases]
        envelopes += [srt_sample(**{key: value}) for key, value in
                      [('rtt_us', float('nan')), ('received_bytes', 2**64), ('buffer_ms', -1),
                       ('sample_us', 9_000_000), ('received_packets', 'secret'), ('latency_ms', None)]]
        for envelope in envelopes:
            with self.subTest(envelope=envelope), self.assertRaises(ValueError):
                self.srt.accept(json.dumps(envelope), 101)
        with self.assertRaises(ValueError):
            self.srt.accept('{"schema":1,"schema":1}', 101)
        self.assertEqual(self.srt.sample, valid)
        self.assertEqual(self.srt.received, 100)

    def test_idle_non_srt_and_older_binary(self):
        self.assertEqual(self.srt.view(False, True, 100, 0)['status'], 'not_applicable')
        self.assertEqual(self.srt.view(True, False, 100, 0)['status'], 'idle')
        self.assertEqual(self.srt.view(True, True, 100, 99)['status'], 'waiting')
        self.assertEqual(self.srt.view(True, True, 106, 99)['status'], 'unavailable')


class AlarmTests(unittest.TestCase):
    def setUp(self):
        self.alarms, self.sample = Alarms(), snapshot()

    def step(self, now, advance=True):
        if advance:
            self.sample['telemetry']['video_frames'] += 25
        return self.alarms.evaluate(copy.deepcopy(self.sample), now)

    def test_source_debounce_deduplication_and_recovery(self):
        self.sample['signal_state'] = 'reconnecting'
        self.assertEqual(self.step(0), [])
        self.assertEqual(self.step(2), [])
        self.assertEqual([e['code'] for e in self.step(3)], ['source_missing'])
        self.assertEqual(self.step(20), [])
        self.sample['signal_state'] = 'receiving'
        self.assertEqual(self.step(21), [])
        self.assertEqual(self.step(24)[0]['state'], 'resolved')
        self.assertEqual(len(self.alarms.recent), 2)

    def test_recovered_loss_is_not_drop_alarm_and_counters_can_quiet(self):
        self.step(0)
        self.sample['srt'].update(lost_packets=1000, retransmitted_packets=1000)
        self.assertEqual(self.step(1), [])
        self.sample['srt']['dropped_packets'] = 2
        self.sample['telemetry']['output_errors'] = 1
        self.assertEqual({e['code'] for e in self.step(2)}, {'udp_errors', 'srt_drops'})
        self.assertEqual(self.step(5), [])
        self.step(12)
        events = self.step(15)
        self.assertEqual({e['code'] for e in events}, {'udp_errors', 'srt_drops'})
        self.assertTrue(all(e['state'] == 'resolved' for e in events))
        self.assertEqual(self.step(20), [])

    def test_counter_baseline_survives_missing_samples(self):
        self.sample['srt']['dropped_packets'] = 1
        self.step(0)
        self.step(10)
        self.step(13)
        self.sample['srt'].update(status='stale', dropped_packets=None, connection=None)
        self.step(14)
        self.sample['srt'].update(status='live', dropped_packets=1, connection=1)
        self.assertEqual(self.step(16), [])
        self.assertNotIn('srt_drops', self.alarms.active)

    def test_new_connection_has_independent_drop_counters(self):
        self.step(0)
        self.sample['srt'].update(connection=2, dropped_packets=1)
        self.assertEqual(self.step(1)[0]['code'], 'srt_drops')
        self.sample['srt'].update(connection=3, dropped_packets=0)
        self.step(11)
        self.assertEqual(self.step(14)[0]['state'], 'resolved')

    def test_queue_rtt_hysteresis_and_no_recovery_from_missing_data(self):
        self.sample['telemetry']['video_queue_frames'] = 60
        self.sample['srt']['rtt_ms'] = 170
        self.step(0)
        self.assertEqual({e['code'] for e in self.step(5)}, {'video_queue', 'srt_rtt'})
        self.sample['telemetry']['video_queue_frames'] = 40
        self.sample['srt']['rtt_ms'] = 140
        self.assertEqual(self.step(6), [])
        self.assertEqual(self.step(12), [])
        self.sample['srt']['status'] = 'stale'
        self.step(13)
        self.step(16)
        self.assertIn('srt_rtt', self.alarms.active)
        self.sample['telemetry']['video_queue_frames'] = 20
        self.sample['srt'].update(status='live', rtt_ms=100)
        self.step(17)
        self.step(22)
        self.assertFalse(self.alarms.active)

    def test_stall_stop_and_new_process_reset(self):
        self.step(0, advance=False)
        self.assertEqual(self.step(5, advance=False)[0]['code'], 'video_stalled')
        self.sample['phase'] = 'idle'
        closed = self.step(6, advance=False)
        self.assertEqual(closed[0]['reason'], 'encoder_stopped')
        self.sample.update(phase='running', run_id='run-b')
        self.sample['telemetry']['video_frames'] = 0
        self.assertEqual(self.step(7, advance=False), [])
        self.assertFalse(self.alarms.active)

    def test_encoder_failure_and_telemetry_unavailable(self):
        self.sample['telemetry']['status'] = 'unavailable'
        self.sample['srt']['status'] = 'unavailable'
        self.step(0)
        self.assertEqual({e['code'] for e in self.step(3)}, {'telemetry_missing', 'srt_missing'})
        self.sample.update(phase='error', process_alive=False, encoding_started=False)
        self.assertIn('encoder_error', {e['code'] for e in self.step(4)})


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'monitoring.sqlite3'
        self.history = History(self.path, sample_limit=3, alarm_limit=2)

    def tearDown(self):
        self.history.close()
        self.temp.cleanup()

    def test_retention_persistence_permissions_and_chronological_export(self):
        for number in range(10):
            self.assertTrue(self.history.append(snapshot(revision=number), [{'number': number}]))
        data = self.history.read(2)
        self.assertEqual(data['total_samples'], 3)
        self.assertEqual([s['revision'] for s in data['samples']], [8, 9])
        self.assertEqual([e['number'] for e in data['alarms']], [8, 9])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.history.close()
        self.history = History(self.path, sample_limit=3, alarm_limit=2)
        self.assertEqual(self.history.read(3)['total_samples'], 3)
        self.assertLessEqual(self.path.stat().st_size, 16 * 1024 * 1024)

    def test_csv_quotes_null_and_formula_escape(self):
        sample = snapshot(context={'name': '  =HYPERLINK("test")'})
        self.history.append(sample, [])
        rows = list(csv.DictReader(io.StringIO(history_csv(self.history.read(3)).decode('utf-8-sig'))))
        self.assertEqual(rows[0]['context.name'], "'  =HYPERLINK(\"test\")")
        self.assertEqual(rows[0]['telemetry.video_bitrate_kbps'], '')

    def test_disk_failure_is_reported_without_raising_and_retries(self):
        self.history.db.close()
        self.assertFalse(self.history.append(snapshot(), []))
        self.assertFalse(self.history.available)
        with self.assertRaises(OSError):
            self.history.read(1)
        self.history.retry_at = 0
        self.assertTrue(self.history.append(snapshot(), []))
        self.assertTrue(self.history.available)

    def test_locked_database_does_not_block_monitor(self):
        other = sqlite3.connect(self.path)
        try:
            other.execute('BEGIN EXCLUSIVE')
            self.assertFalse(self.history.append(snapshot(), []))
        finally:
            other.rollback()
            other.close()


class PanelMonitorTests(unittest.TestCase):
    def test_restart_closes_previous_incidents_without_claiming_signal_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            store = History(Path(directory) / 'monitoring.sqlite3')
            sample = snapshot(phase='error', process_alive=False, encoding_started=False)
            store.append(sample, Alarms().evaluate(sample, 0))
            store.close()
            panel = Panel(Path(directory), directory)
            try:
                panel.start_monitoring()
                data, _ = panel.read_history('limit=1800')
                self.assertEqual([event['state'] for event in data['alarms']], ['active', 'resolved'])
                self.assertEqual(data['alarms'][-1]['reason'], 'monitor_restarted')
                self.assertFalse(panel.alarms.active)
                self.assertEqual(panel.state()['phase'], 'idle')
            finally:
                panel.close()

    def test_unwritable_history_does_not_disable_local_alarms(self):
        with tempfile.TemporaryDirectory() as directory:
            panel = Panel(Path(directory), directory)
            panel.history = History(Path(directory) / 'missing-parent' / 'monitoring.sqlite3')
            try:
                panel.phase = 'error'
                panel._sample_monitoring()
                state = panel.state()
                self.assertEqual(state['monitoring']['status'], 'live')
                self.assertFalse(state['monitoring']['history_available'])
                self.assertEqual(state['monitoring']['active_alarms'][0]['code'], 'encoder_error')
                panel._sample_monitoring()
                self.assertEqual(sum('Storico non disponibile' in e['message'] for e in panel.events), 1)
            finally:
                panel.close()


if __name__ == '__main__':
    unittest.main()
