# Original X500 yaw Autotune: isolated investigation

2026-09-16. Follow-up to the investigation requested in
[PR #28706](https://github.com/PX4/PX4-Autopilot/pull/28706#pullrequestreview-5210072095).
This is a local report; no new PR or upstream comment was published.

## Conclusion

The original implementation reproducibly reaches its 20-second yaw timeout on
stock X500 at simulation speed 1. Starting from the actual BasPlanner Autotune UI
(the installed QGC fork) produces the same failure as the MAVLink harness.

The recorded yaw regressors are nearly linearly dependent. They contain too
little information to distinguish the two output-history coefficients in the
five-coefficient ARX model within the experiment budget. An independent
double-precision information-matrix calculation reproduces the large yaw
covariance. This explains the rejection mechanism without attributing it to
single-precision arithmetic, QGC, accelerated simulation, a flight-mode change,
or simultaneous test vehicles.

**This does not establish that the covariance rule contains a coding bug, that
the proposed replacement is necessary, or that raising the threshold is safe.**
The precise physical/sampling cause of the weakly identifiable yaw dynamics and
a safe minimal production fix remain unresolved. No production algorithm was
changed during this investigation.

## Reproduction controls

- Original PX4 revision: `3309f497d8fdb384f1f88cc2c989cf27286acb96`.
- Unmodified baseline executable SHA-256:
  `9eee8b1e5e49d1edd99f3740b008aee2ba0224e04728ed0cdc3755a21182b107`.
- Gazebo model revision: `bb0b9cf974acf4f1bcb5f5fcf80b88841562dea9`, matching
  the original PX4 gitlink. Stock `x500`, except the explicitly labelled inertia experiment.
- Fresh parameter storage for every run, simulation speed 1, instance 73 / system 74.
- One investigation SITL at a time, unique Gazebo partitions and private MAVLink
  ports. Default links to UDP 14550 were suppressed, including after reboot.
  Existing operator simulations and their QGC session were left running.
- Position mode throughout identification; logged roll/pitch stick inputs zero.
  Landing occurs only after the observed rejection or completed identification.
- The native GUI test used an isolated BasPlanner process, settings, display and
  UDP port. Its Start AutoTune button sent the command; the observer sent no
  Autotune command. This tests the installed QGC fork, not a separate upstream
  QGroundControl release.

All original-control runs use the original acceptance threshold, minimum axis
duration and 20-second timeout. Diagnostic builds only add an existing
`debug_array` publication at every RLS update, except the two explicitly labelled
yaw-amplitude modifications. No new uORB message types were introduced.

## Experiments

Covariance is the largest diagonal entry at the last logged yaw sample. Original
acceptance requires **all five entries below 50**, after at least five seconds.
Saturation is the percentage of logged yaw-window allocator samples reporting
`torque_setpoint_achieved == false`; it is not an exact continuous-time duty cycle.

| Case | Change from original | Last maximum covariance | Saturation samples | Identification result |
| --- | --- | ---: | ---: | --- |
| `original-1x` | None; MAVLink harness starts tune | 1616.79 | 0% | Yaw timeout |
| `qgc-1x-final` | Start from actual GUI | 1630.57 | 0% | Yaw timeout; GUI shows Failed |
| `no-yaw-filter` | `MC_YAW_TQ_CUTOFF=0` | 719.81 | 0% | Yaw timeout |
| `double-amplitude` | `MC_AT_SYSID_AMP=1.4` instead of 0.7, all axes | 847.65 | 0% | Yaw timeout |
| `rls-trace` | RLS diagnostic logging only | 1588.83 | 0% | Yaw timeout |
| `small-rotor-inertia` | Rotor inertia tensors multiplied by 0.01 | 1050.94 | 0% | Yaw timeout |
| `yaw-amplitude3` | Only yaw identification signal multiplied by 3 | 63.17 | 6% | Yaw timeout |
| `yaw-amplitude8` | Only yaw identification signal multiplied by 8 | 9.20 | 44% | Passes original identification |

The inertia and yaw-only amplitude experiments use `MC_AT_APPLY=0` to separate
identification from application. Their candidate gains were **not applied**.
The 8x run's subsequent hover therefore uses the original gains; it is not proof
of a successful flight using the candidate controller. The maximum absolute
unallocated yaw torque was 0.7103 in that run, versus below 1e-7 in the baseline.
The original estimator uses requested torque, so saturation makes that input
inconsistent with what the actuators can deliver. A small covariance under these
conditions cannot establish a valid linear plant model.

All eight completed experiments left the 15 audited gain parameters unchanged.
For the baseline, native GUI, double-amplitude, diagnostic-trace, 3x and 8x cases,
the harness also verified that the unchanged values survived a PX4 restart.
The no-filter and small-inertia experiments were initially configured to expect
success: their result files say `failed / Unexpected autotune rejection`, with
unchanged gains. They did not reach the restart audit; these are negative
identification results, not successful end-to-end tests.

Each experimental setting was run once in this investigation. The baseline has
two independent starts (harness and native GUI), plus the diagnostic-only repeat.
This is a mechanism investigation, not an estimate of a fleet failure rate.

![Original covariance and yaw experiments](../../../build/autotune-yaw-diagnosis/covariance.png)

## Why the original rule rejects yaw

With scaled filtered torque `u` and filtered rate `y`, the RLS regressor is:

```text
phi[k] = [-y[k-1], -y[k-2], u[k-1], u[k-2], u[k-3]]
```

The model update interval is approximately 8 ms. The original forgetting factor
is `lambda = 1 - dt / 60`, and the initial covariance is `10000 I`.
The independent replay computes the inverse weighted information matrix:

```text
P[n] = inverse(lambda^n / 10000 * I
               + sum(lambda^(n-1-k) * phi[k] * transpose(phi[k])))
```

It uses float64 arithmetic and omits the initial five recorded samples to avoid
unknown pre-axis delay-line history. It is an approximate independent replay,
not a bit-for-bit copy of PX4's recursive update. For yaw, the dominant values
match within 0.05%:

| Yaw covariance diagonal | a1 | a2 | b0 | b1 | b2 |
| --- | ---: | ---: | ---: | ---: | ---: |
| PX4, final high-rate diagnostic sample | 1585.303 | 1578.339 | 9.898 | 38.389 | 11.908 |
| Independent float64 replay | 1584.639 | 1577.589 | 9.894 | 38.387 | 11.907 |

These samples are published more frequently than the ordinary Autotune status,
explaining the small difference from the preceding experiment table.

The weakest singular value of the full regressor matrix is 0.2354 for roll,
0.2422 for pitch and **0.01696 for yaw**, despite yaw having roughly four times
as many samples. Its weak direction is approximately
`[-0.708, 0.706, -0.006, 0.013, -0.026]`: the two output-history terms are hard
to separate. The full yaw matrix condition number is about 1887. These values
use the original input scaling and are not universal acceptance thresholds.

As another diagnostic, fitting on the first half of each trace and predicting
the second half using measured output history gives:

| Axis | One output term, two input terms: RMS error | Two output terms, three input terms: RMS error |
| --- | ---: | ---: |
| Roll | 0.015882 | 0.000858 |
| Pitch | 0.015522 | 0.000824 |
| Yaw | 0.000482 | 0.000453 |

Under this particular excitation, yaw looks nearly first order in a one-step
prediction. This is closed-loop data, and using measured output history can hide
model errors. It does not prove that a first-order model is sufficient for
controller synthesis, free-response prediction, or other vehicles.

## What the experiments do and do not settle

- Removing the 2 Hz yaw torque filter improves the covariance but does not make
  identification pass. That filter is not a sufficient explanation by itself.
- Doubling excitation without observed saturation also does not make it pass.
  Simply increasing amplitude is not an established fix.
- Reducing rotor inertias did not eliminate the failure. The experiment does not
  identify rotor angular-momentum effects as the sole cause.
- Stronger excitation can make the old covariance test pass while the requested
  torque is not achievable. This is a reason to examine excitation and input
  validity before weakening acceptance. It does not prove candidate instability.
- Stock runs fail while still identifying yaw. The separate completion,
  cancellation/rollback and NaN fixes cannot explain this failure sequence.
- No conclusion about a 1000 kg aircraft, arbitrary inertia, noise, delay or
  coupled axes follows from these X500 experiments.

## Next engineering step

Keep PR #28706 in draft and present the reproducible failure mechanism before
proposing another acceptance algorithm. The bounded next question is whether
an actuator-feasible input can identify the yaw dynamics needed by the existing
controller design, or whether the selected model/sample scale is unnecessarily
hard to identify for this plant. That requires independent torque/rate data and
prediction or controller-response checks, not merely a successful status.

A minimal patch should follow a demonstrated cause: for example, an excitation
change if it supplies the missing information without saturation. Changing the
model order or sample scale needs its own dynamic-response evidence. Raising
50, copying the FW rule, extending timeout, or fixing yaw amplitude at 8x is not
justified by these results. No such production change was made.

## Local evidence and reproduction

[Evidence directory](../../../build/autotune-yaw-diagnosis/) contains retained
ULogs, parameter audits, manifests, console logs, diagnostic patches, the native
GUI screenshot, exact trace arrays, plots, and scripts. It is ignored by Git.
[results.json](../../../build/autotune-yaw-diagnosis/results.json) summarizes
the flight logs; [replay.json](../../../build/autotune-yaw-diagnosis/replay.json)
contains the independent calculations.

Baseline reproduction, using the retained unmodified executable:

```sh
/tmp/px4-autotune-investigation/venv/bin/python test/test_sitl_autotune.py \
  --binary /tmp/autotune-yaw-diagnosis-original-1x/firmware/bin/px4 \
  --output /tmp/autotune-original-new-run --instance 73 --speed-factor 1 \
  --expect-failure
```

Run only when that instance is unused. The output directory must not already
exist. Recalculate retained evidence with:

```sh
/tmp/px4-autotune-investigation/venv/bin/python build/autotune-yaw-diagnosis/analyze.py
```

Temporary source instrumentation in the original worktree was restored and the
original source rebuilt. Experimental executables remain in their isolated run
directories. The main branch's production algorithm was left unchanged.
