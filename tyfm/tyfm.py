#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import threading
import time
import json
import requests
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from concurrent.futures import ThreadPoolExecutor, as_completed
from Crypto.Cipher import ChaCha20_Poly1305, AES
from Crypto.Random import get_random_bytes

BASE_URL = "https://azybk.tingyou8.vip"
KEY = bytes.fromhex('ea9d9d4f9a983fe6f6382f29c7b46b8d6dc47abc6da36662e6ddff8c78902f65')

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 9; RMX1931 Build/PQ3A.190605.05081124; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Mobile Safari/537.36 QSTAPP/1.6.9 Html5Plus/1.0",
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

AUTH_HEADERS = {
    "User-Agent": "zybk/1.0.6",
    "Accept-Encoding": "gzip",
    "authorization": "Bearer gAAAAABpsEFsPBrHfY1bGsn15TbqqXUC4WSFwm8VU97NW6qmSAewn1rbYMzbIXLajyJnSZ94oZsS6hil8Qkb-IknSuggyn9XLDitEE930CD9OapDQOzq1xSJb7foWNh5YdeT_7p4ZSyYyhW4b2ZWmI-Itb8YBTjDmWIM3FiTQ9MYATmWKJQ6d6IY5Z0bupvW6hjWoHppAa5v0_k2KkIEHdzyw7AiKTPPqUYVmKvrvISlBkMHGdyK83AgiOi80-mKwmCIY1kXuj_vg_mY1HxvmsPpNcYLaoOYUA=="
}

AUTH_DATA = '02956c849999374a66745e3ac9957f00ad9022036ec6b1a20c7f86327c8817'

CATEGORIES = [
    (46, "玄幻奇幻"), (11, "武侠小说"), (19, "言情通俗"), (21, "相声小品"),
    (14, "恐怖惊悚"), (17, "官场商战"), (15, "历史军事"), (9, "百家讲坛"),
    (16, "刑侦反腐"), (10, "有声文学"), (18, "人物纪实"), (36, "广播剧"),
    (22, "英文读物"), (23, "轻音清心"), (31, "二人转"), (33, "健康养生"),
    (34, "综艺娱乐"), (40, "头条"), (38, "戏曲"), (41, "脱口秀"),
    (42, "商业财经"), (43, "亲子教育"), (44, "教育培训"), (45, "时尚生活"),
    (20, "童话寓言"), (47, "未分类"), (1, "单田芳"), (2, "刘兰芳"),
    (3, "田连元"), (4, "袁阔成"), (5, "连丽如"), (8, "孙一"),
    (30, "王子封臣"), (25, "马长辉"), (26, "昊儒书场"), (27, "王军"),
    (28, "王玥波"), (29, "石连君"), (12, "粤语评书"), (35, "关永超"),
    (6, "张少佐"), (7, "田战义"), (13, "其他评书")
]

class TingYouCrypto:
    @staticmethod
    def decrypt_payload(payload_hex: str) -> bytes:
        data = bytes.fromhex(payload_hex)
        if len(data) < 41:
            raise ValueError("密文长度不足")
        flag = data[0]
        nonce = data[1:25]
        ciphertext = data[25:]
        if flag == 2:
            ciphertext = bytes(reversed(ciphertext))
        cipher = ChaCha20_Poly1305.new(key=KEY, nonce=nonce)
        return cipher.decrypt(ciphertext)

    @staticmethod
    def encrypt_request(data: bytes, version: int = 1) -> str:
        iv = get_random_bytes(12)
        cipher = AES.new(KEY, AES.MODE_GCM, nonce=iv)
        ciphertext, tag = cipher.encrypt_and_digest(data)
        encrypted = ciphertext + tag
        if version == 2:
            encrypted = bytes(reversed(encrypted))
        result = bytes([2 if version == 2 else 1]) + iv + encrypted
        return result.hex()

class TingYouAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._authed = False

    def auth(self, log_callback=None):
        try:
            resp = self.session.post(
                f"{BASE_URL}/apk/auth/me",
                headers=AUTH_HEADERS,
                data=AUTH_DATA,
                timeout=15
            )
            resp.raise_for_status()
            self._authed = True
            if log_callback:
                log_callback("认证成功，已获取 session")
            return True
        except Exception as e:
            if log_callback:
                log_callback(f"认证失败: {e}")
            return False

    def search(self, keyword: str, page: int = 1, log_callback=None):
        if not self._authed:
            self.auth(log_callback)
        try:
            payload = json.dumps({
                "keyword": keyword,
                "page": page,
                "sort_by": "updated_at",
                "sort_order": "desc"
            }, ensure_ascii=False).encode('utf-8')
            encrypted = TingYouCrypto.encrypt_request(payload)

            resp = self.session.post(
                f"{BASE_URL}/apk/search",
                headers={**DEFAULT_HEADERS, **AUTH_HEADERS},
                data=encrypted,
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            plaintext = TingYouCrypto.decrypt_payload(data['payload'])
            result = json.loads(plaintext)
            return result.get('results', [])
        except Exception as e:
            if log_callback:
                log_callback(f"搜索失败: {e}")
            return []

    def get_category(self, cat_id: int, page: int = 1, log_callback=None):
        if not self._authed:
            self.auth(log_callback)
        try:
            resp = self.session.get(
                f"{BASE_URL}/api/category_page/types/{cat_id}/popular/{page}",
                headers=DEFAULT_HEADERS,
                timeout=15
            )
            resp.raise_for_status()
            text = resp.text.strip()
            if not text:
                return []
            try:
                data = json.loads(text)
                payload = data.get('payload')
            except json.JSONDecodeError:
                payload = text
            if not payload:
                return []
            plaintext = TingYouCrypto.decrypt_payload(payload)
            result = json.loads(plaintext)
            return result.get('data', [])
        except Exception as e:
            if log_callback:
                log_callback(f"获取分类失败: {e}")
            return []

    def get_detail(self, album_id: int, log_callback=None):
        if not self._authed:
            self.auth(log_callback)
        try:
            resp = self.session.get(
                f"{BASE_URL}/api/chapters_list/{album_id}",
                headers=DEFAULT_HEADERS,
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            chapters_plain = TingYouCrypto.decrypt_payload(data['payload'])
            chapters_data = json.loads(chapters_plain)

            resp2 = self.session.get(
                f"{BASE_URL}/api/album_detail/{album_id}",
                headers=DEFAULT_HEADERS,
                timeout=15
            )
            resp2.raise_for_status()
            data2 = resp2.json()
            detail_plain = TingYouCrypto.decrypt_payload(data2['payload'])
            detail_data = json.loads(detail_plain)

            return detail_data, chapters_data.get('chapters', [])
        except Exception as e:
            if log_callback:
                log_callback(f"获取详情失败: {e}")
            return None, []

    def get_play_url(self, album_id: int, chapter_idx: int, log_callback=None):
        if not self._authed:
            self.auth(log_callback)
        try:
            payload = json.dumps({
                "album_id": album_id,
                "chapter_idx": chapter_idx
            }, ensure_ascii=False).encode('utf-8')
            encrypted = TingYouCrypto.encrypt_request(payload)

            resp = self.session.post(
                f"{BASE_URL}/apk/play/play_token",
                headers={**DEFAULT_HEADERS, **AUTH_HEADERS},
                data=encrypted,
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            plaintext = TingYouCrypto.decrypt_payload(data['payload'])
            result = json.loads(plaintext)
            return result.get('play_url')
        except Exception as e:
            if log_callback:
                log_callback(f"获取播放链接失败: {e}")
            return None

def sanitize_name(name):
    mapping = {'\\': '_', '/': '_', ':': '_', '*': '_',
               '?': '_', '"': '_', '<': '_', '>': '_', '|': '_'}
    for old, new in mapping.items():
        name = name.replace(old, new)
    return name.strip()

def download_audio(url, save_path, referer=f"{BASE_URL}/", retry=3, log_callback=None, stop_event=None):
    headers = {
        "User-Agent": DEFAULT_HEADERS["User-Agent"],
        "Referer": referer,
    }
    for attempt in range(retry):
        if stop_event and stop_event.is_set():
            return False
        temp_path = save_path + ".tmp"
        try:
            resp = requests.get(url, headers=headers, stream=True, timeout=60)
            resp.raise_for_status()
            with open(temp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        if stop_event and stop_event.is_set():
                            return False
                        f.write(chunk)
            if os.path.getsize(temp_path) == 0:
                raise ValueError("文件大小为0")
            if os.path.exists(save_path):
                os.remove(save_path)
            os.rename(temp_path, save_path)
            return True
        except Exception as e:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
            if attempt == retry - 1:
                if log_callback:
                    log_callback(f"下载失败: {e}")
                return False
            time.sleep(2)
    return False

class TingYouDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title("TingYou 听友听书下载工具")
        self.root.geometry("1100x800")
        self.root.minsize(900, 650)

        self.api = TingYouAPI()
        self.search_results = []
        self.current_book = None
        self.current_chapters = []
        self.download_folder = tk.StringVar(value=os.path.join(os.getcwd(), "TingYouDownloads"))

        self.paused_event = threading.Event()
        self.stop_event = threading.Event()
        self.downloading = False
        self.downloading_lock = threading.Lock()
        self.download_thread = None
        self.close_waiting = False
        self.destroyed = False

        self.max_workers = 2
        self.record_lock = threading.RLock()
        self.record_cache = {}
        self.json_path = None

        self.create_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def create_widgets(self):
        top_frame = ttk.Frame(self.root)
        ttk.Label(top_frame, text="关键词:").pack(side=tk.LEFT, padx=5)
        self.search_entry = ttk.Entry(top_frame, width=35)
        self.search_entry.pack(side=tk.LEFT, padx=5)
        self.search_entry.bind("<Return>", lambda e: self.start_search())
        ttk.Button(top_frame, text="搜索", command=self.start_search).pack(side=tk.LEFT, padx=5)

        ttk.Label(top_frame, text="分类:").pack(side=tk.LEFT, padx=(20, 5))
        self.cat_combo = ttk.Combobox(top_frame, values=[f"{c[1]}" for c in CATEGORIES], width=12, state="readonly")
        self.cat_combo.current(0)
        self.cat_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="加载分类", command=self.load_category).pack(side=tk.LEFT, padx=5)

        ttk.Label(top_frame, text="保存目录:").pack(side=tk.LEFT, padx=(20, 5))
        ttk.Entry(top_frame, textvariable=self.download_folder, width=25).pack(side=tk.LEFT, padx=5)
        ttk.Button(top_frame, text="浏览", command=self.select_folder).pack(side=tk.LEFT, padx=5)

        top_frame.pack(fill=tk.X, pady=10, padx=10)

        ctrl_top = ttk.Frame(self.root)
        ttk.Label(ctrl_top, text="并发数:").pack(side=tk.LEFT, padx=5)
        self.workers_spin = tk.Spinbox(ctrl_top, from_=1, to=5, width=5)
        self.workers_spin.delete(0, tk.END)
        self.workers_spin.insert(0, str(self.max_workers))
        self.workers_spin.pack(side=tk.LEFT, padx=5)

        self.auth_btn = ttk.Button(ctrl_top, text="重新认证", command=self.do_auth)
        self.auth_btn.pack(side=tk.LEFT, padx=20)
        ctrl_top.pack(fill=tk.X, padx=10)

        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left_frame = ttk.LabelFrame(main_pane, text="搜索结果")
        self.result_tree = ttk.Treeview(left_frame, columns=("title", "narrator"), show="tree headings", height=20)
        self.result_tree.heading("#0", text="序号")
        self.result_tree.heading("title", text="书名")
        self.result_tree.heading("narrator", text="演播")
        self.result_tree.column("#0", width=50)
        self.result_tree.column("title", width=220)
        self.result_tree.column("narrator", width=120)
        self.result_tree.bind("<<TreeviewSelect>>", self.on_book_select)
        sb1 = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=sb1.set)
        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb1.pack(side=tk.RIGHT, fill=tk.Y)
        main_pane.add(left_frame, weight=1)

        right_frame = ttk.LabelFrame(main_pane, text="章节列表")
        range_frame = ttk.Frame(right_frame)
        ttk.Label(range_frame, text="起始:").pack(side=tk.LEFT, padx=2)
        self.start_entry = ttk.Entry(range_frame, width=6)
        self.start_entry.pack(side=tk.LEFT, padx=2)
        ttk.Label(range_frame, text="结束:").pack(side=tk.LEFT, padx=2)
        self.end_entry = ttk.Entry(range_frame, width=6)
        self.end_entry.pack(side=tk.LEFT, padx=2)
        self.download_btn = ttk.Button(range_frame, text="下载选中范围", command=self.start_download_range)
        self.download_btn.pack(side=tk.LEFT, padx=10)
        self.pause_btn = ttk.Button(range_frame, text="暂停", command=self.toggle_pause)
        self.pause_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(range_frame, text="停止", command=self.stop_download)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        range_frame.pack(fill=tk.X, pady=5, padx=5)

        self.chapter_tree = ttk.Treeview(right_frame, columns=("idx", "title", "status"), show="headings", selectmode="none")
        self.chapter_tree.heading("idx", text="序号")
        self.chapter_tree.heading("title", text="章节名")
        self.chapter_tree.heading("status", text="状态")
        self.chapter_tree.column("idx", width=60)
        self.chapter_tree.column("title", width=380)
        self.chapter_tree.column("status", width=80)
        sb2 = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self.chapter_tree.yview)
        self.chapter_tree.configure(yscrollcommand=sb2.set)
        self.chapter_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        main_pane.add(right_frame, weight=1)

        stats_frame = ttk.Frame(self.root)
        self.total_label = ttk.Label(stats_frame, text="总数: 0")
        self.total_label.pack(side=tk.LEFT, padx=10, expand=True)
        self.completed_label = ttk.Label(stats_frame, text="完成: 0")
        self.completed_label.pack(side=tk.LEFT, padx=10, expand=True)
        self.failed_label = ttk.Label(stats_frame, text="失败: 0")
        self.failed_label.pack(side=tk.LEFT, padx=10, expand=True)
        stats_frame.pack(fill=tk.X, pady=(0, 5), padx=10)

        log_frame = ttk.LabelFrame(self.root, text="日志")
        self.log_text = tk.Text(log_frame, height=10, wrap=tk.WORD, font=("Consolas", 9))
        sb3 = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb3.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb3.pack(side=tk.RIGHT, fill=tk.Y)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.root.after(1000, self.do_auth)

    def log(self, msg):
        def _log():
            if self.destroyed:
                return
            self.log_text.insert(tk.END, f"{time.strftime('%H:%M:%S')} - {msg}\n")
            self.log_text.see(tk.END)
            lines = int(self.log_text.index('end-1c').split('.')[0])
            if lines > 800:
                self.log_text.delete('1.0', '400.0')
        if threading.current_thread() is threading.main_thread():
            _log()
        else:
            self.root.after(0, _log)

    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.download_folder.set(folder)

    def do_auth(self):
        def _auth():
            self.api.auth(log_callback=self.log)
        threading.Thread(target=_auth, daemon=True).start()

    def start_search(self):
        keyword = self.search_entry.get().strip()
        if not keyword:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        self.log(f"搜索: {keyword}")
        self.clear_results()

        def _search():
            results = self.api.search(keyword, log_callback=self.log)
            self.root.after(0, self.update_search_results, results)
        threading.Thread(target=_search, daemon=True).start()

    def load_category(self):
        idx = self.cat_combo.current()
        if idx < 0 or idx >= len(CATEGORIES):
            return
        cat_id, cat_name = CATEGORIES[idx]
        self.log(f"加载分类: {cat_name} (ID={cat_id})")
        self.clear_results()

        def _load():
            results = self.api.get_category(cat_id, page=1, log_callback=self.log)
            self.root.after(0, self.update_search_results, results)
        threading.Thread(target=_load, daemon=True).start()

    def clear_results(self):
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self.chapter_tree.delete(*self.chapter_tree.get_children())
        self.search_results = []
        self.current_book = None
        self.current_chapters = []
        self.update_stats(0, 0)

    def update_search_results(self, results):
        self.search_results = results
        if not results:
            self.log("未找到结果")
            return
        for i, item in enumerate(results, 1):
            title = item.get('title', '未知')
            narrator = item.get('narrator', item.get('artist', '未知'))
            self.result_tree.insert("", tk.END, text=str(i), values=(title, narrator), iid=str(i))
        self.log(f"找到 {len(results)} 条结果")

    def on_book_select(self, event):
        with self.downloading_lock:
            if self.downloading:
                if messagebox.askyesno("提示", "当前有下载任务，是否停止并切换书籍？"):
                    self.stop_download_internal()
                else:
                    return

        sel = self.result_tree.selection()
        if not sel:
            return
        idx = int(sel[0]) - 1
        if idx < 0 or idx >= len(self.search_results):
            return

        book = self.search_results[idx]
        self.current_book = book
        self.log(f"加载书籍: {book.get('title')} (ID={book.get('id')})")

        for item in self.chapter_tree.get_children():
            self.chapter_tree.delete(item)
        self.current_chapters = []
        self.record_cache.clear()

        def _load():
            album_id = book.get('id')
            detail, chapters = self.api.get_detail(album_id, log_callback=self.log)
            self.root.after(0, self.update_chapters, detail, chapters)
        threading.Thread(target=_load, daemon=True).start()

    def update_chapters(self, detail, chapters):
        self.current_chapters = chapters
        if not chapters:
            self.log("该书籍暂无章节")
            return

        total = len(chapters)
        self.start_entry.delete(0, tk.END)
        self.start_entry.insert(0, "1")
        self.end_entry.delete(0, tk.END)
        self.end_entry.insert(0, str(total))

        for i, chap in enumerate(chapters, 1):
            title = chap.get('title', f'第{i}章')
            self.chapter_tree.insert("", tk.END, iid=str(i), values=(i, title, "待下载"))

        self.log(f"共 {total} 章")
        self.update_interval_total()

    def get_range(self):
        try:
            s = int(self.start_entry.get().strip())
            e = int(self.end_entry.get().strip())
            total = len(self.current_chapters)
            if total == 0:
                return None, None
            s = max(1, s)
            e = min(total, e)
            if s > e:
                return None, None
            return s, e
        except:
            return None, None

    def update_interval_total(self):
        s, e = self.get_range()
        count = (e - s + 1) if s and e else 0
        self.total_label.config(text=f"总数: {count}")

    def update_stats(self, completed, failed):
        self.completed_label.config(text=f"完成: {completed}")
        self.failed_label.config(text=f"失败: {failed}")

    def start_download_range(self):
        with self.downloading_lock:
            if self.downloading:
                messagebox.showinfo("提示", "已有下载任务进行中")
                return
            if not self.current_chapters:
                messagebox.showinfo("提示", "请先选择书籍")
                return

        s, e = self.get_range()
        if s is None:
            messagebox.showerror("错误", "请输入有效的数字范围")
            return

        try:
            self.max_workers = int(self.workers_spin.get())
        except:
            self.max_workers = 2

        self.log(f"下载范围: 第{s}章 ~ 第{e}章，并发: {self.max_workers}")
        with self.downloading_lock:
            self.downloading = True
        self.paused_event.clear()
        self.stop_event.clear()
        self.download_btn.config(state="disabled")
        self.pause_btn.config(text="暂停")

        self.download_thread = threading.Thread(
            target=self.download_worker,
            args=(s, e),
            daemon=True
        )
        self.download_thread.start()

    def toggle_pause(self):
        if not self.downloading:
            messagebox.showinfo("提示", "没有正在进行的任务")
            return
        if self.paused_event.is_set():
            self.paused_event.clear()
            self.pause_btn.config(text="暂停")
            self.log("恢复下载")
        else:
            self.paused_event.set()
            self.pause_btn.config(text="恢复")
            self.log("暂停下载")

    def stop_download(self):
        if not self.downloading:
            return
        self.stop_download_internal()

    def stop_download_internal(self):
        self.stop_event.set()
        self.paused_event.clear()
        if self.download_thread and self.download_thread.is_alive():
            self.download_thread.join(timeout=15)
        with self.downloading_lock:
            self.downloading = False
        self.stop_event.clear()
        self.download_btn.config(state="normal")
        self.pause_btn.config(text="暂停")
        self.log("已停止下载")

    def download_worker(self, start, end):
        try:
            book = self.current_book
            if not book:
                return

            folder_name = sanitize_name(f"{book.get('title','unknown')}-{book.get('narrator','unknown')}")
            save_dir = os.path.join(self.download_folder.get(), folder_name)
            os.makedirs(save_dir, exist_ok=True)

            self.json_path = os.path.join(save_dir, f"{folder_name}.json")
            if os.path.exists(self.json_path):
                try:
                    with open(self.json_path, "r", encoding="utf-8") as f:
                        self.record_cache = json.load(f)
                except:
                    self.record_cache = {}
            else:
                self.record_cache = {}

            album_id = book.get('id')
            pending = []
            for i in range(start, end + 1):
                key = str(i)
                if key not in self.record_cache or self.record_cache[key] != "完成":
                    pending.append(i)

            if not pending:
                self.log("范围内已全部下载完成")
                return

            self.log(f"待下载: {len(pending)} 章")

            if self.max_workers == 1:
                for idx in pending:
                    if self.stop_event.is_set():
                        break
                    if self.paused_event.is_set():
                        self.log("已暂停")
                        while self.paused_event.is_set() and not self.stop_event.is_set():
                            time.sleep(0.5)
                    self.process_one_chapter(idx, album_id, save_dir)
            else:
                with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                    futures = {}
                    for idx in pending:
                        if self.stop_event.is_set():
                            break
                        future = executor.submit(self.process_one_chapter, idx, album_id, save_dir)
                        futures[future] = idx

                    for future in as_completed(futures):
                        idx = futures[future]
                        try:
                            future.result()
                        except Exception as e:
                            self.log(f"第{idx}章异常: {e}")
                        if self.stop_event.is_set():
                            executor.shutdown(wait=False)
                            break

            self.flush_record()
            self.log("下载任务结束")
        except Exception as e:
            self.log(f"下载线程异常: {e}")
        finally:
            self.download_finished()

    def process_one_chapter(self, idx, album_id, save_dir):
        if self.stop_event.is_set():
            return
        key = str(idx)
        if key in self.record_cache and self.record_cache[key] == "完成":
            self.update_chapter_status(idx, "完成")
            return

        chap = self.current_chapters[idx - 1]
        title = chap.get('title', f'第{idx}章')

        self.log(f"处理 [{idx}/{len(self.current_chapters)}] {title}")
        self.update_chapter_status(idx, "获取链接")

        play_url = self.api.get_play_url(album_id, chap.get('index', idx), log_callback=self.log)
        if not play_url:
            self.log(f"  [失败] 获取链接失败: {title}")
            self.record_cache[key] = "失败"
            self.update_chapter_status(idx, "失败")
            self.flush_record()
            return

        ext = "m4a"
        if "." in play_url.split("?")[0].split("/")[-1]:
            _ext = play_url.split("?")[0].split(".")[-1]
            if _ext in ("m4a", "mp3", "m4b"):
                ext = _ext

        filename = f"{idx:04d}-{sanitize_name(title)}.{ext}"
        filepath = os.path.join(save_dir, filename)

        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            self.log(f"  [跳过] 已存在: {filename}")
            self.record_cache[key] = "完成"
            self.update_chapter_status(idx, "完成")
            self.flush_record()
            return

        self.update_chapter_status(idx, "下载中")
        success = download_audio(play_url, filepath, stop_event=self.stop_event, log_callback=self.log)

        status = "完成" if success else "失败"
        self.record_cache[key] = status
        self.update_chapter_status(idx, status)
        self.flush_record()
        if success:
            self.log(f"  [成功] {filename}")
        else:
            self.log(f"  [失败] {filename}")

    def update_chapter_status(self, idx, status):
        def _update():
            try:
                item = self.chapter_tree.get_children()
                if self.chapter_tree.exists(str(idx)):
                    self.chapter_tree.set(str(idx), "status", status)
            except:
                pass
        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            self.root.after(0, _update)

    def flush_record(self):
        if not self.json_path:
            return
        try:
            with self.record_lock:
                if os.path.exists(self.json_path):
                    with open(self.json_path, "r", encoding="utf-8") as f:
                        old = json.load(f)
                    old.update(self.record_cache)
                    to_save = old
                else:
                    to_save = self.record_cache.copy()
                with open(self.json_path, "w", encoding="utf-8") as f:
                    json.dump(to_save, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"保存记录失败: {e}")

    def download_finished(self):
        if self.destroyed:
            return
        with self.downloading_lock:
            self.downloading = False
        self.paused_event.clear()
        self.stop_event.clear()
        self.download_btn.config(state="normal")
        self.pause_btn.config(text="暂停")
        completed = sum(1 for v in self.record_cache.values() if v == "完成")
        failed = sum(1 for v in self.record_cache.values() if v == "失败")
        self.update_stats(completed, failed)
        self.download_thread = None
        if self.close_waiting:
            self.close_waiting = False
            self.destroyed = True
            self.root.destroy()

    def on_closing(self):
        if self.destroyed or self.close_waiting:
            return
        if self.downloading:
            self.close_waiting = True
            self.stop_event.set()
            self.log("正在停止，请稍候...")
            if self.download_thread and self.download_thread.is_alive():
                self.download_thread.join(timeout=10)
            self.flush_record()
            self.destroyed = True
            self.root.destroy()
        else:
            self.destroyed = True
            self.root.destroy()

def main():
    root = tk.Tk()
    app = TingYouDownloader(root)
    root.mainloop()

if __name__ == "__main__":
    main()
