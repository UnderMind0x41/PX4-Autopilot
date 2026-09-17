# Serial RX/TX swap

`SER_<port>_SWAP=1` exchanges a UART's RX/TX pins at boot, before its assigned
service starts. It defaults to `0` and requires a reboot. The setting follows
the physical port, so GNSS, MAVLink and other services using `rc.serial_port`
share it. No background task or protocol driver changes are needed.

`CONFIG_SYSTEMCMDS_SERIAL_CONFIG=y` is the default for STM32H7 firmware targets
with a ROMFS, excluding bootloaders. It requires `CONFIG_STM32H7_USART_SWAP=y`
in NuttX. Other architectures remain disabled: their UART drivers must first
be verified to preserve SWAP across close/reopen. The runtime parameters still
default to `0`, so enabling the command does not swap any pins by itself.

For example, set `SER_GPS1_SWAP=1` in QGroundControl and reboot to swap Kakute H7's
UART4 (`/dev/ttyS3`). Set it back to `0` and reboot to restore normal assignments.
If the parameter is hidden, assign a service to that port and reboot first.
Keep USB available for recovery and verify communication after reboot.

- This exchanges RX/TX functions; it does not invert signal levels or bypass
  external inverters/transceivers.
- Failed configuration is logged and prevents that port's service from starting.
- Manual driver starts bypass this policy. Apply `serial_config -d /dev/ttyS3 -s`
  before starting the service. Never invoke it on an active port; the command
  also refuses to run while armed.
- A driver that explicitly configures SWAP itself can override this setting.

Run the generator/startup tests with:

```sh
python3 -m unittest discover -s Tools/serial/tests -v
```
