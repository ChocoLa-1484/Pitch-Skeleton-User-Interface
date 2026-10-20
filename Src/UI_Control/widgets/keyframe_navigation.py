"""Click a keyframe image or its heading to pause and inspect that frame."""
from PyQt5.QtCore import QObject, QEvent, Qt


def jump_to_keyframe(controller, attribute):
    frame = getattr(controller, attribute, None)
    if (frame is None or getattr(controller, '_analysis_running', False)
            or getattr(controller, 'single_video_scan_running', False)):
        return
    controller.is_play = False
    timer = getattr(controller, '_play_timer', None)
    if timer is not None:
        timer.stop()
    controller.ui.playBtn.setText('▶')
    controller._current_play_frame = frame
    controller.ui.frameSlider.setValue(frame)


class _KeyframeClick(QObject):
    def __init__(self, controller, attribute):
        super().__init__(controller)
        self.controller, self.attribute = controller, attribute

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            jump_to_keyframe(self.controller, self.attribute)
            return True
        return False


def bind_keyframe_navigation(controller):
    ui = controller.ui
    controller._keyframe_click_filters = []
    for view, button, attribute in (
            (ui.fcView, ui.footContact_ptn, 'foot_contact_frame'),
            (ui.merView, ui.maxER_ptn, 'max_shoulder_angle_frame'),
            (ui.brView, ui.release_ptn, 'wrist_speed_frame')):
        handler = _KeyframeClick(controller, attribute)
        controller._keyframe_click_filters.append(handler)
        view.viewport().installEventFilter(handler)
        view.setCursor(Qt.PointingHandCursor)
        view.setToolTip('點選以暫停並跳到此關鍵幀')
        button.clicked.connect(lambda checked=False, attr=attribute: jump_to_keyframe(controller, attr))
