import os
import sys
import json
import time
import threading
from pathlib import Path
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from tkinter.ttk import Progressbar

BASE_URL = "https://m.i275.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

ILLEGAL_CHAR_MAP = {
    '<': '《', '>': '》', ':': '：', '"': '“', '/': '／',
    '\\': '＼', '|': '｜', '?': '？', '*': '＊'
}

def sanitize_filename(name):
    for illegal, replacement in ILLEGAL_CHAR_MAP.items():
        name = name.replace(illegal, replacement)
    name = name.strip('. ')
    return name

def fetch_url(url):
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    return resp.text

def search_books(keyword):
    url = f"{BASE_URL}/search.php?q={quote(keyword)}"
    html = fetch_url(url)
    soup = BeautifulSoup(html, 'html.parser')
    books = []
    for a_tag in soup.select('a[href^="/book/"]'):
        parent = a_tag.find_parent('div', class_='flex') or a_tag.find_parent('a')
        if not parent:
            continue
        title_elem = parent.select_one('h3') or a_tag.find_next('h3')
        anchor_elem = parent.select_one('p span') or parent.find('span', class_='bg-gray-100')
        if not title_elem:
            continue
        title = title_elem.get_text(strip=True)
        anchor = anchor_elem.get_text(strip=True) if anchor_elem else "未知演播"
        book_url = a_tag['href']
        books.append((title, anchor, book_url))
    seen = set()
    unique_books = []
    for book in books:
        if book[2] not in seen:
            seen.add(book[2])
            unique_books.append(book)
    return unique_books

def get_book_info(book_url):
    url = BASE_URL + book_url
    html = fetch_url(url)
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.select_one('h1').get_text(strip=True) if soup.select_one('h1') else "未知书名"
    info_div = soup.find('div', class_='space-y-1')
    anchor = "未知"
    if info_div:
        for p in info_div.find_all('p'):
            text = p.get_text()
            if '演播' in text:
                anchor = text.split('：')[-1].strip()
                break
    chapters = []
    for a in soup.select('a[id^="chapter-pos-"]'):
        href = a.get('href', '')
        chapter_title = a.select_one('span.text-sm')
        if not chapter_title:
            continue
        chapter_name = chapter_title.get_text(strip=True)
        chapters.append((chapter_name, href))
    return title, anchor, chapters

def get_audio_url(play_url):
    url = BASE_URL + play_url
    html = fetch_url(url)
    pattern = r"url:\s*'([^']+)'"
    match = re.search(pattern, html)
    if not match:
        raise RuntimeError("未找到音频地址")
    audio_url = match.group(1).replace('\\/', '/')
    return audio_url

class DownloadAborted(Exception):
    pass

class AudioDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("275听书网有声小说下载器")
        self.root.geometry("900x650")
        self.root.minsize(800, 600)

        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure('TButton', font=('微软雅黑', 10), padding=6)
        self.style.configure('TLabel', font=('微软雅黑', 10))
        self.style.configure('TEntry', font=('微软雅黑', 10))

        self.book_title = None
        self.anchor = None
        self.chapters = []
        self.save_path = os.getcwd()
        self.downloading = False
        self.failed_chapters = []

        self.create_widgets()

    def create_widgets(self):
        top_frame = ttk.Frame(self.root, padding=10)
        top_frame.pack(fill=tk.X)

        ttk.Label(top_frame, text="搜索书名/播讲人：").pack(side=tk.LEFT, padx=(0,5))
        self.search_entry = ttk.Entry(top_frame, width=40)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0,5))
        self.search_entry.bind('<Return>', lambda e: self.start_search())

        search_btn = ttk.Button(top_frame, text="搜索", command=self.start_search)
        search_btn.pack(side=tk.LEFT)

        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0,10))

        left_frame = ttk.LabelFrame(main_paned, text="搜索结果", padding=5)
        main_paned.add(left_frame, weight=1)

        self.result_tree = ttk.Treeview(left_frame, columns=('title', 'anchor'), show='headings', height=15)
        self.result_tree.heading('title', text='书名')
        self.result_tree.heading('anchor', text='演播')
        self.result_tree.column('title', width=200, anchor='w')
        self.result_tree.column('anchor', width=120, anchor='w')
        self.result_tree.pack(fill=tk.BOTH, expand=True)
        self.result_tree.bind('<Double-1>', self.on_select_book)
        self.result_tree.bind('<Return>', self.on_select_book)

        right_frame = ttk.Frame(main_paned, padding=5)
        main_paned.add(right_frame, weight=2)

        info_frame = ttk.LabelFrame(right_frame, text="书籍信息", padding=10)
        info_frame.pack(fill=tk.X, pady=(0,10))

        self.info_label = ttk.Label(info_frame, text="尚未选择书籍", font=('微软雅黑', 12, 'bold'))
        self.info_label.pack(anchor='w')

        self.chapter_count_label = ttk.Label(info_frame, text="")
        self.chapter_count_label.pack(anchor='w', pady=(5,0))

        path_frame = ttk.Frame(info_frame)
        path_frame.pack(fill=tk.X, pady=(10,0))

        ttk.Label(path_frame, text="保存到：").pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value=self.save_path)
        path_entry = ttk.Entry(path_frame, textvariable=self.path_var, state='readonly', width=50)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(path_frame, text="选择目录", command=self.choose_directory).pack(side=tk.RIGHT)

        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(fill=tk.X, pady=(5,0))

        self.download_btn = ttk.Button(btn_frame, text="开始下载", width=15, command=self.start_download)
        self.download_btn.pack(side=tk.RIGHT, padx=(5,0))
        self.download_btn.configure(state=tk.DISABLED)

        self.stop_btn = ttk.Button(btn_frame, text="停止", width=10, command=self.stop_download, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT)

        log_frame = ttk.LabelFrame(right_frame, text="下载进度", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10,0))

        self.progress_var = tk.DoubleVar()
        self.progress_bar = Progressbar(log_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=(0,5))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, font=('Consolas', 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def start_search(self):
        keyword = self.search_entry.get().strip()
        if not keyword:
            return
        self.log(f"正在搜索：{keyword} ...")
        try:
            books = search_books(keyword)
        except Exception as e:
            messagebox.showerror("搜索失败", str(e))
            self.log(f"搜索失败：{e}")
            return
        if not books:
            self.log("未找到任何结果。")
            self.result_tree.delete(*self.result_tree.get_children())
            return
        self.result_tree.delete(*self.result_tree.get_children())
        for idx, (title, anchor, url) in enumerate(books):
            self.result_tree.insert('', 'end', iid=str(idx), values=(title, anchor), tags=(url,))
        self.log(f"搜索完成，共 {len(books)} 个结果。")

    def on_select_book(self, event=None):
        sel = self.result_tree.selection()
        if not sel:
            return
        item = sel[0]
        values = self.result_tree.item(item, 'values')
        tags = self.result_tree.item(item, 'tags')
        if not tags:
            return
        book_url = tags[0]
        title, anchor = values
        self.log(f"正在获取书籍详情：{title} - {anchor}")
        try:
            book_title, anchor_main, chapters = get_book_info(book_url)
        except Exception as e:
            messagebox.showerror("获取失败", str(e))
            self.log(f"获取详情失败：{e}")
            return
        self.book_title = book_title
        self.anchor = anchor_main
        self.chapters = chapters
        self.info_label.config(text=f"{book_title}　　{anchor_main} 演播")
        self.chapter_count_label.config(text=f"共 {len(chapters)} 章")
        self.download_btn.configure(state=tk.NORMAL)
        self.log(f"已选择：《{book_title}》共 {len(chapters)} 章。")

    def choose_directory(self):
        path = filedialog.askdirectory(initialdir=self.path_var.get())
        if path:
            self.path_var.set(path)
            self.save_path = path

    def start_download(self):
        if self.downloading:
            return
        if not self.chapters:
            messagebox.showwarning("提示", "请先选择一本有声书")
            return
        base_dir = self.save_path
        folder_name = sanitize_filename(f"{self.book_title}-{self.anchor}")
        save_folder = os.path.join(base_dir, folder_name)
        try:
            os.makedirs(save_folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror("创建文件夹失败", str(e))
            return
        self.downloading = True
        self.failed_chapters = []
        self.download_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.progress_var.set(0)
        threading.Thread(target=self.download_all, args=(save_folder,), daemon=True).start()

    def stop_download(self):
        self.downloading = False
        self.stop_btn.configure(state=tk.DISABLED)
        self.download_btn.configure(state=tk.NORMAL)
        self.log("用户中止下载。")

    def download_all(self, save_folder):
        total = len(self.chapters)
        downloaded = 0
        for idx, (chapter_name, play_url) in enumerate(self.chapters, 1):
            if not self.downloading:
                break
            safe_chapter = sanitize_filename(chapter_name)
            ext = ".m4a"
            file_path = os.path.join(save_folder, safe_chapter + ext)
            if os.path.exists(file_path):
                self.log(f"[{idx}/{total}] {chapter_name} 已存在，跳过")
                downloaded += 1
                self.progress_var.set((downloaded / total) * 100)
                continue

            success = False
            last_error = ""
            for attempt in range(1, 4):
                if not self.downloading:
                    break
                try:
                    audio_url = get_audio_url(play_url)
                    self.log(f"[{idx}/{total}] 正在下载：{chapter_name}（尝试 {attempt}/3）")
                    self._download_file(audio_url, file_path)
                    success = True
                    downloaded += 1
                    self.progress_var.set((downloaded / total) * 100)
                    break
                except DownloadAborted:
                    self.log(f"下载中止：{chapter_name}")
                    break
                except Exception as e:
                    last_error = str(e)
                    if attempt < 3:
                        self.log(f"下载失败，1秒后重试：{chapter_name} - {last_error}")
                        time.sleep(1)
                    else:
                        self.log(f"❌ 最终失败：{chapter_name} - {last_error}")

            if not success and self.downloading:
                self.failed_chapters.append({
                    "book": self.book_title,
                    "anchor": self.anchor,
                    "chapter": chapter_name,
                    "url": play_url,
                    "error": last_error
                })

        self.downloading = False
        self.root.after(0, self._download_finished, save_folder)

    def _download_file(self, url, save_path):
        with requests.get(url, headers=HEADERS, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(save_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if not self.downloading:
                        raise DownloadAborted("用户中止")
                    if chunk:
                        f.write(chunk)

    def _download_finished(self, save_folder):
        self.download_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        if self.failed_chapters:
            json_path = os.path.join(save_folder, "failed_chapters.json")
            try:
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(self.failed_chapters, f, ensure_ascii=False, indent=2)
                self.log(f"⚠️ 有 {len(self.failed_chapters)} 个章节下载失败，已记录至：{json_path}")
            except Exception as e:
                self.log(f"无法写入失败记录文件：{e}")
        else:
            self.log("✅ 所有章节下载完成。")

if __name__ == "__main__":
    root = tk.Tk()
    app = AudioDownloaderApp(root)
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.mainloop()
