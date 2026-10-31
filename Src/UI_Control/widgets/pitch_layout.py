"""Layout adjustments shared by the 2D controller and UI previews."""
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QWidget, QSizePolicy, QScrollArea, QLabel
from Src.UI_Control.widgets.analysis_layout import event_card, view_title, SIDEBAR_WIDTH


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
        name = view_title('正面 CF' if column == 0 else '側面 CS')
        header.insertWidget(0, name)
        header_widget = QWidget()
        header_widget.setLayout(header)
        header_widget.setFixedHeight(34)
        grid.addWidget(header_widget, 0, column)
        view.setMinimumSize(280, 190)
        view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        grid.addWidget(view, 1, column)
        grid.setColumnStretch(column, 1)
    ui.resetBRBtn.hide()
    ui.ResolutionLabel.hide()
    ui.line.hide()
    # Keep release visibility directly below the skeleton visibility control.
    ui.gridLayout.addWidget(ui.selectCheckBox, 3, 0)
    ui.gridLayout.addWidget(ui.selectKptCheckBox, 4, 0)
    ui.gridLayout.addWidget(ui.showBR_checkBox, 2, 0)

    ui.tabWidget.setFixedWidth(SIDEBAR_WIDTH)
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
    ui.FC_label_unit.setText('°\ncm')
    ui.MER_label_unit.setText('°\n')
    ui.BR_label_unit.setText('m/s\ncm')
    for old, title, view, metrics, button, label in (
            (ui.verticalLayout_2, ui.FC, ui.fcView, ui.horizontalLayout_10, ui.footContact_ptn, ui.footContact_label),
            (ui.verticalLayout_7, ui.MER, ui.merView, ui.horizontalLayout_16, ui.maxER_ptn, ui.maxER_label),
            (ui.verticalLayout_8, ui.BR, ui.brView, ui.horizontalLayout_17, ui.release_ptn, ui.release_label)):
        ui.horizontalLayout_18.removeItem(old)
        old.setParent(None)
        old.removeWidget(title)
        title.hide()
        metric_widgets = [metrics.itemAt(i).widget() for i in range(metrics.count())
                          if metrics.itemAt(i).widget() is not None]
        card = event_card(button, label, view, metric_widgets)
        old.deleteLater()
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
    ui.groupBox_2.setFixedWidth(SIDEBAR_WIDTH)
    ui.RoiLabel.setMinimumSize(0, 0)
    ui.RoiLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
