import pyqtgraph as pg
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import QSizePolicy, QGraphicsRectItem, QGraphicsTextItem
from PyQt5 import QtCore
from Src.analysis.analyze_3d import PoseAnalyzer

class GraphPlotter():
    def __init__(self, pose_analyzer: PoseAnalyzer, pose_analyzer_2: PoseAnalyzer, speed_name=None, title_names=None):
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
        self.pose_analyzer_2 = pose_analyzer_2
        self.enabled_curves = {name: True for name in self.speed_names}  # 是否畫曲線
        self._motion_phases = []
        self._motion_phases_2 = []
        self.fps = 60
        self.fps_2 = 60
        self.label_font = QFont("Arial", 12)
        self.compare_frame_offset = 0  # footcontact_frame 對齐偏移量

    def _init_graph(self, frame_range: int):
        """Set up the plot widget with labels, axis, and initial configuration."""
        self.plots = []
        self.graph.clear()

        graph_settings = {
            "腳踝速度": {'yrange': (0, 10), 'ylabel': '速度 (m/s)'},
            '膝蓋角度': {'yrange': (0, 180), 'ylabel': '角度 (度)'},
            "肩膀外旋角度": {'yrange': (0, 180), 'ylabel': '角度 (度)'},
            "手腕速度": {'yrange': (0, 40), 'ylabel': '速度 (m/s)'},
            "肩髖分離角度": {'yrange': (-70, 70), 'ylabel': '角度 (度)'}
        }
        for i, title in enumerate(self.title_names):
            p = self.graph.addPlot(row=i, col=0, title=f"{title}")
            p.showAxis('top')
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

    def set_motion_phases(self, segments, segments_2):
        """
        設定要顯示的 X 軸區段。
        segments 格式: [(start, end, QColor), ...]
        """
        self._motion_phases = segments
        self._motion_phases_2 = segments_2
    
    def set_compare_frame_offset(self, offset: int):
        """設置第二組視頻的幀偏移量（用於對齐 footcontact_frame）"""
        self.compare_frame_offset = int(offset)

    def set_video_fps(self, fps, fps_2):
        self.fps = fps
        self.fps_2 = fps_2

    def add_annotation(self, frame, value, message):
        """
        在圖表上標記一個點，並畫出文字框與指向線
        """
        for idx in range(len(self.plots)):
            plot = self.plots[idx]
            
            # 1. 加入一個小散點標示位置
            point = pg.ScatterPlotItem([frame], [value], size=10, pen='w', brush='y')
            plot.addItem(point)
            
            # 2. 加入帶背景的文字說明
            text = pg.TextItem(html=f'<div style="text-align: center;">{message}</div>', 
                            anchor=(0.5, 1.2), # 文字在點的上方中間
                            border='w', 
                            fill=(50, 50, 50, 200))
            text.setPos(frame, value)
            text.setFont(self.label_font)
            plot.addItem(text)

    def updateGraph(self, frame_num: int, legend_name: str = None, legend_name_2: str = None, is_processed=True):
        """Update the graph with the latest angle data for the specified frame."""
        max_len = min(len(self.speed_names), len(self.title_names), len(self.plots))
        need_full_redraw = (not getattr(self, '_static_drawn', False)) or (not is_processed) 
        self.scatters_1 = {}
        self.scatters_2 = {}       
        for idx in range(max_len):
            speed_name = self.speed_names[idx]
            plot = self.plots[idx]
            title = self.title_names[idx]
            is_new_plot = (idx not in self.scatters_1)
            if need_full_redraw or is_new_plot:
                plot.clear()
                if legend_name and legend_name_2:
                    plot.addLegend(offset=(20, 10))

                # --- 1. 繪製背景顏色區段 (LinearRegionItem) ---
                if hasattr(self, '_motion_phases'):
                    for start, end, qcolor in self._motion_phases:
                        # 設定透明度，建議 Alpha 設在 40-60 之間，才不會遮住數據
                        color = QColor(qcolor)
                        
                        rect_item = QGraphicsRectItem(start, 60, end - start, 20)
                        rect_item.setBrush(pg.mkBrush(qcolor))
                        rect_item.setPen(pg.mkPen(None)) # 不要邊框
                        plot.addItem(rect_item)

                        duration_text = f"{(end-start)/self.fps:.2f}s"        
                        # anchor=(0.5, 0.5) 代表文字中心對齊你設定的坐標
                        text_item = pg.TextItem(
                            text=duration_text, 
                            color=color, 
                            anchor=(0.5, 0.5), # 水平垂直皆置中
                            fill=None
                        )
                        self.label_font.setBold(True)                   
                        text_item.setFont(self.label_font)                    
                        # 設定在色條的正中央：X 為 (start+end)/2，Y 為色條中間 (-80 + 20/2)
                        text_item.setPos((start + end) / 2, 50)
                        plot.addItem(text_item)

                if hasattr(self, '_motion_phases_2'):
                    for start, end, qcolor in self._motion_phases_2:
                        # 設定透明度，建議 Alpha 設在 40-60 之間，才不會遮住數據
                        color = QColor(qcolor)
                        # 對齐偏移：將第二組的幀號轉換到第一組的時間軸
                        aligned_start = start - self.compare_frame_offset
                        aligned_end = end - self.compare_frame_offset

                        rect_item = QGraphicsRectItem(aligned_start, -80, aligned_end - aligned_start, 20)
                        rect_item.setBrush(pg.mkBrush(qcolor))
                        rect_item.setPen(pg.mkPen(None)) # 不要邊框
                        plot.addItem(rect_item)

                        duration_text = f"{(end-start)/self.fps_2:.2f}s"
                        # anchor=(0.5, 0.5) 代表文字中心對齊你設定的坐標
                        text_item = pg.TextItem(
                            text=duration_text, 
                            color=color, 
                            anchor=(0.5, 0.5), # 水平垂直皆置中
                            fill=None
                        )
                        self.label_font.setBold(True)                   
                        text_item.setFont(self.label_font)                    
                        # 設定在色條的正中央：X 為 (aligned_start+aligned_end)/2，Y 為色條中間 (-80 + 20/2)
                        text_item.setPos((aligned_start + aligned_end) / 2, -50)
                        plot.addItem(text_item)

                # 取得整條曲線數據
                times, speeds = self.pose_analyzer.get_frame_angleOrSpeed_data(speed_name=speed_name)
                times_2, speeds_2 = self.pose_analyzer_2.get_frame_angleOrSpeed_data(speed_name=speed_name)
                aligned_times_2 = [t - self.compare_frame_offset for t in times_2]
                plot.plot(times, speeds, pen=self.curve_colors[idx % len(self.curve_colors)], name=legend_name)
                plot.plot(aligned_times_2, speeds_2, pen=self.curve_colors[((idx % len(self.curve_colors)) - 1)], name=legend_name_2)

                # 分別繪製上下兩組 motion phases 的垂直線：從 y=0 到對應 frame 的 speed_value
                if hasattr(self, '_motion_phases') and self._motion_phases:
                    phase_xs = sorted({int(s) for s, _, _ in self._motion_phases} | {int(e) for _, e, _ in self._motion_phases})
                    top_pen = pg.mkPen(color=self.curve_colors[idx % len(self.curve_colors)], width=1.2, style=QtCore.Qt.DotLine)
                    for x in phase_xs:
                        _, y_val = self.pose_analyzer.get_frame_angleOrSpeed_data(frame_num=x, speed_name=speed_name)
                        try:
                            y_val = float(y_val)
                        except (TypeError, ValueError):
                            y_val = 80.0
                        plot.addItem(pg.PlotCurveItem(x=[x, x], y=[80.0, y_val], pen=top_pen))

                if hasattr(self, '_motion_phases_2') and self._motion_phases_2:
                    phase_xs_2 = sorted({int(s) for s, _, _ in self._motion_phases_2} | {int(e) for _, e, _ in self._motion_phases_2})
                    bottom_pen = pg.mkPen(color=self.curve_colors[((idx % len(self.curve_colors)) - 1)], width=1.2, style=QtCore.Qt.DashLine)
                    for x in phase_xs_2:
                        _, y_val_2 = self.pose_analyzer_2.get_frame_angleOrSpeed_data(frame_num=x, speed_name=speed_name)
                        try:
                            y_val_2 = float(y_val_2)
                        except (TypeError, ValueError):
                            y_val_2 = -80.0
                        # 對齐偏移：將第二組的幀號轉換到第一組的時間軸
                        aligned_x = x - self.compare_frame_offset
                        plot.addItem(pg.PlotCurveItem(x=[aligned_x, aligned_x], y=[-80.0, y_val_2], pen=bottom_pen))
                self.scatters_1[idx] = pg.ScatterPlotItem(size=5, brush='r')
                self.scatters_2[idx] = pg.ScatterPlotItem(size=5, brush='r')
                plot.addItem(self.scatters_1[idx])
                plot.addItem(self.scatters_2[idx])

            # 當前幀的散點
            _, speed_value = self.pose_analyzer.get_frame_angleOrSpeed_data(frame_num=frame_num, speed_name=speed_name)
            try:
                speed_value = float(speed_value)
            except (TypeError, ValueError):
                speed_value = 0
            self.scatters_1[idx].setData([frame_num], [speed_value])

            frame_num_group2 = frame_num + self.compare_frame_offset
            _, speed_value_2 = self.pose_analyzer_2.get_frame_angleOrSpeed_data(frame_num=frame_num_group2, speed_name=speed_name)
            try:
                speed_value_2 = float(speed_value_2)
            except (TypeError, ValueError):
                speed_value_2 = 0
            self.scatters_2[idx].setData([frame_num], [speed_value_2])

            # 🚩 終極解決方案：符號位分離 + 指定 Consolas 字型
            # 負號常因為渲染引擎問題與空白寬度不一，我們手動拆分符號與數值
            v1_int = int(speed_value)
            v2_int = int(speed_value_2)
            
            # 格式化為：[1位符號] + [3位數字]，總共 4 格
            v1_sign = "-" if v1_int < 0 else " "
            v2_sign = "-" if v2_int < 0 else " "
            v1_val = f"{abs(v1_int):2d}"
            v2_val = f"{abs(v2_int):2d}"
            
            # 使用 Consolas 這是 Windows 上寬度最穩定的字體
            # 我們將符號與數字分開在不同 span 但都放在 pre 屬性下
            html_title = (
                f"<div style='font-family: Consolas, Courier New, monospace; font-size: 10pt;'>"
                f"<span>{title}: </span>"
                f"<span style='white-space: pre;'>{v1_sign}{v1_val}</span><span>度 / </span>"
                f"<span style='white-space: pre;'>{v2_sign}{v2_val}</span><span>度</span>"
                f"</div>"
            )
            plot.setTitle(html_title)
        
        if need_full_redraw:
            self.draw_vertical_lines(*self._pending_verticals) if hasattr(self, '_pending_verticals') else None
            if is_processed:
                self._static_drawn = True
    
    def resize_graph(self,width, height):
        self.graph.resize(width, height)

    def setAngleName(self, angle_name):
        self.angle_name = angle_name
    
    def reset(self):
        self.graph.clear()
        self._static_drawn = False
        # 清除暫存的 vertical lines 設定
        if hasattr(self, '_pending_verticals'):
            del self._pending_verticals
        if hasattr(self, 'vertical_lines'):
            self.vertical_lines = []
        # 清除 motion phases（區域）
        self._motion_phases = []
        self._motion_phases_2 = []
        self.fps = 60
        self.fps_2 = 60

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
                line = pg.PlotCurveItem(x=[x, x], y=[], pen=pen)
                plot.addItem(line)
                plot_lines.append(line)
            self.vertical_lines.append(plot_lines)

