import os
import re
import time
import random
import threading
import queue
from tkinter import *
from tkinter import scrolledtext, messagebox, filedialog
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

class DownloadWorker:
    def __init__(self, album_url, max_workers, request_delay, retry_times, timeout, proxy_list, log_queue):
        self.album_url = album_url
        self.max_workers = max_workers
        self.request_delay = request_delay
        self.retry_times = retry_times
        self.timeout = timeout
        self.proxy_list = proxy_list
        self.log_queue = log_queue
        self.stop_flag = False
        self.headers = {
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
        }

    def log(self, msg):
        self.log_queue.put(msg)

    def get_random_proxy(self):
        if not self.proxy_list:
            return None
        return random.choice(self.proxy_list)

    def fetch_url(self, url, headers=None, timeout=None, retries=None):
        if timeout is None:
            timeout = self.timeout
        if retries is None:
            retries = self.retry_times
        for attempt in range(retries):
            if self.stop_flag:
                return None
            try:
                proxy = self.get_random_proxy()
                resp = requests.get(url, headers=headers or self.headers, proxies=proxy, timeout=timeout)
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
            parent = a.find_parent(attrs={'id': re.compile(r'chapter-pos-\d+')})
            chap_num = 0
            if parent:
                pid = parent.get('id')
                match = re.search(r'chapter-pos-(\d+)', pid)
                if match:
                    chap_num = int(match.group(1))
            title_span = a.find('span', class_='text-sm')
            chap_title = title_span.get_text(strip=True) if title_span else f"第{chap_num}章"
            chapter_links.append({
                'url': full_url,
                'num': chap_num,
                'title': chap_title
            })
        chapter_links.sort(key=lambda x: x['num'])
        return chapter_links

    def get_audio_url(self, play_url):
        resp = self.fetch_url(play_url)
        if not resp:
            return None
        pattern = r"url:\s*'([^']+)'"
        match = re.search(pattern, resp.text)
        if match:
            return match.group(1)
        return None

    def download_audio(self, audio_url, save_path):
        proxy = self.get_random_proxy()
        try:
            with requests.get(audio_url, headers=self.headers, proxies=proxy, stream=True, timeout=self.timeout) as r:
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
        play_url = chapter['url']
        self.log(f"[{chap_num}] 开始处理：{chap_title}")
        time.sleep(random.uniform(*self.request_delay))
        audio_url = self.get_audio_url(play_url)
        if not audio_url:
            self.log(f"[{chap_num}] 获取音频URL失败，跳过")
            return False
        safe_title = re.sub(r'[\\/*?:"<>|]', '_', chap_title)
        filename = f"{chap_num:03d}_{safe_title}.m4a"
        filepath = os.path.join(download_dir, filename)
        if os.path.exists(filepath):
            self.log(f"[{chap_num}] 文件已存在，跳过")
            return True
        success = self.download_audio(audio_url, filepath)
        if success:
            self.log(f"[{chap_num}] 下载完成 -> {filename}")
        else:
            self.log(f"[{chap_num}] 下载失败")
        return success

    def start_download(self, download_dir):
        self.log("开始获取专辑信息...")
        album_title = self.get_album_title()
        if not download_dir:
            download_dir = os.path.join(os.getcwd(), album_title)
        os.makedirs(download_dir, exist_ok=True)
        self.log(f"保存目录：{download_dir}")
        self.log("正在获取章节列表...")
        chapters = self.get_chapter_links()
        if not chapters:
            self.log("未找到任何章节，请检查URL或网络。")
            return
        self.log(f"共找到 {len(chapters)} 个章节")
        success_count = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_chap = {
                executor.submit(self.process_chapter, chap, download_dir): chap
                for chap in chapters
            }
            for future in as_completed(future_to_chap):
                if self.stop_flag:
                    executor.shutdown(wait=False)
                    self.log("下载已停止")
                    break
                chap = future_to_chap[future]
                try:
                    result = future.result()
                    if result:
                        success_count += 1
                except Exception as e:
                    self.log(f"[{chap['num']}] 处理异常: {e}")
        self.log(f"下载完成！成功 {success_count}/{len(chapters)} 章")

class DownloaderApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("有声小说批量下载工具")
        self.geometry("800x600")
        self.resizable(True, True)
        self.url_var = StringVar(value="https://m.i275.com/book/39161.html")
        self.max_workers_var = IntVar(value=5)
        self.delay_min_var = DoubleVar(value=0.5)
        self.delay_max_var = DoubleVar(value=1.5)
        self.retry_var = IntVar(value=3)
        self.timeout_var = IntVar(value=15)
        self.proxy_text = StringVar()
        self.save_path_var = StringVar()
        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.worker = None
        self.running = False
        self.create_widgets()
        self.update_log()

    def create_widgets(self):
        main_frame = Frame(self, padx=10, pady=10)
        main_frame.pack(fill=BOTH, expand=True)
        url_frame = Frame(main_frame)
        url_frame.pack(fill=X, pady=5)
        Label(url_frame, text="专辑URL：").pack(side=LEFT)
        Entry(url_frame, textvariable=self.url_var, width=80).pack(side=LEFT, padx=5, expand=True, fill=X)
        Button(url_frame, text="开始下载", command=self.start_download, bg="green", fg="white").pack(side=LEFT, padx=5)
        config_frame = LabelFrame(main_frame, text="高级设置", padx=5, pady=5)
        config_frame.pack(fill=X, pady=5)
        row1 = Frame(config_frame)
        row1.pack(fill=X, pady=2)
        Label(row1, text="并发线程数：").pack(side=LEFT)
        Spinbox(row1, from_=1, to=20, textvariable=self.max_workers_var, width=5).pack(side=LEFT, padx=5)
        Label(row1, text="重试次数：").pack(side=LEFT, padx=(20,0))
        Spinbox(row1, from_=0, to=10, textvariable=self.retry_var, width=5).pack(side=LEFT, padx=5)
        Label(row1, text="超时(秒)：").pack(side=LEFT, padx=(20,0))
        Spinbox(row1, from_=5, to=60, textvariable=self.timeout_var, width=5).pack(side=LEFT, padx=5)
        row2 = Frame(config_frame)
        row2.pack(fill=X, pady=2)
        Label(row2, text="请求延迟(秒)：").pack(side=LEFT)
        Entry(row2, textvariable=self.delay_min_var, width=5).pack(side=LEFT, padx=2)
        Label(row2, text=" - ").pack(side=LEFT)
        Entry(row2, textvariable=self.delay_max_var, width=5).pack(side=LEFT, padx=2)
        Label(row2, text="(随机延迟，避免被封)").pack(side=LEFT, padx=10)
        row3 = Frame(config_frame)
        row3.pack(fill=X, pady=2)
        Label(row3, text="代理列表(每行一个，格式: {'http':'url','https':'url'})：").pack(anchor=W)
        self.proxy_entry = Text(row3, height=4, width=80)
        self.proxy_entry.pack(fill=X, pady=2)
        row4 = Frame(config_frame)
        row4.pack(fill=X, pady=2)
        Label(row4, text="保存目录：").pack(side=LEFT)
        Entry(row4, textvariable=self.save_path_var, width=60).pack(side=LEFT, padx=5, expand=True, fill=X)
        Button(row4, text="浏览", command=self.select_save_dir).pack(side=LEFT)
        log_frame = LabelFrame(main_frame, text="下载日志")
        log_frame.pack(fill=BOTH, expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=WORD, height=15)
        self.log_text.pack(fill=BOTH, expand=True)
        self.stop_btn = Button(main_frame, text="停止下载", command=self.stop_download, state=DISABLED, bg="red", fg="white")
        self.stop_btn.pack(pady=5)

    def select_save_dir(self):
        dir_path = filedialog.askdirectory(title="选择保存目录")
        if dir_path:
            self.save_path_var.set(dir_path)

    def start_download(self):
        if self.running:
            messagebox.showwarning("提示", "下载已在运行中")
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("错误", "请输入专辑URL")
            return
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
        self.worker = DownloadWorker(
            album_url=url,
            max_workers=self.max_workers_var.get(),
            request_delay=(self.delay_min_var.get(), self.delay_max_var.get()),
            retry_times=self.retry_var.get(),
            timeout=self.timeout_var.get(),
            proxy_list=proxy_list,
            log_queue=self.log_queue
        )
        self.running = True
        self.stop_btn.config(state=NORMAL)
        self.worker_thread = threading.Thread(target=self.worker.start_download, args=(self.save_path_var.get(),))
        self.worker_thread.daemon = True
        self.worker_thread.start()

    def stop_download(self):
        if self.worker:
            self.worker.stop_flag = True
        self.running = False
        self.stop_btn.config(state=DISABLED)
        self.log("用户请求停止下载...")

    def update_log(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self.log_text.insert(END, msg + "\n")
                self.log_text.see(END)
            except queue.Empty:
                break
        if self.worker_thread and not self.worker_thread.is_alive():
            if self.running:
                self.running = False
                self.stop_btn.config(state=DISABLED)
                self.log("下载线程已结束")
        self.after(100, self.update_log)

    def log(self, msg):
        self.log_queue.put(msg)

if __name__ == "__main__":
    app = DownloaderApp()
    app.mainloop()
