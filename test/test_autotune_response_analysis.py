"""Validate gain/phase recovery and rejection of incomplete measurement periods."""
import unittest
import numpy as np
from autotune_response_analysis import coefficients, gain_crossings


class ResponseAnalysisTest(unittest.TestCase):
    def test_irregular_timestamps_and_missing_samples_preserve_gain_phase(self):
        generator=np.random.default_rng(721)
        harmonics=np.array([1,2,3,8,13,34,55,89])
        period=8.;start=17000000.
        t=np.arange(0,period,.004)+generator.uniform(0,.0008,2000)
        keep=generator.random(len(t))>.07
        timestamp=np.round(start+t[keep]*1e6)
        t=(timestamp-start)*1e-6
        expected=generator.normal(size=len(harmonics))+1j*generator.normal(size=len(harmonics))
        phase=2*np.pi*t[:,None]*harmonics[None,:]/period
        values=3+.2*t+np.sum(expected.real*np.cos(phase)-expected.imag*np.sin(phase),axis=1)
        data=np.rec.fromarrays([timestamp,values,values*3-4],names=['sample_timestamp','output','second_output'])
        measured=coefficients(data,'sample_timestamp','output',start,period,0,harmonics)
        np.testing.assert_allclose(measured,expected,rtol=1e-11,atol=1e-11)
        together=coefficients(data,'sample_timestamp',['output','second_output'],start,period,0,harmonics)
        np.testing.assert_allclose(together,np.column_stack([expected,3*expected]),rtol=1e-11,atol=1e-11)

    def test_incomplete_period_is_not_used_as_valid_measurement(self):
        data=np.rec.fromarrays([np.arange(500)*4000,np.ones(500)],names=['sample_timestamp','output'])
        with self.assertRaisesRegex(ValueError,'Incomplete period'):
            coefficients(data,'sample_timestamp','output',0,8,0)

    def test_margin_does_not_inherit_noisy_low_frequency_phase_wraps(self):
        loop=np.array([8,5,3,2,.5])*np.exp(1j*np.radians([-10,150,-50,-100,-140]))
        crossing=gain_crossings(np.array([.01,.02,.04,1,2]),loop)
        self.assertEqual(len(crossing),1)
        self.assertAlmostEqual(crossing[0]['phase_margin_deg'],60)
        self.assertAlmostEqual(crossing[0]['frequency_hz'],np.sqrt(2))

    def test_margin_across_negative_real_axis_retains_negative_sign(self):
        loop=np.array([2,.5])*np.exp(1j*np.radians([-175,155]))
        self.assertAlmostEqual(gain_crossings(np.array([1,2]),loop)[0]['phase_margin_deg'],-10)


if __name__=='__main__':
    unittest.main()
