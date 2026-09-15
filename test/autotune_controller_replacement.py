#!/usr/bin/env python3
"""Evaluate a PID change using measured closed-loop responses to added torque.

For baseline input sensitivity S=U/E, rate response H=Y/E and acceleration
response A=Ydot/E, the return difference for a candidate controller is

 R = diag(r) + diag(1-r) S + diag(Cpn-r Cp0) H
     + diag(Fyaw (Dn-r D0)) A,

where r=(Cpn/Cp0)*(attitude_gain_new/attitude_gain_old). In the local hover
linearization this includes unchanged position control and other axes. The
baseline must be internally stable. Feedback of rates and acceleration has
PX4's P/I/D placement; yaw output filtering is included explicitly.

A uniformly positive Hermitian part of stable R is a sufficient condition for
a stable inverse. Here it is screened only at measured frequencies: empirical
scatter and a finite grid DO NOT establish an all-frequency certificate.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.stats import t as student_t
from autotune_response_analysis import HARMONICS, coefficients

AXES=('ROLL','PITCH','YAW')


def same_parameters(first, second):
    # The PX4 shell rounds floats; MAVLink and ULog preserve float32 values.
    return first.keys()==second.keys() and all(np.isclose(first[key],value,rtol=1e-6,atol=1e-9) for key,value in second.items())


def measure(root):
    settings=json.loads((root/'manifest.json').read_text())
    assert settings['torque_probe'], 'Requires additive torque excitation'
    n=settings['periods']-2; period=settings['period']
    harmonics=np.array(settings.get('harmonics',HARMONICS))
    S=np.empty((n,len(harmonics),3,3),complex)
    H=np.empty_like(S); A=np.empty_like(S)
    sample_intervals=[]
    quality=[]
    for column in range(3):
        data=np.genfromtxt(root/f'response-axis-{column}.csv',delimiter=',',names=True)
        start=data['start'][0]
        sample_intervals.append(np.median(np.diff(np.unique(data['gyro_sample'])))*1e-6)
        quality.append(dict(axis=AXES[column],
                            maximum_tilt_deg=float(max(np.max(abs(data['roll'])),np.max(abs(data['pitch'])))*180/np.pi),
                            largest_sample_gap_s=float(np.max(np.diff(np.unique(data['gyro_sample'])))*1e-6),
                            allocation_not_achieved_fraction=float(np.mean((data['torque_achieved']==0)|(data['thrust_achieved']==0))),
                            largest_allocation_age_s=float(np.max(data['timestamp']-data['allocation_timestamp']))*1e-6))
        for b,block in enumerate(range(2,settings['periods'])):
            E=coefficients(data,'applied_timestamp',f'e{column}',start,period,block,harmonics)
            if not np.all(np.isfinite(E)) or np.min(abs(E))<settings['amplitude']*.5:
                raise ValueError('Applied excitation missing or incomplete')
            U=coefficients(data,'torque_timestamp',['u0','u1','u2'],start,period,block,harmonics)
            YA=coefficients(data,'gyro_timestamp',['y0','y1','y2','a0','a1','a2'],start,period,block,harmonics)
            S[b,:,:,column]=U/E[:,None]
            H[b,:,:,column]=YA[:,:3]/E[:,None]
            A[b,:,:,column]=YA[:,3:]/E[:,None]
    mean_S=S.mean(axis=0)
    low=float(np.linalg.norm(mean_S[0],ord=2))
    high=float(np.linalg.norm(np.eye(3)-mean_S[-1],ord=2))
    return dict(S=S,H=H,A=A,frequencies=harmonics/period,dt=float(np.median(sample_intervals)),
                quality=dict(axes=quality,lowest_frequency_sensitivity_norm=low,
                             highest_frequency_complementary_sensitivity_norm=high,
                             frequency_coverage_screen_pass=low<.2 and high<.2,
                             small_motion_screen_pass=all(q['maximum_tilt_deg']<5 for q in quality),
                             allocation_screen_pass=all(q['allocation_not_achieved_fraction']==0 and q['largest_allocation_age_s']<.25 for q in quality)))


def controller_terms(frequencies,dt,gains,config):
    q=np.exp(-2j*np.pi*frequencies*dt)
    integral=dt*q/(1-q)
    cp=np.empty((len(q),3),complex); d=np.empty_like(cp);att=np.empty(3)
    for axis,name in enumerate(AXES):
        K=gains[f'MC_{name}RATE_K']
        P,I,D=[K*gains[f'MC_{name}RATE_{term}'] for term in ('P','I','D')]
        filt=np.ones(len(q),complex)
        if axis==2 and config['MC_YAW_TQ_CUTOFF']>0:
            alpha=dt/(dt+1/(2*np.pi*config['MC_YAW_TQ_CUTOFF']))
            filt=alpha/(1-(1-alpha)*q)
        cp[:,axis]=filt*(P+I*integral)
        d[:,axis]=filt*D
        att[axis]=gains[f'MC_{name}_P']
    return cp,d,att


def return_difference(S,H,A,cp0,d0,att0,cpn,dn,attn):
    r=cpn/cp0*(attn/att0)
    identity=np.eye(3)
    return (r[..., :,None]*identity + (1-r)[..., :,None]*S
            +(cpn-r*cp0)[..., :,None]*H+(dn-r*d0)[..., :,None]*A)


def evaluate(measurements,current,candidate,config,repeats=()):
    for gains in [current,candidate]:
        for name in AXES:
            values=[gains[f'MC_{name}RATE_{term}'] for term in ['P','I','D','K']]+[gains[f'MC_{name}_P']]
            if not np.all(np.isfinite(values)) or min(values)<0 or min(values[0],values[3],values[4])<=0:
                raise ValueError('Requires finite nonnegative PID and positive P, K and attitude gains')
    for name in AXES:
        if (current[f'MC_{name}RATE_I']>0) != (candidate[f'MC_{name}RATE_I']>0):
            raise ValueError('This prototype does not support adding or removing an integral state')
        # Cp0=P+I*dt*z^-1/(1-z^-1) must have a stable inverse after
        # cancellation of its common integrator in Cpn/Cp0.
        if measurements['dt']*current[f'MC_{name}RATE_I']/current[f'MC_{name}RATE_P']>=2:
            raise ValueError('Baseline PI zero is not strictly inside the unit circle')
    if config.get('MC_BAT_SCALE_EN',0) or any(config.get(f'MC_{name}RATE_FF',0) for name in AXES):
        raise ValueError('This prototype requires disabled battery scaling and zero rate feed-forward')
    if config.get('MC_REF_FF',0) and any(current[f'MC_{name}_P']!=candidate[f'MC_{name}_P'] for name in AXES):
        raise ValueError('Changing attitude gains with reference feed-forward requires an additional measured channel')
    cp0,d0,att0=controller_terms(measurements['frequencies'],measurements['dt'],current,config)
    cpn,dn,attn=controller_terms(measurements['frequencies'],measurements['dt'],candidate,config)
    R=return_difference(measurements['S'],measurements['H'],measurements['A'],cp0,d0,att0,cpn,dn,attn)
    mean=R.mean(axis=0); n=R.shape[0]
    radius=student_t.ppf(.995,n-1)*np.sqrt(np.sum(abs(R-mean)**2,axis=0)/(n*(n-1)))
    uncertainty=np.linalg.norm(radius,axis=(-2,-1))
    # Repeated periods do not reveal a repeatable bias within a single flight.
    # Independent phases/amplitudes provide an additional observed discrepancy.
    # This is still an empirical envelope, not a probability guarantee.
    for repeat in repeats:
        if not np.array_equal(repeat['frequencies'],measurements['frequencies']):
            raise ValueError('Independent measurements require the same frequency grid')
        repeated=evaluate(repeat,current,candidate,config)
        discrepancy=np.linalg.norm(repeated['return_difference']-mean,axis=(-2,-1))
        uncertainty=np.maximum(uncertainty,discrepancy+repeated['empirical_radius'])
    hermitian=(mean+mean.conj().swapaxes(-1,-2))*.5
    lower=np.linalg.eigvalsh(hermitian)[:,0]
    inverse=np.linalg.inv(mean)
    return dict(return_difference=mean,empirical_radius=uncertainty,
                min_hermitian_eigenvalue=lower,
                empirical_lower_bound=lower-uncertainty,
                predicted_S=measurements['S'].mean(axis=0)@inverse,
                predicted_H=measurements['H'].mean(axis=0)@inverse,
                predicted_A=measurements['A'].mean(axis=0)@inverse)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline',type=Path)
    parser.add_argument('candidate',type=Path,help='JSON with all 15 candidate gains')
    parser.add_argument('--observed',type=Path,help='Independent torque measurement using candidate gains')
    parser.add_argument('--repeat',type=Path,action='append',default=[],help='Independent baseline flight with changed excitation phase/amplitude')
    args=parser.parse_args()
    measurement=measure(args.baseline)
    current=json.loads((args.baseline/'probe-gains.json').read_text())
    candidate=json.loads(args.candidate.read_text())
    config=json.loads((args.baseline/'control-config.json').read_text())
    repeats=[]
    for root in args.repeat:
        if not same_parameters(json.loads((root/'probe-gains.json').read_text()),current):
            raise ValueError('Repeated baseline gains differ')
        if not same_parameters(json.loads((root/'control-config.json').read_text()),config):
            raise ValueError('Repeated baseline filter/control configuration differs')
        repeats.append(measure(root))
    result=evaluate(measurement,current,candidate,config,repeats)
    summary=dict(minimum_hermitian_eigenvalue=float(min(result['min_hermitian_eigenvalue'])),
                 minimum_empirical_lower_bound=float(min(result['empirical_lower_bound'])),
                 pointwise_screen_pass=bool(np.all(result['empirical_lower_bound']>.2)),
                 frequencies_hz=measurement['frequencies'].tolist(),
                 lower_bounds=result['empirical_lower_bound'].tolist(),
                 limitation='Finite frequency grid and empirical scatter; not an all-frequency stability certificate')
    summary['measurement_quality']=measurement['quality']
    summary['independent_repeat_quality']=[repeat['quality'] for repeat in repeats]
    quality=[m['quality'] for m in [measurement]+repeats]
    summary['candidate_screen_pass']=summary['pointwise_screen_pass'] and all(
        q['frequency_coverage_screen_pass'] and q['small_motion_screen_pass'] and q['allocation_screen_pass'] for q in quality)
    summary['candidate_screen_scope']='Research screen on measured data; not firmware acceptance or a universal stability claim'
    if args.observed:
        actual_gains=json.loads((args.observed/'probe-gains.json').read_text())
        if not same_parameters(actual_gains,candidate):
            raise ValueError('Observed flight did not use the candidate gains')
        if not same_parameters(json.loads((args.observed/'control-config.json').read_text()),config):
            raise ValueError('Observed filter/control configuration differs')
        observed=measure(args.observed)
        assert np.array_equal(observed['frequencies'],measurement['frequencies'])
        for name in ['S','H','A']:
            actual=observed[name].mean(axis=0);prediction=result['predicted_'+name]
            error=np.linalg.norm(actual-prediction,axis=(-2,-1))/np.maximum(np.linalg.norm(actual,axis=(-2,-1)),1e-12)
            summary['relative_prediction_error_'+name]=error.tolist()
    output=args.baseline/('replacement-'+args.candidate.parent.name+'-'+args.candidate.stem+'.json')
    output.write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
