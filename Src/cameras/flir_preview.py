"""Independent FLIR preview streams; no pose models or synchronization."""
import logging
import threading
import time


class CameraStream(threading.Thread):
    def __init__(self, camera, sdk):
        super().__init__(daemon=True)
        self.camera, self.sdk = camera, sdk
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.frame, self.fps, self.status = None, 0.0, "連線中…"

    def snapshot(self):
        with self.lock:
            return self.frame, self.fps, self.status

    def publish(self, frame=None, fps=0.0, status="即時畫面"):
        with self.lock:
            self.frame, self.fps, self.status = frame, fps, status

    def run(self):
        cam, sdk = self.camera, self.sdk
        initialized = streaming = False
        try:
            cam.Init()
            initialized = True
            cam.TriggerMode.SetValue(sdk.TriggerMode_Off)
            cam.AcquisitionMode.SetValue(sdk.AcquisitionMode_Continuous)
            node = sdk.CEnumerationPtr(cam.GetTLStreamNodeMap().GetNode("StreamBufferHandlingMode"))
            node.SetIntValue(node.GetEntryByName("NewestOnly").GetValue())
            processor = sdk.ImageProcessor()
            processor.SetColorProcessing(sdk.SPINNAKER_COLOR_PROCESSING_ALGORITHM_HQ_LINEAR)
            cam.BeginAcquisition()
            streaming = True
            last = time.monotonic()
            frames, fps, failures = 0, 0.0, 0
            while not self.stop_event.is_set():
                image = None
                try:
                    image = cam.GetNextImage(1000)
                    if image.IsIncomplete():
                        raise RuntimeError("影像不完整（狀態 %s）" % image.GetImageStatus())
                    converted = processor.Convert(image, sdk.PixelFormat_RGB8)
                    frame = converted.GetNDArray().copy()
                    del converted
                    frames += 1
                    now = time.monotonic()
                    if now - last >= 1:
                        fps, frames, last = frames / (now - last), 0, now
                    self.publish(frame, fps)
                    failures = 0
                except Exception as exc:
                    self.publish(status="等待影像：%s" % exc)
                    failures += 1
                    if failures >= 3:
                        break
                    self.stop_event.wait(0.1)
                finally:
                    if image is not None:
                        image.Release()
                # Preview only: cap conversion work and use the newest SDK buffer.
                self.stop_event.wait(1 / 30)
        except Exception as exc:
            self.publish(status="無法開啟：%s" % exc)
        finally:
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
        self.message = "正在偵測 FLIR 攝影機…"

    def snapshot(self):
        with self.lock:
            return {sn: stream.snapshot() for sn, stream in self.streams.items()}, self.message

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
