#!/usr/bin/env python3
"""Measure bounded goodput over the SSH path; this does not measure UDP loss."""
import argparse
import json
import os
from pathlib import Path
import select
import shlex
import struct
import subprocess
import sys
import time

SIZE = 32768
SENDER = r'''
import os, struct, sys, time
size = 32768
rate, duration = float(sys.argv[1]) * 1000000 / 8, float(sys.argv[2])
count = int(rate * duration / size)
payload = os.urandom(size - 16)
start = time.monotonic()
for sequence in range(count):
    sys.stdout.buffer.write(struct.pack("!Qd", sequence, time.monotonic() - start) + payload)
    sys.stdout.buffer.flush()
    time.sleep(max(0, start + (sequence + 1) * size / rate - time.monotonic()))
'''


def measure(command, rate, duration):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    start = time.monotonic()
    pending = bytearray()
    count, jitter = 0, 0.0
    first = previous = None
    try:
        while True:
            if time.monotonic() - start > duration + 15:
                raise RuntimeError("SSH measurement exceeded its bounded duration")
            if not select.select([process.stdout], [], [], 1)[0]:
                continue
            data = os.read(process.stdout.fileno(), 65536)
            if not data:
                break
            pending.extend(data)
            while len(pending) >= SIZE:
                sequence, sent = struct.unpack_from("!Qd", pending)
                del pending[:SIZE]
                arrived = time.monotonic()
                if sequence != count:
                    raise RuntimeError("Incomplete or invalid measurement stream")
                if previous:
                    variation = abs((arrived - previous[0]) - (sent - previous[1]))
                    jitter += (variation - jitter) / 16
                else:
                    first = arrived
                previous = arrived, sent
                count += 1
        status = process.wait(timeout=5)
        expected = int(rate * 1000000 / 8 * duration / SIZE)
        if status or pending or count != expected or count < 2:
            raise RuntimeError(f"Incomplete measurement: status={status}, records={count}/{expected}")
        elapsed = previous[0] - first
        return {"offered_mbps": rate, "requested_seconds": duration,
                "received_bytes": count * SIZE, "records": count,
                "goodput_mbps": round((count - 1) * SIZE * 8 / elapsed / 1000000, 4),
                "receive_span_seconds": round(elapsed, 3),
                "ssh_application_jitter_ms": round(jitter * 1000, 3),
                "udp_packet_loss": None,
                "limitations": "Rate-capped SSH/TCP payload test, not maximum line capacity. "
                                "Application jitter includes TCP buffering; UDP loss is not observable."}
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="SSH destination, for example user@host")
    parser.add_argument("--control-socket", help="Existing authenticated SSH multiplex socket")
    parser.add_argument("--rate-mbps", type=float, default=20)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--output", type=Path, default=Path("tests/.build/contribution-ssh.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        args.rate_mbps, args.seconds = 2, 1
        command = [sys.executable, "-u", "-c", SENDER, "2", "1"]
    else:
        if not args.host or args.host.startswith("-"):
            parser.error("--host is required")
        if not 0 < args.rate_mbps <= 100 or not 1 <= args.seconds <= 60:
            parser.error("Use 0 < rate <= 100 Mbit/s and 1 <= duration <= 60 seconds")
        command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "Compression=no"]
        if args.control_socket:
            command += ["-S", args.control_socket]
        command += [args.host, "python3 -u -c " + shlex.quote(SENDER) +
                    f" {args.rate_mbps} {args.seconds}"]
    result = measure(command, args.rate_mbps, args.seconds)
    result.update(transport="local self-test" if args.self_test else "SSH/TCP", endpoint=args.host)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
