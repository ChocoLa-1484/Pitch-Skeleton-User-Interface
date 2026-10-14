"""Camera backends. Import the SDK only when a backend is requested."""
from importlib import import_module

_BACKENDS = {
    'SingleCameraSystem': '.single_camera.single_camera_system',
    'FlirCameraSystem': '.single_camera.flir_camera_system',
    'TwoCamerasSystem': '.two_cameras.two_cameras_system',
    'DualFlirSystem': '.two_cameras.dual_flir_system',
}
__all__ = list(_BACKENDS)

def __getattr__(name):
    if name not in _BACKENDS:
        raise AttributeError(name)
    value = getattr(import_module(_BACKENDS[name], __name__), name)
    globals()[name] = value
    return value
