import os
import re
import time
import random
import threading
import queue
from tkinter import *
from tkinter import scrolledtext, messagebox, filedialog, ttk
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

    def process_chapter(self, chapter, download_dir, success_callback, fail_callback):
        chap_num = chapter['num']
        chap_title = chapter['title']
        play_url = chapter['url']
        self.log(f"[{chap_num}] 开始处理：{chap_title}")
        time.sleep(random.uniform(*self.request_delay))
        audio_url = None
        fails = 0
        while fails < 3:
            if self.stop_flag:
                return False
            audio_url = self.get_audio_url(play_url)
            if audio_url:
                break
            fails += 1
            self.log(f"[{chap_num}] 获取音频URL失败（第{fails}次）")
            time.sleep(random.uniform(*self.request_delay) * 2)
        if not audio_url:
            self.log(f"[{chap_num}] 连续3次失败，等待手动处理")
            fail_callback(chapter)
            return False
        safe_title = re.sub(r'[\\/*?:"<>|]', '_', chap_title)
        filename = f"{chap_num:03d}_{safe_title}.m4a"
        filepath = os.path.join(download_dir, filename)
        if os.path.exists(filepath):
            self.log(f"[{chap_num}] 文件已存在，跳过")
            success_callback(chapter)
            return True
        success = self.download_audio(audio_url, filepath)
        if success:
            self.log(f"[{chap_num}] 下载完成 -> {filename}")
            success_callback(chapter)
        else:
            self.log(f"[{chap_num}] 下载失败")
            fail_callback(chapter)
        return success

    def start_download(self, download_dir, success_callback, fail_callback):
        self.log("开始获取专辑信息...")
        self.fetch_url("https://m.i275.com/", referer="https://m.i275.com/")
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
                executor.submit(self.process_chapter, chap, download_dir, success_callback, fail_callback): chap
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
        self.geometry("1200x800")
        self.resizable(True, True)
        self.url_var = StringVar()
        self.max_workers_var = IntVar(value=5)
        self.delay_min_var = DoubleVar(value=0.5)
        self.delay_max_var = DoubleVar(value=1.5)
        self.retry_var = IntVar(value=3)
        self.timeout_var = IntVar(value=15)
        self.save_path_var = StringVar()
        self.search_keyword_var = StringVar()
        self.search_results = []
        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.worker = None
        self.running = False
        self.failed_chapters = []
        self.success_chapters = []
        self.create_widgets()
        self.update_log()

    def create_widgets(self):
        main_frame = Frame(self, padx=10, pady=10)
        main_frame.pack(fill=BOTH, expand=True)

        search_frame = LabelFrame(main_frame, text="搜索专辑", padx=5, pady=5)
        search_frame.pack(fill=X, pady=5)
        search_row = Frame(search_frame)
        search_row.pack(fill=X, pady=2)
        Label(search_row, text="关键词：").pack(side=LEFT)
        Entry(search_row, textvariable=self.search_keyword_var, width=40).pack(side=LEFT, padx=5)
        Button(search_row, text="搜索", command=self.search_album).pack(side=LEFT, padx=5)
        self.result_listbox = Listbox(search_frame, height=4)
        self.result_listbox.pack(fill=X, pady=5)
        self.result_listbox.bind('<Double-Button-1>', self.on_result_select)
        Button(search_frame, text="使用选中的专辑", command=self.use_selected_album).pack()

        url_frame = Frame(main_frame)
        url_frame.pack(fill=X, pady=5)
        Label(url_frame, text="专辑URL：").pack(side=LEFT)
        Entry(url_frame, textvariable=self.url_var, width=70).pack(side=LEFT, padx=5, expand=True, fill=X)

        proxy_frame = Frame(main_frame)
        proxy_frame.pack(fill=X, pady=5)
        Label(proxy_frame, text="代理列表(每行一个，格式: {'http':'url','https':'url'})：").pack(anchor=W)
        self.proxy_entry = Text(proxy_frame, height=3, width=80)
        self.proxy_entry.pack(fill=X, pady=2)

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

        ctrl_frame = Frame(main_frame)
        ctrl_frame.pack(fill=X, pady=5)
        self.start_btn = Button(ctrl_frame, text="开始下载", command=self.start_download, bg="green", fg="white", width=15)
        self.start_btn.pack(side=LEFT, padx=2)
        self.stop_btn = Button(ctrl_frame, text="停止", command=self.stop_download, state=DISABLED, bg="red", fg="white", width=8)
        self.stop_btn.pack(side=LEFT, padx=5)

        preview_frame = LabelFrame(main_frame, text="下载预览")
        preview_frame.pack(fill=BOTH, expand=True, pady=5)

        left_frame = Frame(preview_frame)
        left_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=5)
        Label(left_frame, text="成功章节", font=('Arial', 10, 'bold')).pack(anchor=W)
        self.success_listbox = Listbox(left_frame, height=10)
        self.success_listbox.pack(fill=BOTH, expand=True)

        right_frame = Frame(preview_frame)
        right_frame.pack(side=RIGHT, fill=BOTH, expand=True, padx=5)
        Label(right_frame, text="失败章节（连续3次）", font=('Arial', 10, 'bold')).pack(anchor=W)
        self.fail_listbox = Listbox(right_frame, height=10)
        self.fail_listbox.pack(fill=BOTH, expand=True)

        log_frame = LabelFrame(main_frame, text="下载日志")
        log_frame.pack(fill=BOTH, expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=WORD, height=8)
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

    def search_album(self):
        keyword = self.search_keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("提示", "请输入关键词")
            return
        search_url = f"https://m.i275.com/search.php?q={requests.utils.quote(keyword)}"
        try:
            resp = requests.get(search_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
            hot_containers = []
            for elem in soup.find_all(['div', 'section']):
                if '热门搜索' in elem.get_text():
                    hot_containers.append(elem)
            items = []
            for a in soup.find_all('a', href=re.compile(r'^/book/\d+\.html')):
                href = a.get('href')
                full_url = requests.compat.urljoin("https://m.i275.com", href)
                title = a.get_text(strip=True)
                in_hot = False
                for hot in hot_containers:
                    if a in hot.descendants:
                        in_hot = True
                        break
                if in_hot:
                    continue
                parent = a.find_parent(['div', 'li'], class_=re.compile(r'item|result|book'))
                if parent:
                    extra = parent.get_text(' ', strip=True)
                    extra = extra.replace(title, '', 1).strip()
                    if extra:
                        display = f"{title} - {extra}"
                    else:
                        display = title
                else:
                    display = title
                items.append((full_url, display))
            if not items:
                self.log("未搜索到结果")
                return
            self.search_results = items
            self.result_listbox.delete(0, END)
            for url, display in items:
                self.result_listbox.insert(END, display[:60])
        except Exception as e:
            messagebox.showerror("搜索失败", str(e))

    def on_result_select(self, event):
        self.use_selected_album()

    def use_selected_album(self):
        sel = self.result_listbox.curselection()
        if not sel:
            return
        index = sel[0]
        url, desc = self.search_results[index]
        self.url_var.set(url)

    def success_callback(self, chapter):
        self.success_chapters.append(chapter)
        self.after(0, self.update_success_listbox)

    def fail_callback(self, chapter):
        self.failed_chapters.append(chapter)
        self.after(0, self.update_fail_listbox)
        if len(self.failed_chapters) == 1:
            self.after(500, self.prompt_retry)

    def update_success_listbox(self):
        self.success_listbox.delete(0, END)
        for chap in self.success_chapters[-50:]:
            self.success_listbox.insert(END, f"{chap['num']}: {chap['title'][:40]}")

    def update_fail_listbox(self):
        self.fail_listbox.delete(0, END)
        for chap in self.failed_chapters:
            self.fail_listbox.insert(END, f"{chap['num']}: {chap['title'][:40]}")

    def prompt_retry(self):
        if not self.failed_chapters:
            return
        msg = f"有 {len(self.failed_chapters)} 个章节连续3次获取失败。\n是否重试所有失败章节？"
        ret = messagebox.askyesno("手动重试", msg)
        if ret:
            self.log("用户选择重试失败章节")
            to_retry = self.failed_chapters.copy()
            self.failed_chapters.clear()
            self.update_fail_listbox()
            for chap in to_retry:
                self.worker.process_chapter(chap, self.save_path_var.get().strip() or os.getcwd(), self.success_callback, self.fail_callback)
        else:
            self.log("用户跳过失败章节")

    def start_download(self):
        if self.running:
            messagebox.showwarning("提示", "已有任务在运行")
            return
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("错误", "请输入专辑URL")
            return
        save_dir = self.save_path_var.get().strip()
        proxy_list = self.parse_proxy()
        self.success_chapters.clear()
        self.failed_chapters.clear()
        self.update_success_listbox()
        self.update_fail_listbox()
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
        self.start_btn.config(state=DISABLED)
        self.stop_btn.config(state=NORMAL)
        self.worker_thread = threading.Thread(target=self.worker.start_download, args=(save_dir, self.success_callback, self.fail_callback))
        self.worker_thread.daemon = True
        self.worker_thread.start()

    def stop_download(self):
        if self.worker:
            self.worker.stop_flag = True
        self.running = False
        self.start_btn.config(state=NORMAL)
        self.stop_btn.config(state=DISABLED)
        self.log("用户请求停止...")

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
                self.start_btn.config(state=NORMAL)
                self.stop_btn.config(state=DISABLED)
                self.log("下载线程已结束")
        self.after(100, self.update_log)

    def log(self, msg):
        self.log_queue.put(msg)

if __name__ == "__main__":
    app = DownloaderApp()
    app.mainloop()
