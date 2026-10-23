#!/usr/bin/env python3
"""Private output service. Encoder and panel lifetimes do not own its sockets.

One bounded JSON request per Unix connection. Configuration has a prepare/commit
handshake: the panel persists only after validation and worker preparation.
Media never crosses this control socket; it uses stable UDP loopback ports.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import tempfile
import threading
import uuid
from urllib.parse import urlsplit

import transport

MAX_MESSAGE = 65536
CHANNELS = {"main", "channel-2", "channel-3", "channel-4"}


def read_message(stream):
    data = stream.readline(MAX_MESSAGE + 1)
    if not data.endswith(b"\n") or len(data) > MAX_MESSAGE:
        raise ValueError("Invalid control message")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Invalid control message")
    return value


def write_message(stream, value):
    data = json.dumps(value, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(data) > MAX_MESSAGE:
        raise ValueError("Control message too large")
    stream.write(data)
    stream.flush()


class Service:
    def __init__(self, root, directory):
        self.root, self.directory = Path(root), Path(directory)
        self.path = self.directory / "outputs.json"
        self.lock = threading.RLock()
        self.instance = uuid.uuid4().hex
        self.records, self.gateways, self.errors = {}, {}, {}
        if self.path.exists():
            if self.path.stat().st_size > MAX_MESSAGE * len(CHANNELS):
                raise ValueError("Invalid saved outputs")
            self.records = json.loads(self.path.read_text())
            if not isinstance(self.records, dict) or set(self.records) - CHANNELS:
                raise ValueError("Invalid saved channels")
            for channel, record in self.records.items():
                self.validate(record)
                port = record.get("input_port")
                if type(port) is not int or not 1 <= port <= 65535:
                    raise ValueError("Invalid saved ingress")
            for channel, record in self.records.items():
                gateway = transport.Transport(self.root)
                try:
                    gateway.start(record["outputs"], record["credentials"], record["primary_url"],
                                  input_port=record["input_port"])
                    self.gateways[channel] = gateway
                except (OSError, ValueError, RuntimeError):
                    self.errors[channel] = "Ripristino delle uscite non riuscito. Verifica porte e helper SRT."

    @staticmethod
    def validate(record):
        outputs = transport.validate_outputs(record.get("outputs"))
        credentials = transport.merge_credentials([], outputs, {}, record.get("credentials", {}))
        primary = record.get("primary_url")
        if not isinstance(primary, str) or not primary:
            raise transport.TransportError("output_url", "Specifica l'uscita UDP primaria.")
        transport.primary_output(primary)
        return {"outputs": outputs, "credentials": credentials, "primary_url": primary}

    @staticmethod
    def endpoints(record):
        targets, listeners = set(), set()
        items = [transport.primary_output(record["primary_url"]), *record["outputs"]]
        for item in items:
            if not item["enabled"]:
                continue
            parts = urlsplit(item["url"])
            if item["protocol"] == "srt" and item["srt_mode"] == "listener":
                listeners.add(parts.port)
            else:
                host = "127.0.0.1" if parts.hostname == "localhost" else parts.hostname
                targets.add((host, parts.port))
        return targets, listeners

    def _check_ports(self, channel, record):
        targets, listeners = self.endpoints(record)
        for key, previous in self.records.items():
            port = previous["input_port"]
            if ("127.0.0.1", port) in targets or port in listeners:
                raise transport.TransportError("outputs", "La porta è riservata al distributore locale.")
            if key != channel:
                other_targets, other_listeners = self.endpoints(previous)
                if targets & other_targets or listeners & other_listeners:
                    raise transport.TransportError("outputs", "Destinazione già riservata da un altro canale.")

    def _persist(self, records, confirm=lambda: None):
        encoded = json.dumps(records, allow_nan=False)
        if len(encoded.encode()) > MAX_MESSAGE * len(CHANNELS):
            raise ValueError("Saved outputs too large")
        fd, name = tempfile.mkstemp(prefix=".outputs-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            confirm()
            os.replace(name, self.path)
            self.records = records
        finally:
            Path(name).unlink(missing_ok=True)

    def configure(self, channel, supplied, confirm):
        record = self.validate(supplied)
        with self.lock:
            self._check_ports(channel, record)
            gateway = self.gateways.get(channel)
            if gateway:
                record["input_port"] = self.records[channel]["input_port"]
                gateway.reconfigure(record["outputs"], record["credentials"], record["primary_url"],
                    commit=lambda: self._persist({**self.records, channel: record}, confirm))
            else:
                gateway = transport.Transport(self.root)
                def commit(url):
                    record["input_port"] = urlsplit(url).port
                    targets, listeners = self.endpoints(record)
                    if (("127.0.0.1", record["input_port"]) in targets
                            or record["input_port"] in listeners):
                        raise transport.TransportError("outputs", "Porta locale in conflitto; riprova l'operazione.")
                    self._persist({**self.records, channel: record}, confirm)
                gateway.start(record["outputs"], record["credentials"], record["primary_url"],
                              input_port=self.records.get(channel, {}).get("input_port", 0), commit=commit)
                self.gateways[channel] = gateway
            self.errors.pop(channel, None)
            return self.state(channel)

    def state(self, channel):
        with self.lock:
            gateway, record = self.gateways.get(channel), self.records.get(channel)
            state = gateway.state() if gateway else {"running": False, "outputs": [], "input_status": "waiting"}
            return {**state, "independent_process": True, "service_available": True,
                    "service_pid": os.getpid(), "service_instance": self.instance,
                    "input_url": f"udp://127.0.0.1:{record['input_port']}" if record else None,
                    "last_error": self.errors.get(channel)}

    def remove(self, channel):
        with self.lock:
            if channel in self.gateways:
                self.gateways[channel].stop()
                del self.gateways[channel]
            self._persist({key: value for key, value in self.records.items() if key != channel})
            self.errors.pop(channel, None)
            return self.state(channel)

    def close(self):
        for gateway in self.gateways.values():
            gateway.stop()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        try:
            request = read_message(self.rfile)
            channel, operation = request.get("channel"), request.get("op")
            if request.get("version") != 1 or channel not in CHANNELS:
                raise ValueError("Invalid version or channel")
            if operation == "state":
                state = self.server.service.state(channel)
            elif operation == "configure":
                def confirm():
                    write_message(self.wfile, {"ready": True})
                    if read_message(self.rfile) != {"commit": True}:
                        raise ValueError("Configuration cancelled")
                state = self.server.service.configure(channel, request, confirm)
            elif operation == "remove":
                state = self.server.service.remove(channel)
            else:
                raise ValueError("Invalid operation")
            write_message(self.wfile, {"state": state})
        except transport.TransportError as error:
            self.reply_error(error.field, error.message)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError):
            self.reply_error("outputs", "Operazione del distributore non completata; verifica lo stato e riprova.")

    def reply_error(self, field, message):
        try:
            write_message(self.wfile, {"error": {"field": field, "message": message}})
        except (OSError, ValueError):
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = False
    def handle_error(self, request, client_address):
        # Tracebacks may contain private control payloads. Report via API only.
        pass


def main():
    parser = argparse.ArgumentParser(description="Distributore OBE indipendente")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.state_dir.chmod(0o700)
    os.umask(0o077)
    service = server = None
    path = args.state_dir / "control.sock"
    with (args.state_dir / "service.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Distributore già attivo.", flush=True)
            return 1
        try:
            path.unlink(missing_ok=True)
            service = Service(args.root, args.state_dir)
            server = Server(str(path), Handler)
            path.chmod(0o600)
            server.service = service
            def shutdown(signum, frame):
                raise KeyboardInterrupt()
            signal.signal(signal.SIGTERM, shutdown)
            print("Distributore pronto.", flush=True)
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        except (OSError, ValueError, RuntimeError):
            print("Avvio distributore non riuscito: verifica stato, porte e permessi.", flush=True)
            return 1
        finally:
            if server:
                server.server_close()
            if service:
                service.close()
            path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
