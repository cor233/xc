# -*- coding: utf-8 -*-
"""
悦听吧有声书下载器
适用于 http://www.yuetingba.cn 或 http://106.13.91.31:43134
"""
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
import requests

# ================== 配置区 ==================
BASE_URL = "http://www.yuetingba.cn"          # 可替换为实际域名
SEARCH_URL = f"{BASE_URL}/Search"
API_PLAY_URL = f"{BASE_URL}/api/ting/play"    # 获取音频直链的接口

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]

def sanitize_filename(name):
    """去除文件名中的非法字符"""
    char_map = {
        '\\': '＼', '/': '／', ':': '：', '*': '＊',
        '?': '？', '"': '＂', '<': '＜', '>': '＞', '|': '｜'
    }
    return re.sub(r'[\\/:*?"<>|]', lambda m: char_map[m.group(0)], name).strip()

# ================== 请求限速器 ==================
class RateLimiter:
    def __init__(self, min_delay=1.0, max_delay=3.0):
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
                if elapsed < required:
                    time.sleep(required - elapsed)
            self.last_request_time = time.time()

# ================== 下载工作线程 ==================
class DownloadWorker:
    def __init__(self, book_url, max_workers, request_delay, retry_times, timeout, save_dir, log_queue):
        self.book_url = book_url          # 书籍详情页URL
        self.max_workers = max_workers
        self.request_delay = request_delay
        self.retry_times = retry_times
        self.timeout = timeout
        self.save_dir = save_dir
        self.log_queue = log_queue
        self.stop_flag = False
        self.session = requests.Session()
        self.executor = None
        self.rate_limiter = RateLimiter(request_delay[0], request_delay[1])
        self.completed_file = os.path.join(save_dir, '.completed.json')
        self.failed_file = os.path.join(save_dir, '.failed.json')
        self.lock = threading.Lock()
        self.completed_set = set()   # 已完成的章节ID
        self.failed_set = set()      # 失败的章节ID
        self.total_chapters = 0
        self.completed = 0
        self.failed = 0
        # 页面内嵌的关键参数（从详情页解析）
        self.book_id = None
        self.assl = None
        self.ts = None
        self.es = None
        self.load_completed()
        self.load_failed()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.session.close()
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

    def load_completed(self):
        if os.path.exists(self.completed_file):
            with open(self.completed_file, 'r', encoding='utf-8') as f:
                self.completed_set = set(json.load(f))

    def save_completed(self):
        with self.lock:
            with open(self.completed_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.completed_set), f, ensure_ascii=False)

    def load_failed(self):
        if os.path.exists(self.failed_file):
            with open(self.failed_file, 'r', encoding='utf-8') as f:
                self.failed_set = set(json.load(f))

    def save_failed(self):
        with self.lock:
            with open(self.failed_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.failed_set), f, ensure_ascii=False)

    def fetch_url(self, url, method='GET', post_data=None, referer=None, retries=None):
        """统一请求函数，支持GET/POST，自动重试与限速"""
        if retries is None:
            retries = self.retry_times
        for attempt in range(retries):
            if self.stop_flag:
                return None
            try:
                self.rate_limiter.wait()
                headers = {
                    'User-Agent': random.choice(USER_AGENTS),
                    'Referer': referer or self.book_url
                }
                if method.upper() == 'POST':
                    headers['Content-Type'] = 'application/json'
                    resp = self.session.post(url, headers=headers, data=json.dumps(post_data), timeout=self.timeout)
                else:
                    resp = self.session.get(url, headers=headers, timeout=self.timeout)
                resp.encoding = 'utf-8'
                if resp.status_code == 200:
                    if "您的操作太快了" in resp.text or "稍后再试" in resp.text:
                        wait_time = random.randint(30, 60)
                        self.log(f"检测到访问频率过高，等待{wait_time}秒后重试...")
                        time.sleep(wait_time)
                        continue
                    return resp
                else:
                    self.log(f"请求失败，状态码 {resp.status_code}，重试 {attempt+1}/{retries}")
            except Exception as e:
                self.log(f"请求异常: {e}，重试 {attempt+1}/{retries}")
            time.sleep(random.uniform(*self.request_delay) * 2)
        return None

    def parse_book_page(self):
        """解析书籍详情页，提取book_id、assl等参数以及章节列表"""
        resp = self.fetch_url(self.book_url)
        if not resp:
            return None, None
        soup = BeautifulSoup(resp.text, 'html.parser')
        # 提取书名
        title_tag = soup.find('h1', class_='book-detail-title') or soup.find('h1')
        book_title = title_tag.get_text(strip=True) if title_tag else "未知书名"
        # 从页面内嵌脚本中提取全局变量
        script_text = resp.text
        patterns = {
            'bookId': r'var\s+bookId\s*=\s*[\'"]([^\'"]+)[\'"]',
            'assl': r'var\s+assl\s*=\s*[\'"]([^\'"]+)[\'"]',
            'ts': r'var\s+ts\s*=\s*[\'"]([^\'"]+)[\'"]',
            'es': r'var\s+es\s*=\s*[\'"]([^\'"]+)[\'"]',
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, script_text)
            if match:
                setattr(self, key, match.group(1))
                self.log(f"提取到参数 {key} = {getattr(self, key)[:20]}...")
            else:
                self.log(f"警告：未找到参数 {key}")
        # 提取章节列表（从播放列表容器中）
        chapters = []
        # 查找所有章节项容器
        item_divs = soup.find_all('div', class_='ting-list-content-item')
        for div in item_divs:
            item_id = div.get('id', '')
            if not item_id.startswith('item_'):
                continue
            ting_id = item_id[5:]   # 去掉 "item_" 前缀
            # 获取章节标题
            title_a = div.find('a', title=True)
            if title_a:
                chap_title = title_a.get('title', '').strip()
            else:
                chap_title = ting_id
            # 尝试从文本中提取章节序号（可选）
            num_match = re.search(r'(\d+)', chap_title)
            chap_num = int(num_match.group(1)) if num_match else 0
            chapters.append({
                'ting_id': ting_id,
                'title': chap_title,
                'num': chap_num
            })
        # 按序号排序
        chapters.sort(key=lambda x: x['num'])
        return book_title, chapters

    def get_audio_url(self, ting_id):
        """通过API获取音频真实URL"""
        if not all([self.book_id, self.assl, self.ts, self.es]):
            self.log("缺少必要参数，无法获取音频URL")
            return None
        post_data = {
            "tingId": ting_id,
            "bookId": self.book_id,
            "assl": self.assl,
            "ts": self.ts,
            "es": self.es
        }
        resp = self.fetch_url(API_PLAY_URL, method='POST', post_data=post_data, referer=self.book_url)
        if not resp:
            return None
        try:
            data = resp.json()
            # 根据实际返回格式提取url，常见字段：data.url 或 url
            audio_url = data.get('data', {}).get('url') or data.get('url')
            if audio_url:
                return audio_url
            else:
                self.log(f"API返回数据中无URL: {data}")
                return None
        except Exception as e:
            self.log(f"解析API响应失败: {e}")
            return None

    def download_audio(self, audio_url, save_path):
        """下载音频文件"""
        try:
            headers = {
                'User-Agent': random.choice(USER_AGENTS),
                'Referer': self.book_url
            }
            with self.session.get(audio_url, headers=headers, stream=True, timeout=self.timeout) as r:
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

    def process_chapter(self, chapter, download_dir):
        chap_num = chapter['num']
        chap_title = chapter['title']
        ting_id = chapter['ting_id']

        if ting_id in self.completed_set:
            with self.lock:
                self.completed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 已完成，跳过")
            return

        self.log(f"[{chap_num}] 开始处理：{chap_title}")
        audio_url = self.get_audio_url(ting_id)
        if not audio_url:
            with self.lock:
                self.failed += 1
                self.failed_set.add(ting_id)
            self.save_failed()
            self.stats_log()
            self.fail_log(chap_num, chap_title)
            return

        safe_title = sanitize_filename(chap_title)
        # 悦听吧音频一般为MP3格式
        filename = f"{safe_title}.mp3"
        filepath = os.path.join(download_dir, filename)
        temp_path = filepath + ".part"

        success = self.download_audio(audio_url, temp_path)
        if success:
            if os.path.exists(temp_path):
                os.rename(temp_path, filepath)
            with self.lock:
                self.completed_set.add(ting_id)
            self.save_completed()
            if ting_id in self.failed_set:
                with self.lock:
                    self.failed_set.remove(ting_id)
                self.save_failed()
            with self.lock:
                self.completed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 下载完成 -> {filename}")
        else:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            with self.lock:
                self.failed += 1
                self.failed_set.add(ting_id)
            self.save_failed()
            self.stats_log()
            self.fail_log(chap_num, chap_title)

    def start_download(self, download_dir):
        self.clear_fail_log()
        with self.lock:
            self.completed = 0
            self.failed = 0
        self.load_completed()
        self.load_failed()
        self.log("开始获取书籍信息...")
        book_title, chapters = self.parse_book_page()
        if not book_title or not chapters:
            self.log("获取书籍信息失败，请检查网络或URL。")
            return
        self.log(f"书籍标题：{book_title}")
        os.makedirs(download_dir, exist_ok=True)
        self.log(f"保存目录：{download_dir}")
        self.total_chapters = len(chapters)
        self.stats_log()

        # 筛选需要下载的章节（优先处理失败列表中的）
        if self.failed_set:
            todo_chapters = [c for c in chapters if c['ting_id'] in self.failed_set]
            self.log(f"发现失败记录，本次将重试 {len(todo_chapters)} 个章节")
        else:
            todo_chapters = chapters
            self.log(f"首次下载，共 {len(todo_chapters)} 个章节")

        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        future_to_chap = {
            self.executor.submit(self.process_chapter, chap, download_dir): chap
            for chap in todo_chapters
        }
        try:
            for future in as_completed(future_to_chap):
                if self.stop_flag:
                    break
                chap = future_to_chap[future]
                try:
                    future.result()
                except Exception as e:
                    self.log(f"[{chap['num']}] 处理异常: {e}")
                    with self.lock:
                        self.failed_set.add(chap['ting_id'])
                    self.save_failed()
        finally:
            if self.stop_flag:
                self.executor.shutdown(wait=False)
            else:
                self.executor.shutdown(wait=True)

        if not self.stop_flag:
            self.log(f"本轮下载完成！成功 {self.completed} 失败 {self.failed}")
            if self.failed_set:
                self.log(f"仍有 {len(self.failed_set)} 章失败，将继续重试")
        else:
            self.log("下载已停止")

# ================== GUI 主界面 ==================
class DownloaderApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("悦听吧有声书下载器")
        self.geometry("950x800")
        self.resizable(True, True)
        self.search_var = StringVar()
        self.max_workers_var = IntVar(value=3)
        self.delay_min_var = DoubleVar(value=1.0)
        self.delay_max_var = DoubleVar(value=3.0)
        self.retry_var = IntVar(value=3)
        self.timeout_var = IntVar(value=15)
        self.save_path_var = StringVar()
        self.selected_book_url = None
        self.selected_book_title = None
        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.worker = None
        self.running = False
        self.auto_loop_active = False
        self.auto_loop_thread = None
        self.state_lock = threading.Lock()
        self.create_widgets()
        self.update_log()

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
        columns = ('书名', '分类', '状态')
        self.tree = ttk.Treeview(result_frame, columns=columns, show='tree headings', height=6)
        self.tree.heading('#0', text='序号')
        self.tree.heading('书名', text='书名')
        self.tree.heading('分类', text='分类')
        self.tree.heading('状态', text='状态')
        self.tree.column('#0', width=50)
        self.tree.column('书名', width=400)
        self.tree.column('分类', width=120)
        self.tree.column('状态', width=80)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar = ttk.Scrollbar(result_frame, orient=VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)

        # 配置区域
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

        # 统计信息
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

        # 控制按钮
        ctrl_frame = Frame(main_frame)
        ctrl_frame.pack(fill=X, pady=5)
        self.start_btn = Button(ctrl_frame, text="开始下载", command=self.start_download, bg="green", fg="white", width=12)
        self.start_btn.pack(side=LEFT, padx=2)
        self.stop_btn = Button(ctrl_frame, text="停止", command=self.stop_download, state=DISABLED, bg="red", fg="white", width=8)
        self.stop_btn.pack(side=LEFT, padx=2)

        # 日志区域
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
        self.log(f"正在搜索：{keyword}")
        for item in self.tree.get_children():
            self.tree.delete(item)

        search_url = f"{SEARCH_URL}?type=1&name={urllib.parse.quote(keyword)}"
        try:
            headers = {'User-Agent': random.choice(USER_AGENTS)}
            resp = requests.get(search_url, headers=headers, timeout=15)
            resp.encoding = 'utf-8'
            if resp.status_code != 200:
                self.log(f"搜索请求失败，状态码 {resp.status_code}")
                return
            soup = BeautifulSoup(resp.text, 'html.parser')
            # 搜索结果条目容器
            items = soup.select('.books-detail-img a')   # 获取封面链接，再找父级
            if not items:
                # 备用：直接找书名链接
                items = soup.find_all('a', href=re.compile(r'/book/detail/'))
            seen_urls = set()
            index = 1
            for a in items:
                href = a.get('href')
                if not href or '/book/detail/' not in href:
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                book_url = urllib.parse.urljoin(BASE_URL, href)
                # 获取书名
                title_elem = a.find('img') and a.find('img').get('alt') or a.get_text(strip=True)
                if not title_elem:
                    continue
                title = title_elem.strip()
                # 尝试获取分类和状态（需向上查找父容器）
                parent = a.find_parent('div', class_='books-detail-detail') or a.find_parent('div', class_='row')
                category = ""
                status = ""
                if parent:
                    cat_span = parent.find('span', string=re.compile('分类'))
                    if cat_span:
                        category = cat_span.find_next('span').get_text(strip=True)
                    status_span = parent.find('span', string=re.compile('状态'))
                    if status_span:
                        status = status_span.find_next('span').get_text(strip=True)
                self.tree.insert('', 'end', text=str(index), values=(title, category, status), tags=(book_url,))
                index += 1
            self.log(f"搜索完成，共找到 {index-1} 条结果")
        except Exception as e:
            self.log(f"搜索异常: {e}")

    def on_select(self, event):
        selected = self.tree.selection()
        if selected:
            item = selected[0]
            values = self.tree.item(item, 'values')
            self.selected_book_url = self.tree.item(item, 'tags')[0]
            self.selected_book_title = values[0]
            self.log(f"已选中：{self.selected_book_title}")

    def start_download(self):
        with self.state_lock:
            if self.running:
                messagebox.showwarning("提示", "下载正在进行中")
                return
            if not self.selected_book_url:
                messagebox.showerror("错误", "请先在搜索结果中选择一本书")
                return
            save_dir = self.save_path_var.get().strip()
            if not save_dir:
                save_dir = os.getcwd()
                self.save_path_var.set(save_dir)
            book_folder = sanitize_filename(self.selected_book_title)
            self.book_dir = os.path.join(save_dir, book_folder)
            self.auto_loop_active = True
            self.running = True
        self.start_btn.config(state=DISABLED)
        self.stop_btn.config(state=NORMAL)
        self.fail_text.delete(1.0, END)
        self.auto_loop_thread = threading.Thread(target=self.auto_loop)
        self.auto_loop_thread.daemon = True
        self.auto_loop_thread.start()

    def auto_loop(self):
        """自动循环重试失败的章节"""
        while True:
            with self.state_lock:
                if not self.auto_loop_active:
                    break
            with DownloadWorker(
                book_url=self.selected_book_url,
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
                    self.log("全部章节下载完成！")
                    break
                self.log(f"仍有 {len(self.worker.failed_set)} 章失败，10秒后继续重试")
            time.sleep(10)
        with self.state_lock:
            self.auto_loop_active = False
            self.running = False
        self.start_btn.config(state=NORMAL)
        self.stop_btn.config(state=DISABLED)

    def stop_download(self):
        with self.state_lock:
            if self.worker:
                self.worker.stop_flag = True
            self.auto_loop_active = False
            self.running = False
        self.start_btn.config(state=NORMAL)
        self.stop_btn.config(state=DISABLED)
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
                        self.completed_label.config(text=parts[1])
                        self.failed_label.config(text=parts[2])
                        self.total_label.config(text=parts[3])
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
