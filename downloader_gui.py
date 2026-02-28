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
        self.pause_flag = False
        self.pause_cond = threading.Condition()
        self.session = requests.Session()
        self.session.headers.update({
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
        self.downloaded_file = os.path.join(save_dir, '.downloaded.json')
        self.load_downloaded()
        self.executor = None
        self.failed_chapters = []
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

    def load_downloaded(self):
        if os.path.exists(self.downloaded_file):
            with open(self.downloaded_file, 'r', encoding='utf-8') as f:
                self.downloaded = set(json.load(f))
        else:
            self.downloaded = set()

    def save_downloaded(self):
        with open(self.downloaded_file, 'w', encoding='utf-8') as f:
            json.dump(list(self.downloaded), f, ensure_ascii=False)

    def check_pause(self):
        with self.pause_cond:
            while self.pause_flag and not self.stop_flag:
                self.pause_cond.wait()

    def fetch_url(self, url, referer=None, timeout=None, retries=None):
        if timeout is None:
            timeout = self.timeout
        if retries is None:
            retries = self.retry_times
        for attempt in range(retries):
            if self.stop_flag:
                return None
            self.check_pause()
            try:
                headers = {'User-Agent': random.choice(USER_AGENTS)}
                if referer:
                    headers['Referer'] = referer
                else:
                    headers['Referer'] = self.album_url
                resp = self.session.get(url, headers=headers, timeout=timeout)
                resp.encoding = 'utf-8'
                if resp.status_code == 200:
                    if "您的操作太快了" in resp.text or "稍后再试" in resp.text:
                        self.log("检测到访问频率过高，等待30秒后重试...")
                        time.sleep(30)
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
            if "立即开始收听" in chap_title or "继续播放" in chap_title:
                continue
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
        patterns = [
            r"url:\s*'([^']+)'",
            r'"url"\s*:\s*"([^"]+)"',
            r"url:\s*\"([^\"]+)\"",
            r'audioUrl\s*=\s*[\'"]([^\'"]+)[\'"]',
            r'var\s+audioUrl\s*=\s*[\'"]([^\'"]+)[\'"]',
            r'<audio[^>]+src="([^"]+)"',
            r'<source[^>]+src="([^"]+)"',
            r'"audio"\s*:\s*\{\s*"url"\s*:\s*"([^"]+)"',
            r'"play_url"\s*:\s*"([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                url = match.group(1)
                url = url.replace('\\/', '/')
                return url
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'audioUrl' in script.string:
                lines = script.string.split('\n')
                for line in lines:
                    if 'audioUrl' in line:
                        m = re.search(r'audioUrl\s*[=:]\s*[\'"]([^\'"]+)[\'"]', line)
                        if m:
                            return m.group(1).replace('\\/', '/')
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
                            self.check_pause()
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
        if play_url in self.downloaded:
            self.completed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 已下载，计入完成")
            return True
        self.log(f"[{chap_num}] 开始处理：{chap_title}")
        time.sleep(random.uniform(*self.request_delay))
        audio_url = self.get_audio_url(play_url)
        if not audio_url:
            self.failed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 获取音频URL失败，跳过")
            self.fail_log(chap_num, chap_title)
            return False
        safe_title = self.sanitize_filename(chap_title)
        filename = f"{safe_title}.m4a"
        filepath = os.path.join(download_dir, filename)
        success = self.download_audio(audio_url, filepath)
        if success:
            self.downloaded.add(play_url)
            self.save_downloaded()
            self.completed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 下载完成 -> {filename}")
            return True
        else:
            self.failed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 下载失败")
            self.fail_log(chap_num, chap_title)
            return False

    def start_download(self, download_dir):
        self.clear_fail_log()
        self.completed = 0
        self.failed = 0
        self.log("开始获取专辑信息...")
        self.fetch_url("https://m.i275.com/", referer="https://m.i275.com/")
        album_title = self.get_album_title()
        self.log(f"专辑标题：{album_title}")
        os.makedirs(download_dir, exist_ok=True)
        self.log(f"保存目录：{download_dir}")
        self.log("正在获取章节列表...")
        chapters = self.get_chapter_links()
        if not chapters:
            self.log("未找到任何章节，请检查URL或网络。")
            return
        self.total_chapters = len(chapters)
        self.stats_log()
        self.log(f"共找到 {self.total_chapters} 个章节")
        self.failed_chapters = []
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        future_to_chap = {
            self.executor.submit(self.process_chapter, chap, download_dir): chap
            for chap in chapters
        }
        try:
            for future in as_completed(future_to_chap):
                if self.stop_flag:
                    break
                self.check_pause()
                chap = future_to_chap[future]
                try:
                    future.result()
                except Exception as e:
                    self.log(f"[{chap['num']}] 处理异常: {e}")
                    self.failed_chapters.append(chap)
        finally:
            if self.stop_flag:
                self.executor.shutdown(wait=False, cancel_futures=True)
            else:
                self.executor.shutdown(wait=True)
        if not self.stop_flag:
            self.log(f"下载完成！成功 {self.completed} 失败 {self.failed}")
            if self.failed_chapters:
                self.log(f"失败 {len(self.failed_chapters)} 章")
        else:
            self.log("下载已停止")

    def pause(self):
        with self.pause_cond:
            self.pause_flag = True

    def resume(self):
        with self.pause_cond:
            self.pause_flag = False
            self.pause_cond.notify_all()

class DownloaderApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("有声小说搜索下载器")
        self.geometry("950x750")
        self.resizable(True, True)
        self.search_var = StringVar()
        self.max_workers_var = IntVar(value=2)
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

        progress_frame = Frame(main_frame)
        progress_frame.pack(fill=X, pady=5)
        self.progress = ttk.Progressbar(progress_frame, orient=HORIZONTAL, length=100, mode='determinate')
        self.progress.pack(fill=X, expand=True)

        ctrl_frame = Frame(main_frame)
        ctrl_frame.pack(fill=X, pady=5)
        self.start_btn = Button(ctrl_frame, text="开始下载选中书籍", command=self.start_download, bg="green", fg="white", width=20)
        self.start_btn.pack(side=LEFT, padx=2)
        self.pause_btn = Button(ctrl_frame, text="暂停", command=self.pause_download, bg="orange", fg="white", width=8, state=DISABLED)
        self.pause_btn.pack(side=LEFT, padx=2)
        self.resume_btn = Button(ctrl_frame, text="继续", command=self.resume_download, bg="blue", fg="white", width=8, state=DISABLED)
        self.resume_btn.pack(side=LEFT, padx=2)
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
        search_url = f"https://m.i275.com/search.php?q={urllib.parse.quote(keyword)}"
        try:
            headers = {"User-Agent": random.choice(USER_AGENTS)}
            resp = requests.get(search_url, headers=headers, timeout=15)
            resp.encoding = 'utf-8'
            if resp.status_code != 200:
                self.log(f"搜索请求失败，状态码 {resp.status_code}")
                return
            soup = BeautifulSoup(resp.text, 'html.parser')
            items = soup.find_all('a', href=re.compile(r'^/book/\d+\.html'))
            if not items:
                self.log("未找到相关结果")
                return
            index = 1
            for a in items:
                href = a.get('href')
                book_url = requests.compat.urljoin("https://m.i275.com/", href)
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
        except Exception as e:
            self.log(f"搜索异常: {e}")

    def on_select(self, event):
        selected = self.tree.selection()
        if selected:
            item = selected[0]
            self.selected_book_url = self.tree.item(item, 'tags')[0]
            self.selected_book_title = self.tree.item(item, 'tags')[1]
            self.log(f"已选中：{self.selected_book_title}")

    def start_download(self):
        if self.running:
            messagebox.showwarning("提示", "已有任务在运行")
            return
        if not self.selected_book_url:
            messagebox.showerror("错误", "请先在搜索结果中选择一本小说")
            return
        save_dir = self.save_path_var.get().strip()
        if not save_dir:
            save_dir = os.getcwd()
            self.save_path_var.set(save_dir)
        book_dir = os.path.join(save_dir, self.selected_book_title)
        self.worker = DownloadWorker(
            album_url=self.selected_book_url,
            max_workers=self.max_workers_var.get(),
            request_delay=(self.delay_min_var.get(), self.delay_max_var.get()),
            retry_times=self.retry_var.get(),
            timeout=self.timeout_var.get(),
            save_dir=book_dir,
            log_queue=self.log_queue
        )
        self.running = True
        self.start_btn.config(state=DISABLED)
        self.pause_btn.config(state=NORMAL)
        self.resume_btn.config(state=DISABLED)
        self.stop_btn.config(state=NORMAL)
        self.fail_text.delete(1.0, END)
        self.worker_thread = threading.Thread(target=self.worker.start_download, args=(book_dir,))
        self.worker_thread.daemon = True
        self.worker_thread.start()

    def pause_download(self):
        if self.worker:
            self.worker.pause()
            self.pause_btn.config(state=DISABLED)
            self.resume_btn.config(state=NORMAL)
            self.log("下载已暂停")

    def resume_download(self):
        if self.worker:
            self.worker.resume()
            self.pause_btn.config(state=NORMAL)
            self.resume_btn.config(state=DISABLED)
            self.log("下载已继续")

    def stop_download(self):
        if self.worker:
            self.worker.stop_flag = True
            self.worker.resume()
        self.running = False
        self.start_btn.config(state=NORMAL)
        self.pause_btn.config(state=DISABLED)
        self.resume_btn.config(state=DISABLED)
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
                        if total > 0:
                            percent = (completed / total) * 100
                            self.progress['value'] = percent
                else:
                    self.log_text.insert(END, msg + "\n")
                    self.log_text.see(END)
            except queue.Empty:
                break
        if self.worker_thread and not self.worker_thread.is_alive():
            if self.running:
                self.running = False
                self.start_btn.config(state=NORMAL)
                self.pause_btn.config(state=DISABLED)
                self.resume_btn.config(state=DISABLED)
                self.stop_btn.config(state=DISABLED)
                self.log("下载线程已结束")
        self.after(100, self.update_log)

    def log(self, msg):
        self.log_queue.put(msg)

if __name__ == "__main__":
    app = DownloaderApp()
    app.mainloop()
