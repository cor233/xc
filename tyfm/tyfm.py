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
        self.curl_session = curl_requests.Session()
        self.completed_file = os.path.join(save_dir, '.completed.json')
        self.failed_file = os.path.join(save_dir, '.failed.json')
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
        with open(self.completed_file, 'w', encoding='utf-8') as f:
            json.dump(list(self.completed_set), f, ensure_ascii=False)

    def load_failed(self):
        if os.path.exists(self.failed_file):
            with open(self.failed_file, 'r', encoding='utf-8') as f:
                self.failed_set = set(json.load(f))
        else:
            self.failed_set = set()

    def save_failed(self):
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
                resp = self.curl_session.get(url, headers=headers, timeout=timeout, impersonate="chrome120")
                resp.encoding = 'utf-8'
                if resp.status_code == 200:
                    if "请求太频繁" in resp.text or "稍后再试" in resp.text:
                        wait_time = random.randint(30, 60)
                        self.log(f"检测到访问频率限制，等待{wait_time}秒后重试...")
                        time.sleep(wait_time)
                        continue
                    return resp
                else:
                    self.log(f"请求失败，状态码 {resp.status_code}，重试 {attempt+1}/{retries}")
            except Exception as e:
                self.log(f"请求异常: {e}，重试 {attempt+1}/{retries}")
            time.sleep(random.uniform(*self.request_delay) * 2)
        return None

    def get_album_info(self):
        """从播放页获取专辑标题和章节列表"""
        resp = self.fetch_url(self.album_url)
        if not resp:
            return None, []
        soup = BeautifulSoup(resp.text, 'html.parser')
        # 提取专辑名
        title_selectors = ['.album-name', '.album-title', '.player-album', 'h1', '[class*="album"]']
        album_title = None
        for sel in title_selectors:
            elem = soup.select_one(sel)
            if elem and elem.get_text(strip=True):
                album_title = elem.get_text(strip=True)
                break
        if not album_title:
            title_tag = soup.find('title')
            album_title = title_tag.get_text(strip=True) if title_tag else "未知专辑"
        album_title = re.sub(r'[\\/*?:"<>|]', '_', album_title).strip()
        # 获取章节列表：需要先模拟点击章节列表按钮，但静态页面可能已包含数据
        # 听友FM播放页通常有一个隐藏的章节数据，在Nuxt中可能通过__NUXT__获取
        chapters = []
        # 尝试从 __NUXT__ 中提取
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and '__NUXT__' in script.string:
                # 提取 window.__NUXT__ 的 JSON 数据
                m = re.search(r'window\.__NUXT__\s*=\s*({.*?});', script.string, re.DOTALL)
                if m:
                    try:
                        data = json.loads(m.group(1))
                        # 根据实际结构解析章节
                        # 常见路径：state.playing.album.chapters 或 state.player.playlist
                        state = data.get('state', {})
                        playing = state.get('playing', {})
                        album = playing.get('album', {})
                        chapter_list = album.get('chapters', [])
                        if not chapter_list:
                            # 尝试从 player 获取
                            player = state.get('player', {})
                            chapter_list = player.get('playlist', [])
                        for idx, chap in enumerate(chapter_list):
                            title = chap.get('title', f'第{idx+1}章')
                            audio_url = chap.get('audio_url') or chap.get('url') or chap.get('src')
                            chap_id = chap.get('id') or chap.get('chapter_id') or idx
                            chapters.append({
                                'num': idx + 1,
                                'title': title,
                                'audio_url': audio_url,
                                'id': chap_id
                            })
                        break
                    except:
                        pass
        # 如果静态提取失败，尝试通过DOM获取章节列表按钮并模拟点击（更复杂，需要selenium，这里略过）
        if not chapters:
            # 备用：通过页面DOM获取（部分页面可能用van-popup方式）
            # 简单起见，提示用户手动获取章节ID范围
            self.log("无法自动解析章节列表，请使用油猴脚本先获取章节URL列表")
            return album_title, []
        return album_title, chapters

    def process_chapter(self, chapter, download_dir):
        chap_num = chapter['num']
        chap_title = chapter['title']
        chap_id = chapter.get('id', chap_num)
        audio_url = chapter.get('audio_url')
        # 检查是否已完成
        if str(chap_id) in self.completed_set:
            self.completed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 已完成，跳过")
            return
        self.log(f"[{chap_num}] 开始处理：{chap_title}")
        # 如果没有音频URL，需要动态获取（听友FM通常需要请求API）
        if not audio_url:
            # 尝试从API获取
            audio_url = self.get_audio_url_from_api(chap_id)
            if not audio_url:
                self.failed += 1
                self.failed_set.add(str(chap_id))
                self.save_failed()
                self.stats_log()
                self.log(f"[{chap_num}] 获取音频URL失败，跳过")
                self.fail_log(chap_num, chap_title)
                return
            time.sleep(random.uniform(*self.request_delay))
        # 清理文件名
        safe_title = self.sanitize_filename(chap_title)
        filename = f"{chap_num:03d}_{safe_title}.mp3"
        filepath = os.path.join(download_dir, filename)
        success = self.download_audio(audio_url, filepath)
        if success:
            self.completed_set.add(str(chap_id))
            self.save_completed()
            if str(chap_id) in self.failed_set:
                self.failed_set.remove(str(chap_id))
                self.save_failed()
            self.completed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 下载完成 -> {filename}")
        else:
            self.failed += 1
            self.failed_set.add(str(chap_id))
            self.save_failed()
            self.stats_log()
            self.log(f"[{chap_num}] 下载失败")
            self.fail_log(chap_num, chap_title)

    def get_audio_url_from_api(self, chapter_id):
        """尝试通过听友FM的API获取音频直链"""
        # 听友FM的可能API接口（需根据实际抓包调整）
        api_url = f"https://tingyou.fm/api/audio/url?id={chapter_id}"
        resp = self.fetch_url(api_url, referer=self.album_url)
        if resp:
            try:
                data = resp.json()
                url = data.get('url') or data.get('data', {}).get('url')
                return url
            except:
                pass
        return None

    def download_audio(self, audio_url, save_path):
        try:
            headers = {'User-Agent': random.choice(USER_AGENTS), 'Referer': self.album_url}
            with self.curl_session.get(audio_url, headers=headers, stream=True, timeout=self.timeout, impersonate="chrome120") as r:
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

    def start_download(self, download_dir):
        self.clear_fail_log()
        self.completed = 0
        self.failed = 0
        self.load_completed()
        self.load_failed()
        self.log("开始获取专辑信息...")
        # 预热会话
        self.fetch_url("https://tingyou.fm/", referer="https://tingyou.fm/")
        album_title, chapters = self.get_album_info()
        if not chapters:
            self.log("未获取到章节列表，请确认URL正确且可访问。")
            return
        self.log(f"专辑标题：{album_title}")
        os.makedirs(download_dir, exist_ok=True)
        self.log(f"保存目录：{download_dir}")
        self.total_chapters = len(chapters)
        if self.failed_set:
            # 只处理失败的章节
            chapters = [c for c in chapters if str(c.get('id', c['num'])) in self.failed_set]
            self.log(f"发现失败记录，本次将重试 {len(chapters)} 个章节")
        else:
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
                    self.failed_set.add(str(chap.get('id', chap['num'])))
                    self.save_failed()
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
        self.title("听友FM批量下载器")
        self.geometry("950x800")
        self.resizable(True, True)
        self.search_var = StringVar()
        self.max_workers_var = IntVar(value=3)
        self.delay_min_var = DoubleVar(value=1.0)
        self.delay_max_var = DoubleVar(value=3.0)
        self.retry_var = IntVar(value=3)
        self.timeout_var = IntVar(value=15)
        self.save_path_var = StringVar()
        self.selected_album_url = None
        self.selected_album_title = None
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

        # 搜索区
        search_frame = Frame(main_frame)
        search_frame.pack(fill=X, pady=5)
        Label(search_frame, text="关键词：").pack(side=LEFT)
        Entry(search_frame, textvariable=self.search_var, width=50).pack(side=LEFT, padx=5, expand=True, fill=X)
        Button(search_frame, text="搜索", command=self.search, bg="blue", fg="white", width=10).pack(side=LEFT, padx=5)

        # 结果列表
        result_frame = LabelFrame(main_frame, text="搜索结果")
        result_frame.pack(fill=BOTH, expand=True, pady=5)
        columns = ('专辑名', '主播')
        self.tree = ttk.Treeview(result_frame, columns=columns, show='tree headings', height=6)
        self.tree.heading('#0', text='序号')
        self.tree.heading('专辑名', text='专辑名')
        self.tree.heading('主播', text='主播')
        self.tree.column('#0', width=50)
        self.tree.column('专辑名', width=400)
        self.tree.column('主播', width=200)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar = ttk.Scrollbar(result_frame, orient=VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)

        # 配置区
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

        # 保存目录
        dir_frame = Frame(main_frame)
        dir_frame.pack(fill=X, pady=5)
        Label(dir_frame, text="保存目录：").pack(side=LEFT)
        Entry(dir_frame, textvariable=self.save_path_var, width=60).pack(side=LEFT, padx=5, expand=True, fill=X)
        Button(dir_frame, text="浏览", command=self.select_save_dir).pack(side=LEFT)

        # 统计
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

        # 日志区
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
        # 听友FM搜索API
        search_url = f"https://tingyou.fm/search/result?keyword={urllib.parse.quote(keyword)}&page=1"
        try:
            headers = {'User-Agent': random.choice(USER_AGENTS)}
            resp = curl_requests.get(search_url, headers=headers, timeout=15, impersonate="chrome120")
            resp.encoding = 'utf-8'
            if resp.status_code != 200:
                self.log(f"搜索请求失败，状态码 {resp.status_code}")
                return
            # 尝试从 __NUXT__ 提取数据
            soup = BeautifulSoup(resp.text, 'html.parser')
            script_tags = soup.find_all('script')
            albums = []
            for script in script_tags:
                if script.string and '__NUXT__' in script.string:
                    m = re.search(r'window\.__NUXT__\s*=\s*({.*?});', script.string, re.DOTALL)
                    if m:
                        try:
                            data = json.loads(m.group(1))
                            # 搜索结果的路径可能需要调试，大致为 state.search.result.items
                            result = data.get('state', {}).get('search', {}).get('result', {})
                            items = result.get('items', [])
                            for item in items:
                                title = item.get('title', '未知专辑')
                                album_id = item.get('id')
                                anchor = item.get('anchor', {}).get('name', '')
                                album_url = f"https://tingyou.fm/album/{album_id}"
                                albums.append((title, anchor, album_url))
                            break
                        except:
                            pass
            if not albums:
                self.log("未找到相关专辑，请检查搜索关键词或稍后重试。")
                return
            for idx, (title, anchor, url) in enumerate(albums, 1):
                self.tree.insert('', 'end', text=str(idx), values=(title, anchor), tags=(url, title))
            self.log(f"搜索完成，共找到 {len(albums)} 张专辑")
        except Exception as e:
            self.log(f"搜索异常: {e}")

    def on_select(self, event):
        selected = self.tree.selection()
        if selected:
            item = selected[0]
            values = self.tree.item(item, 'values')
            self.selected_album_url = self.tree.item(item, 'tags')[0]
            self.selected_album_title = values[0]
            self.log(f"已选中专辑：{self.selected_album_title}")

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
        if not self.selected_album_url:
            messagebox.showerror("错误", "请先在搜索结果中选择一张专辑")
            return
        save_dir = self.save_path_var.get().strip()
        if not save_dir:
            save_dir = os.getcwd()
            self.save_path_var.set(save_dir)
        safe_folder = self.sanitize_filename(self.selected_album_title)
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
                album_url=self.selected_album_url,
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
