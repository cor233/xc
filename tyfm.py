import os
import json
import re
import threading
import requests
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# 配置
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://tingyou.fm/',
}

# 默认API模板（用户可根据实际情况修改）
DEFAULT_API_URL = 'https://tingyou.fm/api/audio/{chapter_id}'  # 假设的API，需要实际抓包确认

class Downloader:
    def __init__(self, album_url, save_dir, api_url_template=DEFAULT_API_URL):
        self.album_url = album_url
        self.save_dir = save_dir
        self.api_url_template = api_url_template
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def get_album_info(self):
        """获取专辑信息和章节列表"""
        try:
            resp = self.session.get(self.album_url, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            raise Exception(f"获取专辑页面失败: {e}")

        html = resp.text
        # 从HTML中提取 __NUXT_DATA__ 中的 JSON 数据
        match = re.search(r'<script id="__NUXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if not match:
            raise Exception("未找到专辑数据，可能页面结构已变化")

        json_str = match.group(1)
        data = json.loads(json_str)

        # 根据已知数据结构提取专辑信息
        # 专辑数据在 data[3] 中的 "album-detail-{id}" 下，需要遍历找到
        album_info = None
        chapters = None
        for item in data:
            if isinstance(item, dict):
                # 查找包含专辑详情的条目
                for key, value in item.items():
                    if key.startswith('album-detail-') and isinstance(value, dict):
                        album_info = value
                        break
                # 查找包含章节列表的条目
                for key, value in item.items():
                    if key.startswith('album-chapters-') and isinstance(value, dict):
                        chapters = value.get('chapters', [])
                        break
        if not album_info:
            # 备用方案：直接搜索可能的结构
            for item in data:
                if isinstance(item, dict) and 'title' in item and 'author' in item:
                    album_info = item
                    break
            for item in data:
                if isinstance(item, dict) and 'chapters' in item:
                    chapters = item['chapters']
                    break
        if not album_info or not chapters:
            raise Exception("无法解析专辑信息或章节列表")

        title = album_info.get('title', '未知专辑')
        return title, chapters

    def get_audio_url(self, chapter_id):
        """根据章节ID获取音频URL"""
        url = self.api_url_template.format(chapter_id=chapter_id)
        try:
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()
            # 假设API返回JSON，包含 'url' 字段
            data = resp.json()
            return data.get('url')
        except Exception as e:
            raise Exception(f"获取音频URL失败 (章节ID: {chapter_id}): {e}")

    def download_audio(self, chapter, progress_callback):
        """下载单个章节"""
        chapter_id = chapter['id']
        chapter_title = chapter['title']
        # 过滤文件名非法字符
        safe_title = re.sub(r'[\\/*?:"<>|]', '', chapter_title)
        filename = f"{chapter['index']:03d} - {safe_title}.mp3"
        filepath = os.path.join(self.save_dir, filename)

        audio_url = self.get_audio_url(chapter_id)
        if not audio_url:
            raise Exception(f"未获取到音频URL: {chapter_title}")

        # 下载音频
        resp = self.session.get(audio_url, stream=True, timeout=30)
        resp.raise_for_status()
        total_size = int(resp.headers.get('content-length', 0))
        downloaded = 0
        with open(filepath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_size)
        return filepath

class GUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("听友FM下载器")
        self.root.geometry("800x600")
        self.downloader = None
        self.chapters = []
        self.selected_indices = []
        self.save_dir = os.path.expanduser("~/Downloads/听友FM")

        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_widgets(self):
        # 专辑URL输入
        tk.Label(self.root, text="专辑URL:").grid(row=0, column=0, sticky='w', padx=5, pady=5)
        self.url_entry = tk.Entry(self.root, width=70)
        self.url_entry.grid(row=0, column=1, padx=5, pady=5)
        tk.Button(self.root, text="获取专辑信息", command=self.fetch_album).grid(row=0, column=2, padx=5, pady=5)

        # API模板输入（可选）
        tk.Label(self.root, text="音频API模板:").grid(row=1, column=0, sticky='w', padx=5, pady=5)
        self.api_entry = tk.Entry(self.root, width=70)
        self.api_entry.insert(0, DEFAULT_API_URL)
        self.api_entry.grid(row=1, column=1, padx=5, pady=5)
        tk.Label(self.root, text="使用 {chapter_id} 作为章节ID占位符").grid(row=1, column=2, sticky='w', padx=5, pady=5)

        # 保存路径选择
        tk.Label(self.root, text="保存目录:").grid(row=2, column=0, sticky='w', padx=5, pady=5)
        self.dir_var = tk.StringVar(value=self.save_dir)
        tk.Entry(self.root, textvariable=self.dir_var, width=70).grid(row=2, column=1, padx=5, pady=5)
        tk.Button(self.root, text="选择目录", command=self.choose_dir).grid(row=2, column=2, padx=5, pady=5)

        # 专辑信息显示
        tk.Label(self.root, text="专辑信息:").grid(row=3, column=0, sticky='w', padx=5, pady=5)
        self.album_info_label = tk.Label(self.root, text="未获取", anchor='w', justify='left')
        self.album_info_label.grid(row=3, column=1, columnspan=2, sticky='w', padx=5, pady=5)

        # 章节列表
        tk.Label(self.root, text="章节列表:").grid(row=4, column=0, sticky='nw', padx=5, pady=5)
        frame = tk.Frame(self.root)
        frame.grid(row=4, column=1, columnspan=2, sticky='nsew', padx=5, pady=5)
        self.root.grid_rowconfigure(4, weight=1)
        self.root.grid_columnconfigure(1, weight=1)

        self.listbox = tk.Listbox(frame, selectmode='extended', height=15)
        scrollbar = tk.Scrollbar(frame, orient='vertical', command=self.listbox.yview)
        self.listbox.config(yscrollcommand=scrollbar.set)
        self.listbox.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')

        # 按钮区
        btn_frame = tk.Frame(self.root)
        btn_frame.grid(row=5, column=0, columnspan=3, pady=10)
        tk.Button(btn_frame, text="全选", command=self.select_all).pack(side='left', padx=5)
        tk.Button(btn_frame, text="全不选", command=self.select_none).pack(side='left', padx=5)
        tk.Button(btn_frame, text="下载选中", command=self.download_selected).pack(side='left', padx=5)

        # 进度条
        self.progress = ttk.Progressbar(self.root, orient='horizontal', length=400, mode='determinate')
        self.progress.grid(row=6, column=0, columnspan=3, pady=10, padx=10, sticky='ew')
        self.progress_label = tk.Label(self.root, text="")
        self.progress_label.grid(row=7, column=0, columnspan=3)

        # 日志区域
        tk.Label(self.root, text="日志:").grid(row=8, column=0, sticky='nw', padx=5, pady=5)
        self.log_text = ScrolledText(self.root, height=10, state='normal')
        self.log_text.grid(row=8, column=1, columnspan=2, sticky='nsew', padx=5, pady=5)
        self.root.grid_rowconfigure(8, weight=1)

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def choose_dir(self):
        directory = filedialog.askdirectory(initialdir=self.save_dir)
        if directory:
            self.save_dir = directory
            self.dir_var.set(directory)

    def fetch_album(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("错误", "请输入专辑URL")
            return
        api_template = self.api_entry.get().strip()
        if not api_template:
            api_template = DEFAULT_API_URL
        self.save_dir = self.dir_var.get()
        if not os.path.exists(self.save_dir):
            try:
                os.makedirs(self.save_dir)
            except:
                messagebox.showerror("错误", "无法创建保存目录")
                return

        self.log("正在获取专辑信息...")
        self.listbox.delete(0, tk.END)
        self.chapters = []
        self.selected_indices = []

        def task():
            try:
                downloader = Downloader(url, self.save_dir, api_template)
                title, chapters = downloader.get_album_info()
                self.chapters = chapters
                self.root.after(0, self._update_listbox, title, chapters)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("错误", str(e)))
                self.root.after(0, lambda: self.log(f"错误: {e}"))

        threading.Thread(target=task, daemon=True).start()

    def _update_listbox(self, title, chapters):
        self.album_info_label.config(text=f"{title} (共{len(chapters)}集)")
        for ch in chapters:
            idx = ch['index']
            name = ch['title']
            self.listbox.insert(tk.END, f"{idx:03d} - {name}")
        self.log(f"获取成功，共{len(chapters)}集")

    def select_all(self):
        self.listbox.selection_set(0, tk.END)
        self.selected_indices = list(range(len(self.chapters)))

    def select_none(self):
        self.listbox.selection_clear(0, tk.END)
        self.selected_indices = []

    def download_selected(self):
        indices = list(self.listbox.curselection())
        if not indices:
            messagebox.showwarning("警告", "未选中任何章节")
            return
        self.selected_indices = indices
        self.log(f"开始下载，共选中{len(indices)}集")
        # 禁用按钮，避免重复点击
        for child in self.root.winfo_children():
            if isinstance(child, tk.Button) and child['text'] in ['下载选中', '获取专辑信息']:
                child.config(state='disabled')
        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        total = len(self.selected_indices)
        completed = 0
        for pos, idx in enumerate(self.selected_indices):
            chapter = self.chapters[idx]
            title = f"{chapter['index']:03d} - {chapter['title']}"
            self.root.after(0, lambda t=title: self.log(f"正在下载: {t}"))
            # 进度回调
            def progress_callback(downloaded, total_size, chapter_title=title):
                percent = (downloaded / total_size * 100) if total_size > 0 else 0
                self.root.after(0, lambda: self.progress.config(value=percent))
                self.root.after(0, lambda: self.progress_label.config(text=f"{chapter_title}: {percent:.1f}%"))
            try:
                downloader = Downloader(self.url_entry.get().strip(), self.save_dir, self.api_entry.get().strip())
                filepath = downloader.download_audio(chapter, progress_callback)
                self.root.after(0, lambda p=filepath: self.log(f"下载完成: {p}"))
                completed += 1
                self.root.after(0, lambda: self.progress_label.config(text=f"总体进度: {completed}/{total}"))
            except Exception as e:
                self.root.after(0, lambda err=str(e): self.log(f"下载失败: {err}"))
        self.root.after(0, lambda: self.log("所有下载任务完成"))
        self.root.after(0, lambda: self.progress.config(value=0))
        self.root.after(0, lambda: self.progress_label.config(text=""))
        # 恢复按钮
        for child in self.root.winfo_children():
            if isinstance(child, tk.Button) and child['text'] in ['下载选中', '获取专辑信息']:
                child.config(state='normal')

    def on_close(self):
        self.root.destroy()

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    gui = GUI()
    gui.run()
