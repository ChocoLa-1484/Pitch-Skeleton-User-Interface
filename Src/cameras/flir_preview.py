"""Independent FLIR preview streams; no pose models or synchronization."""
import logging
import threading
import time
from collections import deque


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
        self.arrivals = deque()
        self.configured_fps = None
        self.preview_error = ""
        self.preview_condition = threading.Condition()
        self.pending = None
        self.preview_done = False

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            while self.arrivals and self.arrivals[0] <= now - 2:
                self.arrivals.popleft()
            elapsed = now - self.started if self.started is not None else 0
            fps = len(self.arrivals) / min(2, elapsed) if elapsed > 0 else 0
            stats = dict(received=self.received, incomplete=self.incomplete,
                         gaps=self.gaps, errors=self.errors, elapsed=elapsed,
                         average=self.received / elapsed if elapsed > 0 else 0,
                         configured_fps=self.configured_fps)
            return self.frame, fps, self.preview_error or self.status, stats

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
                    if not incomplete and now >= next_preview:
                        # DeepCopy before Release, then convert on a separate thread.
                        preview = sdk.Image.Create()
                        preview.DeepCopy(image)
                        with self.preview_condition:
                            self.pending = preview
                            self.preview_condition.notify()
                        next_preview = now + 1 / 60
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
        self.connection_attempts = {}
        self.last_disconnect = {}
        self.message = "正在偵測 FLIR 攝影機…"

    def snapshot(self):
        with self.lock:
            snapshots = {}
            for sn, stream in self.streams.items():
                frame, fps, status, stats = stream.snapshot()
                stats["reconnects"] = max(0, self.connection_attempts.get(sn, 1) - 1)
                stats["last_disconnect"] = self.last_disconnect.get(sn, "")
                snapshots[sn] = frame, fps, status, stats
            return snapshots, self.message

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def reconcile(self, cameras):
        """Keep existing streams stable as cameras arrive or disappear."""
        with self.lock:
            current = dict(self.streams)
        for serial, stream in current.items():
            if serial not in cameras:
                stream.stop_event.set()
                stream.join()
                with self.lock:
                    self.streams.pop(serial, None)
                    self.last_disconnect[serial] = "偵測不到攝影機"
        for serial in sorted(cameras):
            with self.lock:
                old = self.streams.get(serial)
                full = len(self.streams) >= 5
            if old is not None and old.is_alive():
                continue
            if old is None and full:
                continue
            stream = CameraStream(cameras[serial], self.sdk)
            with self.lock:
                if old is not None:
                    self.last_disconnect[serial] = old.snapshot()[2]
                self.connection_attempts[serial] = self.connection_attempts.get(serial, 0) + 1
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
