import os
import re
import time
import random
import threading
import queue
import urllib.parse
from tkinter import *
from tkinter import scrolledtext, messagebox, filedialog, ttk
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

class DownloadWorker:
    def __init__(self, album_url, max_workers, request_delay, retry_times, timeout, proxy_list, log_queue, success_queue, fail_queue):
        self.album_url = album_url
        self.max_workers = max_workers
        self.request_delay = request_delay
        self.retry_times = retry_times
        self.timeout = timeout
        self.proxy_list = proxy_list
        self.log_queue = log_queue
        self.success_queue = success_queue
        self.fail_queue = fail_queue
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

    def process_chapter(self, chapter, download_dir):
        chap_num = chapter['num']
        chap_title = chapter['title']
        play_url = chapter['url']
        self.log(f"[{chap_num}] 开始处理：{chap_title}")
        time.sleep(random.uniform(*self.request_delay))
        audio_url = self.get_audio_url(play_url)
        if not audio_url:
            self.log(f"[{chap_num}] 获取音频URL失败，跳过")
            self.fail_queue.put(chapter)
            return False
        safe_title = re.sub(r'[\\/*?:"<>|]', '_', chap_title)
        filename = f"{chap_num:03d}_{safe_title}.m4a"
        filepath = os.path.join(download_dir, filename)
        if os.path.exists(filepath):
            self.log(f"[{chap_num}] 文件已存在，跳过")
            self.success_queue.put((chap_num, chap_title, filepath))
            return True
        success = self.download_audio(audio_url, filepath)
        if success:
            self.log(f"[{chap_num}] 下载完成 -> {filename}")
            self.success_queue.put((chap_num, chap_title, filepath))
        else:
            self.log(f"[{chap_num}] 下载失败")
            self.fail_queue.put(chapter)
        return success

    def start_download(self, download_dir):
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
                    self.fail_queue.put(chap)
        self.log(f"下载完成！成功 {success_count}/{len(chapters)} 章")

    @staticmethod
    def search_books(keyword, proxy_list, timeout, retry_times, request_delay, log_queue):
        search_url = f"https://m.i275.com/search.php?q={urllib.parse.quote(keyword)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        session = requests.Session()
        session.headers.update(headers)
        proxy = random.choice(proxy_list) if proxy_list else None

        for attempt in range(retry_times):
            try:
                resp = session.get(search_url, proxies=proxy, timeout=timeout)
                resp.encoding = 'utf-8'
                if resp.status_code != 200:
                    if log_queue:
                        log_queue.put(f"搜索请求失败，状态码 {resp.status_code}，重试 {attempt+1}/{retry_times}")
                    continue
                soup = BeautifulSoup(resp.text, 'html.parser')
                results = []
                for a in soup.find_all('a', href=re.compile(r'^/book/\d+\.html')):
                    href = a.get('href')
                    full_url = requests.compat.urljoin("https://m.i275.com", href)
                    img = a.find('img')
                    cover = img.get('src') if img else ""
                    if cover and not cover.startswith('http'):
                        cover = requests.compat.urljoin("https://m.i275.com", cover)
                    title_elem = a.find('div', class_='font-medium') or a.find('div', class_='text-sm') or a
                    title = title_elem.get_text(strip=True) if title_elem else a.get_text(strip=True)
                    if not title:
                        continue
                    author = ""
                    broadcaster = ""
                    results.append((title, author, broadcaster, cover, full_url))
                seen = set()
                unique_results = []
                for r in results:
                    if r[4] not in seen:
                        seen.add(r[4])
                        unique_results.append(r)
                return unique_results
            except Exception as e:
                if log_queue:
                    log_queue.put(f"搜索异常: {e}，重试 {attempt+1}/{retry_times}")
                time.sleep(random.uniform(*request_delay) * 2)
        return []

class DownloaderApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("有声小说批量下载工具 (成功/失败列表)")
        self.geometry("1100x800")
        self.resizable(True, True)

        self.search_keyword = StringVar()
        self.url_var = StringVar()
        self.max_workers_var = IntVar(value=5)
        self.delay_min_var = DoubleVar(value=0.5)
        self.delay_max_var = DoubleVar(value=1.5)
        self.retry_var = IntVar(value=3)
        self.timeout_var = IntVar(value=15)
        self.save_path_var = StringVar()

        self.search_results = []
        self.search_listbox = None

        self.success_data = []  # (chap_num, chap_title, filepath)
        self.fail_data = []     # chapter dict

        self.log_queue = queue.Queue()
        self.success_queue = queue.Queue()
        self.fail_queue = queue.Queue()
        self.worker_thread = None
        self.worker = None
        self.running = False

        self.create_widgets()
        self.update_queues()

    def create_widgets(self):
        main_frame = Frame(self, padx=10, pady=10)
        main_frame.pack(fill=BOTH, expand=True)

        search_frame = LabelFrame(main_frame, text="搜索专辑", padx=5, pady=5)
        search_frame.pack(fill=X, pady=5)

        row1 = Frame(search_frame)
        row1.pack(fill=X, pady=2)
        Label(row1, text="关键词：").pack(side=LEFT)
        Entry(row1, textvariable=self.search_keyword, width=40).pack(side=LEFT, padx=5)
        Button(row1, text="搜索", command=self.do_search, bg="blue", fg="white").pack(side=LEFT, padx=5)

        list_frame = Frame(search_frame)
        list_frame.pack(fill=BOTH, expand=True, pady=5)

        scrollbar = Scrollbar(list_frame)
        scrollbar.pack(side=RIGHT, fill=Y)

        self.search_listbox = Listbox(list_frame, yscrollcommand=scrollbar.set, height=5, font=('微软雅黑', 10))
        self.search_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.config(command=self.search_listbox.yview)
        self.search_listbox.bind('<Double-Button-1>', self.on_search_select)

        url_frame = Frame(main_frame)
        url_frame.pack(fill=X, pady=5)
        Label(url_frame, text="专辑URL：").pack(side=LEFT)
        Entry(url_frame, textvariable=self.url_var, width=70).pack(side=LEFT, padx=5, expand=True, fill=X)

        proxy_frame = Frame(main_frame)
        proxy_frame.pack(fill=X, pady=5)
        Label(proxy_frame, text="代理列表(每行一个，格式: {'http':'url','https':'url'})：").pack(anchor=W)
        self.proxy_entry = Text(proxy_frame, height=2, width=80)
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
        Button(ctrl_frame, text="清空列表", command=self.clear_lists, width=8).pack(side=LEFT, padx=5)

        list_panel = Frame(main_frame)
        list_panel.pack(fill=BOTH, expand=True, pady=5)

        success_frame = LabelFrame(list_panel, text="下载成功 (双击打开文件夹)")
        success_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(0,5))

        success_scroll = Scrollbar(success_frame)
        success_scroll.pack(side=RIGHT, fill=Y)

        self.success_listbox = Listbox(success_frame, yscrollcommand=success_scroll.set, height=10, font=('微软雅黑', 9))
        self.success_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        success_scroll.config(command=self.success_listbox.yview)
        self.success_listbox.bind('<Double-Button-1>', self.on_success_double)

        fail_frame = LabelFrame(list_panel, text="下载失败 (双击重试)")
        fail_frame.pack(side=RIGHT, fill=BOTH, expand=True, padx=(5,0))

        fail_scroll = Scrollbar(fail_frame)
        fail_scroll.pack(side=RIGHT, fill=Y)

        self.fail_listbox = Listbox(fail_frame, yscrollcommand=fail_scroll.set, height=10, font=('微软雅黑', 9))
        self.fail_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        fail_scroll.config(command=self.fail_listbox.yview)
        self.fail_listbox.bind('<Double-Button-1>', self.on_fail_double)

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

    def do_search(self):
        keyword = self.search_keyword.get().strip()
        if not keyword:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        self.search_listbox.delete(0, END)
        self.search_results.clear()
        self.log(f"正在搜索: {keyword} ...")
        proxy_list = self.parse_proxy()
        threading.Thread(target=self._search_thread, args=(keyword, proxy_list), daemon=True).start()

    def _search_thread(self, keyword, proxy_list):
        results = DownloadWorker.search_books(
            keyword=keyword,
            proxy_list=proxy_list,
            timeout=self.timeout_var.get(),
            retry_times=self.retry_var.get(),
            request_delay=(self.delay_min_var.get(), self.delay_max_var.get()),
            log_queue=self.log_queue
        )
        self.after(0, self._display_results, results)

    def _display_results(self, results):
        if not results:
            self.log("未搜索到结果")
            self.search_listbox.insert(END, "无结果")
            return
        self.search_results = results
        for idx, (title, author, broadcaster, cover, url) in enumerate(results, 1):
            display_text = f"{idx}. {title[:50]}"
            if author:
                display_text += f" - {author}"
            if broadcaster:
                display_text += f" ({broadcaster})"
            self.search_listbox.insert(END, display_text)
        self.log(f"搜索到 {len(results)} 条结果，双击选择")

    def on_search_select(self, event):
        selection = self.search_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx >= len(self.search_results):
            return
        title, author, broadcaster, cover, url = self.search_results[idx]
        self.url_var.set(url)
        self.log(f"已选择: {title}")

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
        self.worker = DownloadWorker(
            album_url=url,
            max_workers=self.max_workers_var.get(),
            request_delay=(self.delay_min_var.get(), self.delay_max_var.get()),
            retry_times=self.retry_var.get(),
            timeout=self.timeout_var.get(),
            proxy_list=proxy_list,
            log_queue=self.log_queue,
            success_queue=self.success_queue,
            fail_queue=self.fail_queue
        )
        self.running = True
        self.start_btn.config(state=DISABLED)
        self.stop_btn.config(state=NORMAL)
        self.worker_thread = threading.Thread(target=self.worker.start_download, args=(save_dir,))
        self.worker_thread.daemon = True
        self.worker_thread.start()

    def stop_download(self):
        if self.worker:
            self.worker.stop_flag = True
        self.running = False
        self.start_btn.config(state=NORMAL)
        self.stop_btn.config(state=DISABLED)
        self.log("用户请求停止...")

    def clear_lists(self):
        self.success_data.clear()
        self.fail_data.clear()
        self.success_listbox.delete(0, END)
        self.fail_listbox.delete(0, END)

    def on_success_double(self, event):
        selection = self.success_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx >= len(self.success_data):
            return
        chap_num, chap_title, filepath = self.success_data[idx]
        dir_path = os.path.dirname(filepath)
        os.startfile(dir_path)

    def on_fail_double(self, event):
        selection = self.fail_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx >= len(self.fail_data):
            return
        chapter = self.fail_data[idx]
        if not self.worker or not self.running:
            messagebox.showwarning("提示", "请先开始下载任务")
            return
        self.log(f"手动重试章节 {chapter['num']}: {chapter['title']}")
        threading.Thread(target=self._retry_chapter, args=(chapter,), daemon=True).start()

    def _retry_chapter(self, chapter):
        save_dir = self.save_path_var.get().strip()
        if not save_dir:
            save_dir = os.getcwd()
        chap_num = chapter['num']
        chap_title = chapter['title']
        play_url = chapter['url']
        self.worker.log(f"[{chap_num}] 手动重试：{chap_title}")
        time.sleep(random.uniform(*self.worker.request_delay))
        audio_url = self.worker.get_audio_url(play_url)
        if not audio_url:
            self.worker.log(f"[{chap_num}] 重试获取音频URL失败")
            return
        safe_title = re.sub(r'[\\/*?:"<>|]', '_', chap_title)
        filename = f"{chap_num:03d}_{safe_title}.m4a"
        filepath = os.path.join(save_dir, filename)
        if os.path.exists(filepath):
            # 可选择覆盖，这里直接覆盖
            os.remove(filepath)
        success = self.worker.download_audio(audio_url, filepath)
        if success:
            self.worker.log(f"[{chap_num}] 重试下载完成 -> {filename}")
            self.success_queue.put((chap_num, chap_title, filepath))
            self.after(0, self._remove_from_fail, chapter)
        else:
            self.worker.log(f"[{chap_num}] 重试下载失败")

    def _remove_from_fail(self, chapter):
        if chapter in self.fail_data:
            idx = self.fail_data.index(chapter)
            self.fail_data.pop(idx)
            self.fail_listbox.delete(idx)

    def update_queues(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self.log_text.insert(END, msg + "\n")
                self.log_text.see(END)
            except queue.Empty:
                break
        while True:
            try:
                success = self.success_queue.get_nowait()
                chap_num, chap_title, filepath = success
                display = f"{chap_num:03d} {chap_title[:30]}"
                self.success_data.append((chap_num, chap_title, filepath))
                self.success_listbox.insert(END, display)
            except queue.Empty:
                break
        while True:
            try:
                fail_chap = self.fail_queue.get_nowait()
                display = f"{fail_chap['num']:03d} {fail_chap['title'][:30]}"
                self.fail_data.append(fail_chap)
                self.fail_listbox.insert(END, display)
            except queue.Empty:
                break
        if self.worker_thread and not self.worker_thread.is_alive():
            if self.running:
                self.running = False
                self.start_btn.config(state=NORMAL)
                self.stop_btn.config(state=DISABLED)
                self.log("下载线程已结束")
        self.after(100, self.update_queues)

    def log(self, msg):
        self.log_queue.put(msg)

if __name__ == "__main__":
    app = DownloaderApp()
    app.mainloop()
