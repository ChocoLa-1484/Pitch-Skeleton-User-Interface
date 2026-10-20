"""Keep the 3D replay arranged like the 2D pitch page."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QGridLayout, QHBoxLayout, QVBoxLayout, QGroupBox, QLabel, QWidget,
    QTabWidget, QScrollArea, QSizePolicy,
)


def _empty_layout(layout):
    # Keep Designer-owned widgets alive; only replace their layout containers.
    while layout.count():
        item = layout.takeAt(0)
        if item.layout() is not None:
            _empty_layout(item.layout())
            item.layout().deleteLater()


def align_video_layout(ui, settings, events, status):
    root = ui.verticalLayout_6
    _empty_layout(root)
    root.setContentsMargins(8, 8, 8, 8)
    root.setSpacing(8)
    for widget in (ui.label, ui.label_2, ui.NameLabel, ui.NameLabel_2,
                   ui.line, ui.loadProcessedVideoBtn, ui.selectKptCheckBox):
        widget.hide()
    ui.loadOriginalVideoBtn.setText('載入影片')
    ui.loadOriginalVideoBtn.setToolTip('選擇 Db/Record 下的原始 CF 或 CS 影片，自動配對同一次投球的另一視角。')
    ui.startCodeBtn.setText('重新分析')
    ui.startCodeBtn.setToolTip('清除手動關鍵幀，使用與 2D 相同的骨架重新判定，完成後自動儲存 JSON。')

    grid = QGridLayout()
    grid.setSpacing(8)
    for column, (name, label, view) in enumerate((
            ('正面 CF', ui.videoNameLabel, ui.FrameView),
            ('側面 CS', ui.videoNameLabel_3, ui.FrameView_2))):
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(name))
        label.setMinimumWidth(0)
        label.setFont(QFont('Microsoft JhengHei', 10))
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(label, 1)
        header.setFixedHeight(34)
        grid.addWidget(header, 0, column)
        view.setMinimumSize(300, 200)
        view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        grid.addWidget(view, 1, column)
        grid.setColumnStretch(column, 1)

    sidebar = QTabWidget()
    sidebar.setMinimumWidth(320)
    sidebar.setMaximumWidth(380)
    controls = QWidget()
    controls_layout = QVBoxLayout(controls)
    action_row = QHBoxLayout()
    for button in (ui.loadOriginalVideoBtn, ui.startCodeBtn):
        button.setMinimumHeight(30)
        action_row.addWidget(button)
    controls_layout.addLayout(action_row)
    hint = QLabel('同一次投球：正面 CF ＋ 側面 CS\n已分析直接回放；未分析自動處理。')
    hint.setWordWrap(True)
    controls_layout.addWidget(hint)
    display = QGroupBox('回放顯示')
    display_layout = QGridLayout(display)
    for i, widget in enumerate((ui.showSkeletonCheckBox, ui.showBboxCheckBox,
                                ui.showBR_checkBox, ui.resetBRBtn,
                                ui.label_5, ui.slowMotionInput, ui.selectCheckBox)):
        display_layout.addWidget(widget, i // 2, i % 2)
    ui.resetBRBtn.setMinimumHeight(30)
    controls_layout.addWidget(display)
    controls_layout.addStretch()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidget(controls)
    sidebar.addTab(scroll, '操作設定')
    calibration_scroll = QScrollArea()
    calibration_scroll.setWidgetResizable(True)
    calibration_scroll.setFrameShape(QScrollArea.NoFrame)
    calibration_scroll.setWidget(settings)
    sidebar.addTab(calibration_scroll, '校正設定')
    manual = QWidget()
    manual_layout = QVBoxLayout(manual)
    manual_layout.addWidget(events)
    manual_layout.addStretch()
    # Like the other settings pages, isolate wrapped text's height-for-width
    # from the shared grid. Otherwise selecting this tab grows the video row.
    manual_scroll = QScrollArea()
    manual_scroll.setWidgetResizable(True)
    manual_scroll.setFrameShape(QScrollArea.NoFrame)
    manual_scroll.setWidget(manual)
    sidebar.addTab(manual_scroll, '關鍵幀')
    rapsodo = QWidget()
    data_layout = QVBoxLayout(rapsodo)
    data_layout.addWidget(ui.RapsodoDataLabel_2)
    data_layout.addWidget(ui.spinDirectionWidget_2)
    for label in (ui.RoiLabel, ui.RoiLabel_2):
        label.setMinimumSize(0, 0)
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        data_layout.addWidget(label, 1)
    sidebar.addTab(rapsodo, '球路資料')
    grid.addWidget(sidebar, 0, 2, 2, 1)
    ui.replay_sidebar = sidebar

    cards = QHBoxLayout()
    cards.setSpacing(8)
    for button, label, view, metric_labels in (
            (ui.footContact_ptn, ui.footContact_label, ui.fcView, (ui.FC_label_2, ui.FC_label, ui.FC_label_unit)),
            (ui.maxER_ptn, ui.maxER_label, ui.merView, (ui.MER_label_2, ui.MER_label_3, ui.MER_label_unit)),
            (ui.release_ptn, ui.release_label, ui.brView, (ui.BR_label_2, ui.BR_label_3, ui.BR_label_unit))):
        card = QVBoxLayout()
        title = QHBoxLayout()
        title.addWidget(button)
        title.addWidget(label)
        title.addStretch()
        button.setMinimumHeight(28)
        card.addLayout(title)
        view.setMinimumSize(0, 150)
        view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        card.addWidget(view, 1)
        metrics = QWidget()
        row = QHBoxLayout(metrics)
        row.setContentsMargins(0, 0, 0, 0)
        for metric in metric_labels:
            metric.setFont(QFont('Microsoft JhengHei', 10))
            row.addWidget(metric)
        metrics.setFixedHeight(54)
        card.addWidget(metrics)
        cards.addLayout(card, 1)
    grid.addLayout(cards, 2, 0, 1, 2)
    reconstruction = QGroupBox('3D 骨架')
    reconstruction.setMinimumWidth(320)
    reconstruction.setMaximumWidth(380)
    reconstruction_layout = QVBoxLayout(reconstruction)
    reconstruction_layout.setContentsMargins(4, 4, 4, 4)
    reconstruction_layout.addWidget(ui.vispy_widget)
    grid.addWidget(reconstruction, 2, 2)
    grid.setRowStretch(1, 3)
    grid.setRowStretch(2, 2)
    root.addLayout(grid, 1)
    ui.graph_widget.setMinimumHeight(155)
    ui.graph_widget.setMaximumHeight(210)
    root.addWidget(ui.graph_widget)
    status.setFont(QFont('Microsoft JhengHei', 10))
    status.setMinimumWidth(0)
    status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    status.setFixedHeight(40)
    root.addWidget(status)
    playback = QHBoxLayout()
    for button in (ui.backKeyBtn, ui.playBtn, ui.forwardKeyBtn):
        button.setMinimumSize(40, 34)
        button.setMaximumSize(50, 40)
        playback.addWidget(button)
    ui.frameSlider.setMinimumSize(300, 48)
    ui.frameNumLabel.setFixedWidth(150)
    playback.addWidget(ui.frameSlider, 1)
    playback.addWidget(ui.frameNumLabel)
    root.addLayout(playback)
    ui.ResolutionLabel.hide()
    for view in (ui.FrameView, ui.FrameView_2, ui.fcView, ui.merView, ui.brView):
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
