"""Click an event heading to pause and inspect its frame."""
from PyQt5.QtCore import Qt


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


def bind_keyframe_navigation(controller):
    ui = controller.ui
    for view, button, attribute in (
            (ui.fcView, ui.footContact_ptn, 'foot_contact_frame'),
            (ui.merView, ui.maxER_ptn, 'max_shoulder_angle_frame'),
            (ui.brView, ui.release_ptn, 'wrist_speed_frame')):
        view.setCursor(Qt.ArrowCursor)
        view.setToolTip('')
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip('點選以暫停並跳到此關鍵幀')
        button.clicked.connect(lambda checked=False, attr=attribute: jump_to_keyframe(controller, attr))
