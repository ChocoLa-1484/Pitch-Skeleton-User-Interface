import pyqtgraph as pg
import numpy as np
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QSizePolicy
from PyQt5 import QtCore
from Src.analysis.analyze_3d import PoseAnalyzer

class GraphPlotter():
    def __init__(self, pose_analyzer: PoseAnalyzer, speed_name=None, title_names=None):
        """Initialize GraphPlotter with frame range and keypoint name."""
        pg.setConfigOptions(foreground=QColor(113,148,116), antialias = True)
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')
        self.speed_names = speed_name 
        self.title_names = title_names 
        self.graph = pg.GraphicsLayoutWidget()
        self.graph.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.graph.ci.layout.setContentsMargins(0, 0, 0, 0)
        self.graph.ci.layout.setSpacing(0)
        self.plots = []
        self.pose_analyzer = pose_analyzer
        self.enabled_curves = {name: True for name in self.speed_names}  # 是否畫曲線
        self._motion_phases = []

    def _init_graph(self, frame_range: int):
        """Set up the plot widget with labels, axis, and initial configuration."""
        self.plots = []
        self.graph.clear()

        graph_settings = {
            "腳踝速度": {'yrange': (0, 10), 'ylabel': '速度 (m/s)'},
            '膝蓋角度': {'yrange': (0, 180), 'ylabel': '角度 (度)'},
            "肩膀外旋角度": {'yrange': (0, 180), 'ylabel': '角度 (度)'},
            "手腕速度": {'yrange': (0, 40), 'ylabel': '速度 (m/s)'},
            "肩峰連線與髖關節連線角度": {'yrange': (-60, 60), 'ylabel': '角度 (度)'}
        }
        for i, title in enumerate(self.title_names):
            p = self.graph.addPlot(row=i, col=0, title=f"{title}")
            p.showGrid(x=False, y=False)
            # p.setLabel('bottom', '幀')
            p.setXRange(0, frame_range+1, padding=0.0) 

            settings = graph_settings.get(title)
            if settings:
                p.setLabel('left', settings['ylabel'])
                p.setYRange(settings['yrange'][0], settings['yrange'][1], padding=0.0)
            p.setMinimumHeight(120)
            p.setMinimumWidth(620)
            p.getAxis('left').setWidth(60)
            self.plots.append(p)

        # 預設每條曲線顏色
        self.curve_colors = ["#000000", "#800080", "#004BAC", "#C60000", '#00aa00']

        # 預設可見的圖表
        visible_titles = {"腳踝速度", "肩膀外旋角度", "手腕速度"}
        for i, plot in enumerate(self.plots):
            title = self.title_names[i]
            plot.setVisible(title in visible_titles)

        # 如果之前呼叫過 draw_vertical_lines 但 plots 尚未建立，則在此恢復
        if hasattr(self, '_pending_verticals'):
            try:
                self.draw_vertical_lines(*self._pending_verticals)
            except Exception:
                pass
            del self._pending_verticals

    def setPlotVisibility(self, speed_name: str, visible: bool):
        """
        根據名稱來設定圖表的可見性。
        
        Args:
            speed_name (str): 曲線的內部名稱 (e.g., "ankle_speed")
            visible (bool): True 為顯示，False 為隱藏
        """
        # 找到對應的索引
        try:
            idx = self.speed_names.index(speed_name)
            plot = self.plots[idx]
            plot.setVisible(visible)

            # Force the GraphicsLayoutWidget to recompute layout and redraw.
            # When all rows are hidden then later one row is shown again, the
            # layout sometimes doesn't re-flow until the window is resized.
            # Trigger a geometry/update pass so the plot fills the available area.
            try:
                # update geometry immediately
                self.graph.updateGeometry()
                # schedule a zero-delay resize to force an internal relayout
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(0, lambda: self.graph.resize(self.graph.width(), self.graph.height()))
            except Exception:
                # best-effort only — do not raise on layout helpers
                pass
            
        except ValueError:
            print(f"找不到名稱為 {speed_name} 的圖表。")

    def set_motion_phases(self, segments):
        """
        設定要顯示的 X 軸區段。
        segments 格式: [(start, end, QColor), ...]
        """
        self._motion_phases = segments

    def updateGraph(self, frame_num: int):
        """Update the graph with the latest angle data for the specified frame."""
        max_len = min(len(self.speed_names), len(self.title_names), len(self.plots))
        for idx in range(max_len):
            speed_name = self.speed_names[idx]
            plot = self.plots[idx]
            title = self.title_names[idx]
            plot.clear()

            # --- 1. 繪製背景顏色區段 (LinearRegionItem) ---
            if hasattr(self, '_motion_phases'):
                for start, end, qcolor in self._motion_phases:
                    # 設定透明度，建議 Alpha 設在 40-60 之間，才不會遮住數據
                    color = QColor(qcolor)
                    color.setAlpha(50) 
                    
                    region = pg.LinearRegionItem(
                        values=[start, end], 
                        brush=pg.mkBrush(color),
                        movable=False  # 禁止使用者拖動
                    )
                    # 隱藏 LinearRegionItem 左右兩條垂直邊界線
                    for line in region.lines:
                        line.setPen(pg.mkPen(None))
                    
                    plot.addItem(region)

            # 取得整條曲線數據
            times, speeds = self.pose_analyzer.get_frame_angleOrSpeed_data(speed_name=speed_name)
            plot.plot(times, speeds, pen=self.curve_colors[idx % len(self.curve_colors)], name=speed_name)

            # 當前幀的散點
            _, speed_value = self.pose_analyzer.get_frame_angleOrSpeed_data(frame_num=frame_num, speed_name=speed_name)
            try:
                speed_value = float(speed_value)
            except (TypeError, ValueError):
                speed_value = float('nan')
            scatter = pg.ScatterPlotItem([frame_num], [speed_value], size=5, brush='r')
            plot.addItem(scatter)

            # 更新標題顯示當前幀速度
            label = f'{speed_value:.2f}' if np.isfinite(speed_value) else '--'
            plot.setTitle(f"{title}: {label}", size='8pt')
        self.draw_vertical_lines(*self._pending_verticals) if hasattr(self, '_pending_verticals') else None
    
    def resize_graph(self,width, height):
        self.graph.resize(width, height)

    def setAngleName(self, angle_name):
        self.angle_name = angle_name
    
    def reset(self):
        self.graph.clear()
        # 清除暫存的 vertical lines 設定
        if hasattr(self, '_pending_verticals'):
            del self._pending_verticals
        if hasattr(self, 'vertical_lines'):
            self.vertical_lines = []
        # 清除 motion phases（區域）
        self._motion_phases = []

    def zoomIn(self, x_min, x_max):
        """
        改變所有子圖的 x 軸範圍（zoom in）。
        
        Args:
            x_min: x 軸最小值（幀號）
            x_max: x 軸最大值（幀號）
        """
        if not hasattr(self, 'plots') or not self.plots:
            return
        
        for plot in self.plots:
            plot.setXRange(x_min, x_max+1, padding=0.0)

    def draw_vertical_lines(self, x1, x2, x3, width=1):
        """
        在所有子圖上畫三條垂直線（x 軸位置）。
        若目前尚未建立 self.plots，會將參數暫存，等 _init_graph 建立後再繪製。

        Args:
            x1, x2, x3: 三個 x 軸位置（數值或 None）
            width: 線寬
        """
        vals = [x1, x2, x3]

        # 若 plots 尚未建立，暫存參數並返回
        if not hasattr(self, 'plots') or len(self.plots) == 0:
            self._pending_verticals = (x1, x2, x3, width)
            return

        # 移除先前的直線（若有）
        if hasattr(self, 'vertical_lines'):
            for plot, lines in zip(self.plots, self.vertical_lines):
                for ln in lines:
                    try:
                        plot.removeItem(ln)
                    except Exception:
                        pass
        self.vertical_lines = []

        # 在每個子圖上加三條垂直線
        for plot in self.plots:
            plot_lines = []
            for idx, x in enumerate(vals):
                if x is None:
                    continue
                color = self.curve_colors[(idx % len(self.curve_colors))+2]
                pen = pg.mkPen(color, width=width, style=QtCore.Qt.DashLine)
                line = pg.InfiniteLine(pos=x, angle=90, pen=pen, movable=False)
                plot.addItem(line)
                plot_lines.append(line)
            self.vertical_lines.append(plot_lines)

