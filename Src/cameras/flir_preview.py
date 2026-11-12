"""Independent FLIR preview streams; no pose models or synchronization."""
import logging
import threading
import time
from collections import deque


def configure_preview_resolution(camera, sdk, width=1920, height=1080):
    """Set an exact sensor ROI before acquisition, without rescaling pixels."""
    offsets = []
    for name in ("OffsetX", "OffsetY"):
        offset = getattr(camera, name)
        if sdk.IsWritable(offset):
            offsets.append((offset, offset.GetValue()))
            offset.SetValue(offset.GetMin())
    for name, value in (("Width", width), ("Height", height)):
        node = getattr(camera, name)
        if not sdk.IsReadable(node):
            raise RuntimeError("無法讀取相機 %s，不能確認 %d×%d" % (name, width, height))
        if not sdk.IsWritable(node):
            if node.GetValue() == value:
                continue
            raise RuntimeError("相機 %s 不可設定為 %d" % (name, value))
        minimum, maximum, increment = node.GetMin(), node.GetMax(), node.GetInc()
        if not minimum <= value <= maximum or (value - minimum) % max(1, increment):
            raise RuntimeError("相機不支援 %s=%d（範圍 %d–%d，步進 %d）" %
                               (name, value, minimum, maximum, increment))
        node.SetValue(value)
        if node.GetValue() != value:
            raise RuntimeError("相機 %s 設定讀回不符 %d" % (name, value))
    for offset, original in offsets:
        minimum, maximum, increment = offset.GetMin(), offset.GetMax(), max(1, offset.GetInc())
        value = max(minimum, min(original, maximum))
        offset.SetValue(minimum + (value - minimum) // increment * increment)


class CameraStream(threading.Thread):
    def __init__(self, camera, sdk):
        super().__init__(daemon=True)
        self.camera, self.sdk = camera, sdk
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame, self.fps, self.status = None, 0.0, "連線中…"
        self.received = self.incomplete = self.gaps = self.errors = 0
        self.last_id = None
        self.started = None
        self.stopped_at = None
        self.arrivals = deque()
        self.configured_fps = None
        self.preview_error = ""
        self.preview_condition = threading.Condition()
        self.pending = None
        self.preview_done = False
        self.preview_enabled = threading.Event()
        self.preview_enabled.set()

    def set_preview_enabled(self, enabled):
        if enabled:
            self.preview_enabled.set()
        else:
            self.preview_enabled.clear()
            with self.preview_condition:
                self.pending = None
            with self.lock:
                self.frame = None
                self.preview_error = ""

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            while self.arrivals and self.arrivals[0] <= now - 2:
                self.arrivals.popleft()
            elapsed = (self.stopped_at or now) - self.started if self.started is not None else 0
            fps = len(self.arrivals) / min(2, elapsed) if elapsed > 0 else 0
            if self.stopped_at is not None:
                fps = 0
            stats = dict(received=self.received, incomplete=self.incomplete,
                         gaps=self.gaps, errors=self.errors, elapsed=elapsed,
                         average=self.received / elapsed if elapsed > 0 else 0,
                         configured_fps=self.configured_fps)
            error = self.preview_error if self.preview_enabled.is_set() else ""
            return self.frame, fps, error or self.status, stats

    def publish(self, frame=None, fps=0.0, status="即時畫面"):
        with self.lock:
            self.frame, self.fps, self.status = frame, fps, status

    def convert_previews(self):
        """Convert only sampled copies; never retain a camera-owned buffer."""
        try:
            processor = self.sdk.ImageProcessor()
            processor.SetColorProcessing(self.sdk.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR)
            while True:
                with self.preview_condition:
                    self.preview_condition.wait_for(lambda: self.pending is not None or self.preview_done)
                    image, self.pending = self.pending, None
                    if image is None:
                        return
                try:
                    converted = processor.Convert(image, self.sdk.PixelFormat_RGB8)
                    frame = converted.GetNDArray().copy()
                    del converted
                    with self.lock:
                        if self.preview_enabled.is_set():
                            self.frame = frame
                            self.preview_error = ""
                except Exception as exc:
                    with self.lock:
                        self.preview_error = "預覽轉換失敗（接收繼續）：%s" % exc
                finally:
                    del image
        except Exception as exc:
            with self.lock:
                self.preview_error = "無法啟動預覽轉換：%s" % exc

    def run(self):
        cam, sdk = self.camera, self.sdk
        initialized = streaming = False
        converter = None
        try:
            cam.Init()
            initialized = True
            configure_preview_resolution(cam, sdk)
            cam.TriggerMode.SetValue(sdk.TriggerMode_Off)
            cam.AcquisitionMode.SetValue(sdk.AcquisitionMode_Continuous)
            node = sdk.CEnumerationPtr(cam.GetTLStreamNodeMap().GetNode("StreamBufferHandlingMode"))
            node.SetIntValue(node.GetEntryByName("OldestFirst").GetValue())
            # Observe the existing camera configuration, without changing exposure/rate.
            try:
                rate = cam.AcquisitionFrameRate
                if sdk.IsReadable(rate):
                    self.configured_fps = float(rate.GetValue())
            except Exception:
                pass
            cam.BeginAcquisition()
            streaming = True
            with self.lock:
                self.started = time.monotonic()
            converter = threading.Thread(target=self.convert_previews, daemon=True)
            converter.start()
            next_preview, failures = 0.0, 0
            while not self.stop_event.is_set():
                image = None
                try:
                    image = cam.GetNextImage(1000)
                    now = time.monotonic()
                    incomplete = image.IsIncomplete()
                    frame_id = int(image.GetFrameID())
                    with self.lock:
                        if self.last_id is not None and frame_id > self.last_id:
                            self.gaps += max(0, frame_id - self.last_id - 1)
                        self.last_id = frame_id
                        if incomplete:
                            self.incomplete += 1
                            self.status = "影像不完整（狀態 %s）" % image.GetImageStatus()
                        else:
                            self.received += 1
                            self.arrivals.append(now)
                            while self.arrivals and self.arrivals[0] <= now - 2:
                                self.arrivals.popleft()
                            self.status = "接收中"
                    failures = 0
                    if (self.preview_enabled.is_set() and not incomplete and now >= next_preview
                            and converter.is_alive()):
                        next_preview = now + 1 / 30
                        # DeepCopy before Release, then convert on a separate thread.
                        # Preview failures must never count toward acquisition failures.
                        try:
                            preview = sdk.Image.Create()
                            preview.DeepCopy(image)
                        except Exception as exc:
                            with self.lock:
                                self.preview_error = "預覽複製失敗（接收繼續）：%s" % exc
                        else:
                            with self.preview_condition:
                                if self.preview_enabled.is_set():
                                    self.pending = preview
                                    self.preview_condition.notify()
                except Exception as exc:
                    with self.lock:
                        self.errors += 1
                        self.status = "等待影像：%s" % exc
                    failures += 1
                    if failures >= 3:
                        break
                    self.stop_event.wait(0.1)
                finally:
                    if image is not None:
                        image.Release()
        except Exception as exc:
            self.publish(status="無法開啟：%s" % exc)
        finally:
            with self.lock:
                self.stopped_at = time.monotonic()
            with self.preview_condition:
                self.preview_done = True
                self.preview_condition.notify()
            if converter is not None:
                converter.join()
            self.pending = None
            if streaming:
                try:
                    cam.EndAcquisition()
                except Exception:
                    logging.exception("Cannot end preview acquisition")
            if initialized:
                try:
                    cam.DeInit()
                except Exception:
                    logging.exception("Cannot deinitialize preview camera")
            self.camera = None


class PreviewService(threading.Thread):
    """Own the SDK lifetime; discovery and all I/O stay outside the GUI thread."""
    def __init__(self, sdk=None):
        super().__init__(daemon=True)
        self.sdk = sdk
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.streams = {}
        self.connected = []
        self.preferences = {}
        self.reconnects = {}
        self.last_disconnect = {}
        self.message = "正在偵測 FLIR 攝影機…"

    def set_enabled(self, serial, acquisition=None, preview=None):
        """GUI-safe request; only the worker performs SDK stop/start operations."""
        with self.lock:
            options = self.preferences.setdefault(serial, {"acquisition": True, "preview": True})
            if acquisition is not None:
                options["acquisition"] = bool(acquisition)
            if preview is not None:
                options["preview"] = bool(preview)
            stream = self.streams.get(serial)
            if stream is not None:
                stream.set_preview_enabled(options["preview"])
                if not options["acquisition"]:
                    stream.stop_event.set()
        self.wake.set()

    def snapshot(self):
        with self.lock:
            snapshots = {}
            for sn in self.connected:
                stream = self.streams.get(sn)
                frame, fps, status, stats = stream.snapshot() if stream else (None, 0, "等待啟動", {})
                options = self.preferences.get(sn, {"acquisition": True, "preview": True})
                stats["acquisition_enabled"] = options["acquisition"]
                stats["preview_enabled"] = options["preview"]
                if not options["acquisition"]:
                    frame, fps = None, 0
                    status = "正在停止接收…" if stream and stream.is_alive() else "接收已關閉"
                elif not options["preview"]:
                    frame = None
                stats["reconnects"] = self.reconnects.get(sn, 0)
                stats["last_disconnect"] = self.last_disconnect.get(sn, "")
                snapshots[sn] = frame, fps, status, stats
            enabled = sum(snapshot[3]["acquisition_enabled"] for snapshot in snapshots.values())
            return snapshots, self.message + " · 接收啟用 %d / %d 台" % (enabled, len(snapshots))

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def reconcile(self, cameras):
        """Keep existing streams stable as cameras arrive or disappear."""
        with self.lock:
            self.connected = [sn for sn in self.connected if sn in cameras]
            self.connected += [sn for sn in sorted(cameras) if sn not in self.connected][:5 - len(self.connected)]
            selected = list(self.connected)
            current = dict(self.streams)
        for serial, stream in current.items():
            if serial not in cameras:
                stream.stop_event.set()
                stream.join()
                with self.lock:
                    self.streams.pop(serial, None)
                    self.last_disconnect[serial] = "偵測不到攝影機"
        for serial in selected:
            with self.lock:
                old = self.streams.get(serial)
                options = self.preferences.get(serial, {"acquisition": True, "preview": True})
                if not options["acquisition"]:
                    if old is not None:
                        old.stop_event.set()
                    continue
            if old is not None and old.is_alive():
                if not old.stop_event.is_set():
                    continue
                # Acquisition was re-enabled while the old stream is still stopping;
                # wait for it (GetNextImage times out within ~1 s) instead of
                # skipping the restart until the next 2 s poll.
                old.join()
            stream = CameraStream(cameras[serial], self.sdk)
            with self.lock:
                # Re-read after construction in case the GUI changed a switch.
                options = self.preferences.get(serial, {"acquisition": True, "preview": True})
                if not options["acquisition"]:
                    continue
                stream.set_preview_enabled(options["preview"])
                if old is not None and not old.stop_event.is_set():
                    self.last_disconnect[serial] = old.snapshot()[2]
                    # Count only real drops of a session that actually streamed,
                    # not every failed reopen attempt while the camera is busy.
                    if old.started is not None:
                        self.reconnects[serial] = self.reconnects.get(serial, 0) + 1
                self.streams[serial] = stream
                stream.start()
        with self.lock:
            self.message = ("已偵測 %d 台 · 最多顯示 5 台 · 各台獨立擷取" % len(cameras)
                            if cameras else "尚未偵測到攝影機，接上 FLIR 後會自動顯示")

    def run(self):
        system = None
        try:
            if self.sdk is None:
                import PySpin
                self.sdk = PySpin
            system = self.sdk.System.GetInstance()
            while not self.stop_event.is_set():
                camera_list = None
                cameras = {}
                cam = None
                try:
                    camera_list = system.GetCameras()
                    for index in range(camera_list.GetSize()):
                        cam = camera_list.GetByIndex(index)
                        serial = cam.TLDevice.DeviceSerialNumber.GetValue()
                        cameras[serial] = cam
                    self.reconcile(cameras)
                except Exception as exc:
                    with self.lock:
                        self.message = "偵測失敗，將自動重試：%s" % exc
                finally:
                    cam = None
                    cameras.clear()
                    if camera_list is not None:
                        camera_list.Clear()
                self.wake.wait(2)
                self.wake.clear()
        except Exception as exc:
            with self.lock:
                self.message = "無法載入 FLIR SDK，請確認 Spinnaker／PySpin 安裝：%s" % exc
        finally:
            with self.lock:
                streams = list(self.streams.values())
            for stream in streams:
                stream.stop_event.set()
            for stream in streams:
                stream.join()
            if system is not None:
                try:
                    system.ReleaseInstance()
                except Exception:
                    logging.exception("Cannot release preview SDK")
