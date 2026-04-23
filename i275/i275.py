# -*- coding: utf-8 -*-
import os
import re
import time
import random
import threading
import queue
import urllib.parse
import json
from tkinter import *
from tkinter import scrolledtext, messagebox, filedialog
from tkinter import ttk
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]
IMPERSONATES = ["chrome120", "chrome119", "firefox121", "safari17_0"]
MAX_FILENAME_LEN = 200
COMPLETED_FILE = ".completed.json"
FAILED_FILE = ".failed.json"

def sanitize_filename(name):
    char_map = {
        '\\': '＼', '/': '／', ':': '：', '*': '＊',
        '?': '？', '"': '＂', '<': '＜', '>': '＞', '|': '｜'
    }
    cleaned = re.sub(r'[\\/:*?"<>|]', lambda m: char_map[m.group(0)], name).strip()
    if len(cleaned) > MAX_FILENAME_LEN:
        name_part, ext_part = os.path.splitext(cleaned)
        cleaned = name_part[:MAX_FILENAME_LEN - len(ext_part)] + ext_part
    return cleaned

class RateLimiter:
    def __init__(self, min_delay=8.0, max_delay=12.0):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.last_request_time = 0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            if self.last_request_time > 0:
                elapsed = now - self.last_request_time
                required = random.uniform(self.min_delay, self.max_delay)
                wait_time = max(0, required - elapsed)
            else:
                wait_time = 0
            self.last_request_time = time.time() + wait_time
        if wait_time > 0:
            time.sleep(wait_time)

class DownloadWorker:
    def __init__(self, album_url, max_workers, request_delay, retry_times, timeout, save_dir, log_queue):
        self.album_url = album_url
        self.max_workers = max_workers
        self.request_delay = request_delay
        self.retry_times = retry_times
        self.timeout = timeout
        self.save_dir = save_dir
        self.log_queue = log_queue
        self.stop_flag = False
        self.curl_session = None
        self.executor = None
        self.rate_limiter = RateLimiter(request_delay[0], request_delay[1])
        self.completed_file = os.path.join(save_dir, COMPLETED_FILE)
        self.failed_file = os.path.join(save_dir, FAILED_FILE)
        self.lock = threading.RLock()
        self.completed_set = set()
        self.failed_set = set()
        self.total_chapters = 0
        self.completed = 0
        self.failed = 0
        self.cool_down_until = 0
        self.cool_down_lock = threading.Lock()
        self._load_completed()
        self._load_failed()

    def __enter__(self):
        self.curl_session = curl_requests.Session()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.curl_session:
            self.curl_session.close()
        if self.executor:
            self.executor.shutdown(wait=True)

    def log(self, msg):
        self.log_queue.put(msg)

    def stats_log(self):
        with self.lock:
            self.log_queue.put(f"STATS:{self.completed}:{self.failed}:{self.total_chapters}")

    def fail_log(self, chap_num, chap_title):
        self.log_queue.put(f"FAIL:{chap_num}:{chap_title}")

    def clear_fail_log(self):
        self.log_queue.put("FAIL_CLEAR")

    def _load_completed(self):
        if os.path.exists(self.completed_file):
            try:
                with open(self.completed_file, 'r', encoding='utf-8') as f:
                    self.completed_set = set(json.load(f))
            except:
                self.completed_set = set()

    def _save_completed(self):
        with self.lock:
            temp_file = self.completed_file + ".tmp"
            try:
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(list(self.completed_set), f, ensure_ascii=False)
                os.replace(temp_file, self.completed_file)
            except Exception as e:
                self.log(f"保存完成记录失败: {e}")

    def _load_failed(self):
        if os.path.exists(self.failed_file):
            try:
                with open(self.failed_file, 'r', encoding='utf-8') as f:
                    self.failed_set = set(json.load(f))
            except:
                self.failed_set = set()

    def _save_failed(self):
        with self.lock:
            temp_file = self.failed_file + ".tmp"
            try:
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(list(self.failed_set), f, ensure_ascii=False)
                os.replace(temp_file, self.failed_file)
            except Exception as e:
                self.log(f"保存失败记录失败: {e}")

    def _mark_completed(self, identifier):
        with self.lock:
            self.completed_set.add(identifier)
            if identifier in self.failed_set:
                self.failed_set.remove(identifier)
            self.completed += 1
        self._save_completed()
        if identifier in self.failed_set:
            self._save_failed()

    def _mark_failed(self, identifier):
        with self.lock:
            self.failed_set.add(identifier)
            self.failed += 1
        self._save_failed()

    def fetch_url(self, url, referer=None, timeout=None, retries=None):
        if timeout is None:
            timeout = self.timeout
        if retries is None:
            retries = self.retry_times
        for attempt in range(retries):
            if self.stop_flag:
                return None
            with self.cool_down_lock:
                if time.time() < self.cool_down_until:
                    wait_remain = self.cool_down_until - time.time()
                    self.log(f"处于全局冷却期，还需等待 {int(wait_remain)} 秒...")
                    time.sleep(min(wait_remain, 10))
                    continue
            try:
                self.rate_limiter.wait()
                headers = {'User-Agent': random.choice(USER_AGENTS)}
                headers['Referer'] = referer if referer else self.album_url
                impersonate = random.choice(IMPERSONATES)
                resp = self.curl_session.get(url, headers=headers, timeout=timeout, impersonate=impersonate)
                resp.encoding = 'utf-8'
                if resp.status_code == 200:
                    if any(kw in resp.text for kw in ["您的操作太快了", "稍后再试", "访问频繁"]) or resp.status_code == 429:
                        with self.cool_down_lock:
                            cool_seconds = random.randint(600, 1200)  # 10~20分钟
                            self.cool_down_until = time.time() + cool_seconds
                        self.log(f"触发频率限制，进入全局冷却 {cool_seconds} 秒，请勿关闭程序...")
                        time.sleep(10)
                        continue
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
            self.log("获取专辑标题失败，使用默认标题")
            return "未知专辑"
        soup = BeautifulSoup(resp.text, 'html.parser')
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
        else:
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else "未知专辑"
            title = re.sub(r'[-|]\s*275听书网.*$', '', title).strip()
        title = re.sub(r'[\\/*?:"<>|]', '_', title)
        return title

    def get_chapter_links(self):
        resp = self.fetch_url(self.album_url)
        if not resp:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        chapter_links = []
        for a in soup.find_all('a', href=re.compile(r'^/play/\d+/\d+\.html')):
            href = a.get('href')
            full_url = urllib.parse.urljoin(self.album_url, href)
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
            if "立即开始收听" in chap_title or "继续播放" in chap_title:
                continue
            chapter_links.append({
                'url': full_url,
                'num': chap_num,
                'title': chap_title,
                'identifier': str(chap_num)
            })
        chapter_links.sort(key=lambda x: x['num'])
        return chapter_links

    def get_audio_url(self, play_url):
        resp = self.fetch_url(play_url, referer=play_url)
        if not resp:
            return None
        text = resp.text

        pattern_ap = r"url:\s*['\"](http[^'\"]+)['\"]"
        match = re.search(pattern_ap, text)
        if match:
            url = match.group(1).replace('\\/', '/')
            return url
            
        patterns = [
            r"url:\s*'([^']+)'",
            r'"url"\s*:\s*"([^"]+)"',
            r'audioUrl\s*=\s*[\'"]([^\'"]+)[\'"]',
            r'var\s+audioUrl\s*=\s*[\'"]([^\'"]+)[\'"]',
            r'<audio[^>]+src="([^"]+)"',
            r'<source[^>]+src="([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                url = match.group(1).replace('\\/', '/')
                if url.startswith('http'):
                    return url

        self.log(f"无法提取音频URL，响应片段：{text[:300]}")
        return None

    def download_audio(self, audio_url, save_path, max_retries=2):
        for attempt in range(max_retries):
            try:
                headers = {'User-Agent': random.choice(USER_AGENTS), 'Referer': self.album_url}
                impersonate = random.choice(IMPERSONATES)
                with self.curl_session.get(audio_url, headers=headers, stream=True, timeout=self.timeout, impersonate=impersonate) as r:
                    if r.status_code == 200:
                        with open(save_path, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=8192):
                                if self.stop_flag:
                                    return False
                                f.write(chunk)
                        return True
                    else:
                        self.log(f"下载失败，状态码 {r.status_code}，重试 {attempt+1}/{max_retries}")
            except Exception as e:
                self.log(f"下载异常: {e}，重试 {attempt+1}/{max_retries}")
            time.sleep(random.uniform(2, 5))
        return False

    def process_chapter(self, chapter, download_dir):
        chap_num = chapter['num']
        chap_title = chapter['title']
        play_url = chapter['url']
        identifier = chapter['identifier']
        if identifier in self.completed_set:
            with self.lock:
                self.completed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 已完成")
            return
        self.log(f"[{chap_num}] 开始处理：{chap_title}")
        audio_url = self.get_audio_url(play_url)
        if not audio_url:
            self._mark_failed(identifier)
            self.stats_log()
            self.log(f"[{chap_num}] 获取音频URL失败")
            self.fail_log(chap_num, chap_title)
            return
        safe_title = sanitize_filename(chap_title)
        filename = f"{chap_num:04d}_{safe_title}.m4a"
        filepath = os.path.join(download_dir, filename)
        temp_path = filepath + ".part"
        success = self.download_audio(audio_url, temp_path)
        if success:
            if os.path.exists(temp_path):
                try:
                    os.rename(temp_path, filepath)
                except OSError as e:
                    self.log(f"文件重命名失败: {e}")
                    success = False
            if success:
                self._mark_completed(identifier)
                self.stats_log()
                self.log(f"[{chap_num}] 下载完成 -> {filename}")
            else:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                self._mark_failed(identifier)
                self.stats_log()
                self.log(f"[{chap_num}] 下载失败")
                self.fail_log(chap_num, chap_title)
        else:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            self._mark_failed(identifier)
            self.stats_log()
            self.log(f"[{chap_num}] 下载失败")
            self.fail_log(chap_num, chap_title)

    def start_download(self, download_dir):
        self.clear_fail_log()
        with self.lock:
            self.completed = 0
            self.failed = 0
        self._load_completed()
        self._load_failed()
        self.log("获取专辑信息...")
        self.fetch_url("https://m.i275.com/", referer="https://m.i275.com/")
        album_title = self.get_album_title()
        self.log(f"专辑标题：{album_title}")
        os.makedirs(download_dir, exist_ok=True)
        self.log(f"保存目录：{download_dir}")
        self.log("获取章节列表...")
        all_chapters = self.get_chapter_links()
        if not all_chapters:
            self.log("未找到任何章节")
            return
        self.total_chapters = len(all_chapters)
        if self.failed_set:
            chapters = [c for c in all_chapters if c['identifier'] in self.failed_set]
            self.log(f"重试 {len(chapters)} 个失败章节")
        else:
            chapters = all_chapters
            self.log(f"共 {len(chapters)} 个章节")
        self.stats_log()
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        future_to_chap = {
            self.executor.submit(self.process_chapter, chap, download_dir): chap
            for chap in chapters
        }
        try:
            for future in as_completed(future_to_chap):
                if self.stop_flag:
                    for f in future_to_chap:
                        f.cancel()
                    break
                chap = future_to_chap[future]
                try:
                    future.result()
                except Exception as e:
                    self.log(f"[{chap['num']}] 处理异常: {e}")
                    self._mark_failed(chap['identifier'])
        finally:
            self.executor.shutdown(wait=True)
        if not self.stop_flag:
            self.log(f"本轮完成，成功 {self.completed} 失败 {self.failed}")
            if self.failed_set:
                self.log(f"仍有 {len(self.failed_set)} 章失败，将继续重试")
        else:
            self.log("下载已停止")

class DownloaderApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("i275有声书下载器")
        self.geometry("950x800")
        self.resizable(True, True)
        self.search_var = StringVar()
        self.max_workers_var = IntVar(value=1)
        self.delay_min_var = DoubleVar(value=8.0)
        self.delay_max_var = DoubleVar(value=12.0)
        self.retry_var = IntVar(value=2)
        self.timeout_var = IntVar(value=20)
        self.save_path_var = StringVar()
        self.selected_book_url = None
        self.selected_book_title = None
        self.selected_book_announcer = None
        self.log_queue = queue.Queue()
        self.worker = None
        self.running = False
        self.auto_loop_active = False
        self.auto_loop_thread = None
        self.state_lock = threading.Lock()
        self.progress_var = IntVar(value=0)
        self.create_widgets()
        self.update_log()

    def create_widgets(self):
        main_frame = Frame(self, padx=10, pady=10)
        main_frame.pack(fill=BOTH, expand=True)

        search_frame = Frame(main_frame)
        search_frame.pack(fill=X, pady=5)
        Label(search_frame, text="关键词：").pack(side=LEFT)
        self.search_entry = Entry(search_frame, textvariable=self.search_var, width=50)
        self.search_entry.pack(side=LEFT, padx=5, expand=True, fill=X)
        self.search_btn = Button(search_frame, text="搜索", command=self.search, bg="blue", fg="white", width=10)
        self.search_btn.pack(side=LEFT, padx=5)

        result_frame = LabelFrame(main_frame, text="搜索结果")
        result_frame.pack(fill=BOTH, expand=True, pady=5)
        columns = ('书名', '演播', '作者')
        self.tree = ttk.Treeview(result_frame, columns=columns, show='tree headings', height=6)
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

        config_frame = Frame(main_frame)
        config_frame.pack(fill=X, pady=5)
        Label(config_frame, text="并发数：").pack(side=LEFT)
        Spinbox(config_frame, from_=1, to=5, textvariable=self.max_workers_var, width=4).pack(side=LEFT, padx=2)
        Label(config_frame, text="重试：").pack(side=LEFT, padx=(10,0))
        Spinbox(config_frame, from_=0, to=5, textvariable=self.retry_var, width=4).pack(side=LEFT, padx=2)
        Label(config_frame, text="超时：").pack(side=LEFT, padx=(10,0))
        Spinbox(config_frame, from_=5, to=60, textvariable=self.timeout_var, width=4).pack(side=LEFT, padx=2)
        Label(config_frame, text="延迟范围：").pack(side=LEFT, padx=(10,0))
        Entry(config_frame, textvariable=self.delay_min_var, width=4).pack(side=LEFT)
        Label(config_frame, text="-").pack(side=LEFT)
        Entry(config_frame, textvariable=self.delay_max_var, width=4).pack(side=LEFT)

        dir_frame = Frame(main_frame)
        dir_frame.pack(fill=X, pady=5)
        Label(dir_frame, text="保存目录：").pack(side=LEFT)
        Entry(dir_frame, textvariable=self.save_path_var, width=60).pack(side=LEFT, padx=5, expand=True, fill=X)
        Button(dir_frame, text="浏览", command=self.select_save_dir).pack(side=LEFT)

        stats_frame = Frame(main_frame)
        stats_frame.pack(fill=X, pady=5)
        Label(stats_frame, text="完成:").pack(side=LEFT)
        self.completed_label = Label(stats_frame, text="0", fg="green", font=("Arial", 10, "bold"))
        self.completed_label.pack(side=LEFT, padx=(0,10))
        Label(stats_frame, text="失败:").pack(side=LEFT)
        self.failed_label = Label(stats_frame, text="0", fg="red", font=("Arial", 10, "bold"))
        self.failed_label.pack(side=LEFT, padx=(0,10))
        Label(stats_frame, text="总章:").pack(side=LEFT)
        self.total_label = Label(stats_frame, text="0", font=("Arial", 10, "bold"))
        self.total_label.pack(side=LEFT)

        self.progress = ttk.Progressbar(main_frame, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=X, pady=2)

        ctrl_frame = Frame(main_frame)
        ctrl_frame.pack(fill=X, pady=5)
        self.start_btn = Button(ctrl_frame, text="开始下载", command=self.start_download, bg="green", fg="white", width=12)
        self.start_btn.pack(side=LEFT, padx=2)
        self.stop_btn = Button(ctrl_frame, text="停止", command=self.stop_download, state=DISABLED, bg="red", fg="white", width=8)
        self.stop_btn.pack(side=LEFT, padx=2)

        log_frame = LabelFrame(main_frame, text="下载日志")
        log_frame.pack(fill=BOTH, expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=WORD, height=6)
        self.log_text.pack(fill=BOTH, expand=True)

        fail_frame = LabelFrame(main_frame, text="失败日志")
        fail_frame.pack(fill=BOTH, expand=True, pady=5)
        self.fail_text = scrolledtext.ScrolledText(fail_frame, wrap=WORD, height=4, fg="red")
        self.fail_text.pack(fill=BOTH, expand=True)

    def select_save_dir(self):
        dir_path = filedialog.askdirectory(title="选择保存目录")
        if dir_path:
            self.save_path_var.set(dir_path)

    def search(self):
        keyword = self.search_var.get().strip()
        if not keyword:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        self.search_btn.config(state=DISABLED, text="搜索中...")
        self.log(f"正在搜索：{keyword}")
        threading.Thread(target=self._search_worker, args=(keyword,), daemon=True).start()

    def _search_worker(self, keyword):
        for item in self.tree.get_children():
            self.tree.delete(item)
        search_url = f"https://m.i275.com/search.php?q={urllib.parse.quote(keyword)}"
        try:
            headers = {'User-Agent': random.choice(USER_AGENTS)}
            resp = curl_requests.get(search_url, headers=headers, timeout=15, impersonate=random.choice(IMPERSONATES))
            resp.encoding = 'utf-8'
            if resp.status_code != 200:
                self.log(f"搜索请求失败，状态码 {resp.status_code}")
                self.after(0, lambda: self.search_btn.config(state=NORMAL, text="搜索"))
                return
            soup = BeautifulSoup(resp.text, 'html.parser')
            items = soup.find_all('a', href=re.compile(r'^/book/\d+\.html'))
            if not items:
                self.log("未找到相关结果")
                self.after(0, lambda: self.search_btn.config(state=NORMAL, text="搜索"))
                return
            def update_tree():
                index = 1
                for a in items:
                    href = a.get('href')
                    book_url = urllib.parse.urljoin("https://m.i275.com/", href)
                    title_div = a.find('h3') or a.find('div', class_='font-medium')
                    if not title_div:
                        continue
                    title = title_div.get_text(strip=True)
                    if not title or title == "未知书名":
                        continue
                    info_spans = a.find_all('p', class_='text-xs')
                    announcer = ""
                    author = ""
                    for p in info_spans:
                        text = p.get_text(strip=True)
                        if "演播" in text:
                            announcer = text.replace("演播", "").strip()
                        elif "作者" in text:
                            author = text.replace("作者", "").strip()
                    self.tree.insert('', 'end', text=str(index), values=(title, announcer, author), tags=(book_url, title))
                    index += 1
                self.log(f"搜索完成，共找到 {index-1} 条结果")
                self.search_btn.config(state=NORMAL, text="搜索")
            self.after(0, update_tree)
        except Exception as e:
            self.log(f"搜索异常: {e}")
            self.after(0, lambda: self.search_btn.config(state=NORMAL, text="搜索"))

    def on_select(self, event):
        selected = self.tree.selection()
        if selected:
            item = selected[0]
            values = self.tree.item(item, 'values')
            self.selected_book_url = self.tree.item(item, 'tags')[0]
            self.selected_book_title = values[0]
            self.selected_book_announcer = values[1]
            self.log(f"已选中：{self.selected_book_title} - {self.selected_book_announcer}")

    def start_download(self):
        with self.state_lock:
            if self.running:
                messagebox.showwarning("提示", "下载正在进行中")
                return
            if not self.selected_book_url:
                messagebox.showerror("错误", "请先在搜索结果中选择一本小说")
                return
            save_dir = self.save_path_var.get().strip()
            if not save_dir:
                save_dir = os.getcwd()
                self.save_path_var.set(save_dir)
            folder_name = self.selected_book_title
            if self.selected_book_announcer and self.selected_book_announcer.strip():
                folder_name = f"{self.selected_book_title}-{self.selected_book_announcer}"
            safe_folder = sanitize_filename(folder_name)
            self.book_dir = os.path.join(save_dir, safe_folder)
            self.auto_loop_active = True
            self.running = True
        self.start_btn.config(state=DISABLED)
        self.stop_btn.config(state=NORMAL)
        self.fail_text.delete(1.0, END)
        self.progress_var.set(0)
        self.auto_loop_thread = threading.Thread(target=self.auto_loop)
        self.auto_loop_thread.daemon = True
        self.auto_loop_thread.start()

    def auto_loop(self):
        while True:
            with self.state_lock:
                if not self.auto_loop_active:
                    break
            with DownloadWorker(
                album_url=self.selected_book_url,
                max_workers=self.max_workers_var.get(),
                request_delay=(self.delay_min_var.get(), self.delay_max_var.get()),
                retry_times=self.retry_var.get(),
                timeout=self.timeout_var.get(),
                save_dir=self.book_dir,
                log_queue=self.log_queue
            ) as worker:
                self.worker = worker
                worker_thread = threading.Thread(target=worker.start_download, args=(self.book_dir,))
                worker_thread.daemon = True
                worker_thread.start()
                worker_thread.join()
            with self.state_lock:
                if not self.auto_loop_active or (self.worker and self.worker.stop_flag):
                    break
                if not self.worker.failed_set:
                    self.log("全部章节下载完成")
                    break
                self.log(f"仍有 {len(self.worker.failed_set)} 章失败，10秒后继续重试")
            time.sleep(10)
        with self.state_lock:
            self.auto_loop_active = False
            self.running = False
        self.after(0, lambda: self.start_btn.config(state=NORMAL))
        self.after(0, lambda: self.stop_btn.config(state=DISABLED))

    def stop_download(self):
        with self.state_lock:
            if self.worker:
                self.worker.stop_flag = True
            self.auto_loop_active = False
            self.running = False
        self.after(0, lambda: self.start_btn.config(state=NORMAL))
        self.after(0, lambda: self.stop_btn.config(state=DISABLED))
        self.log("用户请求停止...")

    def update_log(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
                if msg.startswith("FAIL:"):
                    parts = msg.split(':', 2)
                    if len(parts) == 3:
                        self.fail_text.insert(END, f"第 {parts[1]} 章：{parts[2]}\n")
                        self.fail_text.see(END)
                elif msg == "FAIL_CLEAR":
                    self.fail_text.delete(1.0, END)
                elif msg.startswith("STATS:"):
                    parts = msg.split(':')
                    if len(parts) == 4:
                        completed = int(parts[1])
                        failed = int(parts[2])
                        total = int(parts[3])
                        self.completed_label.config(text=str(completed))
                        self.failed_label.config(text=str(failed))
                        self.total_label.config(text=str(total))
                        if total > 0:
                            progress = int((completed + failed) / total * 100)
                            self.progress_var.set(progress)
                else:
                    self.log_text.insert(END, msg + "\n")
                    self.log_text.see(END)
            except queue.Empty:
                break
        self.after(100, self.update_log)

    def log(self, msg):
        self.log_queue.put(msg)

if __name__ == "__main__":
    app = DownloaderApp()
    app.mainloop()
