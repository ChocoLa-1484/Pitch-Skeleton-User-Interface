"""Common dimensions for the 2D/3D video, event cards and playback row."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget, QSizePolicy, QLabel

SIDEBAR_WIDTH = 340


def view_title(text):
    label = QLabel(text)
    label.setObjectName('viewTitle')
    label.setFont(QFont('Microsoft JhengHei', 10, QFont.DemiBold))
    return label


def event_card(button, label, view, metrics):
    card = QWidget()
    card.setProperty('card', True)
    card.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(8, 8, 8, 4)
    layout.setSpacing(6)
    button.setFixedHeight(32)
    button.setMinimumWidth(0)
    button.setMaximumWidth(16777215)
    button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    button.setStyleSheet('text-align: left; padding-left: 12px;')
    # Keep the frame number inside the full-width, clickable event heading.
    title = QHBoxLayout(button)
    title.setContentsMargins(12, 0, 12, 0)
    label.setFixedWidth(90)
    label.setFont(QFont('Microsoft JhengHei', 10))
    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    label.setAttribute(Qt.WA_TransparentForMouseEvents)
    title.addStretch()
    title.addWidget(label)
    layout.addWidget(button)
    view.setMinimumSize(0, 180)
    view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
    layout.addWidget(view, 1)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    for index, metric in enumerate(metrics):
        metric.setFont(QFont('Microsoft JhengHei', 12))
        metric.setAlignment((Qt.AlignRight if index == 1 else Qt.AlignLeft) | Qt.AlignVCenter)
        row.addWidget(metric)
    footer = QWidget()
    footer.setFixedHeight(44)
    footer.setLayout(row)
    layout.addWidget(footer)
    return card


def playback_row(ui, reanalyze_button):
    row = QHBoxLayout()
    row.setSpacing(8)
    for button in (ui.backKeyBtn, ui.playBtn, ui.forwardKeyBtn):
        button.setFixedSize(52, 40)
        row.addWidget(button)
    ui.frameSlider.setMinimumWidth(200)
    ui.frameSlider.setFixedHeight(40)
    ui.frameSlider.setOrientation(Qt.Horizontal)
    ui.frameSlider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    ui.frameNumLabel.setFixedWidth(100)
    row.addWidget(ui.frameSlider, 1)
    row.addWidget(ui.frameNumLabel)
    reanalyze_button.setFixedSize(120, 40)
    row.addWidget(reanalyze_button)
    return row
