import os
import re
import sys
import json
import time
import uuid
import threading
import traceback
import requests
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

APP_UA = 'okhttp/4.9.3'
BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0')
API = 'https://api.365ting.com'
TY = API + '/tingyou-app'
DEVICE_PREFIX = 'tvbox-tingshijie-'
CACHE_FILE = 'syp_cache.json'
LOG_FILE = 'syp.log'
META_KEYS = ('id', 'title', 'cover_url', 'teller', 'author', 'count', 'description')

_DISK_LOCK = threading.Lock()

def LOG(msg):
    try:
        print('[syp]', msg)
    except Exception:
        pass

def get_data_dir():
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    candidates = [exe_dir]
    if os.environ.get('APPDATA'):
        candidates.append(os.path.join(os.environ['APPDATA'], 'syp'))
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

    def __init__(self, status_cb=None, error_cb=None):
        self.status = status_cb or LOG
        self.error = error_cb or LOG
        self._app = None
        self._uid = None
        self._device_id = ''
        self._plat_map = {}
        self._info_cache = {}
        self._ty_meta = {}
        self._boot_lock = threading.Lock()
        self._map_lock = threading.RLock()

    def _boot(self):
        if self._app is not None:
            return
        with self._boot_lock:
            if self._app is not None:
                return
            disk = self._loadDisk()
            self._device_id = disk.get('device_id') or ''
            with self._map_lock:
                self._plat_map = dict(disk.get('plat_map') or {})
                self._ty_meta = dict(disk.get('meta') or {})
            self._app = requests.Session()
            self._app.headers.update({'User-Agent': APP_UA, 'Content-Type': 'application/json'})
            self.status('拾音盘源就绪 (已提取专辑 %d 个)' % len(self._plat_map))

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
        with self._map_lock:
            payload = {
                'device_id': self._device_id or '',
                'plat_map': dict(self._plat_map),
                'meta': dict(self._ty_meta),
            }
        with _DISK_LOCK:
            try:
                path = os.path.join(APP_DIR, CACHE_FILE)
                tmp = path + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, ensure_ascii=False)
                os.replace(tmp, path)
            except Exception as e:
                self.error('缓存落盘失败: %s' % e)

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
        with self._map_lock:
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
        if added:
            self._saveDisk()
        return items

    def _albumMeta(self, uid, ty_id):
        try:
            j = self._json('GET', API + '/api/users/%s/albums' % uid, timeout=15)
            for a in j.get('data') or []:
                if str(a.get('external_id')) == str(ty_id):
                    return a
        except Exception as e:
            self.error('用户歌单查询失败: %s' % e)
        return {}

    def _doImport(self, uid, ty_id, rounds=2):
        for _ in range(rounds):
            try:
                j = self._json('POST', TY + '/import',
                               json={'user_id': uid, 'album_id': ty_id}, timeout=30)
                task = (j.get('data') or {}).get('task_id')
            except Exception as e:
                self.error('提取提交异常: %s' % e)
                time.sleep(1)
                continue
            if not task:
                self.error('提取任务无 task_id: %s' % str(j)[:120])
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
            self.status('提取%s (task=%s)' % ('失败' if failed else '超时', task))
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
                self.error('曲目获取异常: %s' % e)
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
        with self._map_lock:
            plat = self._plat_map.get(ty_id)
        if plat is None:
            meta = self._albumMeta(uid, ty_id)
            plat = meta.get('id')
        if plat is None:
            self.status('首次提取专辑, 请稍候 (最多 12 秒)...')
            plat = self._doImport(uid, ty_id)
            if plat is None:
                raise RuntimeError('提取未完成, 请稍后重试')
            with self._map_lock:
                self._plat_map[ty_id] = plat
            self._saveDisk()
        with self._map_lock:
            cached = dict(self._ty_meta.get(ty_id) or {})
        if not meta and not cached:
            meta = self._albumMeta(uid, ty_id)
        tracks = self._getTracks(plat, uid)
        if not tracks and not _retried:
            self.status('曲目为空, 重置缓存重试: %s' % ty_id)
            with self._map_lock:
                self._plat_map.pop(ty_id, None)
            self._info_cache.pop(ty_id, None)
            self._saveDisk()
            return self._ensureAlbum(ty_id, _retried=True)
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

def _probe_file(url, headers, timeout):
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=timeout)
        try:
            ct = (r.headers.get('Content-Type') or '').lower()
            if r.status_code in (429, 503) or 'json' in ct:
                return None, False, 'rate_limited'
            if r.status_code not in (200, 206):
                return None, False, ct
            accept = 'bytes' in (r.headers.get('Accept-Ranges') or '').lower()
            total = None
            cl = r.headers.get('Content-Length')
            cr = r.headers.get('Content-Range') or ''
            m = re.search(r'/(\d+)\s*$', cr)
            if m:
                total = int(m.group(1))
            elif cl and cl.isdigit() and r.status_code == 200:
                total = int(cl)
            return total, accept, ct
        finally:
            r.close()
    except Exception:
        return None, False, ''

def _download_range(url, headers, start, end, save_path, stop_event, timeout):
    h = dict(headers)
    h['Range'] = 'bytes=%d-%d' % (start, end)
    try:
        r = requests.get(url, headers=h, stream=True, timeout=timeout)
        ct = (r.headers.get('Content-Type') or '').lower()
        if r.status_code in (429, 503) or 'json' in ct:
            return -2
        r.raise_for_status()
        written = 0
        with open(save_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                if stop_event and stop_event.is_set():
                    r.close()
                    return -1
                f.write(chunk)
                written += len(chunk)
        if written == 0:
            return -1
        return written
    except Exception:
        return -1

def _interruptible_sleep(stop_event, secs):
    if stop_event:
        return stop_event.wait(secs)
    time.sleep(secs)
    return False

def download_audio(url, save_path, retry=3, status_cb=None, stop_event=None,
                   connect_timeout=10, read_timeout=60, segments=2):
    temp_path = save_path + '.tmp'
    headers = {
        'User-Agent': BROWSER_UA,
        'Referer': url,
        'Accept': '*/*',
        'Accept-Encoding': 'identity',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    last_err = ['']

    def _single_download():
        r = requests.get(url, headers=headers, stream=True,
                         timeout=(connect_timeout, read_timeout))
        ct = (r.headers.get('Content-Type') or '').lower()
        if r.status_code in (429, 503) or 'json' in ct:
            r.close()
            return 'rate_limited'
        r.raise_for_status()
        written = 0
        with open(temp_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                if stop_event and stop_event.is_set():
                    raise InterruptedError()
                f.write(chunk)
                written += len(chunk)
        if written == 0:
            raise Exception('下载字节数为 0')
        os.replace(temp_path, save_path)
        return 'ok'

    for attempt in range(retry):
        if stop_event and stop_event.is_set():
            return False, '已取消'
        part_files = []
        try:
            total, accept_range, ct = _probe_file(
                url, headers, (connect_timeout, read_timeout))

            if ct == 'rate_limited':
                wait_secs = 30 * (attempt + 1)
                if status_cb:
                    status_cb('服务端限流(503), 等待 %d 秒...' % wait_secs)
                if _interruptible_sleep(stop_event, wait_secs):
                    return False, '已取消'
                continue

            if 'html' in ct or 'json' in ct or 'text/' in ct:
                raise Exception('返回非音频: %s' % ct)
            if total is None or total == 0:
                raise Exception('未知文件大小')

            if accept_range and total >= 1024 * 1024 and segments > 1:
                seg_size = (total + segments - 1) // segments
                plans = []
                for i in range(segments):
                    s = i * seg_size
                    e = min(s + seg_size - 1, total - 1)
                    if s > e:
                        break
                    p = '%s.part%d' % (temp_path, i)
                    part_files.append(p)
                    plans.append((s, e, p))

                results = {}
                rate_limited = False
                with ThreadPoolExecutor(max_workers=len(plans)) as ex:
                    futures = {}
                    for s, e, p in plans:
                        f = ex.submit(_download_range, url, headers,
                                      s, e, p, stop_event,
                                      (connect_timeout, read_timeout))
                        futures[f] = (s, e, p)
                    for f in futures:
                        s, e, p = futures[f]
                        results[p] = f.result()
                        if results[p] == -2:
                            rate_limited = True

                if rate_limited:
                    if status_cb:
                        status_cb('分段触发限流, 降级单连接...')
                    for p in part_files:
                        if os.path.exists(p):
                            try:
                                os.remove(p)
                            except OSError:
                                pass
                    part_files = []
                    r = _single_download()
                    if r == 'rate_limited':
                        raise Exception('单连接也被限流')
                    return True, ''

                written_total = 0
                for s, e, p in plans:
                    n = results.get(p, -1)
                    if n < 0:
                        raise Exception('分段下载失败')
                    written_total += n

                if written_total != total:
                    raise Exception('分段大小不一致 %d/%d'
                                    % (written_total, total))

                with open(temp_path, 'wb') as out:
                    for p in part_files:
                        with open(p, 'rb') as f:
                            while True:
                                buf = f.read(65536)
                                if not buf:
                                    break
                                out.write(buf)

                os.replace(temp_path, save_path)
                return True, ''

            r = _single_download()
            if r == 'rate_limited':
                raise Exception('单连接被限流')
            return True, ''

        except InterruptedError:
            return False, '已取消'
        except Exception as e:
            last_err[0] = str(e) or '下载失败'
            if attempt == retry - 1:
                return False, last_err[0]
            if _interruptible_sleep(stop_event, 10):
                return False, '已取消'
        finally:
            for p in [temp_path] + part_files:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
    return False, last_err[0] or '下载失败'

class DownloadApp:

    def __init__(self, root):
        self.root = root
        self.root.title('拾音盘下载器')
        self.root.geometry('1100x660')
        self.root.minsize(1100, 660)

        self.search_results = []
        self.current_book = None
        self.current_chapters = []
        self.current_book_title = ''
        self.current_book_narrator = ''
        self.download_folder = tk.StringVar(value=os.path.join(APP_DIR, 'syp'))

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
        self.session_id = 0
        self.load_token = 0

        self._err_lock = threading.Lock()
        self._err_fp = None

        self._init_log_file()
        self.create_widgets()
        self.client = SourceClient(status_cb=self.status, error_cb=self.error)
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
        ttk.Label(top, text='搜索:').pack(side=tk.LEFT, padx=5)
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
        result_frame = ttk.Frame(left)
        result_frame.pack(fill=tk.BOTH, expand=True)
        
        self.result_tree = ttk.Treeview(result_frame, columns=('idx', 'title', 'narrator'),
                                        show='headings', height=20)
        self.result_tree.heading('idx', text='序号', anchor=tk.CENTER)
        self.result_tree.heading('title', text='书名', anchor=tk.CENTER)
        self.result_tree.heading('narrator', text='演播', anchor=tk.CENTER)
        
        self.result_tree.column('idx', width=60, minwidth=50, anchor=tk.CENTER, stretch=False)
        self.result_tree.column('title', width=280, minwidth=200, anchor=tk.CENTER, stretch=True)
        self.result_tree.column('narrator', width=160, minwidth=100, anchor=tk.CENTER, stretch=True)
        
        self.result_tree.bind('<<TreeviewSelect>>', self.on_book_select)
        
        sl = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=sl.set)
        self.result_tree.grid(row=0, column=0, sticky='nsew')
        sl.grid(row=0, column=1, sticky='ns')
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        pane.add(left, weight=1)

        right = ttk.LabelFrame(pane, text='章节列表')
        ctrl_wrap = ttk.Frame(right)
        ctrl_wrap.pack(fill=tk.X, pady=5)
        ctrl = ttk.Frame(ctrl_wrap)
        ctrl.pack(anchor=tk.CENTER)
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

        chap_frame = ttk.Frame(right)
        chap_frame.pack(fill=tk.BOTH, expand=True)
        
        self.chapter_tree = ttk.Treeview(chap_frame, columns=('index', 'title', 'status'),
                                         show='headings', selectmode='none')
        self.chapter_tree.heading('index', text='序号', anchor=tk.CENTER)
        self.chapter_tree.heading('title', text='章节名', anchor=tk.CENTER)
        self.chapter_tree.heading('status', text='状态', anchor=tk.CENTER)
        
        self.chapter_tree.column('index', width=60, minwidth=50, anchor=tk.CENTER, stretch=False)
        self.chapter_tree.column('title', width=300, minwidth=200, anchor=tk.CENTER, stretch=True)
        self.chapter_tree.column('status', width=130, minwidth=100, anchor=tk.CENTER, stretch=False)
        
        sr = ttk.Scrollbar(chap_frame, orient=tk.VERTICAL, command=self.chapter_tree.yview)
        self.chapter_tree.configure(yscrollcommand=sr.set)
        self.chapter_tree.grid(row=0, column=0, sticky='nsew')
        sr.grid(row=0, column=1, sticky='ns')
        chap_frame.rowconfigure(0, weight=1)
        chap_frame.columnconfigure(0, weight=1)
        pane.add(right, weight=1)

        stats = ttk.Frame(self.root)
        stats.pack(fill=tk.X, pady=(0, 5), padx=10)
        self.total_label = ttk.Label(stats, text='总数: 0', font=('', 10))
        self.total_label.pack(side=tk.LEFT, padx=10, expand=True)
        self.completed_label = ttk.Label(stats, text='完成: 0', font=('', 10))
        self.completed_label.pack(side=tk.LEFT, padx=10, expand=True)
        self.failed_label = ttk.Label(stats, text='失败: 0', font=('', 10))
        self.failed_label.pack(side=tk.LEFT, padx=10, expand=True)

        self.status_var = tk.StringVar(value='就绪')
        status_bar = ttk.Frame(self.root)
        status_bar.pack(fill=tk.X, padx=10, pady=(0, 10))
        tk.Label(status_bar, textvariable=self.status_var, anchor=tk.W,
                 relief=tk.SUNKEN, bd=1, padx=6, pady=3).pack(fill=tk.X)

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

    def status(self, message):
        def _ui():
            if self.destroyed:
                return
            try:
                self.status_var.set(message)
            except tk.TclError:
                pass
        if threading.current_thread() is threading.main_thread():
            _ui()
        else:
            try:
                self.root.after(0, _ui)
            except tk.TclError:
                pass

    def error(self, message):
        line = '%s [ERROR] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), message)
        with self._err_lock:
            try:
                if self._err_fp is None:
                    self._err_fp = open(os.path.join(APP_DIR, LOG_FILE), 'a', encoding='utf-8')
                self._err_fp.write(line)
                self._err_fp.flush()
            except Exception:
                pass
        self.status('错误: %s' % message)

    @staticmethod
    def _record_status(entry):
        if isinstance(entry, dict):
            return entry.get('status', '')
        return entry or ''

    @staticmethod
    def _record_err(entry):
        if isinstance(entry, dict):
            return entry.get('err', '')
        return ''

    @classmethod
    def _is_done(cls, entry):
        return cls._record_status(entry) == '完成'

    @classmethod
    def _normalize_record(cls, record):
        normalized = {}
        if not isinstance(record, dict):
            return normalized
        for k, v in record.items():
            if isinstance(v, dict):
                normalized[k] = {'status': v.get('status', ''), 'err': v.get('err', '')}
            else:
                normalized[k] = {'status': str(v), 'err': ''}
        return normalized

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
        self.status('搜索: %s' % keyword)
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self.chapter_tree.delete(*self.chapter_tree.get_children())
        self.search_results = []
        self.current_book = None
        with self.chapters_lock:
            self.current_chapters = []
        with self.chapter_items_lock:
            self.chapter_items.clear()
        self.update_completed_failed(0, 0)
        self.update_interval_total()

        def do_search():
            try:
                books = self.client.search(keyword)
            except Exception as e:
                self.error('搜索异常: %s' % e)
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
            self.status('未找到书籍')
            return
        for idx, b in enumerate(books, start=1):
            self.result_tree.insert('', tk.END, values=(idx, b['title'], b['narrator']),
                                    iid=str(idx))
        self.status('找到 %d 本书籍' % len(books))

    def on_book_select(self, event):
        should_stop = False
        with self.downloading_lock:
            if self.downloading:
                if not messagebox.askyesno('提示', '当前有下载任务, 是否停止并切换书籍?'):
                    return
                should_stop = True
        if should_stop:
            self.stop_download_internal()
            with self.downloading_lock:
                still = self.downloading
            if still:
                self.status('旧下载任务尚未完全停止')
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
        self.status('加载: %s' % book['title'])
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

        self.load_token += 1
        token = self.load_token

        def load():
            self.status('正在获取章节列表 (首次提取最长 12 秒)...')
            try:
                chapters = self.client.get_chapters(book)
            except Exception as e:
                self.error('加载章节失败: %s' % e)
                chapters = []
            try:
                self.root.after(0, self._on_chapters_loaded, chapters, token)
            except tk.TclError:
                pass

        threading.Thread(target=load, daemon=True).start()

    def _on_chapters_loaded(self, chapters, token):
        if token != self.load_token:
            return
        try:
            self.result_tree.config(selectmode='browse')
        except tk.TclError:
            pass
        self.update_chapters(chapters)

    def stop_download_internal(self):
        self.stop_event.set()
        self.paused_event.clear()
        with self.downloading_lock:
            self.session_id += 1
            self.downloading = False
        if self.download_thread and self.download_thread.is_alive():
            self.download_thread.join(timeout=5)
            if self.download_thread.is_alive():
                self.status('下载线程仍在收尾...')
                return
        self.download_thread = None
        self.stop_event.clear()

        def _restore_ui():
            try:
                self.download_btn.config(state='normal')
                self.pause_btn.config(text='暂停')
            except tk.TclError:
                pass
        if threading.current_thread() is threading.main_thread():
            _restore_ui()
        else:
            try:
                self.root.after(0, _restore_ui)
            except tk.TclError:
                pass
        self.status('已停止下载任务')

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
            self.status('未找到章节')
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
        self.status('获取到 %d 个章节' % len(chapters))
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
        save_root = self.download_folder.get() or os.path.join(APP_DIR, 'syp')
        json_path = os.path.join(save_root, folder_name, '%s.json' % folder_name)
        self.json_path = json_path
        completed, failed = 0, 0
        if os.path.exists(json_path):
            try:
                with self.json_lock:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        record = json.load(f)
                normalized = self._normalize_record(record)
                for entry in normalized.values():
                    st = self._record_status(entry)
                    if st == '完成':
                        completed += 1
                    elif st == '失败':
                        failed += 1
                with self.record_lock:
                    self.record_cache.clear()
                    self.record_cache.update(normalized)
            except Exception as e:
                self.error('读取记录失败: %s' % e)
                with self.record_lock:
                    self.record_cache.clear()
        else:
            with self.record_lock:
                self.record_cache.clear()
        self.update_completed_failed(completed, failed)
        self._apply_record_to_chapter_tree()

    def _apply_record_to_chapter_tree(self):
        with self.record_lock:
            snapshot = dict(self.record_cache)
        if not snapshot:
            return
        with self.chapter_items_lock:
            items = dict(self.chapter_items)
        if not items:
            return

        def _update():
            for key, entry in snapshot.items():
                if not key.isdigit():
                    continue
                item_id = items.get(int(key))
                if not item_id:
                    continue
                st = self._record_status(entry)
                err = self._record_err(entry)
                text = st
                if st == '失败' and err:
                    text = '失败: %s' % err
                try:
                    self.chapter_tree.set(item_id, 'status', text)
                except tk.TclError:
                    pass

        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            try:
                self.root.after(0, _update)
            except tk.TclError:
                pass

    def update_completed_failed(self, completed, failed):
        def _update():
            try:
                self.completed_label.config(text='完成: %d' % completed)
                self.failed_label.config(text='失败: %d' % failed)
            except tk.TclError:
                pass
        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            try:
                self.root.after(0, _update)
            except tk.TclError:
                pass

    def _update_stats_from_cache(self):
        with self.record_lock:
            completed = sum(1 for e in self.record_cache.values() if self._record_status(e) == '完成')
            failed = sum(1 for e in self.record_cache.values() if self._record_status(e) == '失败')
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
                messagebox.showinfo('提示', '已有下载任务进行中')
                return
        with self.chapters_lock:
            if not self.current_chapters:
                messagebox.showinfo('提示', '请先选择一本书籍')
                return
        start, end = self.get_range_values()
        if start is None:
            messagebox.showerror('错误', '请输入有效的数字范围')
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
            failed_nums = sorted([int(k) for k, v in self.record_cache.items()
                                  if self._record_status(v) == '失败' and k.isdigit()])
        if not failed_nums:
            messagebox.showinfo('提示', '没有失败的章节需要重试')
            return
        self.status('重试失败章节: %d 个' % len(failed_nums))
        self._start_download_custom(failed_nums)

    def _start_download(self, start, end):
        self.status('下载范围: 第 %d 章 至 第 %d 章 (并发 %d)' % (start, end, self.max_workers))
        with self.downloading_lock:
            self.downloading = True
            self.session_id += 1
            sid = self.session_id
        self.paused_event.clear()
        self.stop_event.clear()
        self.download_btn.config(state='disabled')
        self.pause_btn.config(text='暂停')
        self.download_thread = threading.Thread(target=self.download_range_worker,
                                                args=(start, end, sid), daemon=True)
        self.download_thread.start()

    def _start_download_custom(self, indices):
        with self.downloading_lock:
            self.downloading = True
            self.session_id += 1
            sid = self.session_id
        self.paused_event.clear()
        self.stop_event.clear()
        self.download_btn.config(state='disabled')
        self.pause_btn.config(text='暂停')
        self.download_thread = threading.Thread(target=self.download_custom_worker,
                                                args=(indices, sid), daemon=True)
        self.download_thread.start()

    def toggle_pause(self):
        if not self.downloading:
            messagebox.showinfo('提示', '当前没有正在进行的下载任务')
            return
        if self.paused_event.is_set():
            self.paused_event.clear()
            self.pause_btn.config(text='暂停')
            self.status('恢复下载')
        else:
            self.paused_event.set()
            self.pause_btn.config(text='恢复')
            self.status('已暂停, 完成当前任务后停止新任务')

    def download_one_chapter(self, chap_num, chap, save_dir, json_path, sid):
        if sid != self.session_id or self.stop_event.is_set():
            return False, '取消'
        chap_title = chap['title']
        key = str(chap_num)

        with self.record_lock:
            entry = self.record_cache.get(key)
            if entry and self._is_done(entry):
                self.update_chapter_status(chap_num, '完成')
                return True, '完成'

        filename = '%s.m4a' % sanitize_name(chap_title)
        filepath = os.path.join(save_dir, filename)

        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            self.status('跳过已存在: %s' % filename)
            if sid != self.session_id:
                return False, '取消'
            with self.record_lock:
                self.record_cache[key] = {'status': '完成', 'err': ''}
            self.update_chapter_status(chap_num, '完成')
            self.flush_record(json_path)
            self._update_stats_from_cache()
            return True, '完成'

        self.status('处理: %s' % chap_title)

        audio_url = ''
        url_err = ''
        try:
            audio_url = self.client.get_audio_url(chap)
        except Exception as e:
            url_err = str(e) or '获取链接失败'

        if sid != self.session_id or self.stop_event.is_set():
            return False, '取消'

        if not audio_url:
            self.status('失败: %s (%s)' % (chap_title, url_err))
            with self.record_lock:
                self.record_cache[key] = {'status': '失败', 'err': url_err}
            self.update_chapter_status(chap_num, '失败', url_err)
            self.flush_record(json_path)
            self._update_stats_from_cache()
            return False, url_err

        self.status('下载: %s' % chap_title)
        success, err = download_audio(audio_url, filepath,
                                      status_cb=self.status, stop_event=self.stop_event)
        if sid != self.session_id:
            return False, '取消'

        if not success:
            if self.stop_event.is_set() or err == '已取消':
                return False, '取消'
            status_entry = {'status': '失败', 'err': err or '下载失败'}
            self.status('失败: %s (%s)' % (chap_title, err or '下载失败'))
            self.update_chapter_status(chap_num, '失败', err or '下载失败')
        else:
            status_entry = {'status': '完成', 'err': ''}
            self.status('完成: %s' % filename)
            self.update_chapter_status(chap_num, '完成')

        with self.record_lock:
            self.record_cache[key] = status_entry
        self.flush_record(json_path)
        self._update_stats_from_cache()
        return success, status_entry.get('err') or status_entry['status']

    def flush_record(self, json_path=None):
        path = json_path or self.json_path
        if not path:
            return
        with self.json_lock:
            with self.record_lock:
                if not self.record_cache:
                    return
                try:
                    to_save = dict(self.record_cache)
                    tmp = path + '.tmp'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        json.dump(to_save, f, ensure_ascii=False)
                    os.replace(tmp, path)
                except Exception as e:
                    self.error('保存记录失败: %s' % e)

    def _prepare_context(self):
        save_root = self.download_folder.get() or os.path.join(APP_DIR, 'syp')
        with self.chapters_lock:
            book_title = self.current_book_title
            book_narrator = self.current_book_narrator
            current_chapters = list(self.current_chapters)
        folder_name = sanitize_name('%s-%s' % (book_title, book_narrator))
        save_dir = os.path.join(save_root, folder_name)
        os.makedirs(save_dir, exist_ok=True)
        json_path = os.path.join(save_dir, '%s.json' % folder_name)
        if os.path.exists(json_path):
            try:
                with self.json_lock:
                    with open(json_path, 'r', encoding='utf-8') as f:
                        old_record = json.load(f)
                normalized = self._normalize_record(old_record)
                with self.record_lock:
                    self.record_cache.update(normalized)
            except Exception as e:
                self.error('读取记录失败: %s' % e)
        return save_dir, current_chapters, json_path

    def _run_pending(self, pending, save_dir, json_path, sid):
        if not pending:
            self.status('范围内没有需要下载的章节')
            return

        if self.max_workers == 1:
            for chap_num, chap in pending:
                if sid != self.session_id or self.stop_event.is_set():
                    break
                if self.paused_event.is_set():
                    self.status('已暂停, 按[恢复]继续')
                    while self.paused_event.is_set() and not self.stop_event.is_set():
                        time.sleep(1)
                    if self.stop_event.is_set() or sid != self.session_id:
                        break
                self.download_one_chapter(chap_num, chap, save_dir, json_path, sid)
            return

        pending_index = 0
        active = {}
        executor = ThreadPoolExecutor(max_workers=self.max_workers)

        def submit_next():
            nonlocal pending_index
            while pending_index < len(pending):
                if self.paused_event.is_set() or self.stop_event.is_set() or sid != self.session_id:
                    return False
                chap_num, chap = pending[pending_index]
                future = executor.submit(self.download_one_chapter, chap_num, chap,
                                         save_dir, json_path, sid)
                active[future] = (chap_num, chap)
                pending_index += 1
                return True
            return False

        for _ in range(self.max_workers):
            if not submit_next():
                break

        while active or pending_index < len(pending):
            if self.stop_event.is_set() or sid != self.session_id:
                break
            if active:
                done, _ = wait(list(active.keys()), timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    chap_num, _c = active.pop(future)
                    try:
                        future.result(timeout=0)
                    except Exception as e:
                        self.error('章节 %d 下载异常: %s' % (chap_num, e))
            if self.paused_event.is_set() and not active:
                time.sleep(0.5)
                continue
            while len(active) < self.max_workers:
                if not submit_next():
                    break

        if self.stop_event.is_set() or sid != self.session_id:
            executor.shutdown(wait=False)
        else:
            executor.shutdown(wait=True)

    def download_range_worker(self, start, end, sid):
        try:
            if sid != self.session_id:
                return
            save_dir, current_chapters, json_path = self._prepare_context()
            with self.record_lock:
                snapshot = self.record_cache.copy()
            pending = []
            for i in range(start, end + 1):
                if i - 1 >= len(current_chapters):
                    continue
                entry = snapshot.get(str(i))
                if entry is None or not self._is_done(entry):
                    pending.append((i, current_chapters[i - 1]))
            self._run_pending(pending, save_dir, json_path, sid)
            if sid == self.session_id:
                self.flush_record(json_path)
        except Exception as e:
            self.error('下载工作线程异常: %s' % e)
        finally:
            self.download_finished(sid)

    def download_custom_worker(self, indices, sid):
        try:
            if sid != self.session_id:
                return
            save_dir, current_chapters, json_path = self._prepare_context()
            pending = []
            for i in indices:
                if 1 <= i <= len(current_chapters):
                    pending.append((i, current_chapters[i - 1]))
            self._run_pending(pending, save_dir, json_path, sid)
            if sid == self.session_id:
                self.flush_record(json_path)
        except Exception as e:
            self.error('下载工作线程异常: %s' % e)
        finally:
            self.download_finished(sid)

    def download_finished(self, sid=None):
        if sid is not None and sid != self.session_id:
            return
        if self.destroyed:
            return
        stopped = self.stop_event.is_set()
        with self.downloading_lock:
            self.downloading = False
        self.paused_event.clear()
        self.stop_event.clear()

        def _restore_ui():
            try:
                self.download_btn.config(state='normal')
                self.pause_btn.config(text='暂停')
            except tk.TclError:
                pass
        try:
            self.root.after(0, _restore_ui)
        except tk.TclError:
            pass

        self.status('下载已停止' if stopped else '所有任务已完成')
        self._update_stats_from_cache()
        self.download_thread = None
        if self.close_waiting:
            self.close_waiting = False
            self.destroyed = True
            try:
                self.root.after(0, self.root.destroy)
            except tk.TclError:
                pass

    def update_chapter_status(self, chapter_num, status, err=''):
        if self.destroyed:
            return
        text = status
        if status == '失败' and err:
            text = '失败: %s' % err

        def _update():
            try:
                with self.chapter_items_lock:
                    item_id = self.chapter_items.get(chapter_num)
                if item_id:
                    self.chapter_tree.set(item_id, 'status', text)
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
            with self.downloading_lock:
                self.session_id += 1
            self.status('正在停止下载任务...')
            if self.download_thread and self.download_thread.is_alive():
                self.download_thread.join(timeout=3)
            self.flush_record()
        try:
            with self._err_lock:
                if self._err_fp:
                    self._err_fp.close()
                    self._err_fp = None
        except Exception:
            pass
        self.destroyed = True
        self.root.destroy()

def main():
    root = tk.Tk()
    app = DownloadApp(root)

    def _excepthook(exc_type, exc_value, exc_tb):
        msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            app.error('未捕获异常:\n%s' % msg)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _thread_excepthook(args):
        msg = ''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        try:
            app.error('线程异常:\n%s' % msg)
        except Exception:
            pass

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    root.mainloop()

if __name__ == '__main__':
    main()
