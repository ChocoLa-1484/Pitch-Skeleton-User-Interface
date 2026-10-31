"""Shared desktop styling and layouts in Qt logical (DPI-independent) pixels."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QGuiApplication
from PyQt5.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLayout, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget, QBoxLayout,
)
from Src.UI_Control.widgets.analysis_layout import SIDEBAR_WIDTH


def configure_high_dpi():
    """Must run before QApplication; let Qt follow each monitor's scale."""
    if QApplication.instance() is None:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        if hasattr(QGuiApplication, 'setHighDpiScaleFactorRoundingPolicy'):
            QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)


STYLESHEET = """
QWidget { font-family: 'Microsoft JhengHei', 'Segoe UI'; font-size: 10pt; color: #24354b; }
QMainWindow, QWidget#centralwidget { background: #edf2f7; }
QFrame#appHeader { background: #132b46; border-radius: 10px; }
QLabel#appTitle { color: #ffffff; font-size: 17pt; font-weight: 700; }
QLabel#appSubtitle { color: #bbd0e4; font-size: 9pt; }
QLabel#appBadge { color: #82e5d5; background: #24445c; padding: 6px 12px; border-radius: 12px; font-weight: 600; }
QLabel#viewTitle { color: #365474; font-weight: 600; }
QTabWidget::pane { background: #f7f9fc; border: 1px solid #d6e0eb; border-radius: 8px; }
QTabBar::tab { background: #e7edf5; color: #52657c; padding: 8px 14px; margin-right: 3px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { background: #ffffff; color: #087f8c; border-bottom: 3px solid #14a6a1; font-weight: 600; }
QTabBar::tab:hover { background: #d8e9ee; }
QTabWidget#Two_d_Tab > QTabBar::tab { padding: 10px 22px; }
QGroupBox { background: #ffffff; border: 1px solid #dce5ef; border-radius: 8px; margin-top: 13px; padding: 12px 7px 7px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 5px; color: #365474; }
QWidget[card="true"] { background: #ffffff; border: 1px solid #dce5ef; border-radius: 8px; }
QWidget[card="true"] QLabel { border: none; background: transparent; }
QPushButton { background: #ffffff; border: 1px solid #cbd8e6; border-radius: 6px; padding: 5px 10px; min-height: 20px; }
QPushButton:hover { background: #eaf6f5; border-color: #30a7a3; }
QPushButton:pressed { background: #ceeae7; }
QPushButton:focus { border: 2px solid #14a6a1; }
QPushButton:disabled { color: #8091a5; background: #f0f3f7; border-color: #e0e6ed; }
QPushButton#loadOriginalVideoBtn, QPushButton#playBtn, QPushButton#pictureButton { background: #087f8c; color: white; border-color: #087f8c; font-weight: 600; }
QPushButton#loadOriginalVideoBtn:hover, QPushButton#playBtn:hover, QPushButton#pictureButton:hover { background: #096d78; }
QPushButton#footContact_ptn, QPushButton#footContact_ptn_2 { border-left: 4px solid #12a486; color: #087861; background: #e9f7f2; }
QPushButton#maxER_ptn, QPushButton#maxER_ptn_2 { border-left: 4px solid #8672d5; color: #6651ae; background: #f0ecfb; }
QPushButton#release_ptn, QPushButton#release_ptn_2 { border-left: 4px solid #e1a129; color: #9b6b0d; background: #fff6df; }
QPushButton#footContact_ptn:disabled, QPushButton#maxER_ptn:disabled, QPushButton#release_ptn:disabled { color: #798799; }
QGraphicsView { background: #172a3e; border: 1px solid #c4d1df; border-radius: 6px; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: white; border: 1px solid #cbd8e6; border-radius: 5px; padding: 4px 6px; min-height: 20px; selection-background-color: #087f8c; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #14a6a1; }
QComboBox QAbstractItemView { background: white; selection-background-color: #d6efeb; selection-color: #24354b; }
QCheckBox, QRadioButton { spacing: 6px; padding: 3px 0; }
QTreeWidget, QTableWidget { background: white; alternate-background-color: #f3f7fb; border: 1px solid #dce5ef; border-radius: 5px; selection-background-color: #d6efeb; selection-color: #17504f; }
QHeaderView::section { background: #eaf0f6; color: #42607b; border: none; padding: 6px; }
QSlider::groove:horizontal { height: 6px; background: #dce5ef; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #14a6a1; border-radius: 3px; }
QSlider::handle:horizontal { background: #087f8c; width: 14px; margin: -5px 0; border-radius: 7px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #edf2f7; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #b5c5d5; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QLabel#StateLabel { padding: 5px 9px; border-radius: 5px; }
QStatusBar { background: #edf2f7; color: #62768c; }
QToolTip { color: #ffffff; background: #243c56; border: none; padding: 5px; }
"""


class ResponsivePage(QScrollArea):
    """Keep controls readable; move side panels below previews on small screens.

    Scrolling is a fallback for short screens rather than scaling text/images
    into unreadable sizes. All thresholds are logical pixels, including HiDPI.
    """
    def __init__(self, page, kind, parent=None):
        super().__init__(parent)
        self.page, self.kind = page, kind
        self._compact = None
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        page.setMinimumSize(0, 0)
        if page.layout() is not None:
            page.layout().setSizeConstraint(QLayout.SetMinimumSize)
        self.setWidget(page)

    def resizeEvent(self, event):
        self._adapt(self.viewport().width())
        super().resizeEvent(event)

    def _adapt(self, width):
        compact = width < 1120
        if compact == self._compact:
            return
        self._compact = compact
        ui = self.page.ui
        if hasattr(ui, 'analysis_grid'):
            grid = ui.analysis_grid
            sidebar, auxiliary = ui.analysis_sidebar, ui.analysis_auxiliary
            grid.removeWidget(sidebar)
            grid.removeWidget(auxiliary)
            if compact:
                sidebar.setMinimumWidth(0)
                auxiliary.setMinimumWidth(0)
                sidebar.setMaximumWidth(16777215)
                auxiliary.setMaximumWidth(16777215)
                sidebar.setMinimumHeight(320)
                auxiliary.setMinimumHeight(220)
                grid.addWidget(sidebar, 3, 0)
                grid.addWidget(auxiliary, 3, 1)
                grid.setColumnStretch(2, 0)
                grid.setRowStretch(3, 0)
            else:
                sidebar.setFixedWidth(SIDEBAR_WIDTH)
                auxiliary.setFixedWidth(SIDEBAR_WIDTH)
                sidebar.setMinimumHeight(0)
                auxiliary.setMinimumHeight(0)
                grid.addWidget(sidebar, 0, 2, 2, 1)
                grid.addWidget(auxiliary, 2, 2)
                grid.setColumnStretch(2, 0)
            grid.invalidate()
        elif self.kind == 'camera':
            layout = ui.horizontalLayout_4
            scroll = ui.settingGroupBox.parentWidget().parentWidget()
            layout.setDirection(QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight)
            scroll.setMinimumWidth(0 if compact else 430)
            scroll.setMaximumWidth(16777215 if compact else 460)
            scroll.setMinimumHeight(360 if compact else 0)
            ui.settingGroupBox.setMinimumWidth(0)
            ui.settingGroupBox.setMaximumWidth(16777215)
            for view in (ui.FrameView, ui.FrameView_2):
                view.setMinimumSize(260, 230)
        self.page.updateGeometry()


def style_main_window(window):
    app = QApplication.instance()
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft JhengHei', 10))
    window.setStyleSheet(STYLESHEET)
    window.setWindowTitle('Pitch Lab · 投球骨架分析')
    window.ui.menubar.hide()
    root = window.ui.verticalLayout
    root.setContentsMargins(12, 10, 12, 6)
    root.setSpacing(10)
    header = QFrame()
    header.setObjectName('appHeader')
    row = QHBoxLayout(header)
    row.setContentsMargins(18, 10, 18, 10)
    names = QVBoxLayout()
    names.setSpacing(1)
    title = QLabel('PITCH LAB  /  投球骨架分析')
    title.setObjectName('appTitle')
    subtitle = QLabel('雙視角回放 · 三個動作關鍵幀 · 骨架與球路數據')
    subtitle.setObjectName('appSubtitle')
    names.addWidget(title)
    names.addWidget(subtitle)
    row.addLayout(names, 1)
    badge = QLabel('2D  /  3D')
    badge.setObjectName('appBadge')
    row.addWidget(badge)
    root.insertWidget(0, header)
    tabs = window.ui.Two_d_Tab
    pages = [(window.camera_tab, 'camera', '相機設定'),
             (window.pitch_tab, 'pitch', '2D 投球分析'),
             (window.video_tab, 'video', '3D 骨架回放'),
             (window.video_compare_tab, 'compare', '雙組比較')]
    tabs.clear()
    window.responsive_pages = []
    for page, kind, title in pages:
        wrapper = ResponsivePage(page, kind)
        window.responsive_pages.append(wrapper)
        tabs.addTab(wrapper, title)
    tabs.setCurrentIndex(1)
    window.ui.statusbar.showMessage('空白鍵  播放 / 暫停     A / D  逐幀移動     點選 FC / MER / BR  跳至關鍵幀')
    window.setMinimumSize(720, 480)
    screen = window.screen() or app.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        window.resize(min(1500, max(720, int(area.width() * .94))),
                      min(960, max(480, int(area.height() * .91))))
