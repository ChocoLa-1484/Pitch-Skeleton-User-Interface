"""Capability, persistence and live-dialog behavior without owning a camera."""
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
from threading import RLock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from Src.cameras.settings import CameraSettings
import yaml


class Number:
    def __init__(self, value, minimum=0, maximum=10, kind=1, step=1, writable=True):
        self.value, self.minimum, self.maximum = value, minimum, maximum
        self.kind, self.step, self.writable = kind, step, writable
        self.writes = []
    def GetPrincipalInterfaceType(self): return self.kind
    def GetValue(self): return self.value
    def GetMin(self): return self.minimum
    def GetMax(self): return self.maximum
    def GetInc(self): return self.step
    def GetUnit(self): return ""
    def SetValue(self, value):
        if not self.writable: raise RuntimeError("read only")
        self.writes.append(value)
        self.value = value


class Enumeration:
    writable = True
    def __init__(self, entries, value=0): self.entries, self.value = entries, value
    def GetIntValue(self): return self.value
    def SetIntValue(self, value): self.value = value
    def GetEntryByName(self, name):
        return SimpleNamespace(GetValue=lambda: self.entries[name]) if name in self.entries else None


class ColorNumber(Number):
    def __init__(self, selector):
        super().__init__(0, .1, 8)
        self.selector, self.values = selector, {0: 1.93, 1: 1.99}
    def GetValue(self): return self.values[self.selector.value]
    def SetValue(self, value):
        self.writes.append((self.selector.value, value))
        self.values[self.selector.value] = value


def fake_camera(folder, serial="front"):
    selector = Enumeration({"Red": 0, "Blue": 1}, 1)
    nodes = {"ExposureTime": Number(2000, 50, 100000), "Gain": Number(5, 0, 29),
             "BalanceRatioSelector": selector, "BalanceRatio": ColorNumber(selector),
             "ExposureAuto": Enumeration({"Off": 0, "Continuous": 1}),
             "GainAuto": Enumeration({"Off": 0}), "BalanceWhiteAuto": Enumeration({"Off": 0})}
    sdk = SimpleNamespace(intfIFloat=1, intfIInteger=2,
        IsAvailable=lambda node: node is not None, IsReadable=lambda node: node is not None,
        IsWritable=lambda node: node is not None and node.writable,
        CFloatPtr=lambda node: node, CIntegerPtr=lambda node: node, CEnumerationPtr=lambda node: node)
    path = Path(folder) / (serial + ".yaml")
    config = {"acquisition_settings": {"fps": 179}, "exposure_settings": {"exposure_time": 2000},
              "gain_settings": {"gain_value": 5},
              "white_balance_settings": {"white_balance_red_ratio": 1.93, "white_balance_blue_ratio": 1.99},
              "gpio_secondary": {"trigger_source": "Line3"}}
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    camera = SimpleNamespace(_released=False, settings_lock=RLock(), serial_number=serial,
        config_yaml_path=str(path), full_config=config,
        cam=SimpleNamespace(GetNodeMap=lambda: SimpleNamespace(GetNode=nodes.get)))
    camera.settings = CameraSettings(camera, sdk)
    return camera, nodes


class CameraSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.camera, self.nodes = fake_camera(self.temp.name)

    def test_read_capabilities_preserves_values_channel_and_file(self):
        before = Path(self.camera.config_yaml_path).read_bytes()
        states = self.camera.settings.describe()
        self.assertEqual(states["red"].value, 1.93)
        self.assertEqual(states["blue"].value, 1.99)
        self.assertFalse(states["focus"].writable)
        self.assertFalse(states["focal_length"].writable)
        self.assertIn("手動", states["focus"].reason)
        self.assertEqual(self.nodes["BalanceRatioSelector"].value, 1)
        self.assertEqual(self.nodes["BalanceRatio"].writes, [])
        self.assertEqual(before, Path(self.camera.config_yaml_path).read_bytes())
        self.assertAlmostEqual(states["exposure"].maximum, 1e6 / 179)

    def test_selected_camera_and_color_only_are_persisted(self):
        other, _ = fake_camera(self.temp.name, "side")
        before = Path(other.config_yaml_path).read_bytes()
        self.camera.settings.apply("red", 2.1)
        saved = yaml.safe_load(Path(self.camera.config_yaml_path).read_text(encoding="utf-8"))
        self.assertEqual(saved["white_balance_settings"]["white_balance_red_ratio"], 2.1)
        self.assertEqual(saved["white_balance_settings"]["white_balance_blue_ratio"], 1.99)
        self.assertEqual(saved["gpio_secondary"]["trigger_source"], "Line3")
        self.assertEqual(before, Path(other.config_yaml_path).read_bytes())
        self.assertEqual(self.nodes["BalanceRatioSelector"].value, 1)

    def test_invalid_and_unsupported_controls_never_save(self):
        before = deepcopy(self.camera.full_config)
        for key, value in (("exposure", 6000), ("gain", float("nan")), ("focus", 5), ("bad", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.camera.settings.apply(key, value)
        self.assertEqual(self.camera.full_config, before)

    def test_hardware_error_does_not_change_saved_config(self):
        before = Path(self.camera.config_yaml_path).read_bytes()
        with patch.object(self.nodes["ExposureTime"], "SetValue", side_effect=RuntimeError("offline")):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                self.camera.settings.apply("exposure", 1500)
        self.assertEqual(before, Path(self.camera.config_yaml_path).read_bytes())
        self.assertEqual(self.camera.full_config["exposure_settings"]["exposure_time"], 2000)

    def test_save_failure_rolls_back_hardware_and_auto_mode(self):
        self.nodes["ExposureAuto"].value = 1
        before = deepcopy(self.camera.full_config)
        with patch.object(self.camera.settings, "_save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.camera.settings.apply("exposure", 1500)
        self.assertEqual(self.nodes["ExposureTime"].value, 2000)
        self.assertEqual(self.nodes["ExposureAuto"].value, 1)
        self.assertEqual(self.camera.full_config, before)

    def test_integer_focus_and_read_only_focal_length(self):
        self.nodes["FocusPos"] = Number(10, 0, 100, kind=2, step=5)
        self.nodes["FocalLength"] = Number(8, 8, 8, writable=False)
        self.assertTrue(self.camera.settings.describe()["focus"].writable)
        self.assertFalse(self.camera.settings.describe()["focal_length"].writable)
        with self.assertRaises(ValueError): self.camera.settings.apply("focus", 12)
        self.camera.settings.apply("focus", 15)
        self.assertEqual(self.camera.full_config["lens_settings"], {"focus": 15, "serial_number": "front"})
        with self.assertRaises(ValueError): self.camera.settings.apply("focal_length", 8)

    def test_disconnected_camera_is_rejected(self):
        self.camera._released = True
        with self.assertRaisesRegex(RuntimeError, "關閉"): self.camera.settings.describe()
        with self.assertRaises(RuntimeError): self.camera.settings.apply("gain", 4)


class CameraSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PyQt5.QtWidgets import QApplication
        from Src.UI_Control.widgets.camera_settings import CameraSettingsDialog
        cls.app = QApplication.instance() or QApplication([])
        cls.dialog_type = CameraSettingsDialog

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.front, self.nodes = fake_camera(self.temp.name)
        self.side, _ = fake_camera(self.temp.name, "side")
        self.owner = SimpleNamespace(is_opened=True, is_auto_recording=False, video_writer=None,
            video_thread=SimpleNamespace(running=True, camera1=self.front, camera2=self.side))
        self.dialog = self.dialog_type(lambda: [("2D 相機", self.owner)])
        self.addCleanup(self.dialog.close)

    def test_switching_cameras_does_not_write_and_apply_targets_selected(self):
        before = Path(self.front.config_yaml_path).read_bytes()
        self.dialog.camera_combo.setCurrentIndex(1)
        self.assertFalse(self.dialog.rows["focus"][2].isEnabled())
        self.assertEqual(self.nodes["ExposureTime"].writes, [])
        self.dialog.rows["exposure"][1].setValue(1200)
        self.dialog.rows["exposure"][2].click()
        self.assertEqual(self.side.full_config["exposure_settings"]["exposure_time"], 1200)
        self.assertEqual(before, Path(self.front.config_yaml_path).read_bytes())

    def test_recording_and_disconnect_block_pending_edits(self):
        self.dialog.rows["gain"][1].setValue(8)
        self.owner.is_auto_recording = True
        self.dialog.apply("gain")
        self.assertEqual(self.nodes["Gain"].writes, [])
        self.assertFalse(self.dialog.rows["gain"][2].isEnabled())
        self.owner.is_auto_recording = False
        self.dialog.sync_sources()
        self.owner.is_opened = False
        self.dialog.apply("gain")
        self.assertEqual(self.nodes["Gain"].writes, [])
        self.assertEqual(self.dialog.camera_combo.count(), 0)

    def test_failed_write_is_reported_and_live_value_is_restored(self):
        self.dialog.rows["gain"][1].setValue(8)
        with patch.object(self.front.settings, "apply", side_effect=RuntimeError("device gone")):
            self.dialog.apply("gain")
        self.assertIn("device gone", self.dialog.status.text())
        self.assertEqual(self.dialog.rows["gain"][1].value(), 5)


if __name__ == "__main__":
    unittest.main()
