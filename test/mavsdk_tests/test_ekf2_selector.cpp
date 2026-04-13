/****************************************************************************
 *
 *   Copyright (c) 2026 PX4 Development Team. All rights reserved.
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

/**
 * @file test_ekf2_selector.cpp
 *
 * Regression test for EKF2 multi-instance selector bug (#27013).
 *
 * When one IMU has accel clipping (triggering bad_acc_clipping on its EKF
 * instance) and the other EKF instance has a baro fault with a diverged
 * Z state, the selector should not whipsaw between them causing altitude
 * spikes.
 *
 * Requires SIH with dual-IMU support (EKF2_MULTI_IMU=2) and per-IMU
 * fault injection (SIH_FAULT_IMU / SIH_FAULT_VIBE).
 */

#include "autopilot_tester.h"

#include <iostream>

TEST_CASE("EKF2 selector - altitude stable under single IMU clipping", "[ekf2_selector]")
{
	// The vehicle should hold altitude within this tolerance even when one
	// IMU is clipping. If the selector whipsaws to a diverged instance,
	// altitude deviations will far exceed this.
	const float takeoff_altitude = 20.f;
	const float altitude_tolerance = 5.f;

	AutopilotTester tester;
	tester.connect(connection_url);
	tester.wait_until_ready();

	tester.set_takeoff_altitude(takeoff_altitude);
	tester.store_home();
	tester.sleep_for(std::chrono::seconds(1));

	// Takeoff and stabilize
	tester.arm();
	tester.takeoff();
	tester.wait_until_hovering();
	tester.wait_until_altitude(takeoff_altitude, std::chrono::seconds(30));

	// Start monitoring altitude for spikes
	tester.start_checking_altitude(altitude_tolerance);

	// Let it hover clean for a few seconds as baseline
	tester.sleep_for(std::chrono::seconds(5));

	// Inject accel clipping on IMU 1 (param value 2 = second IMU).
	// 200 m/s^2 amplitude exceeds the ~157 m/s^2 clip limit and triggers
	// bad_acc_clipping on EKF instance 1.
	std::cout << time_str() << "Injecting accel clipping on IMU 1" << std::endl;
	tester.set_param_int("SIH_FAULT_IMU", 2);
	tester.set_param_float("SIH_FAULT_VIBE", 200.f);

	// Hold for 30 seconds with the fault active.
	// The selector should keep using the healthy EKF instance (0) and
	// altitude should remain stable. If the selector whipsaws, we will
	// see altitude excursions well beyond the tolerance.
	tester.sleep_for(std::chrono::seconds(30));

	// Remove the fault
	std::cout << time_str() << "Removing accel clipping fault" << std::endl;
	tester.set_param_int("SIH_FAULT_IMU", 0);
	tester.set_param_float("SIH_FAULT_VIBE", 0.f);

	tester.sleep_for(std::chrono::seconds(5));
	tester.stop_checking_altitude();

	// Land
	tester.land();
	tester.wait_until_disarmed(std::chrono::seconds(60));
}
