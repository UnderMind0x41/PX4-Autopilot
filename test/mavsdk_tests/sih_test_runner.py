#!/usr/bin/env python3
"""
Test runner for SIH SITL MAVSDK C++ tests.

Starts PX4 SIH, waits for ready, runs the mavsdk_tests binary with
a test filter, collects results. Follows the same pattern as
mavsdk_test_runner.py but without Gazebo.

Usage:
    python3 test/mavsdk_tests/sih_test_runner.py \
        --speed-factor 10 \
        --build-dir build/px4_sitl_sih \
        --model quadx \
        --test-filter "[ekf2_selector]"
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from typing import IO, NoReturn, Optional


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


class SihRunner:
    """Manages a PX4 SIH SITL process."""

    def __init__(
        self,
        build_dir: str,
        model: str,
        speed_factor: float,
        rootfs_dir: str,
        log_file: str,
    ) -> None:
        self.build_dir = os.path.abspath(build_dir)
        self.model = model
        self.speed_factor = speed_factor
        self.rootfs_dir = rootfs_dir
        self.log_file = log_file
        self.process: Optional[subprocess.Popen[bytes]] = None
        self._log_fd: Optional[IO[str]] = None

    def _prepare_rootfs(self) -> None:
        os.makedirs(self.rootfs_dir, exist_ok=True)
        for item in os.listdir(self.rootfs_dir):
            if item == "log":
                continue
            path = os.path.join(self.rootfs_dir, item)
            if os.path.isfile(path) or os.path.islink(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)

    def start(self) -> None:
        self._prepare_rootfs()

        env = os.environ.copy()
        env["PX4_SYS_AUTOSTART"] = "10040"
        env["PX4_SIM_MODEL"] = f"sihsim_{self.model}"
        env["PX4_SIM_SPEED_FACTOR"] = str(
            self.speed_factor
        )

        cmd = [
            os.path.join(self.build_dir, "bin/px4"),
            os.path.join(self.build_dir, "etc"),
            "-s", "etc/init.d-posix/rcS",
            "-d",
        ]

        self._log_fd = open(self.log_file, "w")
        self.process = subprocess.Popen(
            cmd,
            cwd=self.rootfs_dir,
            env=env,
            stdout=self._log_fd,
            stderr=subprocess.STDOUT,
        )
        log(
            f"PX4 SIH started (PID {self.process.pid}, "
            f"speed={self.speed_factor}x)"
        )

    def wait_until_ready(
        self, timeout_s: float = 60.0
    ) -> bool:
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            if self.process and self.process.poll() is not None:
                log("PX4 SIH exited unexpectedly")
                return False
            try:
                with open(self.log_file) as f:
                    if "Ready for takeoff" in f.read():
                        log("PX4 SIH ready")
                        return True
            except FileNotFoundError:
                pass
            time.sleep(0.5)

        log("PX4 SIH failed to become ready")
        try:
            with open(self.log_file) as f:
                print(f.read())
        except FileNotFoundError:
            pass
        return False

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            log("PX4 SIH stopped")
        if self._log_fd is not None:
            self._log_fd.close()
            self._log_fd = None


def run_mavsdk_tests(
    build_dir: str,
    test_filter: str,
    speed_factor: float,
    connection: str,
) -> int:
    """Run the compiled mavsdk_tests binary."""
    binary = os.path.join(
        build_dir, "mavsdk_tests", "mavsdk_tests"
    )
    if not os.path.isfile(binary):
        log(f"mavsdk_tests not found at {binary}")
        return 1

    cmd = [
        binary,
        "--url", connection,
        "--speed-factor", str(speed_factor),
        test_filter,
    ]
    log(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, timeout=300)
    return result.returncode


def main() -> NoReturn:
    parser = argparse.ArgumentParser(
        description="SIH SITL test runner"
    )
    parser.add_argument(
        "--speed-factor", type=float, default=1.0,
        help="simulation speed factor"
    )
    parser.add_argument(
        "--build-dir", default="build/px4_sitl_sih",
        help="PX4 SIH build directory"
    )
    parser.add_argument(
        "--model", default="quadx",
        help="SIH vehicle model"
    )
    parser.add_argument(
        "--test-filter", default="[ekf2_selector]",
        help="Catch2 test filter"
    )
    parser.add_argument(
        "--connection",
        default="udp://0.0.0.0:14540",
        help="MAVLink connection URL"
    )
    parser.add_argument(
        "--log-dir", default="logs",
        help="directory for log files"
    )
    args = parser.parse_args()

    os.makedirs(args.log_dir, exist_ok=True)

    rootfs_dir = os.path.join(
        args.build_dir, "tmp_sih_tests", "rootfs"
    )
    sih_log = os.path.join(args.log_dir, "sih.log")

    sih = SihRunner(
        build_dir=args.build_dir,
        model=args.model,
        speed_factor=args.speed_factor,
        rootfs_dir=rootfs_dir,
        log_file=sih_log,
    )

    def sigint_handler(
        _sig: int, _frame: object
    ) -> None:
        sih.stop()
        sys.exit(130)

    signal.signal(signal.SIGINT, sigint_handler)

    sih.start()
    if not sih.wait_until_ready():
        sih.stop()
        sys.exit(1)

    rc = run_mavsdk_tests(
        build_dir=args.build_dir,
        test_filter=args.test_filter,
        speed_factor=args.speed_factor,
        connection=args.connection,
    )

    sih.stop()

    if rc == 0:
        log("All tests passed")
    else:
        log(f"Tests failed (exit code {rc})")

    sys.exit(rc)


if __name__ == "__main__":
    main()
