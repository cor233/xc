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
import requests
from bs4 import BeautifulSoup

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1"
]

HEADERS_TEMPLATE = {
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
}

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
        self.session = requests.Session()
        self.session.headers.update(HEADERS_TEMPLATE)
        self.completed_file = os.path.join(save_dir, '.completed.json')
        self.failed_file = os.path.join(save_dir, '.failed.json')
        self.file_lock = threading.Lock()
        self.load_completed()
        self.load_failed()
        self.executor = None
        self.total_chapters = 0
        self.completed = 0
        self.failed = 0

    def log(self, msg):
        self.log_queue.put(msg)

    def stats_log(self):
        self.log_queue.put(f"STATS:{self.completed}:{self.failed}:{self.total_chapters}")

    def fail_log(self, chap_num, chap_title):
        self.log_queue.put(f"FAIL:{chap_num}:{chap_title}")

    def clear_fail_log(self):
        self.log_queue.put("FAIL_CLEAR")

    def load_completed(self):
        if os.path.exists(self.completed_file):
            with open(self.completed_file, 'r', encoding='utf-8') as f:
                self.completed_set = set(json.load(f))
        else:
            self.completed_set = set()

    def save_completed(self):
        with self.file_lock:
            with open(self.completed_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.completed_set), f, ensure_ascii=False)

    def load_failed(self):
        if os.path.exists(self.failed_file):
            with open(self.failed_file, 'r', encoding='utf-8') as f:
                self.failed_set = set(json.load(f))
        else:
            self.failed_set = set()

    def save_failed(self):
        with self.file_lock:
            with open(self.failed_file, 'w', encoding='utf-8') as f:
                json.dump(list(self.failed_set), f, ensure_ascii=False)

    def fetch_url(self, url, referer=None, timeout=None, retries=None):
        if timeout is None:
            timeout = self.timeout
        if retries is None:
            retries = self.retry_times
        for attempt in range(retries):
            if self.stop_flag:
                return None
            try:
                headers = {'User-Agent': random.choice(USER_AGENTS)}
                if referer:
                    headers['Referer'] = referer
                else:
                    headers['Referer'] = self.album_url
                resp = self.session.get(url, headers=headers, timeout=timeout)
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
            title = "未知专辑"
        title = re.sub(r'[\\/*?:"<>|]', '_', title)
        return title

    def get_album_announcer(self):
        resp = self.fetch_url(self.album_url)
        if not resp:
            return ""
        soup = BeautifulSoup(resp.text, 'html.parser')
        announcer_links = soup.select('ul.d-grid a[href^="/user/"]')
        if announcer_links:
            announcer = announcer_links[0].get_text(strip=True)
            return announcer
        return ""

    def get_all_chapters(self):
        resp = self.fetch_url(self.album_url)
        if not resp:
            self.log("无法获取专辑详情页")
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        total_span = soup.find('span', string=re.compile(r'章节：(\d+)'))
        total_chapters = 0
        if total_span:
            match = re.search(r'(\d+)', total_span.get_text())
            if match:
                total_chapters = int(match.group(1))
        else:
            total_span = soup.find('li', string=re.compile(r'章节：(\d+)'))
            if total_span:
                match = re.search(r'(\d+)', total_span.get_text())
                if match:
                    total_chapters = int(match.group(1))
        if total_chapters == 0:
            self.log("警告：无法从页面解析总章节数，将尝试使用默认分页")
        resource_id = None
        script = soup.find('script', text=re.compile(r'resourcesid=(\d+)'))
        if script:
            match = re.search(r'resourcesid=(\d+)', script.string)
            if match:
                resource_id = match.group(1)
        if not resource_id:
            player_trigger = soup.find('a', class_='player-trigger')
            if player_trigger:
                info = player_trigger.get('player-info', '')
                match = re.search(r'resourcesid=(\d+)', info)
                if match:
                    resource_id = match.group(1)
        if not resource_id:
            self.log("错误：未找到资源ID，无法获取章节列表")
            return []
        self.log(f"资源ID：{resource_id}，总章节数：{total_chapters}")
        page_size = 10
        total_pages = (total_chapters + page_size - 1) // page_size if total_chapters else 1
        all_chapters = []
        for page in range(total_pages):
            if self.stop_flag:
                break
            ajax_url = f"https://www.lrts.me/ajax/book/{resource_id}/{page+1}/{page_size}"
            self.log(f"正在获取第 {page+1} 页章节...")
            resp = self.fetch_url(ajax_url, referer=self.album_url)
            if not resp:
                self.log(f"获取第 {page+1} 页失败")
                continue
            try:
                data = resp.json()
                if data.get('status') == 'success':
                    items = data['data']['data']
                    if not items:
                        self.log(f"第 {page+1} 页无数据")
                        continue
                    for item in items:
                        chap_num = item.get('section')
                        chap_title = item.get('resName')
                        chap_url = f"https://www.lrts.me/player?type=2&resourcesid={resource_id}&sections={chap_num}"
                        all_chapters.append({
                            'num': chap_num,
                            'title': chap_title,
                            'url': chap_url
                        })
                    self.log(f"第 {page+1} 页获取到 {len(items)} 个章节")
                else:
                    self.log(f"获取章节列表失败: {data.get('errMsg')}")
            except Exception as e:
                self.log(f"解析章节列表异常: {e}")
            time.sleep(random.uniform(*self.request_delay))
        all_chapters.sort(key=lambda x: x['num'])
        self.log(f"总共获取到 {len(all_chapters)} 个章节")
        return all_chapters

    def get_audio_url(self, play_url):
        resp = self.fetch_url(play_url, referer=play_url)
        if not resp:
            return None
        text = resp.text
        soup = BeautifulSoup(text, 'html.parser')
        audio_tag = soup.find('audio')
        if audio_tag and audio_tag.get('src'):
            return audio_tag['src']
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'audioUrl' in script.string:
                lines = script.string.split('\n')
                for line in lines:
                    if 'audioUrl' in line:
                        m = re.search(r'audioUrl\s*[=:]\s*[\'"]([^\'"]+)[\'"]', line)
                        if m:
                            return m.group(1).replace('\\/', '/')
        patterns = [
            r'"audioUrl"\s*:\s*"([^"]+)"',
            r'audioUrl\s*=\s*"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).replace('\\/', '/')
        self.log(f"无法从页面提取音频URL，响应片段：{text[:300]}")
        return None

    def download_audio(self, audio_url, save_path):
        try:
            headers = {'User-Agent': random.choice(USER_AGENTS), 'Referer': self.album_url}
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

    def sanitize_filename(self, name):
        map = {
            '\\': '＼', '/': '／', ':': '：', '*': '＊',
            '?': '？', '"': '＂', '<': '＜', '>': '＞', '|': '｜'
        }
        return re.sub(r'[\\/:*?"<>|]', lambda m: map[m.group(0)], name).strip()

    def process_chapter(self, chapter, download_dir):
        chap_num = chapter['num']
        chap_title = chapter['title']
        play_url = chapter['url']
        if play_url in self.completed_set:
            self.completed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 已完成，计入完成")
            return True
        self.log(f"[{chap_num}] 开始处理：{chap_title}")
        time.sleep(random.uniform(*self.request_delay))
        audio_url = self.get_audio_url(play_url)
        if not audio_url:
            self.failed += 1
            self.failed_set.add(play_url)
            self.save_failed()
            self.stats_log()
            self.log(f"[{chap_num}] 获取音频URL失败，跳过")
            self.fail_log(chap_num, chap_title)
            return False
        safe_title = self.sanitize_filename(chap_title)
        filename = f"{safe_title}.m4a"
        filepath = os.path.join(download_dir, filename)
        success = self.download_audio(audio_url, filepath)
        if success:
            self.completed_set.add(play_url)
            self.save_completed()
            if play_url in self.failed_set:
                self.failed_set.remove(play_url)
                self.save_failed()
            self.completed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 下载完成 -> {filename}")
            return True
        else:
            self.failed += 1
            self.failed_set.add(play_url)
            self.save_failed()
            self.stats_log()
            self.log(f"[{chap_num}] 下载失败")
            self.fail_log(chap_num, chap_title)
            return False

    def start_download(self, download_dir):
        self.clear_fail_log()
        self.completed = 0
        self.failed = 0
        self.load_completed()
        self.load_failed()
        self.log("开始获取专辑信息...")
        self.fetch_url("https://www.lrts.me/", referer="https://www.lrts.me/")
        album_title = self.get_album_title()
        announcer = self.get_album_announcer()
        self.log(f"专辑标题：{album_title}，演播：{announcer}")
        os.makedirs(download_dir, exist_ok=True)
        self.log(f"保存目录：{download_dir}")
        self.log("正在获取章节列表...")
        all_chapters = self.get_all_chapters()
        if not all_chapters:
            self.log("错误：未获取到任何章节，请检查网络或页面结构。")
            return
        self.total_chapters = len(all_chapters)
        if self.failed_set:
            chapters = [c for c in all_chapters if c['url'] in self.failed_set]
            self.log(f"发现失败记录，本次将重试 {len(chapters)} 个章节")
        else:
            chapters = all_chapters
            self.log(f"首次下载，共 {len(chapters)} 个章节")
        self.stats_log()
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        future_to_chap = {
            self.executor.submit(self.process_chapter, chap, download_dir): chap
            for chap in chapters
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
        finally:
            if self.stop_flag:
                self.executor.shutdown(wait=False, cancel_futures=True)
            else:
                self.executor.shutdown(wait=True)
        if not self.stop_flag:
            self.log(f"本轮下载完成！成功 {self.completed} 失败 {self.failed}")
            if self.failed_set:
                self.log(f"仍有 {len(self.failed_set)} 章失败，将继续重试")
        else:
            self.log("下载已停止")

class DownloaderApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("懒人听书下载器")
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
        self.selected_book_announcer = None
        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.worker = None
        self.running = False
        self.auto_loop_active = False
        self.auto_loop_thread = None
        self.create_widgets()
        self.update_log()

    def create_widgets(self):
        main_frame = Frame(self, padx=10, pady=10)
        main_frame.pack(fill=BOTH, expand=True)

        search_frame = Frame(main_frame)
        search_frame.pack(fill=X, pady=5)
        Label(search_frame, text="关键词：").pack(side=LEFT)
        Entry(search_frame, textvariable=self.search_var, width=50).pack(side=LEFT, padx=5, expand=True, fill=X)
        Button(search_frame, text="搜索", command=self.search, bg="blue", fg="white", width=10).pack(side=LEFT, padx=5)

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
        Spinbox(config_frame, from_=1, to=20, textvariable=self.max_workers_var, width=4).pack(side=LEFT, padx=2)
        Label(config_frame, text="重试：").pack(side=LEFT, padx=(10,0))
        Spinbox(config_frame, from_=0, to=10, textvariable=self.retry_var, width=4).pack(side=LEFT, padx=2)
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
        self.log(f"正在搜索：{keyword}")
        for item in self.tree.get_children():
            self.tree.delete(item)
        search_url = f"https://www.lrts.me/search/book/{urllib.parse.quote(keyword)}"
        try:
            headers = HEADERS_TEMPLATE.copy()
            headers['User-Agent'] = random.choice(USER_AGENTS)
            headers['Referer'] = 'https://www.lrts.me/'
            resp = requests.get(search_url, headers=headers, timeout=15)
            resp.encoding = 'utf-8'
            if resp.status_code != 200:
                self.log(f"搜索请求失败，状态码 {resp.status_code}")
                return
            if "没有找到相关内容" in resp.text or "未找到相关结果" in resp.text:
                self.log("搜索结果为空")
                return
            soup = BeautifulSoup(resp.text, 'html.parser')
            items = soup.find_all('li', class_='book-item')
            if not items:
                self.log("未解析到任何书籍条目，可能页面结构已变化")
                self.log(f"响应片段：{resp.text[:500]}")
                return
            index = 1
            for item in items:
                link = item.find('a', href=re.compile(r'^/book/\d+'))
                if not link:
                    continue
                book_url = "https://www.lrts.me" + link['href']
                title_div = item.find('a', class_='book-item-name')
                title = title_div.get_text(strip=True) if title_div else "未知书名"
                info_divs = item.find_all('div', class_='book-item-info')
                announcer = ""
                author = ""
                for info in info_divs:
                    text = info.get_text()
                    if "主播：" in text:
                        announcer = text.replace("主播：", "").strip()
                    elif "原著：" in text:
                        author = text.replace("原著：", "").strip()
                self.tree.insert('', 'end', text=str(index), values=(title, announcer, author), tags=(book_url, title))
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
            self.selected_book_announcer = values[1]
            self.log(f"已选中：{self.selected_book_title} - {self.selected_book_announcer}")

    def sanitize_filename(self, name):
        map = {
            '\\': '＼', '/': '／', ':': '：', '*': '＊',
            '?': '？', '"': '＂', '<': '＜', '>': '＞', '|': '｜'
        }
        return re.sub(r'[\\/:*?"<>|]', lambda m: map[m.group(0)], name).strip()

    def start_download(self):
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
        safe_folder = self.sanitize_filename(folder_name)
        self.book_dir = os.path.join(save_dir, safe_folder)
        self.auto_loop_active = True
        self.running = True
        self.start_btn.config(state=DISABLED)
        self.stop_btn.config(state=NORMAL)
        self.fail_text.delete(1.0, END)
        self.auto_loop_thread = threading.Thread(target=self.auto_loop)
        self.auto_loop_thread.daemon = True
        self.auto_loop_thread.start()

    def auto_loop(self):
        while self.auto_loop_active:
            self.worker = DownloadWorker(
                album_url=self.selected_book_url,
                max_workers=self.max_workers_var.get(),
                request_delay=(self.delay_min_var.get(), self.delay_max_var.get()),
                retry_times=self.retry_var.get(),
                timeout=self.timeout_var.get(),
                save_dir=self.book_dir,
                log_queue=self.log_queue
            )
            self.worker_thread = threading.Thread(target=self.worker.start_download, args=(self.book_dir,))
            self.worker_thread.daemon = True
            self.worker_thread.start()
            self.worker_thread.join()
            if not self.auto_loop_active or self.worker.stop_flag:
                break
            if not self.worker.failed_set:
                self.log("全部章节下载完成")
                break
            self.log(f"仍有 {len(self.worker.failed_set)} 章失败，10秒后继续重试")
            time.sleep(10)
        self.auto_loop_active = False
        self.running = False
        self.start_btn.config(state=NORMAL)
        self.stop_btn.config(state=DISABLED)

    def stop_download(self):
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
                        completed = int(parts[1])
                        failed = int(parts[2])
                        total = int(parts[3])
                        self.completed_label.config(text=str(completed))
                        self.failed_label.config(text=str(failed))
                        self.total_label.config(text=str(total))
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
