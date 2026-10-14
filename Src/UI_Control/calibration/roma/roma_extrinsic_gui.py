"""RoMa 外參校正的視窗版：選錄影資料夾 → 按「開始計算」，不必打指令。

雙擊專案根目錄的 start_roma.bat 啟動（需 Roma 環境，見 docs/CALIBRATION.md 第 7 節）。
計算內容與 roma_extrinsic.py 相同；這裡只是把選項做成按鈕，並把過程訊息顯示在視窗裡。
"""
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import roma_extrinsic  # noqa: E402

RECORD_DIR = Path(__file__).resolve().parents[4] / 'Db' / 'Record'


class QueueWriter:
    """把 print 的內容轉到視窗（背景執行緒不能直接改 tkinter 元件）"""

    def __init__(self, q):
        self.q = q

    def write(self, text):
        self.q.put(text)

    def flush(self):
        pass


class App:
    def __init__(self, root):
        self.root = root
        root.title('RoMa 外參校正（測試中）')
        root.geometry('820x640')
        self.q = queue.Queue()
        self.running = False

        top = ttk.LabelFrame(root, text='1. 選擇錄影（每球資料夾，或整天的資料夾都可以）')
        top.pack(fill='x', padx=10, pady=6)
        self.listbox = tk.Listbox(top, height=6)
        self.listbox.pack(side='left', fill='both', expand=True, padx=6, pady=6)
        buttons = ttk.Frame(top)
        buttons.pack(side='left', padx=6)
        ttk.Button(buttons, text='加入資料夾…', command=self.add_folder).pack(fill='x', pady=2)
        ttk.Button(buttons, text='移除選取', command=self.remove_selected).pack(fill='x', pady=2)

        options = ttk.LabelFrame(root, text='2. 選項')
        options.pack(fill='x', padx=10, pady=6)
        self.save = tk.BooleanVar(value=False)
        self.snapshot = tk.BooleanVar(value=False)
        self.region = tk.BooleanVar(value=False)
        self.frames = tk.StringVar(value='0,30,60')
        ttk.Checkbutton(options, text='存成目前的外參（Db/Calibration/extrinsic/fundamental.json，之後錄影會用它）',
                        variable=self.save).pack(anchor='w', padx=6)
        ttk.Checkbutton(options, text='也替選到的每一球寫入 calibration.json（3D 分頁會讀它；已有的不覆蓋）',
                        variable=self.snapshot).pack(anchor='w', padx=6)
        ttk.Checkbutton(options, text='只用投手附近的匹配點（研究中，需要 CS 骨架 JSON）',
                        variable=self.region).pack(anchor='w', padx=6)
        row = ttk.Frame(options)
        row.pack(anchor='w', padx=6, pady=4)
        ttk.Label(row, text='每球取第幾幀（逗號分隔）：').pack(side='left')
        ttk.Entry(row, textvariable=self.frames, width=16).pack(side='left')

        self.run_button = ttk.Button(root, text='3. 開始計算', command=self.start)
        self.run_button.pack(pady=6)
        self.log = tk.Text(root, height=18)
        self.log.pack(fill='both', expand=True, padx=10, pady=6)
        self.write('不勾任何選項時只計算、顯示結果，不會寫入任何檔案。\n'
                   '第一次執行會自動下載模型權重（約 2 GB，需要網路）。\n')
        root.after(100, self.poll)

    def add_folder(self):
        folder = filedialog.askdirectory(initialdir=str(RECORD_DIR if RECORD_DIR.exists() else Path.home()))
        if folder and folder not in self.listbox.get(0, 'end'):
            self.listbox.insert('end', folder)

    def remove_selected(self):
        for index in reversed(self.listbox.curselection()):
            self.listbox.delete(index)

    def write(self, text):
        self.log.insert('end', text)
        self.log.see('end')

    def poll(self):
        while not self.q.empty():
            self.write(self.q.get())
        self.root.after(100, self.poll)

    def start(self):
        if self.running:
            return
        folders = list(self.listbox.get(0, 'end'))
        if not folders:
            messagebox.showwarning('RoMa 外參校正', '請先加入至少一個錄影資料夾。')
            return
        argv = folders + ['--frames', self.frames.get().replace(' ', '')]
        if self.save.get():
            argv.append('--save')
        if self.snapshot.get():
            argv.append('--snapshot')
        if self.region.get():
            argv.append('--pitcher-region')
        self.running = True
        self.run_button.config(state='disabled')
        self.write('\n===== 開始計算 =====\n')
        threading.Thread(target=self.work, args=(argv,), daemon=True).start()

    def work(self, argv):
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = QueueWriter(self.q)
        try:
            roma_extrinsic.main(argv)
            print('\n===== 完成 =====')
        except SystemExit as exc:
            print(f'\n停止：{exc}')
        except Exception as exc:     # 顯示在視窗裡，不讓視窗直接關掉
            print(f'\n發生錯誤：{exc!r}')
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            self.running = False
            self.root.after(0, lambda: self.run_button.config(state='normal'))


if __name__ == '__main__':
    root = tk.Tk()
    App(root)
    root.mainloop()
