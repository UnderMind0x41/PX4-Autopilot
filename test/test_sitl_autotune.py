#!/usr/bin/env python3
"""X500 Autotune regression, including MAVLink progress and persistent PID gains.

Requires a built Gazebo SITL (make px4_sitl_default), Gazebo and pymavlink.
Usage: python3 test/test_sitl_autotune.py [--output /tmp/autotune-results]
Runs an isolated PX4 instance and Gazebo partition, with temporary parameters.
"""

import argparse
from collections import deque
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time

from pymavlink import mavutil

GAIN_NAMES = [f"MC_{axis}RATE_{term}" for axis in ("ROLL", "PITCH", "YAW")
              for term in ("P", "I", "D", "K")]
GAIN_NAMES += [f"MC_{axis}_P" for axis in ("ROLL", "PITCH", "YAW")]
AUTOTUNE = mavutil.mavlink.MAV_CMD_DO_AUTOTUNE_ENABLE


def unused_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class AutotuneSITL:
    def __init__(self, binary, root):
        self.binary = binary
        self.root = root
        self.instance = 73
        self.system = self.instance + 1
        self.processes = []
        self.logs = []
        self.mav = None
        self.messages = deque(maxlen=10000)
        self.last_input = 0
        self.input_interval = .1
        self.time_scale = 1.
        self.env = dict(os.environ, HEADLESS="1", PX4_SIM_MODEL="gz_x500",
                        GZ_IP="127.0.0.1", GZ_PARTITION=f"autotune-test-{os.getpid()}")
        self.env.pop("PX4_GZ_MODEL_NAME", None)
        self.env.pop("PX4_GZ_STANDALONE", None)
        self.env.pop("PX4_SIM_SPEED_FACTOR", None)
        (root / "etc").symlink_to(binary.parent.parent / "etc", target_is_directory=True)
        shutil.copy(binary.parent.parent / "rootfs/gz_env.sh", root)

    def client(self, name, *args):
        return subprocess.check_output(
            [str(self.binary.with_name("px4-" + name)), "--instance", str(self.instance), *args],
            text=True, timeout=10,
        )

    def start(self, reboot=False):
        if reboot:
            model = self.env['PX4_SIM_MODEL'].removeprefix('gz_')
            self.env["PX4_GZ_MODEL_NAME"] = f"{model}_{self.instance}"
        log = (self.root / ("reboot-console.txt" if reboot else "console.txt")).open("w")
        self.logs.append(log)
        process = subprocess.Popen(
            [str(self.binary), "-d", "-i", str(self.instance), "-w", str(self.root)],
            env=self.env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
        self.processes.append(process)
        deadline = time.monotonic() + 60
        while "Startup script returned successfully" not in Path(log.name).read_text():
            assert process.poll() is None, f"PX4 exited: see {log.name}"
            assert time.monotonic() < deadline, f"Startup timeout: see {log.name}"
            time.sleep(.2)
        self.mav = mavutil.mavlink_connection("udpin:127.0.0.1:0", source_system=249, source_component=190)
        port = self.mav.port.getsockname()[1]
        self.client("mavlink", "start", "-u", str(unused_port()), "-o", str(port),
                    "-t", "127.0.0.1", "-m", "onboard", "-r", "1000000")
        self.messages.clear()
        self.pump(2)

    def pump(self, duration):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if time.monotonic() - self.last_input >= self.input_interval:
                self.mav.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                                            mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
                # Centered virtual RC sticks, allowing a Position-mode tune like QGC.
                self.mav.mav.manual_control_send(self.system, 0, 0, 500, 0, 0)
                self.last_input = time.monotonic()
            message = self.mav.recv_match(blocking=True, timeout=.02)
            if message and message.get_srcSystem() == self.system:
                self.messages.append(message)

    def wait(self, predicate, timeout, description):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump(.1)
            if predicate():
                return
        raise AssertionError(f"Timeout: {description}")

    def status(self, field, expected):
        data = self.client("listener", "vehicle_status")
        return re.search(rf"^\s*{field}: {expected}$", data, re.MULTILINE) is not None

    def gains(self, label):
        values = {}
        for name in GAIN_NAMES:
            self.messages.clear()
            deadline = time.monotonic() + 10
            next_request = 0
            while not any(m.get_type() == "PARAM_VALUE" and m.param_id == name for m in self.messages):
                assert time.monotonic() < deadline, f"Timeout reading parameter {name}"
                if time.monotonic() >= next_request:
                    self.mav.mav.param_request_read_send(self.system, 1, name.encode(), -1)
                    next_request = time.monotonic() + 1 / self.time_scale
                self.pump(.1)
            values[name] = next(m.param_value for m in self.messages
                                if m.get_type() == "PARAM_VALUE" and m.param_id == name)
        (self.root / (label + ".json")).write_text(json.dumps(values, indent=2) + "\n")
        return values

    def takeoff(self):
        self.wait(lambda: self.status("pre_flight_checks_pass", "True"), 45, "preflight checks")
        self.client("commander", "takeoff")
        self.wait(lambda: self.status("arming_state", 2) and self.status("nav_state", 4),
                  45, "takeoff and Hold")
        self.client("commander", "mode", "posctl")
        self.wait(lambda: self.status("nav_state", 2), 10, "Position mode")
        self.pump(3)

    def tune_and_land(self, before, label):
        self.messages.clear()
        timeout = float(self.client('param', 'show', '-q', 'MC_AT_TIMEOUT'))
        deadline = time.monotonic() + max(120, timeout / self.time_scale + 30)
        next_request = 0
        landing = False
        success = False
        last_progress = None
        with (self.root / (label + "-acks.jsonl")).open("w") as log:
            while time.monotonic() < deadline:
                if time.monotonic() >= next_request:
                    self.mav.mav.command_long_send(self.system, 1, AUTOTUNE, 0, 1, 0, 0, 0, 0, 0, 0)
                    next_request = time.monotonic() + 1 / self.time_scale
                self.pump(.1)
                while self.messages:
                    message = self.messages.popleft()
                    if message.get_type() != "COMMAND_ACK" or message.command != AUTOTUNE:
                        continue
                    log.write(json.dumps(message.to_dict()) + "\n")
                    log.flush()
                    assert message.result in (mavutil.mavlink.MAV_RESULT_IN_PROGRESS,
                                              mavutil.mavlink.MAV_RESULT_ACCEPTED), str(message)
                    if message.progress != last_progress:
                        print(f"{label}: progress {message.progress}%, result {message.result}", flush=True)
                        last_progress = message.progress
                    if message.progress == 95 and not landing:
                        assert self.gains(label + "-before-disarm") == before, "Gains applied before disarm"
                        self.client("commander", "land")
                        landing = True
                    if message.result == mavutil.mavlink.MAV_RESULT_ACCEPTED and message.progress == 100:
                        success = True
                        break
                if success:
                    break
        assert landing and success, "Autotune did not report success after landing"
        self.wait(lambda: self.status("arming_state", 1), 30, "disarm")
        after = self.gains(label + "-after-disarm")
        assert all(math.isfinite(v) for v in after.values()), "Non-finite gains"
        for axis in ("ROLL", "PITCH", "YAW"):
            assert after[f"MC_{axis}RATE_P"] != before[f"MC_{axis}RATE_P"], f"{axis} P unchanged"
        # Allow the ordinary parameter autosave to finish; do not issue param save.
        self.pump(3)
        assert (self.root / "fs/parameters.bson").is_file()
        print(f"PASS {label}: all axes changed after disarm; QGC protocol reports success", flush=True)
        return after

    def cancel_by_mode_change(self, before):
        self.messages.clear()
        deadline = time.monotonic() + 40
        changed_mode = False
        failed = False
        while time.monotonic() < deadline:
            self.mav.mav.command_long_send(self.system, 1, AUTOTUNE, 0, 1, 0, 0, 0, 0, 0, 0)
            self.pump(1 / self.time_scale)
            while self.messages:
                message = self.messages.popleft()
                if message.get_type() != "COMMAND_ACK" or message.command != AUTOTUNE:
                    continue
                if message.progress == 20 and not changed_mode:
                    self.client("commander", "mode", "auto:loiter")
                    changed_mode = True
                if message.result == mavutil.mavlink.MAV_RESULT_FAILED:
                    failed = True
            if failed:
                break
        assert changed_mode and failed, "Mode change did not abort identification"
        self.pump(3)
        assert self.gains("after-cancel") == before, "Cancelled tune changed gains"
        self.client("commander", "mode", "posctl")
        self.wait(lambda: self.status("nav_state", 2), 10, "Position mode after cancellation")
        self.pump(3)
        print("PASS: mode change aborts active tuning without changing gains", flush=True)

    def stop_px4(self):
        # The daemon can exit before sending the client its final command result.
        subprocess.run([str(self.binary.with_name("px4-shutdown")),
                        "--instance", str(self.instance)], check=False, timeout=10)
        assert self.processes[-1].wait(timeout=15) == 0, "PX4 shutdown failed"
        self.mav.close()
        self.mav = None

    def close(self):
        if self.mav:
            self.mav.close()
        for process in reversed(self.processes):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        for log in self.logs:
            log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path("build/px4_sitl_default/bin/px4"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.output.resolve() if args.output else Path(tempfile.mkdtemp(prefix="px4-autotune-"))
    root.mkdir(parents=True, exist_ok=True)
    print(f"Artifacts: {root}", flush=True)
    sim = AutotuneSITL(args.binary.resolve(), root)
    try:
        sim.start()
        before = sim.gains("before")
        sim.takeoff()
        sim.cancel_by_mode_change(before)
        after = sim.tune_and_land(before, "first")
        sim.stop_px4()
        sim.start(reboot=True)
        assert sim.gains("after-reboot") == after, "Gains lost after full PX4 restart"
        print("PASS: all gains retained exactly after full PX4 restart", flush=True)
        sim.takeoff()
        sim.tune_and_land(after, "second")
        sim.stop_px4()
    finally:
        sim.close()


if __name__ == "__main__":
    main()
