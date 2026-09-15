"""Check return-difference algebra against independently assembled feedback loops."""
import unittest
import numpy as np
from autotune_controller_replacement import return_difference, evaluate


class ReplacementTest(unittest.TestCase):
    def test_coupled_vehicle_and_outer_gain_changes(self):
        # Noncommuting plant and outer-controller matrices catch orientation errors.
        for w in np.geomspace(.01,100,50):
            s=1j*w
            plant=np.array([[1,.12,.03],[-.04,1.2,.05],[.06,.02,.4]])/(s*(1+.1*s))
            outer=np.array([[2,.1,0],[.03,1.8,.02],[0,.04,.7]])/s
            cp0=np.array([.2+.1/s,.3+.08/s,.25+.03/s])
            cpn=np.array([.25+.08/s,.24+.07/s,.3+.04/s])
            d0=np.array([.002,.003,.001]);dn=np.array([.004,.002,.0015])
            att0=np.array([2,1.8,.7]);attn=np.array([2.3,1.5,1.1])
            c0=np.diag(cp0)@outer+np.diag(cp0+d0*s)
            cn=np.diag(cpn*attn/att0)@outer+np.diag(cpn+dn*s)
            S=np.linalg.inv(np.eye(3)+c0@plant)
            H=plant@S;A=s*H
            expected=(np.eye(3)+cn@plant)@S
            computed=return_difference(S,H,A,cp0,d0,att0,cpn,dn,attn)
            np.testing.assert_allclose(computed,expected,rtol=1e-11,atol=1e-11)
            np.testing.assert_allclose(S@np.linalg.inv(computed),np.linalg.inv(np.eye(3)+cn@plant),rtol=1e-11,atol=1e-11)

    def test_unchanged_controller_has_identity_return_difference(self):
        generator=np.random.default_rng(904)
        matrices=[generator.normal(size=(3,3))+1j*generator.normal(size=(3,3)) for _ in range(3)]
        cp=np.array([1+2j,2+3j,3+4j]);d=np.array([.1,.2,.3]);att=np.array([1,2,3])
        np.testing.assert_array_equal(return_difference(*matrices,cp,d,att,cp,d,att),np.eye(3))

    def test_independent_repeat_exposes_bias_hidden_by_identical_periods(self):
        gains={f'MC_{axis}RATE_{term}': value for axis in ['ROLL','PITCH','YAW']
               for term,value in [('P',1.),('I',0.),('D',0.),('K',1.)]}
        gains.update({f'MC_{axis}_P':1. for axis in ['ROLL','PITCH','YAW']})
        candidate=gains.copy(); candidate['MC_ROLLRATE_P']=2.
        shape=(4,2,3,3)
        m=dict(S=np.broadcast_to(np.eye(3)*.5,shape),H=np.zeros(shape),A=np.zeros(shape),
               frequencies=np.array([1.,2.]),dt=.004)
        repeat=dict(m,S=np.broadcast_to(np.eye(3)*2.5,shape))
        config={'MC_YAW_TQ_CUTOFF':0}
        self.assertTrue(np.all(evaluate(m,gains,candidate,config)['empirical_lower_bound']>0))
        self.assertTrue(np.all(evaluate(m,gains,candidate,config,[repeat])['empirical_lower_bound']<0))
        candidate['MC_ROLLRATE_I']=.1
        with self.assertRaisesRegex(ValueError,'integral state'):
            evaluate(m,gains,candidate,config)


if __name__=='__main__':
    unittest.main()
