import copy
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import cv2
import numpy as np
from Src.calibration.two_rod_calibration import (IDS,rod_target,fit_two_rod_rig,
    coverage_report,suggest_points,save_two_rod_rig)
from Src.calibration.chessboard_extrinsic import load_rig


def fixture():
    K=np.array([[1400.,0,960],[0,1400.,540],[0,0,1.]])
    cameras={r:{'intrinsic_matrix':K.tolist(),'distortion_coefficients':[0.]*5,
                'image_size':[1920,1080]} for r in ('cf','cs')}
    R=cv2.Rodrigues(np.array([.015,-.15,.01]))[0]; t=np.array([650.,10,25.])
    rng=np.random.default_rng(58); pairs=[]
    for i in range(20):
        rods=[]
        for j,gaps in enumerate(rod_target()['center_spacings_mm']):
            direction=np.array([rng.uniform(-.7,.7),1.,rng.uniform(-.7,.7)])
            direction/=np.linalg.norm(direction)
            center=rng.uniform([-700,-500,2200],[300,0,4500])+[j*350,0,0]
            rods.append(center+np.r_[0,np.cumsum(gaps)][:,None]*direction)
        xyz=np.concatenate(rods); pair={'id':str(i),'confirmed':True}
        for role,rotation,translation in [('cf',np.eye(3),np.zeros(3)),('cs',R,t)]:
            p=cv2.projectPoints(xyz,cv2.Rodrigues(rotation)[0],translation,K,np.zeros(5))[0].reshape(-1,2)
            pair[role]={'image_size':[1920,1080],'points':dict(zip(IDS,p.tolist()))}
        pairs.append(pair)
    return cameras,pairs,R,t


class BackendTests(unittest.TestCase):
    def test_metric_scale_and_existing_loader(self):
        cameras,pairs,R,t=fixture()
        result=fit_two_rod_rig(cameras,rod_target(),pairs)
        np.testing.assert_allclose(result['R_cs_from_cf'],R,atol=1e-4)
        np.testing.assert_allclose(result['t_cs_from_cf_mm'],t,atol=.1)
        self.assertTrue(all(p['accepted'] for p in result['candidate_diagnostics']))
        self.assertFalse(set(result['training_pairs'])&set(result['held_out_pairs']))
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'stereo.json'; save_two_rod_rig(path,result)
            self.assertEqual(load_rig(path)['method'],'8point')

    def test_rejects_missing_confirmation_few_samples_and_wrong_scale(self):
        cameras,pairs,_,_=fixture()
        with self.assertRaises(ValueError):fit_two_rod_rig(cameras,rod_target(),pairs[:14])
        pairs[0]['confirmed']=False
        with self.assertRaises(ValueError):fit_two_rod_rig(cameras,rod_target(),pairs)
        pairs[0]['confirmed']=True
        for pair in pairs[::5]:
            pair['cs']['points']={k:list(v) for k,v in pairs[1]['cs']['points'].items()}
        with self.assertRaises(ValueError):fit_two_rod_rig(cameras,rod_target(),pairs)

    def test_detector_identifies_rod_groups_without_silent_confirmation(self):
        cameras,_,_,_=fixture(); image=np.zeros((1080,1920,3),np.uint8)
        color=tuple(int(x) for x in cv2.cvtColor(np.uint8([[[150,240,240]]]),cv2.COLOR_HSV2BGR)[0,0])
        expected={}
        for j,gaps in enumerate(rod_target()['center_spacings_mm']):
            for i,y in enumerate(180+np.r_[0,np.cumsum(gaps)]*.6):
                p=[500+600*j,float(y)]; expected[IDS[j*4+i]]=p
                cv2.circle(image,tuple(np.round(p).astype(int)),10,color,-1)
        result=suggest_points(image,cameras['cf'],rod_target())
        self.assertEqual(result['candidate_count'],8)
        self.assertEqual(set(result['points']),set(IDS))
        np.testing.assert_allclose(list(result['points'].values()),list(expected.values()),atol=1)

    def test_coverage_is_data_driven_and_exclusions_do_not_count(self):
        cameras,pairs,_,_=fixture()
        empty=coverage_report([],cameras)
        self.assertEqual(len(empty['cf']['missing_positions']),9)
        observed=coverage_report(pairs,cameras)
        self.assertLess(len(observed['cf']['missing_positions']),9)
        for p in pairs:p['excluded']=True
        self.assertEqual(coverage_report(pairs,cameras),empty)

    def test_noisy_points_recover_metric_rig_and_static_repeats_are_rejected(self):
        cameras,pairs,R,t=fixture()
        rng=np.random.default_rng(129)
        for pair in pairs:
            for r in ('cf','cs'):
                for key in IDS:
                    pair[r]['points'][key]=(np.array(pair[r]['points'][key])+rng.normal(0,.15,2)).tolist()
        result=fit_two_rod_rig(cameras,rod_target(),pairs)
        np.testing.assert_allclose(result['R_cs_from_cf'],R,atol=.005)
        np.testing.assert_allclose(result['t_cs_from_cf_mm'],t,atol=10.)
        _,source,_,_=fixture()
        repeated=[dict(copy.deepcopy(source[0]),id=str(i)) for i in range(15)]
        with self.assertRaises(ValueError):fit_two_rod_rig(cameras,rod_target(),repeated)


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from Src.UI_Control import calibrate_two_rods as gui
        cls.gui=gui; cls.app=gui.QApplication.instance() or gui.QApplication([])

    def test_fifteen_pair_import_runs_extractor_and_requires_confirmation(self):
        g=self.gui; cameras,_,_,_=fixture()
        with tempfile.TemporaryDirectory() as directory, patch.object(g.calib_store,'CALIB_DIR',directory), \
                patch.object(g.TwoRodWindow,'_load_defaults'):
            w=g.TwoRodWindow(); w.fail=Mock()
            try:
                w.set_intrinsics(cameras,'test.json'); w.start_session()
                source=Path(directory)/'photos'
                image=np.zeros((1080,1920,3),np.uint8)
                color=tuple(int(x) for x in cv2.cvtColor(np.uint8([[[150,240,240]]]),cv2.COLOR_HSV2BGR)[0,0])
                for j,gaps in enumerate(rod_target()['center_spacings_mm']):
                    for y in 180+np.r_[0,np.cumsum(gaps)]*.6:
                        cv2.circle(image,(500+600*j,int(y)),10,color,-1)
                for r in ('cf','cs'):
                    (source/r).mkdir(parents=True)
                    for i in range(15):cv2.imwrite(str(source/r/(str(i)+'.png')),image)
                with patch.object(g.QFileDialog,'getExistingDirectory',return_value=str(source)):
                    w.import_pairs()
                deadline=time.monotonic()+30
                while w.job is not None and time.monotonic()<deadline:
                    self.app.processEvents(); time.sleep(.01)
                self.assertIsNone(w.job)
                self.assertEqual(len(w.pairs),15)
                self.assertTrue(all(set(p['cf']['points'])==set(IDS) for p in w.pairs))
                self.assertFalse(any(p.get('confirmed') for p in w.pairs))
                w.begin_annotation(); self.assertFalse(w.calculate_button.isEnabled())
                self.assertIn('缺位置',w.instruction.text())
                w.fail.assert_not_called()
            finally:w.close()

    def test_session_confirm_reverse_calculate_export_resume(self):
        g=self.gui; cameras,pairs,_,_=fixture()
        with tempfile.TemporaryDirectory() as directory, patch.object(g.calib_store,'CALIB_DIR',directory), \
                patch.object(g.TwoRodWindow,'_load_defaults'):
            w=g.TwoRodWindow(); w.fail=Mock()
            try:
                w.set_intrinsics(cameras,'test.json'); w.start_session()
                self.assertEqual(w.phase,'capture')
                image=np.zeros((1080,1920,3),np.uint8)
                for p in pairs:
                    for r in ('cf','cs'):
                        path=w.session/r/(p['id']+'.png'); cv2.imwrite(str(path),image)
                        p[r]['path']=str(path)
                w.pairs=pairs; w.persist(); w.begin_annotation()
                w.reverse_rod('cf','A'); self.assertFalse(w.pairs[0]['confirmed'])
                w.reverse_rod('cf','A'); w.confirm_pair()
                self.assertTrue(w.calculate_button.isEnabled())
                w.calculate(); deadline=time.monotonic()+30
                while w.job is not None and time.monotonic()<deadline:
                    self.app.processEvents(); time.sleep(.01)
                self.assertIsNone(w.job)
                self.assertEqual(w.phase,'result',w.fail.call_args)
                self.assertEqual(w.result['method'],'8point')
                session=w.session/'session.json'; w.reset(); w.resume_session(str(session))
                self.assertEqual(w.phase,'annotate',w.fail.call_args)
                self.assertEqual(len(w.pairs),20)
                self.assertEqual(w.target_definition()['center_spacings_mm'],[[200.,120.,180.],[100.,140.,210.]])
            finally:w.close()


if __name__=='__main__':unittest.main()
