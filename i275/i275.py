import os
import re
import sys
import json
import time
import uuid
import threading
import requests
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED


APP_UA = 'okhttp/4.9.3'
API = 'https://api.365ting.com'
TY = API + '/tingyou-app'
DEVICE_PREFIX = 'tvbox-tingshijie-'
CACHE_FILE = 'i275_cache.json'
LOG_FILE = 'i275.log'
META_KEYS = ('id', 'title', 'cover_url', 'teller', 'author', 'count', 'description')

_DISK_LOCK = threading.Lock()


def LOG(msg):
    try:
        print('[i275]', msg)
    except Exception:
        pass


def get_data_dir():
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    candidates = [exe_dir]
    if os.environ.get('APPDATA'):
        candidates.append(os.path.join(os.environ['APPDATA'], 'i275'))
    candidates.append(os.path.expanduser('~'))
    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, '.wtest')
            with open(probe, 'w') as f:
                f.write('x')
            os.remove(probe)
            return d
        except Exception:
            continue
    return os.path.expanduser('~')


APP_DIR = get_data_dir()

_WIN_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5',
                 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 'LPT3', 'LPT4',
                 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'}


def sanitize_name(name):
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', name or '').strip()
    name = name.rstrip('. ')
    if not name:
        name = 'untitled'
    if name.upper().split('.')[0] in _WIN_RESERVED:
        name = '_' + name
    while len(name.encode('utf-8')) > 200:
        name = name[:-1]
    return name


class SourceClient:

    def __init__(self, log_callback=None):
        self.log = log_callback or LOG
        self._app = None
        self._uid = None
        self._device_id = ''
        self._plat_map = {}
        self._info_cache = {}
        self._ty_meta = {}
        self._boot_lock = threading.Lock()

    def _boot(self):
        if self._app is not None:
            return
        with self._boot_lock:
            if self._app is not None:
                return
            disk = self._loadDisk()
            self._device_id = disk.get('device_id') or ''
            self._plat_map = dict(disk.get('plat_map') or {})
            self._ty_meta = dict(disk.get('meta') or {})
            self._app = requests.Session()
            self._app.headers.update({'User-Agent': APP_UA, 'Content-Type': 'application/json'})
            self.log('拾音盘源就绪 (已提取专辑 %d 个)' % len(self._plat_map))

    def _loadDisk(self):
        path = os.path.join(APP_DIR, CACHE_FILE)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        return {}

    def _saveDisk(self):
        with _DISK_LOCK:
            try:
                d = {
                    'device_id': self._device_id or '',
                    'plat_map': self._plat_map,
                    'meta': self._ty_meta,
                }
                path = os.path.join(APP_DIR, CACHE_FILE)
                tmp = path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(d, f, ensure_ascii=False)
                os.replace(tmp, path)
            except Exception as e:
                self.log('缓存落盘失败: %s' % e)

    def _req(self, method, url, tries=3, **kw):
        kw.setdefault('timeout', 15)
        last = None
        for i in range(tries):
            try:
                r = self._app.request(method, url, **kw)
                if r.status_code in (429, 500, 502, 503, 504) and i < tries - 1:
                    last = RuntimeError('HTTP %d' % r.status_code)
                    time.sleep(0.6 * (i + 1))
                    continue
                r.raise_for_status()
                return r
            except Exception as e:
                last = e
                if i < tries - 1:
                    time.sleep(0.6 * (i + 1))
        raise last

    def _json(self, method, url, **kw):
        r = self._req(method, url, **kw)
        try:
            return r.json()
        except Exception:
            raise RuntimeError('响应非 JSON: %s | %s' % (url, r.text[:200]))

    def _uidGet(self):
        if self._uid:
            return self._uid
        with self._boot_lock:
            if self._uid:
                return self._uid
            if not self._device_id:
                self._device_id = DEVICE_PREFIX + uuid.uuid4().hex[:12]
                self._saveDisk()
            j = self._json('POST', API + '/api/auth/device-login',
                           json={'device_id': self._device_id}, timeout=15)
            uid = (j.get('data') or {}).get('user_id')
            if not uid:
                raise RuntimeError('device-login 无 user_id: %s' % str(j)[:120])
            self._uid = uid
        return self._uid

    def _tySearch(self, keyword):
        j = self._json('GET', TY + '/search', params={'keyword': keyword}, timeout=15)
        items = (j.get('data') or {}).get('items') or []
        added = False
        for it in items:
            if it.get('id'):
                k = str(it['id'])
                if k not in self._ty_meta:
                    added = True
                self._ty_meta[k] = {x: it.get(x) for x in META_KEYS}
        if added:
            if len(self._ty_meta) > 500:
                keep = list(self._ty_meta.items())[-300:]
                self._ty_meta = dict(keep)
            self._saveDisk()
        return items

    def _albumMeta(self, uid, ty_id):
        try:
            j = self._json('GET', API + '/api/users/%d/albums' % uid, timeout=15)
            for a in j.get('data') or []:
                if str(a.get('external_id')) == str(ty_id):
                    return a
        except Exception as e:
            self.log('用户歌单查询失败: %s' % e)
        return {}

    def _doImport(self, uid, ty_id, rounds=2):
        for _ in range(rounds):
            try:
                j = self._json('POST', TY + '/import',
                               json={'user_id': uid, 'album_id': ty_id}, timeout=30)
                task = (j.get('data') or {}).get('task_id')
            except Exception as e:
                self.log('提取提交异常: %s' % e)
                time.sleep(1)
                continue
            if not task:
                self.log('提取任务无 task_id: %s' % str(j)[:120])
                time.sleep(1)
                continue
            deadline = time.time() + 12
            plat, failed = None, False
            while time.time() < deadline:
                try:
                    st = (self._json('GET', TY + '/import/status',
                                     params={'task_id': task}, timeout=10).get('data')) or {}
                except Exception:
                    time.sleep(0.5)
                    continue
                if st.get('status') == 'done':
                    plat = st.get('album_id')
                    break
                if st.get('status') == 'failed':
                    failed = True
                    break
                time.sleep(0.3)
            if plat:
                return plat
            self.log('提取%s (task=%s)' % ('失败' if failed else '超时', task))
            time.sleep(0.5)
        return None

    def _getTracks(self, plat, uid, tries=3):
        tracks = []
        for i in range(tries):
            try:
                j = self._json('GET', API + '/api/albums/%s/tracks' % plat,
                               params={'user_id': uid}, timeout=20)
                tracks = j.get('data') or []
                if not isinstance(tracks, list):
                    tracks = tracks.get('tracks') or tracks.get('list') or []
            except Exception as e:
                tracks = []
                self.log('曲目获取异常: %s' % e)
            if tracks:
                return tracks
            time.sleep(0.6)
        return []

    def _ensureAlbum(self, ty_id, _retried=False):
        ty_id = str(ty_id)
        if ty_id in self._info_cache:
            return self._info_cache[ty_id]
        uid = self._uidGet()
        meta = {}
        plat = self._plat_map.get(ty_id)
        if plat is None:
            meta = self._albumMeta(uid, ty_id)
            plat = meta.get('id')
        if plat is None:
            self.log('首次提取专辑, 请稍候 (最多 12 秒)...')
            plat = self._doImport(uid, ty_id)
            if plat is None:
                raise RuntimeError('提取未完成, 请稍后重试')
            self._plat_map[ty_id] = plat
            self._saveDisk()
        if not meta and ty_id not in self._ty_meta:
            meta = self._albumMeta(uid, ty_id)
        tracks = self._getTracks(plat, uid)
        if not tracks and not _retried:
            self.log('曲目为空, 重置缓存重试: %s' % ty_id)
            self._plat_map.pop(ty_id, None)
            self._saveDisk()
            return self._ensureAlbum(ty_id, _retried=True)
        cached = self._ty_meta.get(ty_id) or {}
        info = {
            'platform_id': plat, 'ty_id': ty_id,
            'title': meta.get('title') or cached.get('title') or '',
            'cover': meta.get('cover_url') or cached.get('cover_url') or '',
            'desc': meta.get('description') or cached.get('description') or '',
            'teller': cached.get('teller') or '',
            'count': int(meta.get('count') or cached.get('count') or 0),
            'tracks': tracks,
        }
        if tracks:
            if len(self._info_cache) > 20:
                self._info_cache.clear()
            self._info_cache[ty_id] = info
        return info

    def search(self, keyword):
        self._boot()
        books, seen = [], set()
        for it in self._tySearch(keyword) or []:
            if not it.get('id'):
                continue
            name = it.get('title') or ''
            k = re.sub(r'\s+', '', name)
            if not k or k in seen:
                continue
            seen.add(k)
            books.append({
                'id': str(it.get('id')),
                'title': name,
                'narrator': it.get('teller') or it.get('author') or '',
                'cover': it.get('cover_url') or '',
                'count': int(it.get('count') or 0),
            })
        return books

    def get_chapters(self, book):
        self._boot()
        info = self._ensureAlbum(book['id'])
        ty_id = info.get('ty_id') or book['id']
        out = []
        for i, t in enumerate(info.get('tracks') or []):
            idx = t.get('track_no') or i + 1
            title = re.sub(r'[#$]', ' ', t.get('title') or '') or ('第%d集' % (i + 1))
            out.append({
                'index': i + 1,
                'title': title,
                'album_id': ty_id,
                'track_no': idx,
            })
        if not out:
            raise RuntimeError('该专辑无可用曲目')
        return out

    def get_audio_url(self, chap):
        return '%s/tingyou/stream?album=%s&idx=%s' % (API, chap['album_id'], chap['track_no'])


def download_audio(url, save_path, retry=3, log_callback=None, stop_event=None):
    temp_path = save_path + '.tmp'
    for attempt in range(retry):
        if stop_event and stop_event.is_set():
            return False
        try:
            headers = {'User-Agent': APP_UA, 'Referer': API + '/'}
            resp = requests.get(url, headers=headers, stream=True, timeout=30)
            resp.raise_for_status()
            ct = (resp.headers.get('Content-Type') or '').lower()
            if 'html' in ct or 'json' in ct:
                raise Exception('返回了非音频内容: %s' % ct)
            with open(temp_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    if stop_event and stop_event.is_set():
                        raise InterruptedError()
                    f.write(chunk)
            if os.path.getsize(temp_path) == 0:
                raise Exception('下载文件大小为 0')
            os.replace(temp_path, save_path)
            return True
        except InterruptedError:
            return False
        except Exception as e:
            if attempt == retry - 1:
                if log_callback:
                    log_callback('下载失败: %s' % e)
                return False
            time.sleep(2)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
    return False


class DownloadApp:

    def __init__(self, root):
        self.root = root
        self.root.title('听书下载器 (拾音盘源)')
        self.root.geometry('1000x720')
        self.root.minsize(820, 580)

        self.client = SourceClient(log_callback=self.log)
        self.search_results = []
        self.current_book = None
        self.current_chapters = []
        self.current_book_title = ''
        self.current_book_narrator = ''
        self.download_folder = tk.StringVar(value=os.path.join(APP_DIR, 'i275'))

        self.paused_event = threading.Event()
        self.stop_event = threading.Event()
        self.downloading = False
        self.downloading_lock = threading.Lock()
        self.download_thread = None
        self.close_waiting = False
        self.destroyed = False
        self.chapters_lock = threading.RLock()
        self.record_lock = threading.RLock()
        self.json_lock = threading.Lock()
        self.chapter_items_lock = threading.Lock()
        self.record_cache = {}
        self.json_path = None
        self.chapter_items = {}
        self.max_workers = 1
        self._searching = False

        self._init_log_file()
        self.create_widgets()
        self.root.protocol('WM_DELETE_WINDOW', self.on_closing)

    def _init_log_file(self):
        try:
            path = os.path.join(APP_DIR, LOG_FILE)
            if os.path.exists(path) and os.path.getsize(path) > 2 * 1024 * 1024:
                os.remove(path)
        except Exception:
            pass

    def create_widgets(self):
        top = ttk.Frame(self.root)
        ttk.Label(top, text='搜索书籍:').pack(side=tk.LEFT, padx=5)
        self.search_entry = ttk.Entry(top, width=36)
        self.search_entry.pack(side=tk.LEFT, padx=5)
        self.search_entry.bind('<Return>', lambda e: self.start_search())
        self.search_btn = ttk.Button(top, text='搜索', command=self.start_search)
        self.search_btn.pack(side=tk.LEFT, padx=5)

        ttk.Label(top, text='保存目录:').pack(side=tk.LEFT, padx=(16, 5))
        ttk.Entry(top, textvariable=self.download_folder, width=28).pack(side=tk.LEFT, padx=5)
        ttk.Button(top, text='浏览', command=self.select_folder).pack(side=tk.LEFT, padx=5)

        ttk.Label(top, text='并发数:').pack(side=tk.LEFT, padx=(16, 5))
        self.workers_spinbox = tk.Spinbox(top, from_=1, to=10, width=5, command=self.update_max_workers)
        self.workers_spinbox.delete(0, tk.END)
        self.workers_spinbox.insert(0, str(self.max_workers))
        self.workers_spinbox.bind('<FocusOut>', lambda e: self.update_max_workers())
        self.workers_spinbox.pack(side=tk.LEFT, padx=5)
        top.pack(fill=tk.X, pady=10, padx=10)

        pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        left = ttk.LabelFrame(pane, text='搜索结果')
        self.result_tree = ttk.Treeview(left, columns=('title', 'narrator'),
                                        show='tree headings', height=20)
        self.result_tree.heading('#0', text='序号')
        self.result_tree.heading('title', text='书名')
        self.result_tree.heading('narrator', text='演播')
        self.result_tree.column('#0', width=50)
        self.result_tree.column('title', width=280)
        self.result_tree.column('narrator', width=140)
        self.result_tree.bind('<<TreeviewSelect>>', self.on_book_select)
        sl = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=sl.set)
        self.result_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sl.pack(side=tk.RIGHT, fill=tk.Y)
        pane.add(left, weight=1)

        right = ttk.LabelFrame(pane, text='章节列表')
        ctrl = ttk.Frame(right)
        ttk.Label(ctrl, text='起始:').pack(side=tk.LEFT, padx=2)
        self.start_entry = ttk.Entry(ctrl, width=6)
        self.start_entry.pack(side=tk.LEFT, padx=2)
        self.start_entry.bind('<KeyRelease>', lambda e: self.update_interval_total())
        ttk.Label(ctrl, text='结束:').pack(side=tk.LEFT, padx=2)
        self.end_entry = ttk.Entry(ctrl, width=6)
        self.end_entry.pack(side=tk.LEFT, padx=2)
        self.end_entry.bind('<KeyRelease>', lambda e: self.update_interval_total())
        self.download_btn = ttk.Button(ctrl, text='下载', command=self.start_download_range)
        self.download_btn.pack(side=tk.LEFT, padx=10)
        self.pause_btn = ttk.Button(ctrl, text='暂停', command=self.toggle_pause)
        self.pause_btn.pack(side=tk.LEFT, padx=5)
        self.retry_btn = ttk.Button(ctrl, text='重试失败', command=self.retry_failed)
        self.retry_btn.pack(side=tk.LEFT, padx=5)
        ttk.Button(ctrl, text='停止', command=self.stop_download).pack(side=tk.LEFT, padx=5)
        ctrl.pack(fill=tk.X, pady=5, padx=5, anchor=tk.W)

        self.chapter_tree = ttk.Treeview(right, columns=('index', 'title', 'status'),
                                         show='headings', selectmode='none')
        self.chapter_tree.heading('index', text='序号')
        self.chapter_tree.heading('title', text='章节名')
        self.chapter_tree.heading('status', text='状态')
        self.chapter_tree.column('index', width=60)
        self.chapter_tree.column('title', width=360)
        self.chapter_tree.column('status', width=80)
        sr = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.chapter_tree.yview)
        self.chapter_tree.configure(yscrollcommand=sr.set)
        self.chapter_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sr.pack(side=tk.RIGHT, fill=tk.Y)
        pane.add(right, weight=1)

        stats = ttk.Frame(self.root)
        stats.pack(fill=tk.X, pady=(0, 5), padx=10)
        self.total_label = ttk.Label(stats, text='总数: 0', font=('', 10))
        self.total_label.pack(side=tk.LEFT, padx=10, expand=True)
        self.completed_label = ttk.Label(stats, text='完成: 0', font=('', 10))
        self.completed_label.pack(side=tk.LEFT, padx=10, expand=True)
        self.failed_label = ttk.Label(stats, text='失败: 0', font=('', 10))
        self.failed_label.pack(side=tk.LEFT, padx=10, expand=True)

        logf = ttk.LabelFrame(self.root, text='日志 (同时写入 %s)' % LOG_FILE)
        logf.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.log_text = tk.Text(logf, height=8, wrap=tk.WORD, font=('Consolas', 9))
        slg = ttk.Scrollbar(logf, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=slg.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        slg.pack(side=tk.RIGHT, fill=tk.Y)

    def update_max_workers(self):
        val = self.max_workers
        try:
            v = int(self.workers_spinbox.get())
            if 1 <= v <= 10:
                val = v
        except Exception:
            pass
        self.max_workers = val
        self.workers_spinbox.delete(0, tk.END)
        self.workers_spinbox.insert(0, str(val))

    def log(self, message):
        line = '%s - %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), message)
        try:
            with open(os.path.join(APP_DIR, LOG_FILE), 'a', encoding='utf-8') as f:
                f.write(line)
        except Exception:
            pass

        def _log():
            if self.destroyed:
                return
            try:
                self.log_text.insert(tk.END, line)
                self.log_text.see(tk.END)
                self._trim_log()
            except tk.TclError:
                pass
        if threading.current_thread() is threading.main_thread():
            _log()
        else:
            try:
                self.root.after(0, _log)
            except tk.TclError:
                pass

    def _trim_log(self):
        max_lines, keep = 1000, 500
        n = int(self.log_text.index('end-1c').split('.')[0])
        if n > max_lines:
            self.log_text.delete('1.0', '%d.0' % (n - keep))
            self.log_text.see(tk.END)

    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.download_folder.set(folder)

    def start_search(self):
        if self._searching:
            return
        keyword = self.search_entry.get().strip()
        if not keyword:
            messagebox.showwarning('提示', '请输入搜索关键词')
            return
        self._searching = True
        self.search_btn.config(state='disabled')
        self.log('搜索关键词: %s' % keyword)
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self.chapter_tree.delete(*self.chapter_tree.get_children())
        self.search_results = []
        self.current_book = None
        with self.chapters_lock:
            self.current_chapters = []
        self.update_completed_failed(0, 0)
        self.update_interval_total()

        def do_search():
            try:
                books = self.client.search(keyword)
            except Exception as e:
                self.log('搜索异常: %s' % e)
                books = []
            try:
                self.root.after(0, self._on_search_done, books)
            except tk.TclError:
                pass

        threading.Thread(target=do_search, daemon=True).start()

    def _on_search_done(self, books):
        self._searching = False
        try:
            self.search_btn.config(state='normal')
        except tk.TclError:
            pass
        self.update_search_results(books)

    def update_search_results(self, books):
        if self.destroyed:
            return
        self.search_results = books
        if not books:
            self.log('未找到任何书籍')
            return
        for idx, b in enumerate(books, start=1):
            self.result_tree.insert('', tk.END, text=str(idx),
                                    values=(b['title'], b['narrator']),
                                    iid=str(idx))
        self.log('找到 %d 本书籍' % len(books))

    def on_book_select(self, event):
        with self.downloading_lock:
            if self.downloading:
                if not messagebox.askyesno('提示', '当前有下载任务, 是否停止并切换书籍?'):
                    return
                self.stop_download_internal()
                if self.downloading:
                    self.log('旧下载任务尚未完全停止, 请稍候再切换书籍')
                    return
        with self.record_lock:
            self.record_cache.clear()
        self.json_path = None

        selection = self.result_tree.selection()
        if not selection:
            return
        idx = int(selection[0]) - 1
        if idx < 0 or idx >= len(self.search_results):
            return
        book = self.search_results[idx]
        self.current_book = book
        self.current_book_title = book['title']
        self.current_book_narrator = book['narrator']
        self.log('加载书籍: %s - %s' % (book['title'], book['narrator']))
        for item in self.chapter_tree.get_children():
            self.chapter_tree.delete(item)
        with self.chapter_items_lock:
            self.chapter_items.clear()
        with self.chapters_lock:
            self.current_chapters = []
        self.update_completed_failed(0, 0)
        self.update_interval_total()
        try:
            self.result_tree.config(selectmode='none')
        except tk.TclError:
            pass

        def load():
            self.log('正在获取章节列表 (首次提取最长 12 秒, 请勿重复点击)...')
            try:
                chapters = self.client.get_chapters(book)
            except Exception as e:
                self.log('加载章节失败: %s' % e)
                chapters = []
            try:
                self.root.after(0, self._on_chapters_loaded, chapters)
            except tk.TclError:
                pass

        threading.Thread(target=load, daemon=True).start()

    def _on_chapters_loaded(self, chapters):
        try:
            self.result_tree.config(selectmode='browse')
        except tk.TclError:
            pass
        self.update_chapters(chapters)

    def stop_download_internal(self):
        self.stop_event.set()
        self.paused_event.clear()
        if self.download_thread and self.download_thread.is_alive():
            self.download_thread.join(timeout=15)
            if self.download_thread.is_alive():
                self.log('强制终止超时, 下载线程仍在后台收尾, 请勿重复操作')
                return
        with self.downloading_lock:
            self.downloading = False
        self.stop_event.clear()
        try:
            self.download_btn.config(state='normal')
            self.pause_btn.config(text='暂停')
        except tk.TclError:
            pass
        self.log('已停止下载任务')

    def stop_download(self):
        if not self.downloading:
            messagebox.showinfo('提示', '没有正在进行的下载任务')
            return
        self.stop_download_internal()

    def update_chapters(self, chapters):
        if self.destroyed:
            return
        with self.chapters_lock:
            self.current_chapters = chapters
        if not chapters:
            self.log('未找到任何章节')
            self.update_completed_failed(0, 0)
            self.update_interval_total()
            return
        with self.chapter_items_lock:
            self.chapter_items.clear()
        for idx, chap in enumerate(chapters, start=1):
            item_id = self.chapter_tree.insert('', tk.END, iid=str(idx),
                                               values=(idx, chap['title'], '待下载'))
            with self.chapter_items_lock:
                self.chapter_items[idx] = item_id
            if idx % 100 == 0:
                try:
                    self.root.update_idletasks()
                except tk.TclError:
                    return
        self.log('获取到 %d 个章节' % len(chapters))
        total = len(chapters)
        self.start_entry.delete(0, tk.END)
        self.start_entry.insert(0, '1')
        self.end_entry.delete(0, tk.END)
        self.end_entry.insert(0, str(total))
        self.refresh_stats_from_json()
        self.update_interval_total()

    def refresh_stats_from_json(self):
        if not self.current_book_title or not self.current_book_narrator:
            self.update_completed_failed(0, 0)
            return
        folder_name = sanitize_name('%s-%s' % (self.current_book_title, self.current_book_narrator))
        save_root = self.download_folder.get() or os.path.join(APP_DIR, 'i275')
        json_path = os.path.join(save_root, folder_name, '%s.json' % folder_name)
        self.json_path = json_path
        completed, failed = 0, 0
        if os.path.exists(json_path):
            try:
                with self.json_lock:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        record = json.load(f)
                for status in record.values():
                    if status == '完成':
                        completed += 1
                    elif status == '失败':
                        failed += 1
                with self.record_lock:
                    self.record_cache.clear()
                    self.record_cache.update(record)
            except Exception as e:
                self.log('读取记录文件失败: %s' % e)
                with self.record_lock:
                    self.record_cache.clear()
        else:
            with self.record_lock:
                self.record_cache.clear()
        self.update_completed_failed(completed, failed)

    def update_completed_failed(self, completed, failed):
        try:
            self.completed_label.config(text='完成: %d' % completed)
            self.failed_label.config(text='失败: %d' % failed)
        except tk.TclError:
            pass

    def _update_stats_from_cache(self):
        with self.record_lock:
            completed = sum(1 for s in self.record_cache.values() if s == '完成')
            failed = sum(1 for s in self.record_cache.values() if s == '失败')
        self.update_completed_failed(completed, failed)

    def update_interval_total(self):
        start, end = self.get_range_values()
        count = end - start + 1 if start is not None else 0
        try:
            self.total_label.config(text='总数: %d' % count)
        except tk.TclError:
            pass

    def get_range_values(self):
        start_str = self.start_entry.get().strip()
        end_str = self.end_entry.get().strip()
        with self.chapters_lock:
            total = len(self.current_chapters)
        if total == 0:
            return None, None
        try:
            if (start_str and not start_str.isdigit()) or (end_str and not end_str.isdigit()):
                raise ValueError
            start = int(start_str) if start_str else 1
            end = int(end_str) if end_str else total
        except ValueError:
            return None, None
        if start < 1:
            start = 1
        if end > total:
            end = total
        if start > end:
            return None, None
        return start, end

    def start_download_range(self):
        with self.downloading_lock:
            if self.downloading:
                messagebox.showinfo('提示', '已有下载任务进行中, 如需重新下载请先停止')
                return
        with self.chapters_lock:
            if not self.current_chapters:
                messagebox.showinfo('提示', '请先选择一本书籍')
                return
        start, end = self.get_range_values()
        if start is None:
            messagebox.showerror('错误', '请输入有效的数字范围(正整数), 起始不能大于结束')
            return
        self._start_download(start, end)

    def retry_failed(self):
        with self.downloading_lock:
            if self.downloading:
                messagebox.showinfo('提示', '已有下载任务进行中')
                return
        with self.chapters_lock:
            if not self.current_chapters:
                messagebox.showinfo('提示', '请先选择一本书籍')
                return
        with self.record_lock:
            failed_nums = sorted([int(k) for k, v in self.record_cache.items() if v == '失败' and k.isdigit()])
        if not failed_nums:
            messagebox.showinfo('提示', '没有失败的章节需要重试')
            return
        self.log('重试失败章节: %d 个' % len(failed_nums))
        self._start_download_custom(failed_nums)

    def _start_download(self, start, end):
        self.log('下载范围: 第 %d 章 至 第 %d 章, 并发数: %d' % (start, end, self.max_workers))
        with self.downloading_lock:
            self.downloading = True
        self.paused_event.clear()
        self.stop_event.clear()
        self.download_btn.config(state='disabled')
        self.pause_btn.config(text='暂停')
        self.download_thread = threading.Thread(target=self.download_range_worker,
                                                args=(start, end), daemon=True)
        self.download_thread.start()

    def _start_download_custom(self, indices):
        with self.downloading_lock:
            self.downloading = True
        self.paused_event.clear()
        self.stop_event.clear()
        self.download_btn.config(state='disabled')
        self.pause_btn.config(text='暂停')
        self.download_thread = threading.Thread(target=self.download_custom_worker,
                                                args=(indices,), daemon=True)
        self.download_thread.start()

    def toggle_pause(self):
        if not self.downloading:
            messagebox.showinfo('提示', '当前没有正在进行的下载任务')
            return
        if self.paused_event.is_set():
            self.paused_event.clear()
            self.pause_btn.config(text='暂停')
            self.log('恢复下载')
        else:
            self.paused_event.set()
            self.pause_btn.config(text='恢复')
            self.log('暂停下载, 完成当前已提交的章节后将停止新任务')

    def download_one_chapter(self, chap_num, chap, save_dir):
        if self.stop_event.is_set():
            return False, '取消'
        chap_title = chap['title']
        key = str(chap_num)

        with self.record_lock:
            if key in self.record_cache and self.record_cache[key] == '完成':
                self.update_chapter_status(chap_num, '完成')
                return True, '完成'

        filename = '%04d_%s.m4a' % (chap_num, sanitize_name(chap_title))
        filepath = os.path.join(save_dir, filename)

        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            self.log('[跳过] 文件已存在: %s' % filename)
            with self.record_lock:
                self.record_cache[key] = '完成'
            self.update_chapter_status(chap_num, '完成')
            self.flush_record()
            self._update_stats_from_cache()
            return True, '完成'

        self.log('正在处理: %s (第%d章)' % (chap_title, chap_num))
        try:
            audio_url = self.client.get_audio_url(chap)
        except Exception as e:
            self.log('获取音频链接异常: %s' % e)
            audio_url = ''

        if not audio_url:
            self.log('[失败] 获取音频链接失败: %s' % chap_title)
            with self.record_lock:
                self.record_cache[key] = '失败'
            self.update_chapter_status(chap_num, '失败')
            self.flush_record()
            self._update_stats_from_cache()
            return False, '失败'

        self.log('正在下载: %s' % chap_title)
        success = download_audio(audio_url, filepath,
                                 log_callback=self.log, stop_event=self.stop_event)
        status = '完成' if success else '失败'
        if success:
            self.log('[成功] %s' % filename)
        else:
            self.log('[失败] %s' % chap_title)

        with self.record_lock:
            self.record_cache[key] = status
        self.update_chapter_status(chap_num, status)
        self.flush_record()
        self._update_stats_from_cache()
        return success, status

    def flush_record(self):
        if not self.json_path:
            return
        with self.json_lock:
            with self.record_lock:
                if not self.record_cache:
                    return
                try:
                    to_save = dict(self.record_cache)
                    tmp = self.json_path + '.tmp'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        json.dump(to_save, f, ensure_ascii=False)
                    os.replace(tmp, self.json_path)
                except Exception as e:
                    self.log('保存记录文件失败: %s' % e)

    def _prepare_context(self):
        save_root = self.download_folder.get() or os.path.join(APP_DIR, 'i275')
        with self.chapters_lock:
            book_title = self.current_book_title
            book_narrator = self.current_book_narrator
            current_chapters = self.current_chapters.copy()
        folder_name = sanitize_name('%s-%s' % (book_title, book_narrator))
        save_dir = os.path.join(save_root, folder_name)
        os.makedirs(save_dir, exist_ok=True)
        self.json_path = os.path.join(save_dir, '%s.json' % folder_name)
        if os.path.exists(self.json_path):
            try:
                with self.json_lock:
                    with open(self.json_path, 'r', encoding='utf-8') as f:
                        old_record = json.load(f)
                with self.record_lock:
                    self.record_cache.update(old_record)
            except Exception as e:
                self.log('读取记录文件失败: %s' % e)
        return save_dir, current_chapters

    def _run_pending(self, pending, save_dir):
        if not pending:
            self.log('范围内没有需要下载的章节')
            return

        if self.max_workers == 1:
            for chap_num, chap in pending:
                if self.paused_event.is_set():
                    self.log('下载已暂停, 按[恢复]继续')
                    while self.paused_event.is_set() and not self.stop_event.is_set():
                        time.sleep(1)
                    if self.stop_event.is_set():
                        break
                if self.stop_event.is_set():
                    break
                self.download_one_chapter(chap_num, chap, save_dir)
            return

        pending_index = 0
        active = {}
        executor = ThreadPoolExecutor(max_workers=self.max_workers)

        def submit_next():
            nonlocal pending_index
            while pending_index < len(pending):
                if self.paused_event.is_set() or self.stop_event.is_set():
                    return False
                chap_num, chap = pending[pending_index]
                future = executor.submit(self.download_one_chapter, chap_num, chap, save_dir)
                active[future] = (chap_num, chap)
                pending_index += 1
                return True
            return False

        for _ in range(self.max_workers):
            if not submit_next():
                break

        while active or pending_index < len(pending) or self.paused_event.is_set():
            if self.stop_event.is_set():
                break
            if active:
                done, _ = wait(list(active.keys()), timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    chap_num, _c = active.pop(future)
                    try:
                        future.result(timeout=0)
                    except Exception as e:
                        self.log('章节 %d 下载异常: %s' % (chap_num, e))
            if self.paused_event.is_set() and not active:
                time.sleep(0.5)
                continue
            while len(active) < self.max_workers:
                if not submit_next():
                    break

        executor.shutdown(wait=True)

    def download_range_worker(self, start, end):
        try:
            save_dir, current_chapters = self._prepare_context()
            with self.record_lock:
                snapshot = self.record_cache.copy()
            pending = []
            for i in range(start, end + 1):
                if i - 1 >= len(current_chapters):
                    continue
                if str(i) not in snapshot or snapshot[str(i)] != '完成':
                    pending.append((i, current_chapters[i - 1]))
            self._run_pending(pending, save_dir)
            self.flush_record()
        except Exception as e:
            self.log('下载工作线程异常: %s' % e)
        finally:
            self.download_finished()

    def download_custom_worker(self, indices):
        try:
            save_dir, current_chapters = self._prepare_context()
            pending = []
            for i in indices:
                if 1 <= i <= len(current_chapters):
                    pending.append((i, current_chapters[i - 1]))
            self._run_pending(pending, save_dir)
            self.flush_record()
        except Exception as e:
            self.log('下载工作线程异常: %s' % e)
        finally:
            self.download_finished()

    def download_finished(self):
        if self.destroyed:
            return
        with self.downloading_lock:
            self.downloading = False
        self.paused_event.clear()
        self.stop_event.clear()
        try:
            self.download_btn.config(state='normal')
            self.pause_btn.config(text='暂停')
        except tk.TclError:
            pass
        self.log('所有任务已完成')
        self._update_stats_from_cache()
        self.download_thread = None
        if self.close_waiting:
            self.close_waiting = False
            self.destroyed = True
            try:
                self.root.after(0, self.root.destroy)
            except tk.TclError:
                pass

    def update_chapter_status(self, chapter_num, status):
        if self.destroyed:
            return

        def _update():
            try:
                with self.chapter_items_lock:
                    item_id = self.chapter_items.get(chapter_num)
                if item_id:
                    self.chapter_tree.set(item_id, 'status', status)
            except tk.TclError:
                pass

        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            try:
                self.root.after(0, _update)
            except tk.TclError:
                pass

    def on_closing(self):
        if self.destroyed or self.close_waiting:
            return
        if self.downloading:
            self.close_waiting = True
            self.stop_event.set()
            self.paused_event.clear()
            self.log('正在停止下载任务, 请稍候...')
            if self.download_thread and self.download_thread.is_alive():
                self.download_thread.join(timeout=10)
            self.flush_record()
            if not self.destroyed:
                self.destroyed = True
                self.root.destroy()
        else:
            self.destroyed = True
            self.root.destroy()


def main():
    root = tk.Tk()
    DownloadApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
