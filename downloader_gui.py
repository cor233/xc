import os
import re
import time
import random
import threading
import queue
import json
import urllib.parse
from tkinter import *
from tkinter import scrolledtext, messagebox, filedialog
from tkinter import ttk
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

# ==================== 配置 ====================
STORAGE_FILE = "downloaded_records.json"   # 记录已下载章节的文件
MAX_CONCURRENT = 3
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2
REQUEST_DELAY = 0.5

# ==================== 辅助函数 ====================
def load_downloaded_set(book_id):
    """从本地JSON文件加载已下载章节的URL集合"""
    if not os.path.exists(STORAGE_FILE):
        return set()
    try:
        with open(STORAGE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return set(data.get(book_id, []))
    except:
        return set()

def save_downloaded_set(book_id, downloaded_set):
    """保存已下载章节的URL集合到JSON文件"""
    data = {}
    if os.path.exists(STORAGE_FILE):
        try:
            with open(STORAGE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except:
            data = {}
    data[book_id] = list(downloaded_set)
    with open(STORAGE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def sanitize_filename(name):
    """过滤文件名中的非法字符"""
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    return name.strip()

# ==================== 下载工作线程 ====================
class DownloadWorker:
    def __init__(self, album_url, max_workers, request_delay, retry_times, timeout, proxy_list, log_queue, book_id, downloaded_set):
        self.album_url = album_url
        self.max_workers = max_workers
        self.request_delay = request_delay
        self.retry_times = retry_times
        self.timeout = timeout
        self.proxy_list = proxy_list
        self.log_queue = log_queue
        self.book_id = book_id
        self.downloaded_set = downloaded_set
        self.stop_flag = False
        self.pause_flag = False
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        })
        # 初始化会话，访问首页获取必要Cookie
        self.fetch_url("https://m.i275.com/", referer="https://m.i275.com/")

    def log(self, msg):
        self.log_queue.put(msg)

    def get_random_proxy(self):
        if not self.proxy_list:
            return None
        return random.choice(self.proxy_list)

    def fetch_url(self, url, referer=None, timeout=None, retries=None):
        if timeout is None:
            timeout = self.timeout
        if retries is None:
            retries = self.retry_times
        for attempt in range(retries):
            if self.stop_flag:
                return None
            try:
                proxy = self.get_random_proxy()
                headers = {}
                if referer:
                    headers['Referer'] = referer
                else:
                    headers['Referer'] = self.album_url
                resp = self.session.get(url, headers=headers, proxies=proxy, timeout=timeout)
                resp.encoding = 'utf-8'
                if resp.status_code == 200:
                    return resp
                else:
                    self.log(f"请求失败，状态码 {resp.status_code}，重试 {attempt+1}/{retries}")
            except Exception as e:
                self.log(f"请求异常: {e}，重试 {attempt+1}/{retries}")
            time.sleep(random.uniform(*self.request_delay) * 2)
        return None

    def get_album_title(self):
        resp = self.fetch_url(self.album_url)
        if not resp:
            return "未知专辑"
        soup = BeautifulSoup(resp.text, 'html.parser')
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
        else:
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else "未知专辑"
            title = re.sub(r'[-|]\s*275听书网.*$', '', title).strip()
        title = sanitize_filename(title)
        return title

    def get_chapter_links(self):
        resp = self.fetch_url(self.album_url)
        if not resp:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        chapter_links = []
        for a in soup.find_all('a', href=re.compile(r'^/play/\d+/\d+\.html')):
            href = a.get('href')
            full_url = requests.compat.urljoin(self.album_url, href)
            a_id = a.get('id', '')
            chap_num = 0
            if a_id and a_id.startswith('chapter-pos-'):
                try:
                    chap_num = int(a_id.replace('chapter-pos-', ''))
                except:
                    pass
            title_span = a.find('span', class_='text-sm')
            if title_span:
                chap_title = title_span.get_text(strip=True)
            else:
                chap_title = a.get_text(strip=True) or f"第{chap_num}章"
            chapter_links.append({
                'url': full_url,
                'num': chap_num,
                'title': chap_title
            })
        chapter_links.sort(key=lambda x: x['num'])
        return chapter_links

    def get_audio_url(self, play_url):
        resp = self.fetch_url(play_url, referer=play_url)
        if not resp:
            return None
        text = resp.text
        soup = BeautifulSoup(text, 'html.parser')
        title_tag = soup.find('title')
        page_title = title_tag.get_text() if title_tag else ""
        if "正在播放" not in page_title and "贺岁剧" not in page_title:
            self.log(f"警告：可能不是播放页，标题为：{page_title[:50]}")
        patterns = [
            r"url:\s*'([^']+)'",
            r'"url"\s*:\s*"([^"]+)"',
            r'<audio[^>]+src="([^"]+)"',
            r'var\s+audioUrl\s*=\s*[\'"]([^\'"]+)[\'"]',
            r'audioUrl\s*:\s*[\'"]([^\'"]+)[\'"]',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                url = match.group(1)
                url = url.replace('\\/', '/')
                return url
        self.log(f"无法从页面提取音频URL，响应片段：{text[:300]}")
        return None

    def download_audio(self, audio_url, save_path):
        proxy = self.get_random_proxy()
        try:
            headers = {'Referer': self.album_url}
            with self.session.get(audio_url, headers=headers, proxies=proxy, stream=True, timeout=self.timeout) as r:
                if r.status_code == 200:
                    with open(save_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if self.stop_flag:
                                return False
                            f.write(chunk)
                    return True
                else:
                    self.log(f"下载失败，状态码 {r.status_code}")
                    return False
        except Exception as e:
            self.log(f"下载异常: {e}")
            return False

    def process_chapter(self, chapter, download_dir, progress_callback):
        chap_num = chapter['num']
        chap_title = chapter['title']
        play_url = chapter['url']
        self.log(f"[{chap_num}] 开始处理：{chap_title}")

        # 检查是否已下载
        if play_url in self.downloaded_set:
            self.log(f"[{chap_num}] 已下载，跳过")
            progress_callback('skipped')
            return

        # 检查暂停/停止
        if self.stop_flag: return
        while self.pause_flag and not self.stop_flag:
            time.sleep(0.5)
        if self.stop_flag: return

        # 获取音频URL（带重试）
        audio_url = None
        for attempt in range(self.retry_times):
            if self.stop_flag or self.pause_flag:
                return
            audio_url = self.get_audio_url(play_url)
            if audio_url:
                break
            self.log(f"[{chap_num}] 获取音频URL失败，{RETRY_DELAY_BASE}s后重试 ({attempt+1}/{self.retry_times})")
            time.sleep(RETRY_DELAY_BASE)
        if not audio_url:
            self.log(f"[{chap_num}] 获取音频URL失败，跳过")
            progress_callback('failed', chapter)
            return

        # 检查暂停/停止
        if self.stop_flag or self.pause_flag:
            return

        # 下载音频（带重试）
        safe_title = sanitize_filename(chap_title)
        filename = f"{chap_num:03d}_{safe_title}.m4a"
        filepath = os.path.join(download_dir, filename)

        success = False
        for attempt in range(self.retry_times):
            if self.stop_flag or self.pause_flag:
                return
            success = self.download_audio(audio_url, filepath)
            if success:
                break
            self.log(f"[{chap_num}] 下载失败，{RETRY_DELAY_BASE}s后重试 ({attempt+1}/{self.retry_times})")
            time.sleep(RETRY_DELAY_BASE)

        if success:
            self.log(f"[{chap_num}] 下载完成 -> {filename}")
            self.downloaded_set.add(play_url)
            save_downloaded_set(self.book_id, self.downloaded_set)
            progress_callback('completed')
        else:
            self.log(f"[{chap_num}] 下载失败")
            progress_callback('failed', chapter)

    def start_download(self, download_dir, chapters, progress_callback, finished_callback):
        self.log(f"保存目录：{download_dir}")
        self.log(f"共找到 {len(chapters)} 个章节")
        self.log("开始下载...")

        total = len(chapters)
        completed = 0
        failed = 0
        skipped = 0
        failed_chapters = []
        lock = threading.Lock()

        def update_progress(status, chapter=None):
            nonlocal completed, failed, skipped, failed_chapters
            with lock:
                if status == 'completed':
                    completed += 1
                elif status == 'failed' and chapter:
                    failed += 1
                    failed_chapters.append(chapter)
                elif status == 'skipped':
                    skipped += 1
                progress_callback(completed, failed, skipped, total, failed_chapters)

        # 使用线程池管理并发
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_chapter = {}
            for chapter in chapters:
                if self.stop_flag:
                    break
                while self.pause_flag and not self.stop_flag:
                    time.sleep(0.5)
                if self.stop_flag:
                    break
                future = executor.submit(self.process_chapter, chapter, download_dir, update_progress)
                future_to_chapter[future] = chapter

            # 等待所有任务完成或停止
            for future in as_completed(future_to_chapter):
                if self.stop_flag:
                    # 取消尚未开始的任务
                    for f in future_to_chapter:
                        f.cancel()
                    break
                chapter = future_to_chapter[future]
                try:
                    future.result()  # 如果有异常会抛出
                except Exception as e:
                    self.log(f"[{chapter['num']}] 处理异常: {e}")
                    with lock:
                        failed += 1
                        failed_chapters.append(chapter)
                        update_progress('failed', chapter)

        finished_callback(completed, failed, skipped, total, failed_chapters)

    def pause(self):
        self.pause_flag = True

    def resume(self):
        self.pause_flag = False

    def stop(self):
        self.stop_flag = True

# ==================== 主界面 ====================
class DownloaderApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("有声小说搜索下载器")
        self.geometry("950x750")
        self.resizable(True, True)
        self.search_var = StringVar()
        self.max_workers_var = IntVar(value=MAX_CONCURRENT)
        self.delay_min_var = DoubleVar(value=0.5)
        self.delay_max_var = DoubleVar(value=1.5)
        self.retry_var = IntVar(value=MAX_RETRIES)
        self.timeout_var = IntVar(value=15)
        self.save_path_var = StringVar()
        self.selected_book_url = None
        self.selected_book_id = None
        self.log_queue = queue.Queue()
        self.worker = None
        self.running = False
        self.paused = False
        self.create_widgets()
        self.update_log()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self):
        main_frame = Frame(self, padx=10, pady=10)
        main_frame.pack(fill=BOTH, expand=True)

        # 搜索区域
        search_frame = Frame(main_frame)
        search_frame.pack(fill=X, pady=5)
        Label(search_frame, text="关键词：").pack(side=LEFT)
        Entry(search_frame, textvariable=self.search_var, width=50).pack(side=LEFT, padx=5, expand=True, fill=X)
        Button(search_frame, text="搜索", command=self.search, bg="blue", fg="white", width=10).pack(side=LEFT, padx=5)

        # 搜索结果列表
        result_frame = LabelFrame(main_frame, text="搜索结果")
        result_frame.pack(fill=BOTH, expand=True, pady=5)
        columns = ('书名', '演播', '作者')
        self.tree = ttk.Treeview(result_frame, columns=columns, show='tree headings', height=8)
        self.tree.heading('#0', text='序号')
        self.tree.heading('书名', text='书名')
        self.tree.heading('演播', text='演播')
        self.tree.heading('作者', text='作者')
        self.tree.column('#0', width=50)
        self.tree.column('书名', width=300)
        self.tree.column('演播', width=150)
        self.tree.column('作者', width=150)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar = ttk.Scrollbar(result_frame, orient=VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)

        # 代理输入
        proxy_frame = Frame(main_frame)
        proxy_frame.pack(fill=X, pady=5)
        Label(proxy_frame, text="代理列表(每行一个，格式: {'http':'url','https':'url'})：").pack(anchor=W)
        self.proxy_entry = Text(proxy_frame, height=3, width=80)
        self.proxy_entry.pack(fill=X, pady=2)

        # 下载参数设置
        config_frame = Frame(main_frame)
        config_frame.pack(fill=X, pady=5)
        Label(config_frame, text="并发数：").pack(side=LEFT)
        Spinbox(config_frame, from_=1, to=20, textvariable=self.max_workers_var, width=4).pack(side=LEFT, padx=2)
        Label(config_frame, text="重试：").pack(side=LEFT, padx=(10,0))
        Spinbox(config_frame, from_=0, to=10, textvariable=self.retry_var, width=4).pack(side=LEFT, padx=2)
        Label(config_frame, text="超时：").pack(side=LEFT, padx=(10,0))
        Spinbox(config_frame, from_=5, to=60, textvariable=self.timeout_var, width=4).pack(side=LEFT, padx=2)
        Label(config_frame, text="延迟范围：").pack(side=LEFT, padx=(10,0))
        Entry(config_frame, textvariable=self.delay_min_var, width=4).pack(side=LEFT)
        Label(config_frame, text="-").pack(side=LEFT)
        Entry(config_frame, textvariable=self.delay_max_var, width=4).pack(side=LEFT)

        # 保存目录选择
        dir_frame = Frame(main_frame)
        dir_frame.pack(fill=X, pady=5)
        Label(dir_frame, text="保存目录：").pack(side=LEFT)
        Entry(dir_frame, textvariable=self.save_path_var, width=60).pack(side=LEFT, padx=5, expand=True, fill=X)
        Button(dir_frame, text="浏览", command=self.select_save_dir).pack(side=LEFT)

        # ===== 下载控制面板 =====
        panel_frame = LabelFrame(main_frame, text="下载控制面板")
        panel_frame.pack(fill=X, pady=5)

        # 统计信息
        stats_frame = Frame(panel_frame)
        stats_frame.pack(fill=X, pady=5)
        self.total_label = Label(stats_frame, text="总章节: 0")
        self.total_label.pack(side=LEFT, padx=5)
        self.completed_label = Label(stats_frame, text="已完成: 0", fg="green")
        self.completed_label.pack(side=LEFT, padx=5)
        self.failed_label = Label(stats_frame, text="失败: 0", fg="red")
        self.failed_label.pack(side=LEFT, padx=5)
        self.skipped_label = Label(stats_frame, text="跳过: 0", fg="orange")
        self.skipped_label.pack(side=LEFT, padx=5)

        # 进度条
        self.progress = ttk.Progressbar(panel_frame, orient=HORIZONTAL, length=400, mode='determinate')
        self.progress.pack(fill=X, pady=5)

        # 失败章节列表
        fail_frame = Frame(panel_frame)
        fail_frame.pack(fill=X, pady=5)
        Label(fail_frame, text="失败章节：").pack(anchor=W)
        self.failed_list_text = scrolledtext.ScrolledText(fail_frame, height=3, wrap=WORD)
        self.failed_list_text.pack(fill=X)

        # 按钮行
        btn_frame = Frame(panel_frame)
        btn_frame.pack(fill=X, pady=5)
        self.start_btn = Button(btn_frame, text="开始下载", command=self.start_download, bg="green", fg="white", width=12)
        self.start_btn.pack(side=LEFT, padx=2)
        self.pause_btn = Button(btn_frame, text="暂停", command=self.pause_download, state=DISABLED, bg="orange", fg="white", width=8)
        self.pause_btn.pack(side=LEFT, padx=2)
        self.resume_btn = Button(btn_frame, text="继续", command=self.resume_download, state=DISABLED, bg="blue", fg="white", width=8)
        self.resume_btn.pack(side=LEFT, padx=2)
        self.check_btn = Button(btn_frame, text="检查缺失", command=self.check_missing, state=DISABLED, bg="purple", fg="white", width=8)
        self.check_btn.pack(side=LEFT, padx=2)
        self.retry_btn = Button(btn_frame, text="重试失败", command=self.retry_failed, state=DISABLED, bg="orange", fg="white", width=8)
        self.retry_btn.pack(side=LEFT, padx=2)
        self.reset_btn = Button(btn_frame, text="重置记录", command=self.reset_records, bg="red", fg="white", width=8)
        self.reset_btn.pack(side=LEFT, padx=2)

        # 日志输出
        log_frame = LabelFrame(main_frame, text="下载日志")
        log_frame.pack(fill=BOTH, expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=WORD, height=10)
        self.log_text.pack(fill=BOTH, expand=True)

    def select_save_dir(self):
        dir_path = filedialog.askdirectory(title="选择保存目录")
        if dir_path:
            self.save_path_var.set(dir_path)

    def parse_proxy(self):
        proxy_text = self.proxy_entry.get("1.0", END).strip()
        proxy_list = []
        if proxy_text:
            for line in proxy_text.splitlines():
                line = line.strip()
                if line:
                    try:
                        proxy_dict = eval(line)
                        if isinstance(proxy_dict, dict):
                            proxy_list.append(proxy_dict)
                    except:
                        self.log("代理格式错误，已忽略：" + line)
        return proxy_list

    def search(self):
        keyword = self.search_var.get().strip()
        if not keyword:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        self.log(f"正在搜索：{keyword}")
        for item in self.tree.get_children():
            self.tree.delete(item)
        search_url = f"https://m.i275.com/search.php?q={urllib.parse.quote(keyword)}"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://m.i275.com/",
            }
            resp = requests.get(search_url, headers=headers, timeout=15)
            resp.encoding = 'utf-8'
            if resp.status_code != 200:
                self.log(f"搜索请求失败，状态码 {resp.status_code}")
                return
            soup = BeautifulSoup(resp.text, 'html.parser')
            # 获取所有书籍链接，并确保有h3标签（书名）
            items = soup.find_all('a', href=re.compile(r'^/book/\d+\.html'))
            items = [a for a in items if a.find('h3')]
            if not items:
                self.log("未找到相关结果")
                return
            index = 1
            for a in items:
                href = a.get('href')
                book_url = requests.compat.urljoin("https://m.i275.com/", href)
                h3 = a.find('h3')
                title = h3.get_text(strip=True) if h3 else "未知书名"
                # 提取演播和作者
                announcer = ""
                author = ""
                info_spans = a.find_all('p', class_='text-xs')
                for p in info_spans:
                    text = p.get_text(strip=True)
                    if "演播" in text:
                        announcer = text.replace("演播", "").strip()
                    elif "作者" in text:
                        author = text.replace("作者", "").strip()
                self.tree.insert('', 'end', text=str(index), values=(title, announcer, author), tags=(book_url,))
                index += 1
            self.log(f"搜索完成，共找到 {len(items)} 条结果")
        except Exception as e:
            self.log(f"搜索异常: {e}")

    def on_select(self, event):
        selected = self.tree.selection()
        if selected:
            item = selected[0]
            book_url = self.tree.item(item, 'tags')[0]
            self.selected_book_url = book_url
            match = re.search(r'/book/(\d+)\.html', book_url)
            if match:
                self.selected_book_id = match.group(1)
            self.log(f"已选中：{self.tree.item(item, 'values')[0]}")

    def start_download(self):
        if self.running:
            messagebox.showwarning("提示", "已有任务在运行")
            return
        if not self.selected_book_url:
            messagebox.showerror("错误", "请先在搜索结果中选择一本小说")
            return
        save_dir = self.save_path_var.get().strip()
        if not save_dir:
            save_dir = filedialog.askdirectory(title="选择音频保存目录")
            if not save_dir:
                return
            self.save_path_var.set(save_dir)
        proxy_list = self.parse_proxy()

        downloaded_set = load_downloaded_set(self.selected_book_id)

        self.worker = DownloadWorker(
            album_url=self.selected_book_url,
            max_workers=self.max_workers_var.get(),
            request_delay=(self.delay_min_var.get(), self.delay_max_var.get()),
            retry_times=self.retry_var.get(),
            timeout=self.timeout_var.get(),
            proxy_list=proxy_list,
            log_queue=self.log_queue,
            book_id=self.selected_book_id,
            downloaded_set=downloaded_set
        )

        self.log("正在获取章节列表...")
        chapters = self.worker.get_chapter_links()
        if not chapters:
            self.log("未找到任何章节，请检查URL或网络。")
            return
        self.chapters = chapters
        self.total_chapters = len(chapters)
        self.total_label.config(text=f"总章节: {self.total_chapters}")
        self.progress['maximum'] = self.total_chapters
        self.update_stats(0, 0, 0, self.total_chapters, [])

        self.running = True
        self.paused = False
        self.start_btn.config(state=DISABLED)
        self.pause_btn.config(state=NORMAL)
        self.resume_btn.config(state=DISABLED)
        self.check_btn.config(state=DISABLED)
        self.retry_btn.config(state=DISABLED)

        self.worker.start_download(
            download_dir=save_dir,
            chapters=chapters,
            progress_callback=self.update_stats,
            finished_callback=self.download_finished
        )

    def pause_download(self):
        if self.worker and self.running and not self.paused:
            self.worker.pause()
            self.paused = True
            self.pause_btn.config(state=DISABLED)
            self.resume_btn.config(state=NORMAL)
            self.log("下载已暂停")

    def resume_download(self):
        if self.worker and self.running and self.paused:
            self.worker.resume()
            self.paused = False
            self.pause_btn.config(state=NORMAL)
            self.resume_btn.config(state=DISABLED)
            self.log("下载继续")

    def update_stats(self, completed, failed, skipped, total, failed_chapters):
        self.completed_label.config(text=f"已完成: {completed}")
        self.failed_label.config(text=f"失败: {failed}")
        self.skipped_label.config(text=f"跳过: {skipped}")
        self.progress['value'] = completed + failed + skipped

        self.failed_list_text.delete(1.0, END)
        if failed_chapters:
            for fc in failed_chapters:
                self.failed_list_text.insert(END, f"第 {fc['num']} 章：{fc['title']}\n")
            self.retry_btn.config(state=NORMAL)
        else:
            self.failed_list_text.insert(END, "暂无失败章节")
            self.retry_btn.config(state=DISABLED)

    def download_finished(self, completed, failed, skipped, total, failed_chapters):
        self.running = False
        self.paused = False
        self.start_btn.config(state=NORMAL)
        self.pause_btn.config(state=DISABLED)
        self.resume_btn.config(state=DISABLED)
        self.check_btn.config(state=NORMAL)
        self.log(f"下载完成！成功: {completed} 失败: {failed} 跳过: {skipped}")

    def check_missing(self):
        if not self.selected_book_id or not hasattr(self, 'chapters'):
            return
        downloaded_set = load_downloaded_set(self.selected_book_id)
        missing = [ch for ch in self.chapters if ch['url'] not in downloaded_set]
        if missing:
            self.log(f"发现 {len(missing)} 章缺失")
            self.failed_list_text.delete(1.0, END)
            for ch in missing:
                self.failed_list_text.insert(END, f"第 {ch['num']} 章：{ch['title']}\n")
            self.retry_btn.config(state=NORMAL)
        else:
            self.log("所有章节均已记录，无缺失")
            self.failed_list_text.delete(1.0, END)
            self.failed_list_text.insert(END, "暂无缺失章节")
            self.retry_btn.config(state=DISABLED)

    def retry_failed(self):
        failed_text = self.failed_list_text.get(1.0, END).strip()
        if not failed_text or failed_text == "暂无失败章节" or failed_text == "暂无缺失章节":
            return
        failed_chapters = []
        for line in failed_text.splitlines():
            match = re.search(r'第 (\d+) 章', line)
            if match:
                num = int(match.group(1))
                for ch in self.chapters:
                    if ch['num'] == num:
                        failed_chapters.append(ch)
                        break
        if failed_chapters:
            self.log(f"准备重试 {len(failed_chapters)} 章")
            save_dir = self.save_path_var.get().strip()
            if not save_dir:
                save_dir = filedialog.askdirectory(title="选择音频保存目录")
                if not save_dir:
                    return
                self.save_path_var.set(save_dir)
            proxy_list = self.parse_proxy()
            downloaded_set = load_downloaded_set(self.selected_book_id)
            self.worker = DownloadWorker(
                album_url=self.selected_book_url,
                max_workers=self.max_workers_var.get(),
                request_delay=(self.delay_min_var.get(), self.delay_max_var.get()),
                retry_times=self.retry_var.get(),
                timeout=self.timeout_var.get(),
                proxy_list=proxy_list,
                log_queue=self.log_queue,
                book_id=self.selected_book_id,
                downloaded_set=downloaded_set
            )
            self.running = True
            self.paused = False
            self.start_btn.config(state=DISABLED)
            self.pause_btn.config(state=NORMAL)
            self.resume_btn.config(state=DISABLED)
            self.check_btn.config(state=DISABLED)
            self.retry_btn.config(state=DISABLED)
            self.worker.start_download(
                download_dir=save_dir,
                chapters=failed_chapters,
                progress_callback=self.update_stats,
                finished_callback=self.download_finished
            )

    def reset_records(self):
        if self.selected_book_id:
            if messagebox.askyesno("确认", "确定清除当前书籍的下载记录吗？"):
                save_downloaded_set(self.selected_book_id, set())
                self.log("已清除下载记录")
                self.update_stats(0, 0, 0, self.total_chapters if hasattr(self, 'total_chapters') else 0, [])
                self.failed_list_text.delete(1.0, END)
                self.failed_list_text.insert(END, "暂无失败章节")

    def update_log(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self.log_text.insert(END, msg + "\n")
                self.log_text.see(END)
            except queue.Empty:
                break
        self.after(100, self.update_log)

    def log(self, msg):
        self.log_queue.put(msg)

    def on_closing(self):
        if self.running:
            if messagebox.askyesno("确认", "下载正在进行中，确定退出吗？"):
                if self.worker:
                    self.worker.stop()
                self.destroy()
        else:
            self.destroy()

if __name__ == "__main__":
    app = DownloaderApp()
    app.mainloop()
