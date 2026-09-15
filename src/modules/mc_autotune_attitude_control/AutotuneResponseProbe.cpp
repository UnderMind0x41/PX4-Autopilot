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

// SITL research instrumentation. Does not calculate or apply parameter gains.
#include <px4_platform_common/px4_config.h>
#if defined(CONFIG_COMMON_SIMULATION)

#include <px4_platform_common/log.h>
#include <px4_platform_common/posix.h>
#include <drivers/drv_hrt.h>
#include <matrix/matrix/math.hpp>
#include <uORB/Publication.hpp>
#include <uORB/Subscription.hpp>
#include <uORB/topics/autotune_attitude_control_status.h>
#include <uORB/topics/control_allocator_status.h>
#include <uORB/topics/debug_vect.h>
#include <uORB/topics/vehicle_angular_velocity.h>
#include <uORB/topics/vehicle_attitude.h>
#include <uORB/topics/vehicle_local_position.h>
#include <uORB/topics/vehicle_rates_setpoint.h>
#include <uORB/topics/vehicle_status.h>
#include <uORB/topics/vehicle_torque_setpoint.h>
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <cstring>

int autotune_response_probe(int argc, char *argv[]);

int autotune_response_probe(int argc, char *argv[])
{
	if (argc != 7 && argc != 8) {
		PX4_ERR("probe <axis 0..2> <period s> <periods> <component amplitude rad/s> <phase offset rad> <csv>");
		return PX4_ERROR;
	}

	const int axis = atoi(argv[1]);
	const bool torque_excitation = !strcmp(argv[0], "torque_probe");
	const float period = strtof(argv[2], nullptr);
	const int periods = atoi(argv[3]);
	const float amplitude = strtof(argv[4], nullptr);
	const float phase_offset = strtof(argv[5], nullptr);
	const float max_frequency = argc == 8 ? strtof(argv[7], nullptr) : 144.f / period;

	if (axis < 0 || axis > 2 || !std::isfinite(period) || period < 4.f || period > 256.f
	    || periods < 4 || periods > 32 || !std::isfinite(amplitude) || amplitude <= 0.f || amplitude > 0.15f
	    || !std::isfinite(phase_offset) || !std::isfinite(max_frequency)
	    || max_frequency < 1.f / period || max_frequency > 100.f) {
		return PX4_ERROR;
	}

	uORB::Subscription gyro_sub{ORB_ID(vehicle_angular_velocity)};
	uORB::Subscription torque_sub{ORB_ID(vehicle_torque_setpoint)};
	uORB::Subscription attitude_sub{ORB_ID(vehicle_attitude)};
	uORB::Subscription position_sub{ORB_ID(vehicle_local_position)};
	uORB::Subscription rates_sub{ORB_ID(vehicle_rates_setpoint)};
	uORB::Subscription status_sub{ORB_ID(vehicle_status)};
	uORB::Subscription allocation_sub{ORB_ID(control_allocator_status)};
	uORB::Subscription applied_sub{ORB_ID(debug_vect)};
	uORB::Publication<autotune_attitude_control_status_s> excitation_pub{ORB_ID(autotune_attitude_control_status)};
	vehicle_status_s status{};

	if (!status_sub.copy(&status) || status.arming_state != vehicle_status_s::ARMING_STATE_ARMED
	    || status.nav_state != vehicle_status_s::NAVIGATION_STATE_POSCTL) {
		PX4_ERR("probe requires armed Position mode");
		return PX4_ERROR;
	}

	FILE *file = fopen(argv[6], "w");

	if (!file) {
		PX4_ERR("cannot open probe CSV");
		return PX4_ERROR;
	}

	fprintf(file,
		"timestamp,start,excitation,torque_timestamp,gyro_timestamp,gyro_sample,torque,rate,acceleration,rate_sp,roll,pitch,z,allocation_timestamp,torque_achieved,thrust_achieved,u0,u1,u2,y0,y1,y2,a0,a1,a2,applied_timestamp,e0,e1,e2\n");
	int harmonics[64] {1, 2};
	int components = max_frequency * period >= 2.f ? 2 : 1;
	const int max_harmonic = int(max_frequency * period);

	while (components >= 2 && components < 63) {
		const int next = argc == 8 ? math::max(harmonics[components - 1] + 1, int(1.2f * harmonics[components - 1] + .5f))
				 : harmonics[components - 1] + harmonics[components - 2];

		if (next > max_harmonic) {
			break;
		}

		harmonics[components] = next;
		++components;
	}

	if (harmonics[components - 1] < max_harmonic) {
		harmonics[components++] = max_harmonic;
	}

	constexpr float two_pi = 2.f * M_PI_F;
	const hrt_abstime start = hrt_absolute_time();
	hrt_abstime last_gyro = start;
	int result = PX4_OK;
	PX4_INFO("response probe axis %d, period %.3f s, %d periods", axis, (double)period, periods);

	while (true) {
		const hrt_abstime now = hrt_absolute_time();
		const float elapsed = (now - start) * 1e-6f;

		if (elapsed >= period * periods) {
			break;
		}

		vehicle_angular_velocity_s gyro{};

		if (!gyro_sub.update(&gyro)) {
			if (now - last_gyro > 500000) {
				PX4_ERR("response probe lost gyro updates");
				result = PX4_ERROR;
				break;
			}

			px4_usleep(500);
			continue;
		}

		last_gyro = now;

		vehicle_torque_setpoint_s torque{};
		vehicle_attitude_s attitude{};
		vehicle_local_position_s position{};
		vehicle_rates_setpoint_s rates{};
		control_allocator_status_s allocation{};
		debug_vect_s applied{};
		torque_sub.copy(&torque);
		attitude_sub.copy(&attitude);
		position_sub.copy(&position);
		rates_sub.copy(&rates);
		allocation_sub.copy(&allocation);
		applied_sub.copy(&applied);

		if (memcmp(applied.name, "AT_TORQUE", 10) != 0) {
			applied = {};
		}

		status_sub.copy(&status);
		const matrix::Eulerf angles{matrix::Quatf{attitude.q}};

		if (status.arming_state != vehicle_status_s::ARMING_STATE_ARMED
		    || status.nav_state != vehicle_status_s::NAVIGATION_STATE_POSCTL
		    || fabsf(angles.phi()) > M_PI_F / 4.f || fabsf(angles.theta()) > M_PI_F / 4.f) {
			PX4_ERR("response probe interrupted");
			result = PX4_ERROR;
			break;
		}

		float excitation = 0.f;

		for (int k = 0; k < components; ++k) {
			// Schroeder phases reduce the crest factor of the simultaneous tones.
			const float phase = M_PI_F * k * (k - 1) / components + phase_offset * (k + 1);
			excitation += amplitude * sinf(two_pi * harmonics[k] * elapsed / period + phase);
		}

		autotune_attitude_control_status_s probe{};
		probe.timestamp = now;
		probe.state = axis == 0 ? probe.STATE_ROLL : (axis == 1 ? probe.STATE_PITCH : probe.STATE_YAW);

		if (torque_excitation) {
			probe.state = probe.STATE_VERIFICATION;
		}

		probe.rate_sp[axis] = excitation;
		excitation_pub.publish(probe);
		fprintf(file, "%llu,%llu,%.9g,%llu,%llu,%llu,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%llu,%u,%u",
			(unsigned long long)now, (unsigned long long)start, (double)excitation,
			(unsigned long long)torque.timestamp, (unsigned long long)gyro.timestamp,
			(unsigned long long)gyro.timestamp_sample, (double)torque.xyz[axis], (double)gyro.xyz[axis],
			(double)gyro.xyz_derivative[axis], (double)(axis == 0 ? rates.roll : (axis == 1 ? rates.pitch : rates.yaw)),
			(double)angles.phi(), (double)angles.theta(), (double)position.z,
			(unsigned long long)allocation.timestamp, allocation.torque_setpoint_achieved, allocation.thrust_setpoint_achieved);

		for (int j = 0; j < 3; ++j) { fprintf(file, ",%.9g", (double)torque.xyz[j]); }

		for (int j = 0; j < 3; ++j) { fprintf(file, ",%.9g", (double)gyro.xyz[j]); }

		for (int j = 0; j < 3; ++j) { fprintf(file, ",%.9g", (double)gyro.xyz_derivative[j]); }

		fprintf(file, ",%llu,%.9g,%.9g,%.9g\n", (unsigned long long)applied.timestamp,
			(double)applied.x, (double)applied.y, (double)applied.z);
	}

	autotune_attitude_control_status_s idle{};
	idle.timestamp = hrt_absolute_time();
	idle.state = idle.STATE_IDLE;
	excitation_pub.publish(idle);
	fclose(file);
	PX4_INFO("response probe finished: %d", result);
	return result;
}
#endif
