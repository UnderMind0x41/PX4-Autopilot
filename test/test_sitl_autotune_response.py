#!/usr/bin/env python3
"""Measure a multirotor's response to periodic rate excitation in isolated Gazebo SITL."""
import argparse
import hashlib
import json
import shutil
import shlex
import subprocess
import time
from pathlib import Path

from test_sitl_autotune import AutotuneSITL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=Path('build/px4_sitl_default/bin/px4'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='x500')
    parser.add_argument('--autostart', type=int)
    parser.add_argument('--models-root', type=Path)
    parser.add_argument('--instance', type=int, default=73)
    parser.add_argument('--axes', nargs='+', type=int, default=[0, 1, 2])
    parser.add_argument('--period', type=float, default=8.)
    parser.add_argument('--periods', type=int, default=8)
    parser.add_argument('--amplitude', type=float, default=.06)
    parser.add_argument('--phase', type=float, default=0.)
    parser.add_argument('--param', action='append', default=[])
    parser.add_argument('--param-file', type=Path)
    parser.add_argument('--tune-first', action='store_true')
    parser.add_argument('--torque-probe', action='store_true')
    parser.add_argument('--speed-factor', type=float, default=1.)
    parser.add_argument('--max-frequency', type=float)
    parser.add_argument('--verify-persistence', action='store_true')
    parser.add_argument('--cancel-first', action='store_true')
    parser.add_argument('--expect-rejection', help='Required console reason for an expected tune failure')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    # Each run owns its executable so rebuilding PX4 cannot interrupt clients.
    original_binary = args.binary.resolve()
    firmware = root / 'firmware'
    (firmware / 'bin').mkdir(parents=True)
    (firmware / 'rootfs').mkdir()
    binary = firmware / 'bin/px4'
    shutil.copy2(original_binary, binary)
    for client in original_binary.parent.glob('px4-*'):
        if client.is_symlink() and client.resolve() == original_binary:
            (binary.parent / client.name).symlink_to('px4')
        else:
            shutil.copy2(client, binary.parent / client.name)
    (firmware / 'etc').symlink_to(original_binary.parent.parent / 'etc', target_is_directory=True)
    shutil.copy(original_binary.parent.parent / 'rootfs/gz_env.sh', firmware / 'rootfs')
    sim = AutotuneSITL(binary, root)
    sim.instance = args.instance
    sim.system = args.instance + 1
    sim.time_scale = args.speed_factor
    sim.input_interval = .1 / args.speed_factor
    if args.speed_factor != 1:
        sim.env['PX4_SIM_SPEED_FACTOR'] = str(args.speed_factor)
    sim.env['PX4_SIM_MODEL'] = 'gz_' + args.model
    if args.autostart:
        sim.env['PX4_SYS_AUTOSTART'] = str(args.autostart)
    if args.models_root:
        env = root / 'gz_env.sh'
        lines = env.read_text().splitlines()
        lines = ['export PX4_GZ_MODELS=' + shlex.quote(str(args.models_root.resolve()))
                 if line.startswith('export PX4_GZ_MODELS=') else line for line in lines]
        env.write_text('\n'.join(lines) + '\n')
    manifest = dict(vars(args), binary=str(binary), output=str(root),
                    models_root=str(args.models_root.resolve()) if args.models_root else None,
                    param_file=str(args.param_file.resolve()) if args.param_file else None,
                    binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
    params = json.loads(args.param_file.read_text()) if args.param_file else {}
    params.update(entry.split('=', 1) for entry in args.param)
    manifest['effective_parameter_overrides'] = params
    maximum_harmonic = int(args.period * args.max_frequency) if args.max_frequency else 144
    harmonics = [1, 2] if maximum_harmonic >= 2 else [1]
    while len(harmonics) >= 2:
        next_harmonic = max(harmonics[-1]+1, int(1.2*harmonics[-1]+.5)) if args.max_frequency else sum(harmonics[-2:])
        if next_harmonic > maximum_harmonic:
            break
        harmonics.append(next_harmonic)
    if harmonics[-1] < maximum_harmonic:
        harmonics.append(maximum_harmonic)
    manifest['harmonics'] = harmonics
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    result = dict(status='running')
    try:
        sim.start()
        for name, value in params.items():
            sim.client('param', 'set', name, str(value))
            sim.pump(.05)
        before = sim.gains('before')
        sim.takeoff()
        if args.cancel_first:
            sim.cancel_by_mode_change(before)
        if args.tune_first:
            try:
                sim.tune_and_land(before, 'autotune')
            except AssertionError:
                after_failure = sim.gains('after-autotune-failure')
                (root / 'failure-gain-audit.json').write_text(json.dumps(
                    {'all_15_gains_unchanged': after_failure == before}, indent=2) + '\n')
                sim.client('commander', 'land')
                sim.wait(lambda: sim.status('arming_state', 1), 45, 'landing after failed tune')
                if args.expect_rejection:
                    assert after_failure == before, 'Rejected tune changed gains'
                    assert args.expect_rejection in (root / 'console.txt').read_text(), 'Unexpected failure reason'
                    sim.pump(3)
                    sim.stop_px4()
                    if args.verify_persistence:
                        sim.start(reboot=True)
                        assert sim.gains('after-reboot') == before, 'Rejected gains persisted after restart'
                        sim.stop_px4()
                    result = dict(status='rejected_as_expected', reason=args.expect_rejection,
                                  all_15_gains_unchanged=True)
                    return
                raise
            assert not args.expect_rejection, 'Expected rejection, but tune succeeded'
            sim.takeoff()
        probe_gains = sim.gains('probe-gains')
        names = ['MC_YAW_TQ_CUTOFF', 'IMU_GYRO_CUTOFF', 'IMU_DGYRO_CUTOFF', 'MC_BAT_SCALE_EN']
        names += ['MC_REF_W_N', 'MC_REF_FF', 'MC_REF_FF_MAX', 'MC_YAW_WEIGHT', 'MC_AT_RISE_TIME']
        names += [f'MC_{axis}RATE_FF' for axis in ('ROLL', 'PITCH', 'YAW')]
        config = {name: float(sim.client('param', 'show', '-q', name)) for name in names}
        (root / 'control-config.json').write_text(json.dumps(config, indent=2) + '\n')
        sim.client('mc_autotune_attitude_control', 'stop')
        for axis in args.axes:
            path = root / f'response-axis-{axis}.csv'
            command = [str(sim.binary.with_name('px4-mc_autotune_attitude_control')),
                       '--instance', str(sim.instance), 'torque_probe' if args.torque_probe else 'probe', str(axis), str(args.period),
                       str(args.periods), str(args.amplitude), str(args.phase), str(path)]
            if args.max_frequency:
                command.append(str(args.max_frequency))
            print(f'MEASURE {args.model} axis={axis} period={args.period}s periods={args.periods}', flush=True)
            with (root / f'probe-axis-{axis}.txt').open('w') as log:
                process = subprocess.Popen(command, stdout=log, stderr=log)
                deadline = time.monotonic() + args.period * args.periods * 3 + 30
                while process.poll() is None:
                    sim.pump(.2)
                    if time.monotonic() > deadline:
                        sim.client('commander', 'mode', 'auto:loiter')
                        process.wait(timeout=10)
                        raise AssertionError('Response probe exceeded wall time budget')
                assert process.returncode == 0, f'Probe failed: {log.name}'
            print(f'CAPTURED {path}', flush=True)
            sim.pump(4)
        sim.client('commander', 'land')
        sim.wait(lambda: sim.status('arming_state', 1), 45, 'probe landing/disarm')
        assert sim.gains('after-probe') == probe_gains, 'Parameters changed during probing'
        sim.stop_px4()
        if args.verify_persistence:
            sim.start(reboot=True)
            assert sim.gains('after-reboot') == probe_gains, 'Gains lost after PX4 restart'
            sim.stop_px4()
        result = dict(status='captured', axes=args.axes)
    except BaseException as error:
        result = dict(status='failed', error=repr(error))
        raise
    finally:
        (root / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        sim.close()


if __name__ == '__main__':
    main()
