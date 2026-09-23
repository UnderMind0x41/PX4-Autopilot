/****************************************************************************
 *
 *   Copyright (c) 2020-2021 PX4 Development Team. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 * 1. Redistributions of source code must retain the above copyright
 *    notice, this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in
 *    the documentation and/or other materials provided with the
 *    distribution.
 * 3. Neither the name PX4 nor the names of its contributors may be
 *    used to endorse or promote products derived from this software
 *    without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 * FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
 * INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
 * BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
 * OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
 * AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
 * LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
 * ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 *
 ****************************************************************************/

#include "IST8310.hpp"

#include <px4_platform_common/getopt.h>
#include <px4_platform_common/module.h>

void IST8310::print_usage()
{
	PRINT_MODULE_USAGE_NAME("ist8310", "driver");
	PRINT_MODULE_USAGE_SUBCATEGORY("magnetometer");
	PRINT_MODULE_DESCRIPTION("Without -a, start probes I2C addresses 0x0e and 0x0c.");
	PRINT_MODULE_USAGE_COMMAND("start");
	PRINT_MODULE_USAGE_PARAMS_I2C_SPI_DRIVER(true, false);
	PRINT_MODULE_USAGE_PARAMS_I2C_ADDRESS(0x0E);
	PRINT_MODULE_USAGE_PARAM_INT('R', 0, 0, 35, "Rotation", true);
	PRINT_MODULE_USAGE_DEFAULT_COMMANDS();
}

extern "C" int ist8310_main(int argc, char *argv[])
{
	int ch;
	using ThisDriver = IST8310;
	BusCLIArguments cli{true, false};
	cli.i2c_address = I2C_ADDRESS_DEFAULT;
	cli.default_i2c_frequency = I2C_SPEED;

	while ((ch = cli.getOpt(argc, argv, "R:")) != EOF) {
		switch (ch) {
		case 'R':
			cli.rotation = (enum Rotation)atoi(cli.optArg());
			break;
		}
	}

	const char *verb = cli.optArg();

	if (!verb) {
		ThisDriver::print_usage();
		return -1;
	}

	if (!strcmp(verb, "start")) {
		const bool probe_both_addresses = !cli.i2c_address_explicit && cli.bus_option != I2CSPIBusOption::All;
		const bool quiet_start = cli.quiet_start;

		if (probe_both_addresses) {
			// Report failure only after both addresses have been tried.
			cli.quiet_start = true;
		}

		int default_result;

		{
			BusInstanceIterator iterator(MODULE_NAME, cli, DRV_MAG_DEVTYPE_IST8310);
			default_result = ThisDriver::module_start(cli, iterator);
		}

		if (!probe_both_addresses) {
			return default_result;
		}

		cli.i2c_address = I2C_ADDRESS_ALTERNATE;
		BusInstanceIterator alternate_iterator(MODULE_NAME, cli, DRV_MAG_DEVTYPE_IST8310);
		const int alternate_result = ThisDriver::module_start(cli, alternate_iterator);

		if (default_result != 0 && alternate_result != 0 && !quiet_start) {
			PX4_WARN("no IST8310 found at I2C address 0x0e or 0x0c");
		}

		return (default_result == 0 || alternate_result == 0) ? 0 : -1;
	}

	BusInstanceIterator iterator(MODULE_NAME, cli, DRV_MAG_DEVTYPE_IST8310);

	if (!strcmp(verb, "stop")) {
		return ThisDriver::module_stop(iterator);
	}

	if (!strcmp(verb, "status")) {
		return ThisDriver::module_status(iterator);
	}

	ThisDriver::print_usage();
	return -1;
}
