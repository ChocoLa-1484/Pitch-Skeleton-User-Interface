"""Capability-driven GenICam controls shared by every camera UI.

No camera is opened here. Writes use the existing FLIR instance and persist
only hardware read-back values, after a successful write.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import math
import os
from pathlib import Path
import tempfile

import yaml


@dataclass
class ControlState:
    value: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    step: float = 0.01
    unit: str = ""
    writable: bool = False
    reason: str = ""
    integer: bool = False


# Only actual lens-control nodes are accepted; calibration metadata such as
# Scan3dFocalLength is deliberately excluded.
FEATURES = {
    "exposure": (("ExposureTime",), "exposure_settings", "exposure_time", "ExposureAuto", None),
    "gain": (("Gain",), "gain_settings", "gain_value", "GainAuto", None),
    "red": (("BalanceRatio",), "white_balance_settings", "white_balance_red_ratio", "BalanceWhiteAuto", "Red"),
    "blue": (("BalanceRatio",), "white_balance_settings", "white_balance_blue_ratio", "BalanceWhiteAuto", "Blue"),
    "focus": (("Focus", "FocusPos"), "lens_settings", "focus", "FocusAuto", None),
    "focal_length": (("FocalLength",), "lens_settings", "focal_length", None, None),
}


class CameraSettings:
    def __init__(self, camera, sdk):
        self.camera = camera
        self.sdk = sdk

    def _nodemap(self):
        if self.camera._released or self.camera.cam is None:
            raise RuntimeError("攝影機已關閉，請重新開啟後再調整。")
        return self.camera.cam.GetNodeMap()

    def _number(self, nodemap, key):
        for name in FEATURES[key][0]:
            raw = nodemap.GetNode(name)
            if not self.sdk.IsAvailable(raw):
                continue
            kind = raw.GetPrincipalInterfaceType()
            if kind == self.sdk.intfIFloat:
                node = self.sdk.CFloatPtr(raw)
            elif kind == self.sdk.intfIInteger:
                node = self.sdk.CIntegerPtr(raw)
            else:
                continue
            if self.sdk.IsReadable(node):
                return node, kind == self.sdk.intfIInteger
        raise ValueError("硬體未提供此控制；焦距／對焦可能需手動轉動鏡頭。" if key in ("focus", "focal_length")
                         else "硬體未提供此控制。")

    @contextmanager
    def _channel(self, nodemap, key):
        color = FEATURES[key][4]
        selector, previous = None, None
        try:
            if color:
                selector = self.sdk.CEnumerationPtr(nodemap.GetNode("BalanceRatioSelector"))
                if not self.sdk.IsReadable(selector) or not self.sdk.IsWritable(selector):
                    raise ValueError("目前無法選擇白平衡色彩通道。")
                previous = selector.GetIntValue()
                entry = selector.GetEntryByName(color)
                if not self.sdk.IsReadable(entry):
                    raise ValueError("硬體未提供此白平衡通道。")
                selector.SetIntValue(entry.GetValue())
            yield
        finally:
            if selector is not None and previous is not None:
                selector.SetIntValue(previous)

    def _describe(self, nodemap, key):
        node, integer = self._number(nodemap, key)
        minimum, maximum = node.GetMin(), node.GetMax()
        if key == "exposure":
            fps = float(self.camera.full_config.get("acquisition_settings", {}).get("fps", 0))
            if fps > 0:
                maximum = min(maximum, 1e6 / fps)
        step = max(1, node.GetInc()) if integer else (10.0 if key == "exposure" else 0.01)
        writable = self.sdk.IsWritable(node) and minimum <= maximum
        unit = "µs" if key == "exposure" else (node.GetUnit() if hasattr(node, "GetUnit") else "")
        return ControlState(float(node.GetValue()), float(minimum), float(maximum), step,
                            unit, writable, "" if writable else "目前為唯讀／擷取中無法調整。", integer)

    def describe(self):
        states = {}
        with self.camera.settings_lock:
            nodemap = self._nodemap()
            for key in FEATURES:
                try:
                    with self._channel(nodemap, key):
                        states[key] = self._describe(nodemap, key)
                except Exception as exc:
                    states[key] = ControlState(reason=str(exc))
        return states

    def _manual(self, nodemap, key):
        name = FEATURES[key][3]
        if not name:
            return None
        raw = nodemap.GetNode(name)
        if not self.sdk.IsAvailable(raw):
            return None
        node = self.sdk.CEnumerationPtr(raw)
        if not self.sdk.IsReadable(node):
            raise ValueError("無法讀取自動控制狀態。")
        previous = node.GetIntValue()
        off = node.GetEntryByName("Off")
        if not self.sdk.IsReadable(off):
            raise ValueError("硬體未提供手動模式。")
        if previous != off.GetValue():
            if not self.sdk.IsWritable(node):
                raise ValueError("請先關閉相機的自動控制。")
            node.SetIntValue(off.GetValue())
        return node, previous

    def apply(self, key, value):
        if key not in FEATURES:
            raise ValueError("未知的攝影機設定。")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("設定值必須是有限數字。")
        with self.camera.settings_lock:
            nodemap = self._nodemap()
            with self._channel(nodemap, key):
                node, integer = self._number(nodemap, key)
                old_value = node.GetValue()
                auto = None
                wrote = False
                try:
                    auto = self._manual(nodemap, key)
                    state = self._describe(nodemap, key)
                    if not state.writable:
                        raise ValueError(state.reason)
                    if not state.minimum <= value <= state.maximum:
                        raise ValueError(f"允許範圍為 {state.minimum:g}–{state.maximum:g} {state.unit}")
                    if integer:
                        if not value.is_integer() or (int(value) - int(state.minimum)) % int(state.step):
                            raise ValueError(f"請使用從 {state.minimum:g} 起、間隔 {state.step:g} 的整數。")
                        value = int(value)
                    node.SetValue(value)
                    wrote = True
                    actual = node.GetValue()
                    config = deepcopy(self.camera.full_config)
                    _, section, field, _, _ = FEATURES[key]
                    config.setdefault(section, {})[field] = actual
                    if key == "exposure": config[section]["exposure_auto"] = False
                    if key == "gain": config[section]["gain_auto"] = "Off"
                    if key in ("red", "blue"): config[section]["white_balance_auto"] = "Off"
                    if key in ("focus", "focal_length"):
                        config[section]["serial_number"] = str(self.camera.serial_number)
                    self._save(config)
                    self.camera.full_config.clear()
                    self.camera.full_config.update(config)
                    return actual
                except Exception as exc:
                    try:
                        if wrote: node.SetValue(old_value)
                        if auto is not None and self.sdk.IsWritable(auto[0]): auto[0].SetIntValue(auto[1])
                    except Exception as rollback:
                        raise RuntimeError(f"設定失敗：{exc}；還原失敗：{rollback}，請重新讀取相機。") from exc
                    raise

    def _save(self, config):
        path = Path(self.camera.config_yaml_path)
        filename = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.stem + "_", suffix=".tmp", delete=False) as stream:
                filename = stream.name
                yaml.safe_dump(config, stream, allow_unicode=True, sort_keys=False)
            os.replace(filename, path)
        finally:
            if filename and os.path.exists(filename):
                os.unlink(filename)
