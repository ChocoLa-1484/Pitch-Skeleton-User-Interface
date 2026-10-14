"""
This module initializes the single camera systems by importing the necessary classes.
Classes:
    SingleCameraSystem: Handles operations related to a single camera system.
    FlirCameraSystem: Manages operations for FLIR camera systems.
__all__:
    List of public objects of this module, as interpreted by `import *`.
"""

from Src.cameras.single_camera.single_camera_system import SingleCameraSystem
__all__ = ['SingleCameraSystem']
try:
    import PySpin
    from Src.cameras.single_camera.flir_camera_system import FlirCameraSystem
    __all__.append('FlirCameraSystem')
except ModuleNotFoundError:
    pass
