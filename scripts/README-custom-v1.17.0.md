# Custom firmware based on PX4 v1.17.0

Base: `v1.17.0` (`d6f12ad1c4`). Local changes: the 16 commits after
`ca1d36476a` through `1c10345888` on the user's `main`.

Build from the original repository (its `main` is unchanged):

```sh
./scripts/build.sh --ref custom-v1.17.0 --target holybro_kakuteh7_default --action build
```

Includes serial SWAP, the RC-input disable fix for MAVLink ELRS, the Kakute
configuration, experimental autotune, build/configuration tools, and SITL
reboot changes. Experimental autotune remains enabled for Kakute as on `main`.
This is customized firmware on a release base, not an official PX4 release.
Git may identify it as `1.17.0` development/custom because it has local commits.

Release compatibility:

- Keep the release submodule revisions, including NuttX and GPSDrivers.
- Adapt autotune to the release ModuleBase/filter APIs and `MC_AT_START` /
  `FW_AT_START` interface used by its MAVLink receiver. Keep VTOL ownership
  checks. The release has no `MC_REF_FF` feature, so that check is unnecessary.
- Backport GPS build options from upstream `5184d66239` and CRSF diagnostic
  injection from `bb72088ff6`, adapting them to the release drivers.
- Keep the release bootloader binary and configuration; a locally rebuilt
  NuttX 12 bootloader is not a prerequisite for these application changes.
- Adapt the saved Kakute profile to the release kernel and its historical
  `CONFIG_NUM_MISSION_ITMES_SUPPORTED` spelling.
- Disable the copied upstream-update helper: merging `upstream/main` would
  replace the intended release base with development code.

Firmware builds do not restore runtime parameters or SD-card startup files.
Keep your existing ELRS/Yaapu configuration and `SER_GPS1_SWAP=1` for the
verified GPS wiring. Changing a `SER_*_SWAP` parameter requires a reboot.

Validation: Kakute H7 firmware and SITL builds; 15 build-tool tests, seven
serial-generation tests, four autotune/VTOL/controller-validation suites;
isolated SITL reboot regression (three reboots, parameter persistence,
MAVLink reconnection, armed/bootloader rejection). No hardware flashing or
flight validation was performed for this branch.
