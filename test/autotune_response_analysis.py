#!/usr/bin/env python3
"""Estimate empirical frequency response and evaluate PX4 rate PID on captured SITL data.

The intervals quantify repeat-period scatter; they are not a certified uncertainty
bound or a proof of stability between the measured frequencies.
"""
import argparse
import csv
import json
from pathlib import Path
import numpy as np
from scipy.stats import t as student_t

HARMONICS = np.array([1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144])


def gain_crossings(frequencies, loop):
    """Local sampled margins, without inheriting phase wraps from noisy low bins.

    The phase margin is mapped to the nearest odd multiple of 180 degrees.
    This does not count Nyquist encirclements or establish closed-loop stability.
    """
    crossings = []
    for k in range(len(frequencies) - 1):
        if abs(loop[k]) >= 1 and abs(loop[k + 1]) < 1:
            weight = -np.log(abs(loop[k])) / np.log(abs(loop[k + 1] / loop[k]))
            phase = np.angle(loop[k]) + weight * np.angle(loop[k + 1] / loop[k])
            margin = (np.degrees(phase) + 360) % 360 - 180
            frequency = np.exp(np.log(frequencies[k]) + weight * np.log(frequencies[k + 1] / frequencies[k]))
            crossings.append(dict(frequency_hz=float(frequency), phase_margin_deg=float(margin)))
    return crossings


def coefficients(data, timestamp, column, start, period, block, harmonics=HARMONICS):
    times, indices = np.unique(data[timestamp], return_index=True)
    time = (times - start) * 1e-6
    mask = (time >= block * period) & (time < (block + 1) * period)
    x = time[mask] - block * period
    values = (data[column] if isinstance(column,str) else np.column_stack([data[name] for name in column]))[indices[mask]]
    if len(x) < 100 or np.ptp(x) < .98 * period:
        raise ValueError(f'Incomplete period {block}: {column}')
    phase = 2 * np.pi * x[:, None] * harmonics[None, :] / period
    design = np.column_stack([np.ones(len(x)), x / period - .5, np.cos(phase), np.sin(phase)])
    fit, _, _, _ = np.linalg.lstsq(design, values, rcond=None)
    return fit[2:2+len(harmonics)] - 1j * fit[2+len(harmonics):]


def analyze(root, axis, gains=None):
    settings = json.loads((root / 'manifest.json').read_text())
    config = json.loads((root / 'control-config.json').read_text())
    gains = gains or json.loads((root / 'probe-gains.json').read_text())
    data = np.genfromtxt(root / f'response-axis-{axis}.csv', delimiter=',', names=True)
    period = settings['period']; start = data['start'][0]
    harmonics = np.array(settings.get('harmonics', HARMONICS))
    blocks = range(2, settings['periods'])
    spectra = {}
    for name, timestamp in [('excitation', 'timestamp'), ('torque', 'torque_timestamp'),
                            ('rate', 'gyro_timestamp'), ('acceleration', 'gyro_timestamp')]:
        spectra[name] = np.array([coefficients(data, timestamp, name, start, period, b, harmonics) for b in blocks])
    E = spectra['excitation']; U = spectra['torque']; Y = spectra['rate']; A = spectra['acceleration']
    # External excitation acts as the instrument for both measured channels.
    reference = np.conj(E)
    mean_input = np.mean(U * reference, axis=0)
    G = np.mean(Y * reference, axis=0) / mean_input
    derivative = np.mean(A * reference, axis=0) / np.mean(Y * reference, axis=0)
    residual = (Y - G * U) * reference
    n = len(blocks)
    scatter = np.sqrt(np.sum(abs(residual - residual.mean(axis=0))**2, axis=0) / (n-1))
    radius = student_t.ppf(.995, n-1) * scatter / (np.sqrt(n) * abs(mean_input))
    f = harmonics / period
    dt = float(np.median(np.diff(np.unique(data['gyro_sample']))) * 1e-6)
    q = np.exp(-2j * np.pi * f * dt)
    name = ('ROLL', 'PITCH', 'YAW')[axis]
    P, I, D, K = [gains[f'MC_{name}RATE_{term}'] for term in ('P', 'I', 'D', 'K')]
    C = K * (P + I * dt * q / (1-q) + D * derivative)
    if axis == 2 and config['MC_YAW_TQ_CUTOFF'] > 0:
        alpha = dt / (dt + 1/(2*np.pi*config['MC_YAW_TQ_CUTOFF']))
        C *= alpha / (1-(1-alpha)*q)
    L = C * G
    phase = np.unwrap(np.angle(L)) * 180 / np.pi
    if phase[0] > 0:
        phase -= 360
    crossings = gain_crossings(f, L)
    rows = [dict(frequency_hz=float(f[k]), plant_real=float(G[k].real), plant_imag=float(G[k].imag),
                 plant_magnitude=float(abs(G[k])), plant_phase_deg=float(np.angle(G[k])*180/np.pi),
                 empirical_radius=float(radius[k]), relative_radius=float(radius[k]/abs(G[k])),
                 derivative_real=float(derivative[k].real), derivative_imag=float(derivative[k].imag),
                 input_amplitude=float(abs(np.mean(U[:,k],axis=0))), output_amplitude=float(abs(np.mean(Y[:,k],axis=0))),
                 loop_real=float(L[k].real), loop_imag=float(L[k].imag), loop_magnitude=float(abs(L[k])),
                 loop_phase_deg=float(phase[k]), distance_to_minus_one=float(abs(1+L[k])),
                 distance_minus_empirical_radius=float(abs(1+L[k])-abs(C[k])*radius[k])) for k in range(len(f))]
    summary = dict(axis=name, periods_used=n, sample_interval_s=dt, crossings=crossings,
                   largest_sample_gap_s=float(np.max(np.diff(np.unique(data['gyro_sample'])))*1e-6),
                   max_roll_deg=float(np.max(abs(data['roll']))*180/np.pi),
                   max_pitch_deg=float(np.max(abs(data['pitch']))*180/np.pi),
                   altitude_range_m=[float(-max(data['z'])),float(-min(data['z']))],
                   allocation_not_achieved_fraction=float(np.mean((data['torque_achieved']==0)|(data['thrust_achieved']==0))),
                   minimum_sampled_distance=float(min(abs(1+L))),
                   minimum_distance_minus_empirical_radius=float(min(abs(1+L)-abs(C)*radius)),
                   maximum_relative_empirical_radius=float(max(radius/abs(G))),
                   warning='Empirical intervals and interpolation at discrete frequencies; no all-frequency stability certificate')
    return rows, summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--axes',type=int,nargs='+',default=[0,1,2])
    args=parser.parse_args()
    summaries=[]
    for axis in args.axes:
        rows,summary=analyze(args.root,axis)
        with (args.root/f'frequency-response-axis-{axis}.csv').open('w') as f:
            writer=csv.DictWriter(f,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
        summaries.append(summary)
        print(json.dumps(summary,indent=2))
    (args.root/'frequency-response-summary.json').write_text(json.dumps(summaries,indent=2)+'\n')


if __name__=='__main__':
    main()
