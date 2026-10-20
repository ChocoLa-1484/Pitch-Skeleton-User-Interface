"""校正中心：內參、外參的所有方法集中在一個視窗，點選方法、填好欄位就能執行。

雙擊專案根目錄的 start_calibrate.bat 開啟（Pitcher 環境）。各方法仍呼叫原本的程式，計算結果與單獨執行相同：
- 有自己視窗的工具（calibrate.py、calib.py、03_3d.py）另開視窗
- 指令型工具（RoMa、棋盤格、十字架）在本視窗執行，訊息顯示在下方；RoMa 自動改用 Roma 環境執行
"""
import os
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt5.QtCore import QProcess, QProcessEnvironment, Qt
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                             QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from Src.paths import DB_DIR, ROOT_DIR

CAL = ROOT_DIR / 'Src' / 'calibration'
CHESSBOARD_OUT = DB_DIR / 'Calibration' / 'chessboard'
INTRINSICS_JSON = DB_DIR / 'Calibration' / 'intrinsic' / 'intrinsics_all.json'


def conda_exe():
    """Roma 環境要透過 conda 執行：優先用環境變數，其次 PATH，最後由目前 Python 位置推回 anaconda 根目錄"""
    found = os.environ.get('CONDA_EXE') or shutil.which('conda')
    if found:
        return found
    guess = Path(sys.prefix).parents[1] / 'Scripts' / 'conda.exe'
    return str(guess) if guess.exists() else 'conda'


class FolderField(QWidget):
    """一行路徑輸入＋「選擇…」按鈕"""

    def __init__(self, text='', pick_file=False, start=DB_DIR):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(str(text))
        button = QPushButton('選擇…')
        button.clicked.connect(lambda: self._pick(pick_file, start))
        layout.addWidget(self.edit, 1)
        layout.addWidget(button)

    def _pick(self, pick_file, start):
        if pick_file:
            path, _ = QFileDialog.getOpenFileName(self, '選擇檔案', str(start), 'JSON (*.json)')
        else:
            path = QFileDialog.getExistingDirectory(self, '選擇資料夾', str(start))
        if path:
            self.edit.setText(path)

    def text(self):
        return self.edit.text().strip()


class CalibrationCenter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('校正中心')
        self.resize(1000, 720)
        self.process = None
        self.queue = []

        self.methods = QListWidget()
        self.pages = QStackedWidget()
        self._add_header('內參（每台相機各做一次）')
        self._add_window_method('即時拍攝棋盤格', CAL / 'calibrate.py',
                                '開啟校正精靈。只要內參時，請勾選「只做內參」，兩台相機完成後會自動儲存內參與正面／側面角色。'
                                '板子要上下、左右、斜向都傾斜。')
        self._add_window_method('讀取已拍好的照片資料夾', CAL / 'calib.py',
                                '開啟單台內參工具，在「從照片校正」填相機序號後讀取該台相機的照片資料夾。每台相機各做一次，最後在第 4 區指定正面／側面。')
        self._add_header('外參（選一種方法）')
        self._add_roma()
        self._add_window_method('校正桿手點（學姊方法，baseline）', CAL / 'calibrate.py',
                                '開啟校正精靈：兩台內參 → 雙機拍校正桿 → 左右各點 6 個球心 → RANSAC 求 F。'
                                '已有內參時可勾「沿用這兩台已有內參」直接做外參。')
        self._add_window_method('校正桿 3D 驗證（學姊 03，340 mm 尺度）', ROOT_DIR / 'Src' / 'legacy' / 'calibration' / '03_3d.py',
                                '讀取校正桿外參的標註點，以第 1、4 球實距 340 mm 換算尺度並畫出 3D 點。需先完成「校正桿手點」。')
        self._add_chessboard()
        self._add_cross()
        self.methods.currentRowChanged.connect(self.pages.setCurrentIndex)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.stop_button = QPushButton('停止')
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop)

        right = QWidget()
        rlayout = QVBoxLayout(right)
        rlayout.addWidget(self.pages, 1)
        top = QSplitter(Qt.Horizontal)
        top.addWidget(self.methods)
        top.addWidget(right)
        top.setSizes([320, 680])
        bottom = QWidget()
        blayout = QVBoxLayout(bottom)
        row = QHBoxLayout()
        row.addWidget(QLabel('執行訊息'))
        row.addStretch()
        row.addWidget(self.stop_button)
        blayout.addLayout(row)
        blayout.addWidget(self.log)
        main = QSplitter(Qt.Vertical)
        main.addWidget(top)
        main.addWidget(bottom)
        main.setSizes([430, 290])
        self.setCentralWidget(main)
        self.methods.setCurrentRow(1)

    # ---------- 方法清單 ----------
    def _add_header(self, title):
        item = QListWidgetItem(title)
        item.setFlags(Qt.NoItemFlags)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        self.methods.addItem(item)
        self.pages.addWidget(QWidget())

    def _page(self, title, description):
        page = QWidget()
        layout = QVBoxLayout(page)
        head = QLabel(title)
        font = head.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        head.setFont(font)
        text = QLabel(description)
        text.setWordWrap(True)
        layout.addWidget(head)
        layout.addWidget(text)
        return page, layout

    def _add_method(self, title, page):
        self.methods.addItem(QListWidgetItem('  ' + title))
        self.pages.addWidget(page)

    def _add_window_method(self, title, script, description):
        page, layout = self._page(title, description)
        button = QPushButton('開啟')
        button.clicked.connect(lambda: self.open_window(script))
        layout.addWidget(button, 0, Qt.AlignLeft)
        layout.addStretch()
        self._add_method(title, page)

    def _add_roma(self):
        page, layout = self._page(
            'RoMa（不需道具，用當天的錄影）',
            '用正面、側面同時拍的錄影畫面直接求外參，不需校正桿或棋盤格。加入當天的錄影資料夾（每球或整天都可以），'
            '程式會檢查是否有某一球前後相機被移動。內參沿用 Db/Calibration 現有的（先完成內參）。'
            '研究測試中；第一次執行會下載模型權重（需要網路）。')
        self.roma_folders = QListWidget()
        self.roma_folders.setMaximumHeight(110)
        add = QPushButton('加入錄影資料夾…')
        remove = QPushButton('移除選取')
        add.clicked.connect(self._roma_add)
        remove.clicked.connect(lambda: [self.roma_folders.takeItem(self.roma_folders.row(i))
                                        for i in self.roma_folders.selectedItems()])
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        self.roma_frames = QLineEdit('0,30,60')
        self.roma_save = QCheckBox('存成目前的外參（Db/Calibration/extrinsic/fundamental.json，之後錄影會用它）')
        self.roma_snapshot = QCheckBox('也替選到的每一球寫入 calibration.json（3D 分頁會讀它；已有的不覆蓋）')
        self.roma_region = QCheckBox('只用投手附近的匹配點（研究中，需要 CS 骨架 JSON）')
        form = QFormLayout()
        form.addRow('每球取第幾幀', self.roma_frames)
        layout.addWidget(self.roma_folders)
        layout.addLayout(buttons)
        layout.addLayout(form)
        for box in (self.roma_save, self.roma_snapshot, self.roma_region):
            layout.addWidget(box)
        run = QPushButton('開始計算')
        run.clicked.connect(self.run_roma)
        layout.addWidget(run, 0, Qt.AlignLeft)
        layout.addStretch()
        self._add_method('RoMa（不需道具，研究測試中）', page)

    def _roma_add(self):
        folder = QFileDialog.getExistingDirectory(self, '選擇錄影資料夾', str(DB_DIR / 'Record'))
        if folder and folder not in [self.roma_folders.item(i).text() for i in range(self.roma_folders.count())]:
            self.roma_folders.addItem(folder)

    def _add_chessboard(self):
        page, layout = self._page(
            '棋盤格 PnP／8Points（同學方法）',
            '兩台相機同時拍的棋盤格照片（資料夾內含 cf、cs 兩個子資料夾、同名照片）。先各自算內參，再求雙機外參；'
            '棋盤每格 100 mm 提供真實尺度。結果寫到 Db/Calibration/chessboard/，3D 分頁選「PnP／8Points」時使用。'
            '注意：只適用拍棋盤那天的相機位置。')
        self.cb_images = FolderField(DB_DIR / 'Record')
        self.cb_method = QComboBox()
        for label, value in (('兩種都算', 'both'), ('PnP', 'pnp'), ('8Points', '8point')):
            self.cb_method.addItem(label, value)
        form = QFormLayout()
        form.addRow('照片資料夾（含 cf、cs）', self.cb_images)
        form.addRow('外參方法', self.cb_method)
        layout.addLayout(form)
        run = QPushButton('開始計算')
        run.clicked.connect(self.run_chessboard)
        layout.addWidget(run, 0, Qt.AlignLeft)
        layout.addStretch()
        self._add_method('棋盤格 PnP／8Points（同學方法）', page)

    def _add_cross(self):
        page, layout = self._page(
            '粉紅球十字架 PnP（同學方法）',
            '8 顆粉紅球十字架的照片（資料夾內含 cf、cs）。自動偵測球心並以已知球距求公制外參，結果另存到 '
            'Db/Calibration/cross_pnp/<時間>/，不覆蓋現行校正。長焦相機需要正確的內參。')
        self.cross_images = FolderField(DB_DIR / 'Record')
        self.cross_intrinsics = FolderField(INTRINSICS_JSON, pick_file=True, start=INTRINSICS_JSON.parent)
        form = QFormLayout()
        form.addRow('照片資料夾（含 cf、cs）', self.cross_images)
        form.addRow('內參檔', self.cross_intrinsics)
        layout.addLayout(form)
        run = QPushButton('開始計算')
        run.clicked.connect(self.run_cross)
        layout.addWidget(run, 0, Qt.AlignLeft)
        layout.addStretch()
        self._add_method('粉紅球十字架 PnP（同學方法）', page)

    # ---------- 執行 ----------
    def write(self, text):
        self.log.moveCursor(self.log.textCursor().End)
        self.log.insertPlainText(text)
        self.log.moveCursor(self.log.textCursor().End)

    def open_window(self, script):
        ok = QProcess.startDetached(sys.executable, [str(script)], str(ROOT_DIR))
        self.write(f'\n已開啟：{Path(script).name}\n' if ok else f'\n無法開啟 {script}\n')

    def run_roma(self):
        folders = [self.roma_folders.item(i).text() for i in range(self.roma_folders.count())]
        if not folders:
            QMessageBox.warning(self, 'RoMa', '請先加入至少一個錄影資料夾。')
            return
        args = folders + ['--frames', self.roma_frames.text().replace(' ', '')]
        args += ['--save'] if self.roma_save.isChecked() else []
        args += ['--snapshot'] if self.roma_snapshot.isChecked() else []
        args += ['--pitcher-region'] if self.roma_region.isChecked() else []
        script = str(CAL / 'roma' / 'roma_extrinsic.py')
        self.start([(conda_exe(), ['run', '-n', 'Roma', '--no-capture-output', 'python', '-u', script] + args)])

    def run_chessboard(self):
        images = self.cb_images.text()
        if not (Path(images) / 'cf').is_dir() or not (Path(images) / 'cs').is_dir():
            QMessageBox.warning(self, '棋盤格', '照片資料夾內要有 cf 與 cs 兩個子資料夾。')
            return
        py, folder = sys.executable, CAL / 'chessboard_cross'
        self.start([(py, ['-u', str(folder / 'calibrate_chessboard.py'), '--input', images, '--output', str(CHESSBOARD_OUT)]),
                    (py, ['-u', str(folder / 'calibrate_chessboard_extrinsic.py'), '--calibration', str(CHESSBOARD_OUT),
                          '--method', self.cb_method.currentData()])])

    def run_cross(self):
        images = self.cross_images.text()
        if not (Path(images) / 'cf').is_dir() or not (Path(images) / 'cs').is_dir():
            QMessageBox.warning(self, '十字架', '照片資料夾內要有 cf 與 cs 兩個子資料夾。')
            return
        self.start([(sys.executable, ['-u', str(CAL / 'chessboard_cross' / 'calibrate_cross.py'),
                                      '--images', images, '--intrinsics', self.cross_intrinsics.text()])])

    def start(self, commands):
        if self.process is not None:
            QMessageBox.information(self, '校正中心', '目前有計算在執行，請等它完成或按「停止」。')
            return
        self.queue = list(commands)
        self.write('\n===== 開始 =====\n')
        self._next()

    def _next(self):
        if not self.queue:
            self.write('===== 完成 =====\n')
            self.process = None
            self.stop_button.setEnabled(False)
            return
        program, args = self.queue.pop(0)
        self.process = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONIOENCODING', 'utf-8')
        env.insert('PYTHONUNBUFFERED', '1')
        self.process.setProcessEnvironment(env)
        self.process.setWorkingDirectory(str(ROOT_DIR))
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(
            lambda: self.write(bytes(self.process.readAllStandardOutput()).decode('utf-8', 'replace')))
        self.process.finished.connect(self._finished)
        self.stop_button.setEnabled(True)
        self.process.start(program, args)

    def _finished(self, code, status):
        if code != 0 or status != QProcess.NormalExit:
            self.write(f'\n===== 停止（結束代碼 {code}）=====\n')
            self.queue = []
            self.process = None
            self.stop_button.setEnabled(False)
            return
        self._next()

    def stop(self):
        if self.process is not None:
            self.queue = []
            self.process.kill()

    def closeEvent(self, event):
        self.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = CalibrationCenter()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
