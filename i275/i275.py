import os
import re
import time
import threading
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

ILLEGAL_CHAR_MAP = {
    '\\': '＼',
    '/': '／',
    ':': '：',
    '*': '＊',
    '?': '？',
    '"': '＂',
    '<': '＜',
    '>': '＞',
    '|': '｜'
}

def safe_filename(s: str) -> str:
    for illegal, safe in ILLEGAL_CHAR_MAP.items():
        s = s.replace(illegal, safe)
    s = s.strip().rstrip('.')
    return s

class AudioDownloader:
    def __init__(self, log_callback=None, stats_callback=None):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.log_callback = log_callback
        self.stats_callback = stats_callback
        self.cancel_flag = False
        self.failed_list = []

    def log(self, msg):
        if self.log_callback:
            self.log_callback(msg)

    def update_stats(self, completed, failed, total):
        if self.stats_callback:
            self.stats_callback(completed, failed, total)

    def search_books(self, keyword: str):
        url = f"https://m.i275.com/search.php?q={requests.utils.quote(keyword)}"
        resp = self.session.get(url, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        results = []
        for a in soup.select('a[href*="/book/"]'):
            img = a.select_one('img')
            if not img:
                continue
            href = a.get('href')
            detail_url = urljoin(url, href)
            title_tag = a.select_one('h3') or a.select_one('.font-medium')
            title = title_tag.text.strip() if title_tag else '未知书名'
            narrator_tag = a.select_one('.text-xs.text-gray-500')
            narrator = narrator_tag.text.strip() if narrator_tag else ''
            results.append((title, narrator, detail_url))
        return results

    def fetch_chapters(self, detail_url: str):
        resp = self.session.get(detail_url, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        book_title = safe_filename(soup.select_one('h1').text.strip() if soup.select_one('h1') else '未知书名')
        narrator_raw = soup.find(string=re.compile(r'演播[：:]\s*'))
        narrator = narrator_raw.text.strip() if narrator_raw else ''
        chapters = []
        for a in soup.select('a[id^="chapter-pos-"]'):
            href = a.get('href')
            play_url = urljoin(detail_url, href)
            title_span = a.select_one('span.text-sm')
            chap_title = title_span.text.strip() if title_span else f"章节{len(chapters)+1}"
            chapters.append((chap_title, play_url))
        return book_title, narrator, chapters

    def extract_audio_url(self, play_url: str):
        resp = self.session.get(play_url, timeout=15)
        resp.encoding = 'utf-8'
        html = resp.text
        match = re.search(r"url:\s*['\"]([^'\"]+\.m4a[^'\"]*)['\"]", html)
        if match:
            return match.group(1).replace('\\', '')
        raise Exception("未找到音频链接")

    def download_audio(self, audio_url: str, save_path: str):
        resp = self.session.get(audio_url, stream=True, timeout=30)
        total_size = int(resp.headers.get('content-length', 0))
        downloaded = 0
        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if self.cancel_flag:
                    raise Exception("下载被取消")
                f.write(chunk)
                downloaded += len(chunk)
                self.update_stats(downloaded, 0, total_size)
        return True

    def stop(self):
        self.cancel_flag = True

    def download_selected(self, selected_chapters: list, output_dir: str):
        book_dir = os.path.join(output_dir, self.folder_name)
        os.makedirs(book_dir, exist_ok=True)
        total = len(selected_chapters)
        completed = 0
        failed = 0
        for i, (chap_title, play_url) in enumerate(selected_chapters):
            self.log(f"[{i+1}/{total}] {chap_title}")
            try:
                audio_url = self.extract_audio_url(play_url)
                ext = os.path.splitext(urlparse(audio_url).path)[1]
                filename = f"{chap_title}{ext}"
                save_path = os.path.join(book_dir, filename)
                self.download_audio(audio_url, save_path)
                completed += 1
                self.update_stats(completed, failed, total)
            except Exception as e:
                self.log(f"  ✗ 失败: {str(e)}")
                failed += 1
                self.failed_list.append((chap_title, play_url))
        return completed, failed

class DownloaderGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("275听书网批量下载工具")
        self.root.geometry("1000x750")
        self.root.minsize(800, 600)
        self.root.configure(bg='#f0f0f0')
        self.output_dir = tk.StringVar(value=os.getcwd())
        self.search_keyword = tk.StringVar()
        self.downloader = None
        self.thread = None
        self.chapters = []
        self.check_vars = []
        self.failed_items = []
        self.setup_ui()
        self.setup_style()

    def setup_style(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background='#f0f0f0')
        style.configure('TLabel', background='#f0f0f0', font=('微软雅黑', 9))
        style.configure('TLabelframe', background='#f0f0f0')
        style.configure('Treeview', rowheight=25, font=('微软雅黑', 9))
        style.configure('Treeview.Heading', font=('微软雅黑', 9, 'bold'))
        style.configure('TButton', font=('微软雅黑', 9))
        style.configure('TEntry', fieldbackground='white')
        style.configure('TProgressbar', troughcolor='#e0e0e0')

    def setup_ui(self):
        main_panel = ttk.Frame(self.root)
        main_panel.pack(fill=tk.BOTH, expand=True)
        left_frame = ttk.Frame(main_panel)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right_frame = ttk.Frame(main_panel)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        search_frame = ttk.LabelFrame(left_frame, text="搜索书籍", padding=10)
        search_frame.pack(fill=tk.X, pady=5)
        ttk.Label(search_frame, text="关键词:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_keyword, width=35)
        self.search_entry.grid(row=0, column=1, padx=5, pady=5, sticky=tk.W)
        ttk.Button(search_frame, text="搜索", command=self.start_search).grid(row=0, column=2, padx=5, pady=5)

        list_frame = ttk.LabelFrame(left_frame, text="搜索结果", padding=10)
        list_frame.pack(fill=tk.X, pady=10)
        self.tree = ttk.Treeview(list_frame, columns=('title', 'narrator'), show='headings', height=15)
        self.tree.pack(fill=tk.BOTH, expand=True)
        self.tree.bind('<Double-1>', self.on_book_select)
        self.tree.heading('title', text='书名')
        self.tree.heading('narrator', text='演播')
        self.tree.column('title', width=350)
        self.tree.column('narrator', width=200)

        chapter_frame = ttk.LabelFrame(right_frame, text="章节列表", padding=10)
        chapter_frame.pack(fill=tk.BOTH, expand=True)
        canvas_frame = ttk.Frame(chapter_frame)
        canvas_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.chapter_canvas = tk.Canvas(canvas_frame, borderwidth=0, highlightthickness=0)
        self.chapter_canvas.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(chapter_frame, orient=tk.VERTICAL, command=self.chapter_canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.chapter_canvas.create_window(0, 0, anchor=tk.NW, window=self.chapter_canvas)
        self.chapter_canvas.bind('<Configure>', self.on_canvas_configure)
        self.chapter_canvas.bind('<MouseWheel>', self.on_mousewheel)

        self.inner_frame = ttk.Frame(self.chapter_canvas)
        self.inner_frame.pack(fill=tk.BOTH, expand=True)
        self.inner_frame.bind('<Configure>', self.on_frame_configure)
        self.chapter_canvas.create_window(0, 0, anchor=tk.NW, window=self.inner_frame)
        self.chapter_canvas.yview_moveto(0)

        self.log_text = scrolledtext.ScrolledText(chapter_frame, height=8, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.stats_label = ttk.Label(right_frame, text="下载统计")
        self.stats_label.pack(side=tk.TOP, fill=tk.X)

        self.retry_btn = ttk.Button(right_frame, text="重试失败章节", command=self.retry_failed)
        self.retry_btn.pack(side=tk.TOP, pady=10)

        self.progress_frame = ttk.Frame(self.root)
        self.progress_frame.pack(side=tk.TOP, fill=tk.X, pady=5)
        self.progress_bar = ttk.Progressbar(self.progress_frame, length=100, mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=5)
        self.progress_label = ttk.Label(self.progress_frame, text="0/0")
        self.progress_label.pack(side=tk.LEFT)

    def start_search(self):
        keyword = self.search_keyword.get()
        if not keyword:
            messagebox.showerror("错误", "请输入搜索关键词")
            return
        self.log(f"开始搜索: {keyword}")
        results = self.downloader.search_books(keyword)
        self.tree.delete(*self.tree.get_children())
        for title, narrator, url in results:
            self.tree.insert('', tk.END, values=(title, narrator, url))
        self.log(f"找到 {len(results)} 个结果")
        self.update_stats(0, 0, len(results))
        return results

    def on_book_select(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        item = self.tree.item(selection[0])
        values = self.tree.item(item, 'values')
        url = values[2] if len(values) > 2 else ''
        self.current_book_url = url
        self.log(f"选中书籍: {self.current_book_url}")
        self.fetch_chapters(self.current_book_url)

    def fetch_chapters(self, book_url: str):
        book_title, narrator, chapters = self.downloader.fetch_chapters(book_url)
        self.log(f"获取到 {len(chapters)} 章")
        self.chapters = chapters
        self.check_vars = []
        for i, (chap_title, play_url) in enumerate(chapters):
            self.log(f"[{i+1}/{len(chapters)}] {chap_title}")
            self.check_vars.append((chap_title, play_url))
        return self.chapters

    def download_selected(self):
        selected = []
        for i, var in enumerate(self.check_vars):
            if var.get():
                selected.append(var)
        if not selected:
            self.log("未选中任何章节")
            return
        self.log(f"选中 {len(selected)} 章")
        self.download_audio(selected)

    def download_audio(self, selected: list):
        output_dir = self.output_dir.get()
        folder_name = safe_filename(self.folder_name)
        save_path = os.path.join(output_dir, folder_name)
        self.log(f"保存到: {save_path}")
        completed = 0
        failed = 0
        for chap_title, play_url in selected:
            try:
                audio_url = self.extract_audio_url(play_url)
                ext = os.path.splitext(urlparse(audio_url).path)[1]
                filename = f"{chap_title}{ext}"
                save_path = os.path.join(output_dir, filename)
                self.download_audio(audio_url, save_path)
                completed += 1
                self.update_stats(completed, failed, len(selected))
            except Exception as e:
                self.log(f"  ✗ 下载失败: {str(e)}")
                failed += 1
                self.failed_list.append((chap_title, play_url))
        return completed, failed

    def retry_failed(self):
        if not self.failed_list:
            self.log("没有失败记录")
            return
        self.log(f"开始重试 {len(self.failed_list)} 个失败项")
        for i, (chap_title, play_url) in enumerate(self.failed_list):
            self.log(f"[{i+1}/{len(self.failed_list)}] 重试: {chap_title}")
            try:
                audio_url = self.extract_audio_url(play_url)
                self.download_audio(audio_url)
                self.failed_list.remove((chap_title, play_url))
            except Exception as e:
                self.log(f"  ✗ 重试失败: {str(e)}")
        return self.failed_list

    def stop_download(self):
        self.cancel_flag = True
        self.log("任务已停止")

class App:
    def __init__(self):
        self.gui = DownloaderGUI()
        self.gui.run()

if __name__ == "__main__":
    app = App()
    app.run()
