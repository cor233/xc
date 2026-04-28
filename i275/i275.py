import os
import re
import threading
import queue
import requests
from pathlib import Path
from urllib.parse import urljoin
import customtkinter as ctk
from tkinter import messagebox, filedialog
from bs4 import BeautifulSoup

# 设置外观
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class AudioBookDownloader(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("有声书下载器 - 275听书网")
        self.geometry("900x650")
        self.minsize(800, 600)

        self.chapters = []
        self.download_queue = queue.Queue()
        self.downloading = False
        self.cancelled = False

        # 主布局
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)  # 输入行
        self.grid_rowconfigure(1, weight=1)  # 列表+日志
        self.grid_rowconfigure(2, weight=0)  # 进度

        # 顶部输入区域
        self.top_frame = ctk.CTkFrame(self)
        self.top_frame.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="ew")
        self.top_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.top_frame, text="书籍URL:").grid(row=0, column=0, padx=5, pady=5)
        self.url_entry = ctk.CTkEntry(self.top_frame, placeholder_text="https://m.i275.com/book/33175.html")
        self.url_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.parse_btn = ctk.CTkButton(self.top_frame, text="解析目录", command=self.parse_book)
        self.parse_btn.grid(row=0, column=2, padx=5, pady=5)

        # 中间内容区域（左右分栏）
        self.content_frame = ctk.CTkFrame(self)
        self.content_frame.grid(row=1, column=0, padx=10, pady=5, sticky="nsew")
        self.content_frame.grid_columnconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(1, weight=1)
        self.content_frame.grid_rowconfigure(0, weight=1)

        # 左侧：章节列表
        self.list_frame = ctk.CTkFrame(self.content_frame)
        self.list_frame.grid(row=0, column=0, padx=(0, 5), pady=0, sticky="nsew")
        self.list_frame.grid_rowconfigure(0, weight=1)
        self.list_frame.grid_columnconfigure(0, weight=1)

        self.chapter_listbox = ctk.CTkTextbox(self.list_frame, wrap="none")
        self.chapter_listbox.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        # 右侧：日志
        self.log_frame = ctk.CTkFrame(self.content_frame)
        self.log_frame.grid(row=0, column=1, padx=(5, 0), pady=0, sticky="nsew")
        self.log_frame.grid_rowconfigure(0, weight=1)
        self.log_frame.grid_columnconfigure(0, weight=1)

        self.log_text = ctk.CTkTextbox(self.log_frame, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        # 底部进度和控制
        self.bottom_frame = ctk.CTkFrame(self)
        self.bottom_frame.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="ew")
        self.bottom_frame.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(self.bottom_frame)
        self.progress.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        self.progress.set(0)

        self.status_label = ctk.CTkLabel(self.bottom_frame, text="就绪")
        self.status_label.grid(row=1, column=0, padx=5, pady=2, sticky="w")

        self.download_btn = ctk.CTkButton(self.bottom_frame, text="开始下载", command=self.start_download)
        self.download_btn.grid(row=0, column=1, padx=5, pady=5)

        self.stop_btn = ctk.CTkButton(self.bottom_frame, text="停止", command=self.stop_download, fg_color="gray")
        self.stop_btn.grid(row=0, column=2, padx=5, pady=5)

        self.save_btn = ctk.CTkButton(self.bottom_frame, text="选择目录", command=self.select_folder)
        self.save_btn.grid(row=0, column=3, padx=5, pady=5)

        self.save_folder = os.path.join(os.path.expanduser("~"), "Downloads", "AudioBooks")
        os.makedirs(self.save_folder, exist_ok=True)

    # ---------- 日志 ----------
    def log(self, msg):
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")

    # ---------- 选择保存目录 ----------
    def select_folder(self):
        folder = filedialog.askdirectory(initialdir=self.save_folder)
        if folder:
            self.save_folder = folder
            self.log(f"保存目录设置为: {folder}")

    # ---------- 解析书籍目录 ----------
    def parse_book(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("错误", "请输入书籍URL")
            return
        self.parse_btn.configure(state="disabled", text="解析中...")
        self.chapters.clear()
        self.chapter_listbox.delete("1.0", "end")
        threading.Thread(target=self._parse_thread, args=(url,), daemon=True).start()

    def _parse_thread(self, url):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(url, headers=headers, timeout=15)
            resp.encoding = "utf-8"
            if resp.status_code != 200:
                self.log(f"请求失败: {resp.status_code}")
                self.parse_btn.configure(state="normal", text="解析目录")
                return

            soup = BeautifulSoup(resp.text, "html.parser")
            # 查找所有章节链接
            pattern = re.compile(r"/play/\d+/\d+\.html")
            links = soup.find_all("a", href=pattern)

            for a in links:
                href = a["href"]
                title = a.get_text(strip=True)
                if not title:
                    span = a.find("span", class_=lambda c: c and "truncate" in c)
                    if span:
                        title = span.get_text(strip=True)
                if not title:
                    title = href.rsplit("/", 1)[-1].replace(".html", "")
                full_url = urljoin(url, href)
                self.chapters.append((title, full_url))

            # 去重 & 排序（按URL中的数字）
            seen = set()
            unique_chapters = []
            for title, link in self.chapters:
                if link not in seen:
                    seen.add(link)
                    unique_chapters.append((title, link))
            self.chapters = unique_chapters
            # 按章节ID排序
            self.chapters.sort(key=lambda x: int(re.search(r"/play/\d+/(\d+)\.html", x[1]).group(1)))

            # 更新UI
            self.after(0, self._update_chapter_list)
        except Exception as e:
            self.log(f"解析出错: {e}")
        finally:
            self.after(0, lambda: self.parse_btn.configure(state="normal", text="解析目录"))

    def _update_chapter_list(self):
        self.chapter_listbox.delete("1.0", "end")
        for i, (title, _) in enumerate(self.chapters, 1):
            self.chapter_listbox.insert("end", f"{i}. {title}\n")
        self.log(f"解析到 {len(self.chapters)} 个章节")

    # ---------- 下载逻辑 ----------
    def start_download(self):
        if not self.chapters:
            messagebox.showwarning("警告", "请先解析目录")
            return
        if self.downloading:
            return
        self.downloading = True
        self.cancelled = False
        self.download_btn.configure(state="disabled", text="下载中...")
        self.progress.set(0)
        self.status_label.configure(text="准备下载...")

        threading.Thread(target=self._download_worker, daemon=True).start()

    def _download_worker(self):
        total = len(self.chapters)
        for idx, (title, page_url) in enumerate(self.chapters, 1):
            if self.cancelled:
                self.log("下载已取消")
                break
            try:
                self.after(0, lambda i=idx, t=title: self.status_label.configure(text=f"下载中 ({i}/{total}): {t}"))
                audio_url = self._get_audio_url(page_url)
                if not audio_url:
                    self.log(f"[{idx}/{total}] 未找到音频链接: {title}")
                    continue
                self._download_file(audio_url, title, idx)
                self.after(0, lambda i=idx: self.progress.set(i / total))
            except Exception as e:
                self.log(f"[{idx}/{total}] 下载异常: {e}")

        if not self.cancelled:
            self.after(0, lambda: self.status_label.configure(text="下载完成"))
            self.log("全部下载完成")
        else:
            self.after(0, lambda: self.status_label.configure(text="已停止"))
        self.downloading = False
        self.after(0, lambda: self.download_btn.configure(state="normal", text="开始下载"))

    def _get_audio_url(self, page_url):
        """从播放页提取音频直链"""
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            resp = requests.get(page_url, headers=headers, timeout=10)
            resp.encoding = "utf-8"
            if resp.status_code != 200:
                return None
            # 查找 APlayer 初始化中的 url
            match = re.search(r"url:\s*'([^']+)'", resp.text)
            if match:
                return match.group(1)
            # 备选: 匹配所有可能的音频直链
            match = re.search(r"http[^\s\"']+\.m4a[^\s\"']*", resp.text)
            if match:
                return match.group(0)
        except Exception as e:
            self.log(f"获取音频链接失败: {e}")
        return None

    def _download_file(self, url, title, index):
        """下载单个文件，支持断点续传"""
        safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)
        filename = f"{index:03d}_{safe_title}.m4a"
        filepath = os.path.join(self.save_folder, filename)

        # 检查已存在文件
        if os.path.exists(filepath):
            self.log(f"文件已存在，跳过: {filename}")
            return

        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            with requests.get(url, headers=headers, stream=True, timeout=30) as r:
                r.raise_for_status()
                total_size = int(r.headers.get('content-length', 0))
                downloaded = 0
                with open(filepath, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if self.cancelled:
                            f.close()
                            os.remove(filepath)
                            return
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                self.log(f"下载完成: {filename}")
        except Exception as e:
            self.log(f"下载失败 {filename}: {e}")
            if os.path.exists(filepath):
                os.remove(filepath)

    def stop_download(self):
        self.cancelled = True
        self.log("正在停止...")

# 程序入口
if __name__ == "__main__":
    app = AudioBookDownloader()
    app.mainloop()
