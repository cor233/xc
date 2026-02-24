import os
import re
import threading
import requests
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
from urllib.parse import urljoin
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

class DownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("275听书网 批量下载器")
        self.root.geometry("700x600")
        self.base_url = ""   # 书籍详情页URL
        self.chapters = []   # 章节列表 [(title, play_url), ...]
        self.check_vars = [] # 复选框变量

        # 顶部输入框
        frame_input = tk.Frame(root)
        frame_input.pack(pady=10, padx=10, fill=tk.X)
        tk.Label(frame_input, text="书籍详情页 URL:").pack(side=tk.LEFT)
        self.url_entry = tk.Entry(frame_input)
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        tk.Button(frame_input, text="获取章节", command=self.fetch_chapters).pack(side=tk.RIGHT)

        # 全选/反选
        frame_control = tk.Frame(root)
        frame_control.pack(pady=5, padx=10, fill=tk.X)
        tk.Button(frame_control, text="全选", command=self.select_all).pack(side=tk.LEFT, padx=2)
        tk.Button(frame_control, text="反选", command=self.invert_selection).pack(side=tk.LEFT, padx=2)
        tk.Button(frame_control, text="下载选中", command=self.start_download).pack(side=tk.LEFT, padx=2)
        tk.Button(frame_control, text="选择保存目录", command=self.choose_dir).pack(side=tk.LEFT, padx=2)
        self.dir_label = tk.Label(frame_control, text="保存目录: 未选择", fg="blue")
        self.dir_label.pack(side=tk.LEFT, padx=10)

        # 章节列表（带复选框）
        self.list_frame = tk.Frame(root)
        self.list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.canvas = tk.Canvas(self.list_frame)
        self.scrollbar = tk.Scrollbar(self.list_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas)

        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 日志区域
        self.log = scrolledtext.ScrolledText(root, height=10, state='disabled')
        self.log.pack(fill=tk.BOTH, padx=10, pady=5)

        self.save_dir = ""

    def log_message(self, msg):
        self.log.config(state='normal')
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.config(state='disabled')

    def choose_dir(self):
        self.save_dir = filedialog.askdirectory()
        if self.save_dir:
            self.dir_label.config(text=f"保存目录: {self.save_dir}")
        else:
            self.dir_label.config(text="保存目录: 未选择")

    def fetch_chapters(self):
        url = self.url_entry.get().strip()
        if not url:
            self.log_message("请输入书籍详情页URL")
            return
        self.base_url = url
        self.log_message(f"正在解析 {url} ...")
        threading.Thread(target=self._fetch_chapters_thread, daemon=True).start()

    def _fetch_chapters_thread(self):
        try:
            resp = requests.get(self.base_url, headers=HEADERS, timeout=10)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
            # 根据网站实际结构调整选择器
            # 假设章节链接在带有 id="chapter-pos-xxx" 的 <a> 标签中
            chapter_links = []
            for a in soup.find_all('a', id=re.compile(r'^chapter-pos-\d+')):
                href = a.get('href')
                if href:
                    full_url = urljoin(self.base_url, href)
                    # 提取标题：可能位于 a 内的 <span> 或其他元素
                    # 这里简化：取 a 的文本
                    title = a.get_text(strip=True)
                    if title:
                        chapter_links.append((title, full_url))
            if not chapter_links:
                # 如果上面没找到，尝试其他通用规则
                for a in soup.find_all('a', href=re.compile(r'/play/\d+/\d+\.html')):
                    href = a.get('href')
                    full_url = urljoin(self.base_url, href)
                    title = a.get_text(strip=True)
                    if title:
                        chapter_links.append((title, full_url))
            # 去重保留顺序
            seen = set()
            unique_links = []
            for title, url in chapter_links:
                if url not in seen:
                    seen.add(url)
                    unique_links.append((title, url))

            self.chapters = unique_links
            self.root.after(0, self.display_chapters)
            self.log_message(f"共找到 {len(self.chapters)} 个章节")
        except Exception as e:
            self.root.after(0, lambda: self.log_message(f"解析失败: {str(e)}"))

    def display_chapters(self):
        # 清空之前的内容
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.check_vars.clear()
        for title, url in self.chapters:
            var = tk.BooleanVar()
            self.check_vars.append(var)
            cb = tk.Checkbutton(self.scrollable_frame, text=title, variable=var, anchor='w', justify=tk.LEFT)
            cb.pack(fill=tk.X, padx=5, pady=2)
        # 全选默认
        for var in self.check_vars:
            var.set(True)

    def select_all(self):
        for var in self.check_vars:
            var.set(True)

    def invert_selection(self):
        for var in self.check_vars:
            var.set(not var.get())

    def start_download(self):
        if not self.save_dir:
            self.log_message("请先选择保存目录")
            return
        selected = [(title, url) for (title, url), var in zip(self.chapters, self.check_vars) if var.get()]
        if not selected:
            self.log_message("没有选中任何章节")
            return
        self.log_message(f"开始下载 {len(selected)} 个章节...")
        threading.Thread(target=self._download_thread, args=(selected,), daemon=True).start()

    def _download_thread(self, selected):
        for idx, (title, play_url) in enumerate(selected, 1):
            self.root.after(0, lambda: self.log_message(f"处理 {idx}/{len(selected)}: {title}"))
            try:
                # 获取播放页
                resp = requests.get(play_url, headers=HEADERS, timeout=10)
                # 提取音频URL
                audio_url = self.extract_audio_url(resp.text)
                if not audio_url:
                    self.root.after(0, lambda: self.log_message(f"  提取失败: {title}"))
                    continue
                # 下载音频
                self.download_file(audio_url, title, idx)
            except Exception as e:
                self.root.after(0, lambda: self.log_message(f"  异常: {str(e)}"))

    def extract_audio_url(self, html):
        # 尝试多种模式匹配音频URL
        patterns = [
            r"(https?://[^'\"]+\.(?:m4a|mp3)[^'\"]*)",
            r"url:\s*['\"]([^'\"]+)['\"]",
        ]
        for pat in patterns:
            match = re.search(pat, html)
            if match:
                url = match.group(1)
                # 处理相对路径
                if url.startswith('//'):
                    url = 'https:' + url
                elif url.startswith('/'):
                    url = urljoin(self.base_url, url)
                return url
        return None

    def download_file(self, audio_url, title, idx):
        # 生成安全的文件名
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        ext = os.path.splitext(audio_url.split('?')[0])[1] or '.m4a'
        filename = f"{idx:03d}_{safe_title}{ext}"
        filepath = os.path.join(self.save_dir, filename)

        try:
            resp = requests.get(audio_url, headers=HEADERS, stream=True, timeout=30)
            resp.raise_for_status()
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            with open(filepath, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    # 可在此更新进度条（略）
            self.root.after(0, lambda: self.log_message(f"  已下载: {filename}"))
        except Exception as e:
            self.root.after(0, lambda: self.log_message(f"  下载失败: {filename} - {str(e)}"))

if __name__ == "__main__":
    root = tk.Tk()
    app = DownloaderGUI(root)
    root.mainloop()
