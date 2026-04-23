import os
import re
import time
import threading
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# -------------------- 非法字符处理 --------------------
def safe_filename(s: str, replace_char: str = '_') -> str:
    illegal_chars = r'[\\/:*?"<>|]'
    return re.sub(illegal_chars, replace_char, s)

# -------------------- 本地下载记录数据库 --------------------
class DownloadDB:
    def __init__(self, db_file='download_records.json'):
        self.db_file = db_file
        self.data = self.load()

    def load(self):
        if os.path.exists(self.db_file):
            with open(self.db_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save(self):
        with open(self.db_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def get_book_record(self, book_id):
        return self.data.get(str(book_id), {})

    def update_chapter_status(self, book_id, chapter_index, status, filename=''):
        book_id = str(book_id)
        if book_id not in self.data:
            self.data[book_id] = {'chapters': {}}
        self.data[book_id]['chapters'][str(chapter_index)] = {
            'status': status,
            'filename': filename,
            'time': time.time()
        }
        self.save()

    def get_book_stats(self, book_id):
        rec = self.get_book_record(book_id)
        chapters = rec.get('chapters', {})
        total = len(chapters)
        success = sum(1 for c in chapters.values() if c['status'] == 'success')
        failed = sum(1 for c in chapters.values() if c['status'] == 'failed')
        return {'total': total, 'success': success, 'failed': failed, 'chapters': chapters}

# -------------------- 核心下载逻辑 --------------------
class AudioDownloader:
    def __init__(self, db, progress_callback=None, log_callback=None):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://m.i275.com/',
        })
        self.db = db
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.is_running = False
        self.cancel_flag = False

    def log(self, msg):
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg)

    def update_progress(self, current, total):
        if self.progress_callback:
            self.progress_callback(current, total)

    def search(self, keyword):
        """搜索返回书籍列表"""
        url = f"https://m.i275.com/search.php?q={keyword}"
        resp = self.session.get(url, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        results = []
        for item in soup.select('a[href*="/book/"]'):
            if not item.select_one('img'):
                continue
            href = item.get('href')
            match = re.search(r'/book/(\d+)\.html', href)
            if not match:
                continue
            book_id = match.group(1)
            img = item.select_one('img')
            cover = img.get('src') if img else ''
            title_elem = item.select_one('h3') or item.select_one('.font-medium')
            title = title_elem.text.strip() if title_elem else '未知'
            author_elem = item.select_one('.text-xs')
            author = author_elem.text.strip() if author_elem else ''
            results.append({
                'id': book_id,
                'title': title,
                'author': author,
                'cover': cover,
                'url': urljoin(url, href)
            })
        return results

    def fetch_book_details(self, book_url):
        """获取书籍详情和章节列表"""
        resp = self.session.get(book_url, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        book_title = safe_filename(soup.select_one('h1').text.strip() if soup.select_one('h1') else '未知书名')
        chapters = []
        for a in soup.select('a[href*="/play/"]'):
            href = a.get('href')
            if not href:
                continue
            full_url = urljoin(book_url, href)
            title_span = a.select_one('span.text-sm')
            chapter_title = title_span.text.strip() if title_span else f"章节{len(chapters)+1}"
            chapters.append({
                'url': full_url,
                'title': safe_filename(chapter_title)
            })
        return book_title, chapters

    def extract_audio_url(self, play_url):
        resp = self.session.get(play_url, timeout=15)
        resp.encoding = 'utf-8'
        html = resp.text
        match = re.search(r"url:\s*['\"]([^'\"]+\.m4a[^'\"]*)['\"]", html)
        if match:
            audio_url = match.group(1).replace('\\', '')
            return audio_url
        match = re.search(r"url:\s*['\"]([^'\"]+)['\"]", html)
        if match:
            return match.group(1).replace('\\', '')
        raise Exception("未找到音频链接")

    def download_audio(self, audio_url, save_path):
        resp = self.session.get(audio_url, stream=True, timeout=30)
        total_size = int(resp.headers.get('content-length', 0))
        downloaded = 0
        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if self.cancel_flag:
                    raise Exception("下载被用户取消")
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size:
                        self.update_progress(downloaded, total_size)
        return True

    def download_chapters(self, book_id, book_title, chapters, output_dir, selected_indices=None):
        self.is_running = True
        self.cancel_flag = False
        book_dir = os.path.join(output_dir, book_title)
        os.makedirs(book_dir, exist_ok=True)

        if selected_indices is None:
            selected_indices = list(range(len(chapters)))

        total = len(selected_indices)
        for seq, idx in enumerate(selected_indices, 1):
            if self.cancel_flag:
                self.log("任务已取消")
                break
            chap = chapters[idx]
            chap_title = chap['title']
            self.log(f"[{seq}/{total}] 正在处理: {chap_title}")
            try:
                audio_url = self.extract_audio_url(chap['url'])
                ext = os.path.splitext(urlparse(audio_url).path)[1] or '.m4a'
                filename = f"{idx+1:03d}_{chap_title}{ext}"
                save_path = os.path.join(book_dir, filename)
                self.download_audio(audio_url, save_path)
                self.db.update_chapter_status(book_id, idx, 'success', filename)
                self.log(f"    ✓ 已保存: {filename}")
            except Exception as e:
                self.db.update_chapter_status(book_id, idx, 'failed')
                self.log(f"    ✗ 失败: {str(e)}")
            time.sleep(1)
        self.log("下载任务结束")
        self.is_running = False
        self.update_progress(0, 0)

    def stop(self):
        self.cancel_flag = True
        self.is_running = False

# -------------------- GUI 界面 --------------------
class DownloaderGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("275听书网批量下载工具 - 带搜索")
        self.root.geometry("950x700")
        self.db = DownloadDB()
        self.downloader = None
        self.thread = None
        self.output_dir = tk.StringVar(value=os.getcwd())
        self.search_keyword = tk.StringVar()
        self.current_book = None
        self.current_chapters = []
        self.setup_ui()

    def setup_ui(self):
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # 左侧搜索区
        left_frame = ttk.Frame(main_paned, width=350)
        main_paned.add(left_frame, weight=1)

        search_frame = ttk.LabelFrame(left_frame, text="搜索有声小说", padding="5")
        search_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Entry(search_frame, textvariable=self.search_keyword).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(search_frame, text="搜索", command=self.do_search).pack(side=tk.LEFT, padx=5)

        result_frame = ttk.LabelFrame(left_frame, text="搜索结果", padding="5")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ('title', 'author', 'stats')
        self.result_tree = ttk.Treeview(result_frame, columns=columns, show='tree headings', height=15)
        self.result_tree.heading('#0', text='ID')
        self.result_tree.heading('title', text='书名')
        self.result_tree.heading('author', text='演播/作者')
        self.result_tree.heading('stats', text='完成/失败/总共')
        self.result_tree.column('#0', width=50, stretch=False)
        self.result_tree.column('title', width=150)
        self.result_tree.column('author', width=100)
        self.result_tree.column('stats', width=120)
        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll_y = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.result_tree.yview)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.result_tree.configure(yscrollcommand=scroll_y.set)

        self.result_tree.bind('<Double-1>', self.on_book_select)

        # 右侧详情区
        right_frame = ttk.Frame(main_paned, width=600)
        main_paned.add(right_frame, weight=2)

        info_frame = ttk.LabelFrame(right_frame, text="书籍信息", padding="5")
        info_frame.pack(fill=tk.X, padx=5, pady=5)
        self.lbl_book_title = ttk.Label(info_frame, text="未选择书籍", font=('', 12, 'bold'))
        self.lbl_book_title.pack(anchor=tk.W)

        chap_frame = ttk.LabelFrame(right_frame, text="章节列表", padding="5")
        chap_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns_chap = ('index', 'title', 'status')
        self.chap_tree = ttk.Treeview(chap_frame, columns=columns_chap, show='tree headings', selectmode='extended')
        self.chap_tree.heading('#0', text='')
        self.chap_tree.heading('index', text='序号')
        self.chap_tree.heading('title', text='章节标题')
        self.chap_tree.heading('status', text='状态')
        self.chap_tree.column('#0', width=0, stretch=False)
        self.chap_tree.column('index', width=50, stretch=False)
        self.chap_tree.column('title', width=300)
        self.chap_tree.column('status', width=80)
        self.chap_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        chap_scroll = ttk.Scrollbar(chap_frame, orient=tk.VERTICAL, command=self.chap_tree.yview)
        chap_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.chap_tree.configure(yscrollcommand=chap_scroll.set)

        btn_frame = ttk.Frame(right_frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(btn_frame, text="刷新章节", command=self.refresh_chapters).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="下载选中章节", command=self.download_selected).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="下载全部未完成", command=self.download_all_incomplete).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="停止", command=self.stop_download).pack(side=tk.LEFT, padx=5)

        dir_frame = ttk.Frame(right_frame)
        dir_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(dir_frame, text="保存到:").pack(side=tk.LEFT)
        ttk.Entry(dir_frame, textvariable=self.output_dir, width=40).pack(side=tk.LEFT, padx=5, expand=True, fill=tk.X)
        ttk.Button(dir_frame, text="浏览", command=self.browse_dir).pack(side=tk.LEFT)

        self.progress_bar = ttk.Progressbar(right_frame, mode='determinate')
        self.progress_bar.pack(fill=tk.X, padx=5, pady=5)

        log_frame = ttk.LabelFrame(right_frame, text="日志", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def browse_dir(self):
        dir_path = filedialog.askdirectory()
        if dir_path:
            self.output_dir.set(dir_path)

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.root.update_idletasks()

    def update_progress(self, current, total):
        if total > 0:
            self.progress_bar['value'] = (current / total) * 100
        else:
            self.progress_bar['value'] = 0
        self.root.update_idletasks()

    def do_search(self):
        keyword = self.search_keyword.get().strip()
        if not keyword:
            return
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self.log(f"搜索: {keyword}")
        try:
            dl = AudioDownloader(self.db)
            results = dl.search(keyword)
            for r in results:
                stats = self.db.get_book_stats(r['id'])
                stats_str = f"{stats['success']}/{stats['failed']}/{stats['total']}"
                self.result_tree.insert('', 'end', iid=r['id'], text=r['id'],
                                        values=(r['title'], r['author'], stats_str))
            self.log(f"找到 {len(results)} 条结果")
        except Exception as e:
            self.log(f"搜索失败: {e}")

    def on_book_select(self, event):
        sel = self.result_tree.selection()
        if not sel:
            return
        book_id = sel[0]
        item = self.result_tree.item(book_id)
        title = item['values'][0]
        author = item['values'][1]
        self.current_book = {
            'id': book_id,
            'title': title,
            'author': author,
            'url': f"https://m.i275.com/book/{book_id}.html"
        }
        self.lbl_book_title.config(text=f"{title} - {author}")
        self.refresh_chapters()

    def refresh_chapters(self):
        if not self.current_book:
            return
        for item in self.chap_tree.get_children():
            self.chap_tree.delete(item)
        self.log(f"正在获取 {self.current_book['title']} 的章节列表...")
        try:
            dl = AudioDownloader(self.db)
            book_title, chapters = dl.fetch_book_details(self.current_book['url'])
            self.current_chapters = chapters
            stats = self.db.get_book_stats(self.current_book['id'])
            for i, chap in enumerate(chapters):
                chap_stat = stats['chapters'].get(str(i), {}).get('status', '未下载')
                status_disp = {'success': '已完成', 'failed': '失败', 'pending': '待下载'}.get(chap_stat, chap_stat)
                self.chap_tree.insert('', 'end', iid=str(i), values=(i+1, chap['title'], status_disp))
            self.update_result_stats(self.current_book['id'])
            self.log(f"共 {len(chapters)} 章")
        except Exception as e:
            self.log(f"获取章节失败: {e}")

    def update_result_stats(self, book_id):
        stats = self.db.get_book_stats(book_id)
        stats_str = f"{stats['success']}/{stats['failed']}/{stats['total']}"
        if self.result_tree.exists(book_id):
            values = list(self.result_tree.item(book_id, 'values'))
            values[2] = stats_str
            self.result_tree.item(book_id, values=values)

    def download_selected(self):
        if not self.current_book:
            return
        selected = self.chap_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择要下载的章节")
            return
        indices = [int(i) for i in selected]
        self.start_download(indices)

    def download_all_incomplete(self):
        if not self.current_book:
            return
        stats = self.db.get_book_record(self.current_book['id'])
        chapters_dict = stats.get('chapters', {})
        indices = [i for i in range(len(self.current_chapters))
                   if chapters_dict.get(str(i), {}).get('status') != 'success']
        if not indices:
            messagebox.showinfo("提示", "所有章节都已下载完成")
            return
        self.start_download(indices)

    def start_download(self, indices):
        output_dir = self.output_dir.get()
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        self.downloader = AudioDownloader(
            self.db,
            progress_callback=self.update_progress,
            log_callback=self.log
        )
        book_id = self.current_book['id']
        book_title = safe_filename(self.current_book['title'])
        self.thread = threading.Thread(
            target=self.downloader.download_chapters,
            args=(book_id, book_title, self.current_chapters, output_dir, indices),
            daemon=True
        )
        self.thread.start()
        self.check_thread()

    def stop_download(self):
        if self.downloader:
            self.downloader.stop()
        self.log("停止指令已发出")

    def check_thread(self):
        if self.thread and self.thread.is_alive():
            self.root.after(200, self.check_thread)
        else:
            self.log("下载线程结束")
            self.refresh_chapters()

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = DownloaderGUI()
    app.run()
