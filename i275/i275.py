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
    '/':  '／',
    ':':  '：',
    '*':  '＊',
    '?':  '？',
    '"':  '＂',
    '<':  '＜',
    '>':  '＞',
    '|':  '｜',
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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://m.i275.com/',
        })
        self.log_callback = log_callback
        self.stats_callback = stats_callback
        self.cancel_flag = False

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
            cover = img.get('src') or ''
            title_tag = a.select_one('h3') or a.select_one('.font-medium')
            title = title_tag.text.strip() if title_tag else '未知书名'
            narrator_tag = a.select_one('.text-xs.text-gray-500')
            narrator = narrator_tag.text.strip() if narrator_tag else ''
            results.append((title, narrator, cover, detail_url))
        return results

    def fetch_chapters(self, detail_url: str):
        resp = self.session.get(detail_url, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        book_title = safe_filename(soup.select_one('h1').text.strip() if soup.select_one('h1') else '未知书名')
        chapters = []
        chapter_links = soup.select('a[id^="chapter-pos-"]')
        if not chapter_links:
            container = soup.select_one('div.mt-3.bg-white')
            if container:
                chapter_links = container.select('a[href*="/play/"]')
            else:
                all_play_links = soup.select('a[href*="/play/"]')
                chapter_links = [a for a in all_play_links if a.get('id', '').startswith('chapter-pos-')]
        for a in chapter_links:
            href = a.get('href')
            if not href or '/play/' not in href:
                continue
            play_url = urljoin(detail_url, href)
            title_span = a.select_one('span.text-sm')
            if title_span:
                chap_title = title_span.text.strip()
            else:
                full_text = a.get_text(strip=True)
                chap_title = re.sub(r'^\d+\.\s*', '', full_text)
            if not chap_title:
                chap_title = f"章节{len(chapters)+1}"
            chapters.append((safe_filename(chap_title), play_url))
        return book_title, chapters

    def extract_audio_url(self, play_url: str):
        resp = self.session.get(play_url, timeout=15)
        resp.encoding = 'utf-8'
        html = resp.text
        match = re.search(r"url:\s*['\"]([^'\"]+\.m4a[^'\"]*)['\"]", html)
        if match:
            return match.group(1).replace('\\', '')
        match = re.search(r"url:\s*['\"]([^'\"]+)['\"]", html)
        if match:
            return match.group(1).replace('\\', '')
        raise Exception("未找到音频链接")

    def download_audio(self, audio_url: str, save_path: str):
        resp = self.session.get(audio_url, stream=True, timeout=30)
        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if self.cancel_flag:
                    raise Exception("下载被用户取消")
                if chunk:
                    f.write(chunk)
        return True

    def download_selected(self, book_title: str, selected_chapters: list, output_dir: str):
        book_dir = os.path.join(output_dir, book_title)
        os.makedirs(book_dir, exist_ok=True)
        total = len(selected_chapters)
        completed = 0
        failed = 0
        self.update_stats(completed, failed, total)
        for chap_title, play_url in selected_chapters:
            if self.cancel_flag:
                self.log("任务已取消")
                break
            self.log(f"[{completed+failed+1}/{total}] {chap_title}")
            try:
                audio_url = self.extract_audio_url(play_url)
                ext = os.path.splitext(urlparse(audio_url).path)[1] or '.m4a'
                filename = f"{chap_title}{ext}"
                save_path = os.path.join(book_dir, filename)
                self.download_audio(audio_url, save_path)
                self.log(f"  ✓ 已保存: {filename}")
                completed += 1
            except Exception as e:
                self.log(f"  ✗ 失败: {str(e)}")
                failed += 1
            self.update_stats(completed, failed, total)
            time.sleep(1)
        self.log(f"下载完成。成功 {completed} 个，失败 {failed} 个，总计 {total} 个。")

    def stop(self):
        self.cancel_flag = True

class DownloaderApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("275听书网批量下载工具 v2.0")
        self.root.geometry("900x700")
        self.root.resizable(True, True)

        self.downloader = None
        self.thread = None
        self.output_dir = tk.StringVar(value=os.getcwd())
        self.search_keyword = tk.StringVar()
        self.current_book_title = ""
        self.chapters = []
        self.check_vars = []

        self.setup_ui()

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        search_frame = ttk.LabelFrame(main_frame, text="搜索书籍", padding="10")
        search_frame.pack(fill=tk.X, pady=5)

        ttk.Label(search_frame, text="关键词:").grid(row=0, column=0, padx=5)
        ttk.Entry(search_frame, textvariable=self.search_keyword, width=40).grid(row=0, column=1, padx=5)
        ttk.Button(search_frame, text="搜索", command=self.start_search).grid(row=0, column=2, padx=5)
        ttk.Button(search_frame, text="停止", command=self.stop_download, state=tk.DISABLED).grid(row=0, column=3, padx=5)

        list_frame = ttk.LabelFrame(main_frame, text="搜索结果（双击选择）", padding="5")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        columns = ('title', 'narrator')
        self.tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=6)
        self.tree.heading('title', text='书名')
        self.tree.heading('narrator', text='演播')
        self.tree.column('title', width=400)
        self.tree.column('narrator', width=200)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<Double-1>', self.on_book_select)

        chapter_frame = ttk.LabelFrame(main_frame, text="章节列表（勾选需要下载的章节）", padding="5")
        chapter_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        btn_bar = ttk.Frame(chapter_frame)
        btn_bar.pack(fill=tk.X, pady=2)
        ttk.Button(btn_bar, text="全选", command=self.select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_bar, text="反选", command=self.invert_selection).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_bar, text="全不选", command=self.select_none).pack(side=tk.LEFT, padx=2)

        canvas_frame = ttk.Frame(chapter_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        self.chapter_canvas = tk.Canvas(canvas_frame, borderwidth=0, highlightthickness=0)
        chapter_scroll = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.chapter_canvas.yview)
        self.chapter_canvas.configure(yscrollcommand=chapter_scroll.set)

        self.inner_frame = ttk.Frame(self.chapter_canvas)
        self.inner_frame.bind("<Configure>", lambda e: self.chapter_canvas.configure(scrollregion=self.chapter_canvas.bbox("all")))
        self.chapter_canvas.create_window((0,0), window=self.inner_frame, anchor="nw")
        self.chapter_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        chapter_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        dir_frame = ttk.Frame(main_frame)
        dir_frame.pack(fill=tk.X, pady=5)
        ttk.Label(dir_frame, text="保存到:").pack(side=tk.LEFT)
        ttk.Entry(dir_frame, textvariable=self.output_dir, width=50).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        ttk.Button(dir_frame, text="浏览", command=self.browse_dir).pack(side=tk.LEFT)
        self.download_btn = ttk.Button(dir_frame, text="开始下载选中章节", command=self.start_download, state=tk.DISABLED)
        self.download_btn.pack(side=tk.LEFT, padx=5)

        stats_frame = ttk.Frame(main_frame)
        stats_frame.pack(fill=tk.X, pady=5)
        self.stats_label = ttk.Label(stats_frame, text="已完成: 0  失败: 0  总计: 0", font=('', 10, 'bold'))
        self.stats_label.pack()

        log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def browse_dir(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.output_dir.set(dir_path)

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def update_stats_display(self, completed, failed, total):
        self.stats_label.config(text=f"已完成: {completed}  失败: {failed}  总计: {total}")
        self.root.update_idletasks()

    def start_search(self):
        keyword = self.search_keyword.get().strip()
        if not keyword:
            messagebox.showerror("错误", "请输入搜索关键词")
            return
        self.log(f"搜索: {keyword}")
        self.tree.delete(*self.tree.get_children())
        self.download_btn.config(state=tk.DISABLED)
        for w in self.inner_frame.winfo_children():
            w.destroy()
        self.chapters = []
        self.check_vars = []

        self.downloader = AudioDownloader(log_callback=self.log)
        self.thread = threading.Thread(target=self._search_thread, args=(keyword,), daemon=True)
        self.thread.start()

    def _search_thread(self, keyword):
        try:
            results = self.downloader.search_books(keyword)
            self.root.after(0, self._populate_search_results, results)
        except Exception as e:
            self.root.after(0, self.log, f"搜索出错: {e}")

    def _populate_search_results(self, results):
        for title, narrator, cover, url in results:
            self.tree.insert('', tk.END, values=(title, narrator), tags=(url,))
        self.log(f"找到 {len(results)} 个结果")

    def on_book_select(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        values = self.tree.item(item, 'values')
        url = self.tree.item(item, 'tags')[0]
        title = values[0]
        self.log(f"加载书籍: {title}")
        self.download_btn.config(state=tk.DISABLED)
        for w in self.inner_frame.winfo_children():
            w.destroy()
        self.chapters = []
        self.check_vars = []
        self.current_book_title = title

        self.downloader = AudioDownloader(log_callback=self.log)
        self.thread = threading.Thread(target=self._fetch_chapters_thread, args=(url,), daemon=True)
        self.thread.start()

    def _fetch_chapters_thread(self, url):
        try:
            book_title, chapters = self.downloader.fetch_chapters(url)
            self.root.after(0, self._populate_chapters, book_title, chapters)
        except Exception as e:
            self.root.after(0, self.log, f"获取章节失败: {e}")

    def _populate_chapters(self, book_title, chapters):
        self.current_book_title = book_title
        self.chapters = chapters
        self.check_vars = []
        for i, (title, _) in enumerate(chapters):
            var = tk.BooleanVar(value=True)
            self.check_vars.append(var)
            cb = ttk.Checkbutton(self.inner_frame, text=f"{i+1:03d}. {title}", variable=var)
            cb.pack(anchor=tk.W, pady=1)
        self.log(f"共 {len(chapters)} 章，默认全选")
        self.download_btn.config(state=tk.NORMAL)

    def select_all(self):
        for var in self.check_vars:
            var.set(True)

    def select_none(self):
        for var in self.check_vars:
            var.set(False)

    def invert_selection(self):
        for var in self.check_vars:
            var.set(not var.get())

    def start_download(self):
        if not self.chapters:
            return
        selected = []
        for i, var in enumerate(self.check_vars):
            if var.get():
                selected.append(self.chapters[i])
        if not selected:
            messagebox.showinfo("提示", "未选择任何章节")
            return
        output_dir = self.output_dir.get()
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
            except:
                messagebox.showerror("错误", "无法创建输出目录")
                return

        self.download_btn.config(state=tk.DISABLED)
        self.update_stats_display(0, 0, len(selected))
        self.log(f"开始下载 {len(selected)} 个章节...")

        self.downloader = AudioDownloader(
            log_callback=self.log,
            stats_callback=self.update_stats_display
        )
        self.thread = threading.Thread(
            target=self.downloader.download_selected,
            args=(self.current_book_title, selected, output_dir),
            daemon=True
        )
        self.thread.start()
        self.check_thread()

    def stop_download(self):
        if self.downloader:
            self.downloader.stop()
        self.log("正在停止...")

    def check_thread(self):
        if self.thread and self.thread.is_alive():
            self.root.after(200, self.check_thread)
        else:
            self.download_btn.config(state=tk.NORMAL)
            self.log("====== 任务结束 ======")

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = DownloaderApp()
    app.run()
