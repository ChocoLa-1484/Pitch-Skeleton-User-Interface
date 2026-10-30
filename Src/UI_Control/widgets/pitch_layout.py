"""Layout adjustments shared by the 2D controller and UI previews."""
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QWidget, QSizePolicy, QScrollArea, QLabel


def align_pitch_layout(ui):
    # Hardware settings have a single owner in the camera tab.
    ui.groupBox.hide()
    ui.camerSettingGroupBox.setTitle("拍攝操作")
    ui.verticalLayout_6.setSpacing(8)
    ui.verticalLayout_6.addStretch()

    root = ui.verticalLayout_11
    root.setContentsMargins(8, 8, 8, 8)
    root.setSpacing(10)
    root.removeItem(ui.horizontalLayout_20)
    root.removeItem(ui.horizontalLayout_18)

    # Share one grid between the preview row, keyframes and right sidebar.
    # The old independent horizontal layouts sized each row differently.
    grid = QGridLayout()
    grid.setSpacing(8)
    for column, (old, header, view) in enumerate((
            (ui.verticalLayout_3, ui.horizontalLayout_19, ui.FrameView),
            (ui.verticalLayout_5, ui.horizontalLayout_15, ui.FrameView_2))):
        old.removeItem(header)
        header.setParent(None)
        header.setContentsMargins(0, 0, 0, 0)
        name = QLabel('正面 CF' if column == 0 else '側面 CS')
        name.setObjectName('viewTitle')
        header.insertWidget(0, name)
        header_widget = QWidget()
        header_widget.setLayout(header)
        header_widget.setFixedHeight(34)
        grid.addWidget(header_widget, 0, column)
        view.setMinimumSize(280, 190)
        view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        grid.addWidget(view, 1, column)
        grid.setColumnStretch(column, 1)
    ui.resetBRBtn.setMinimumHeight(30)

    ui.tabWidget.setMinimumWidth(310)
    ui.tabWidget.setMaximumWidth(360)
    # The capture/replay controls can grow independently from the preview row.
    for index in range(ui.tabWidget.count()):
        page = ui.tabWidget.widget(index)
        title = ui.tabWidget.tabText(index)
        ui.tabWidget.removeTab(index)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(page)
        ui.tabWidget.insertTab(index, scroll, title)
    grid.addWidget(ui.tabWidget, 0, 2, 2, 1)

    keyframes = QHBoxLayout()
    keyframes.setSpacing(8)
    for old, title, view, metrics, button, label in (
            (ui.verticalLayout_2, ui.FC, ui.fcView, ui.horizontalLayout_10, ui.footContact_ptn, ui.footContact_label),
            (ui.verticalLayout_7, ui.MER, ui.merView, ui.horizontalLayout_16, ui.maxER_ptn, ui.maxER_label),
            (ui.verticalLayout_8, ui.BR, ui.brView, ui.horizontalLayout_17, ui.release_ptn, ui.release_label)):
        ui.horizontalLayout_18.removeItem(old)
        old.setParent(None)
        old.removeWidget(title)
        title.hide()
        header = QHBoxLayout()
        button.setParent(view.parentWidget())
        label.setParent(view.parentWidget())
        button.setMinimumHeight(28)
        header.addWidget(button)
        header.addWidget(label)
        old.insertLayout(0, header)
        view.setMinimumSize(0, 150)
        view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        old.setStretch(1, 1)
        old.removeItem(metrics)
        metrics.setParent(None)
        metrics_widget = QWidget()
        metrics_widget.setLayout(metrics)
        metrics.setContentsMargins(0, 0, 0, 0)
        metrics_widget.setFixedHeight(44)
        old.addWidget(metrics_widget)
        card = QWidget()
        card.setProperty('card', True)
        old.setContentsMargins(8, 8, 8, 4)
        old.setSpacing(6)
        card.setLayout(old)
        keyframes.addWidget(card, 1)
    ui.KeyFrameGroupBox.hide()
    grid.addLayout(keyframes, 2, 0, 1, 2)
    grid.addWidget(ui.groupBox_2, 2, 2)
    grid.setRowStretch(1, 3)
    grid.setRowStretch(2, 2)
    root.insertLayout(0, grid, 1)
    ui.analysis_grid = grid
    ui.analysis_sidebar = ui.tabWidget
    ui.analysis_auxiliary = ui.groupBox_2

    for button in (ui.backKeyBtn, ui.playBtn, ui.forwardKeyBtn):
        button.setMinimumSize(44, 34)
        button.setMaximumSize(52, 40)
    ui.frameSlider.setMinimumSize(200, 40)
    ui.frameNumLabel.setMinimumWidth(100)

    for name in ("FC_label", "FC_label_2", "FC_label_unit", "MER_label",
                 "MER_label_2", "MER_label_unit", "BR_label", "BR_label_2",
                 "BR_label_unit", "RapsodoDataLabel"):
        label = getattr(ui, name)
        font = label.font()
        font.setPointSize(12)
        label.setFont(font)
    # Keep long recording names from widening just the first preview column.
    ui.fileNameLabel.setMinimumWidth(0)
    ui.fileNameLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    ui.groupBox_2.setMinimumWidth(310)
    ui.groupBox_2.setMaximumWidth(360)
    ui.RoiLabel.setMinimumSize(0, 0)
    ui.RoiLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
