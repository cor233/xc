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
    '\\': '＼', '/': '／', ':': '：', '*': '＊',
    '?': '？', '"': '＂', '<': '＜', '>': '＞', '|': '｜'
}

def safe_filename(s: str) -> str:
    for illegal, safe in ILLEGAL_CHAR_MAP.items():
        s = s.replace(illegal, safe)
    return s.strip().rstrip('.')

class AudioDownloader:
    def __init__(self, log_callback=None, stats_callback=None):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0',
            'Referer': 'https://m.i275.com/'
        })
        self.log_callback = log_callback
        self.stats_callback = stats_callback
        self.cancel_flag = False
        self.paused_flag = False
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
            narrator_raw = narrator_tag.text.strip() if narrator_tag else ''
            narrator = re.sub(r'^演播[：:]*\s*', '', narrator_raw).strip()
            results.append((title, narrator, detail_url))
        return results

    def fetch_chapters(self, detail_url: str):
        resp = self.session.get(detail_url, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        title_h1 = soup.select_one('.bg-white h1') or soup.select_one('h1')
        book_title = safe_filename(title_h1.text.strip()) if title_h1 else '未知书名'
        narrator = ''
        narrator_p = soup.find('p', string=re.compile(r'演播[：:]'))
        if narrator_p:
            narrator_span = narrator_p.find('span', class_='text-gray-800')
            if narrator_span:
                narrator = narrator_span.text.strip()
            else:
                full_text = narrator_p.get_text(strip=True)
                narrator = re.sub(r'^.*?演播[：:]\s*', '', full_text).strip()
        if not narrator:
            narrator_span = soup.select_one('p:contains("演播") span.text-gray-800')
            if narrator_span:
                narrator = narrator_span.text.strip()
        if not narrator:
            match = re.search(r'演播[：:]\s*<span[^>]*class="text-gray-800"[^>]*>([^<]+)</span>', resp.text)
            if match:
                narrator = match.group(1).strip()
        chapters = []
        for a in soup.select('a[id^="chapter-pos-"]'):
            href = a.get('href')
            if not href:
                continue
            play_url = urljoin(detail_url, href)
            title_span = a.select_one('span.text-sm')
            chap_title = title_span.text.strip() if title_span else f"章节{len(chapters)+1}"
            chap_title = re.sub(r'^\d+\.\s*', '', chap_title)
            chapters.append((safe_filename(chap_title), play_url))
        return book_title, narrator, chapters

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
        content_type = resp.headers.get('Content-Type', '')
        if 'audio' not in content_type and 'octet-stream' not in content_type:
            raise Exception(f"非音频响应: {content_type}")
        total_size = int(resp.headers.get('content-length', 0))
        downloaded = 0
        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if self.cancel_flag:
                    raise Exception("下载被取消")
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
        if total_size > 0 and downloaded < total_size * 0.9:
            raise Exception(f"文件不完整: {downloaded}/{total_size}")
        if os.path.getsize(save_path) < 10240:
            os.remove(save_path)
            raise Exception("文件过小，可能为错误页面")

    def download_selected(self, folder_name: str, selected_chapters: list, output_dir: str):
        book_dir = os.path.join(output_dir, folder_name)
        os.makedirs(book_dir, exist_ok=True)
        total = len(selected_chapters)
        completed = 0
        failed = 0
        self.failed_list = []
        self.update_stats(completed, failed, total)
        for chap_title, play_url in selected_chapters:
            while self.paused_flag and not self.cancel_flag:
                time.sleep(0.5)
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
                self.log(f"  ✓ 已保存")
                completed += 1
            except Exception as e:
                self.log(f"  ✗ 失败: {str(e)}")
                failed += 1
                self.failed_list.append((chap_title, play_url))
            self.update_stats(completed, failed, total)
            time.sleep(1)
        return completed, failed, self.failed_list

    def retry_failed(self, folder_name: str, failed_list: list, output_dir: str):
        if not failed_list:
            return 0, 0, []
        book_dir = os.path.join(output_dir, folder_name)
        os.makedirs(book_dir, exist_ok=True)
        total = len(failed_list)
        completed = 0
        failed = 0
        new_failed = []
        self.update_stats(0, 0, total)
        for chap_title, play_url in failed_list:
            while self.paused_flag and not self.cancel_flag:
                time.sleep(0.5)
            if self.cancel_flag:
                break
            self.log(f"[重试] {chap_title}")
            try:
                audio_url = self.extract_audio_url(play_url)
                ext = os.path.splitext(urlparse(audio_url).path)[1] or '.m4a'
                filename = f"{chap_title}{ext}"
                save_path = os.path.join(book_dir, filename)
                self.download_audio(audio_url, save_path)
                self.log(f"  ✓ 成功")
                completed += 1
            except Exception as e:
                self.log(f"  ✗ 失败: {str(e)}")
                failed += 1
                new_failed.append((chap_title, play_url))
            self.update_stats(completed, failed, total)
            time.sleep(1)
        return completed, failed, new_failed

    def pause(self):
        self.paused_flag = True

    def resume(self):
        self.paused_flag = False

    def stop(self):
        self.cancel_flag = True
        self.paused_flag = False

class DownloaderApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("i275听书网下载工具")
        self.root.geometry("1000x700")
        self.root.minsize(900, 600)
        self.root.configure(bg='#f5f5f5')

        self.downloader = None
        self.thread = None
        self.output_dir = tk.StringVar(value=os.getcwd())
        self.search_keyword = tk.StringVar()
        self.folder_name = ""
        self.chapters = []
        self.failed_items = []
        self.current_book_url = ""

        self.setup_style()
        self.setup_ui()

    def setup_style(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background='#f5f5f5')
        style.configure('TLabel', background='#f5f5f5', font=('微软雅黑', 9))
        style.configure('TLabelframe', background='#f5f5f5', font=('微软雅黑', 10, 'bold'))
        style.configure('TLabelframe.Label', background='#f5f5f5')
        style.configure('TButton', font=('微软雅黑', 9))
        style.configure('Treeview', font=('微软雅黑', 9), rowheight=25)
        style.configure('Treeview.Heading', font=('微软雅黑', 9, 'bold'))

    def setup_ui(self):
        main_panel = ttk.Frame(self.root)
        main_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left_frame = ttk.Frame(main_panel)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        search_frame = ttk.LabelFrame(left_frame, text="搜索书籍", padding=10)
        search_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(search_frame, text="关键词:").grid(row=0, column=0, padx=5, sticky=tk.W)
        ttk.Entry(search_frame, textvariable=self.search_keyword, width=35).grid(row=0, column=1, padx=5, sticky=tk.W)
        ttk.Button(search_frame, text="搜索", command=self.start_search).grid(row=0, column=2, padx=5)
        self.stop_btn = ttk.Button(search_frame, text="停止", command=self.stop_download, state=tk.DISABLED)
        self.stop_btn.grid(row=0, column=3, padx=5)

        list_frame = ttk.LabelFrame(left_frame, text="搜索结果（双击选择）", padding=10)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        columns = ('title', 'narrator')
        self.tree = ttk.Treeview(list_frame, columns=columns, show='headings', height=10)
        self.tree.heading('title', text='书名')
        self.tree.heading('narrator', text='演播')
        self.tree.column('title', width=350)
        self.tree.column('narrator', width=200)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tree_scroll = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind('<Double-1>', self.on_book_select)

        chapter_frame = ttk.LabelFrame(left_frame, text="章节列表（可多选）", padding=10)
        chapter_frame.pack(fill=tk.BOTH, expand=True)

        tool_bar = ttk.Frame(chapter_frame)
        tool_bar.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(tool_bar, text="全选", command=self.select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(tool_bar, text="反选", command=self.invert_selection).pack(side=tk.LEFT, padx=2)
        ttk.Button(tool_bar, text="全不选", command=self.select_none).pack(side=tk.LEFT, padx=2)

        listbox_frame = ttk.Frame(chapter_frame)
        listbox_frame.pack(fill=tk.BOTH, expand=True)
        self.chapter_listbox = tk.Listbox(listbox_frame, selectmode=tk.MULTIPLE, font=('微软雅黑', 9),
                                          bg='white', relief=tk.FLAT, highlightthickness=1,
                                          highlightcolor='#c0c0c0', highlightbackground='#c0c0c0')
        listbox_scroll = ttk.Scrollbar(listbox_frame, orient=tk.VERTICAL, command=self.chapter_listbox.yview)
        self.chapter_listbox.configure(yscrollcommand=listbox_scroll.set)
        self.chapter_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        listbox_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        right_frame = ttk.Frame(main_panel, width=260)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right_frame.pack_propagate(False)

        info_frame = ttk.LabelFrame(right_frame, text="书籍信息", padding=10)
        info_frame.pack(fill=tk.X, pady=(0, 10))
        self.info_label = ttk.Label(info_frame, text="尚未选择书籍", font=('微软雅黑', 10), wraplength=220)
        self.info_label.pack(fill=tk.X)

        dir_frame = ttk.LabelFrame(right_frame, text="保存设置", padding=10)
        dir_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(dir_frame, text="输出目录:").pack(anchor=tk.W)
        dir_entry_frame = ttk.Frame(dir_frame)
        dir_entry_frame.pack(fill=tk.X, pady=5)
        ttk.Entry(dir_entry_frame, textvariable=self.output_dir).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dir_entry_frame, text="浏览", command=self.browse_dir).pack(side=tk.RIGHT, padx=(5, 0))

        action_frame = ttk.LabelFrame(right_frame, text="操作", padding=10)
        action_frame.pack(fill=tk.X, pady=(0, 10))
        self.download_btn = ttk.Button(action_frame, text="下载选中章节", command=self.start_download, state=tk.DISABLED)
        self.download_btn.pack(fill=tk.X, pady=2)
        self.pause_btn = ttk.Button(action_frame, text="暂停", command=self.pause_download, state=tk.DISABLED)
        self.pause_btn.pack(fill=tk.X, pady=2)
        self.resume_btn = ttk.Button(action_frame, text="继续", command=self.resume_download, state=tk.DISABLED)
        self.resume_btn.pack(fill=tk.X, pady=2)
        self.retry_btn = ttk.Button(action_frame, text="重试失败章节", command=self.retry_failed, state=tk.DISABLED)
        self.retry_btn.pack(fill=tk.X, pady=2)

        stats_frame = ttk.LabelFrame(right_frame, text="下载统计", padding=10)
        stats_frame.pack(fill=tk.X)
        self.stats_label = ttk.Label(stats_frame, text="已完成: 0\n失败: 0\n总计: 0", font=('微软雅黑', 10))
        self.stats_label.pack(fill=tk.X)

        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, wrap=tk.WORD, font=('Consolas', 9))
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
        self.stats_label.config(text=f"已完成: {completed}\n失败: {failed}\n总计: {total}")

    def start_search(self):
        keyword = self.search_keyword.get().strip()
        if not keyword:
            messagebox.showerror("错误", "请输入搜索关键词")
            return
        self.log(f"正在搜索: {keyword}")
        self.tree.delete(*self.tree.get_children())
        self.chapter_listbox.delete(0, tk.END)
        self.download_btn.config(state=tk.DISABLED)
        self.retry_btn.config(state=tk.DISABLED)
        self.info_label.config(text="搜索中...")

        self.downloader = AudioDownloader(log_callback=self.log)
        self.thread = threading.Thread(target=self._search_thread, args=(keyword,), daemon=True)
        self.thread.start()

    def _search_thread(self, keyword):
        try:
            results = self.downloader.search_books(keyword)
            self.root.after(0, self._populate_search_results, results)
        except Exception as e:
            self.root.after(0, self.log, f"搜索出错: {e}")
            self.root.after(0, self.info_label.config, {'text': '搜索失败'})

    def _populate_search_results(self, results):
        for title, narrator, url in results:
            self.tree.insert('', tk.END, values=(title, narrator), tags=(url,))
        self.log(f"找到 {len(results)} 个结果")
        self.info_label.config(text=f"找到 {len(results)} 条结果\n请双击选择书籍")

    def on_book_select(self, event):
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        values = self.tree.item(item, 'values')
        url = self.tree.item(item, 'tags')[0]
        title = values[0]
        narrator = values[1] if len(values) > 1 else ''
        self.log(f"加载书籍: {title}")
        self.info_label.config(text=f"书名: {title}\n演播: {narrator}\n正在获取章节...")
        self.download_btn.config(state=tk.DISABLED)
        self.retry_btn.config(state=tk.DISABLED)
        self.chapter_listbox.delete(0, tk.END)
        self.current_book_url = url

        self.downloader = AudioDownloader(log_callback=self.log)
        self.thread = threading.Thread(target=self._fetch_chapters_thread, args=(url,), daemon=True)
        self.thread.start()

    def _fetch_chapters_thread(self, url):
        try:
            book_title, narrator, chapters = self.downloader.fetch_chapters(url)
            self.root.after(0, self._populate_chapters, book_title, narrator, chapters)
        except Exception as e:
            self.root.after(0, self.log, f"获取章节失败: {e}")
            self.root.after(0, self.info_label.config, {'text': '获取章节失败'})

    def _populate_chapters(self, book_title, narrator, chapters):
        self.folder_name = f"{book_title}-{narrator}" if narrator else book_title
        self.info_label.config(text=f"书名: {book_title}\n演播: {narrator}\n共 {len(chapters)} 章")
        self.chapters = chapters
        for title, _ in chapters:
            self.chapter_listbox.insert(tk.END, title)
        self.chapter_listbox.select_set(0, tk.END)
        self.log(f"共 {len(chapters)} 章，默认全选")
        self.download_btn.config(state=tk.NORMAL)

    def select_all(self):
        self.chapter_listbox.select_set(0, tk.END)

    def select_none(self):
        self.chapter_listbox.select_clear(0, tk.END)

    def invert_selection(self):
        for i in range(self.chapter_listbox.size()):
            if self.chapter_listbox.selection_includes(i):
                self.chapter_listbox.selection_clear(i)
            else:
                self.chapter_listbox.select_set(i)

    def start_download(self):
        selected_indices = self.chapter_listbox.curselection()
        if not selected_indices:
            messagebox.showinfo("提示", "未选择任何章节")
            return
        selected = [self.chapters[i] for i in selected_indices]
        output_dir = self.output_dir.get()
        if not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
            except:
                messagebox.showerror("错误", "无法创建输出目录")
                return

        self.download_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL)
        self.resume_btn.config(state=tk.DISABLED)
        self.retry_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.update_stats_display(0, 0, len(selected))
        self.log(f"开始下载 {len(selected)} 个章节...")

        self.downloader = AudioDownloader(
            log_callback=self.log,
            stats_callback=self.update_stats_display
        )
        self.thread = threading.Thread(
            target=self._download_thread,
            args=(selected, output_dir),
            daemon=True
        )
        self.thread.start()
        self.check_thread()

    def _download_thread(self, selected, output_dir):
        completed, failed, failed_list = self.downloader.download_selected(
            self.folder_name, selected, output_dir)
        self.root.after(0, self._on_download_finish, completed, failed, failed_list)

    def _on_download_finish(self, completed, failed, failed_list):
        self.failed_items = failed_list
        self.download_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED)
        self.resume_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.DISABLED)
        if failed_list:
            self.retry_btn.config(state=tk.NORMAL)
        self.log(f"下载结束。成功 {completed}，失败 {failed}")

    def pause_download(self):
        if self.downloader:
            self.downloader.pause()
            self.pause_btn.config(state=tk.DISABLED)
            self.resume_btn.config(state=tk.NORMAL)
            self.log("已暂停，正在进行的章节将继续完成...")

    def resume_download(self):
        if self.downloader:
            self.downloader.resume()
            self.pause_btn.config(state=tk.NORMAL)
            self.resume_btn.config(state=tk.DISABLED)
            self.log("继续下载...")

    def retry_failed(self):
        if not self.failed_items:
            messagebox.showinfo("提示", "没有失败的任务")
            return
        output_dir = self.output_dir.get()
        self.retry_btn.config(state=tk.DISABLED)
        self.download_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL)
        self.resume_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.log(f"开始重试 {len(self.failed_items)} 个失败章节...")
        self.downloader = AudioDownloader(
            log_callback=self.log,
            stats_callback=self.update_stats_display
        )
        self.thread = threading.Thread(
            target=self._retry_thread,
            args=(output_dir,),
            daemon=True
        )
        self.thread.start()
        self.check_thread()

    def _retry_thread(self, output_dir):
        completed, failed, new_failed = self.downloader.retry_failed(
            self.folder_name, self.failed_items, output_dir)
        self.root.after(0, self._on_retry_finish, new_failed)

    def _on_retry_finish(self, new_failed):
        self.failed_items = new_failed
        self.download_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED)
        self.resume_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.DISABLED)
        if new_failed:
            self.retry_btn.config(state=tk.NORMAL)
        else:
            self.retry_btn.config(state=tk.DISABLED)
        self.log("重试任务结束。")

    def stop_download(self):
        if self.downloader:
            self.downloader.stop()
        self.log("正在停止...")

    def check_thread(self):
        if self.thread and self.thread.is_alive():
            self.root.after(200, self.check_thread)
        else:
            self.download_btn.config(state=tk.NORMAL)
            self.pause_btn.config(state=tk.DISABLED)
            self.resume_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.DISABLED)
            if self.failed_items:
                self.retry_btn.config(state=tk.NORMAL)
            self.log("====== 任务结束 ======")

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = DownloaderApp()
    app.run()
