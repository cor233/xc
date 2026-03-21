# tyfm.py
import os
import json
import re
import time
import threading
import queue
from urllib.parse import urljoin
import requests
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

BASE_URL = "https://tingyou.fm"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TIMEOUT = 15
RETRY = 3
THREADS = 3
AUDIO_URL_TEMPLATES = [
    "https://file.tingyou8.vip/audio/{chapter_id}.mp3",
    "https://file.tingyou8.vip/audio/{chapter_id}.m4a",
    "https://file.tingyoufm.com/audio/{chapter_id}.mp3",
    "https://audio.tingyou.fm/{chapter_id}.mp3",
]
DOWNLOADED_IDS_FILE = "downloaded_ids.json"

def request_get(url, params=None):
    headers = {'User-Agent': USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        return resp
    except Exception as e:
        status = resp.status_code if 'resp' in locals() else 'N/A'
        raise Exception(f"Request failed: {e}, status: {status}")

def extract_nuxt_json(html):
    pattern = r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>'
    match = re.search(pattern, html, re.DOTALL)
    if not match:
        preview = html[:500].replace('\n', ' ')
        raise Exception(f"__NUXT_DATA__ not found. Page preview: {preview}")
    json_str = match.group(1).strip()
    json_str = json_str.replace('&quot;', '"').replace('&#39;', "'").replace('&amp;', '&')
    return json.loads(json_str)

def resolve_refs(data, root):
    """递归解析整数引用，将整数索引替换为 root[index]"""
    if isinstance(data, list):
        return [resolve_refs(item, root) for item in data]
    elif isinstance(data, dict):
        return {k: resolve_refs(v, root) for k, v in data.items()}
    elif isinstance(data, int) and 0 <= data < len(root):
        return resolve_refs(root[data], root)
    else:
        return data

def find_value_by_key(nuxt_data, target_key):
    def _search(obj):
        if isinstance(obj, dict):
            if target_key in obj:
                return obj[target_key]
            for v in obj.values():
                res = _search(v)
                if res is not None:
                    return res
        elif isinstance(obj, list):
            for item in obj:
                res = _search(item)
                if res is not None:
                    return res
        return None
    return _search(nuxt_data)

def search(keyword):
    url = urljoin(BASE_URL, "/search/result")
    params = {"keyword": keyword, "page": 1}
    html = request_get(url, params).text
    nuxt_data = extract_nuxt_json(html)
    # 确保 nuxt_data 是列表，用于解析引用
    if not isinstance(nuxt_data, list):
        nuxt_data = [nuxt_data]
    # 先解析整个数据中的引用
    resolved = resolve_refs(nuxt_data, nuxt_data)
    search_res = find_value_by_key(resolved, "searchResults")
    if not search_res:
        raise Exception("searchResults not found")
    if isinstance(search_res, dict):
        album_list = search_res.get("list", [])
    else:
        album_list = search_res if isinstance(search_res, list) else []
    results = []
    for item in album_list:
        # 如果 item 是整数，再次解析
        if isinstance(item, int):
            item = resolved[item] if item < len(resolved) else {}
        results.append({
            "id": item.get("id"),
            "title": item.get("title", ""),
            "author": item.get("author", ""),
            "teller": item.get("teller", ""),
            "cover": item.get("cover_url", ""),
            "count": item.get("count", 0),
            "status": item.get("status", 0),
        })
    return results

def get_album_chapters(album_id):
    url = urljoin(BASE_URL, f"/albums/{album_id}")
    html = request_get(url).text
    nuxt_data = extract_nuxt_json(html)
    if not isinstance(nuxt_data, list):
        nuxt_data = [nuxt_data]
    resolved = resolve_refs(nuxt_data, nuxt_data)
    chapters_key = f"album-chapters-{album_id}"
    chapters_data = find_value_by_key(resolved, chapters_key)
    if not chapters_data:
        chapters_data = find_value_by_key(resolved, "chapters")
    if not chapters_data:
        raise Exception("chapters data not found")
    if isinstance(chapters_data, dict) and "chapters" in chapters_data:
        chapters_list = chapters_data["chapters"]
    elif isinstance(chapters_data, list):
        chapters_list = chapters_data
    else:
        chapters_list = []
    chapters = []
    for ch in chapters_list:
        if isinstance(ch, int):
            ch = resolved[ch] if ch < len(resolved) else {}
        chapters.append({
            "id": ch.get("id"),
            "index": ch.get("index", 0),
            "title": ch.get("title", ""),
            "duration": ch.get("duration", 0),
        })
    return chapters

def download_audio(chapter_id, title, save_path):
    for template in AUDIO_URL_TEMPLATES:
        url = template.format(chapter_id=chapter_id)
        try:
            resp = requests.get(url, stream=True, timeout=TIMEOUT, headers={'User-Agent': USER_AGENT})
            if resp.status_code == 200:
                with open(save_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                return True
        except:
            continue
    return False

class DownloadWorker:
    def __init__(self, max_threads=THREADS, callback=None):
        self.queue = queue.Queue()
        self.max_threads = max_threads
        self.threads = []
        self.callback = callback
        self.running = False

    def start(self):
        self.running = True
        for _ in range(self.max_threads):
            t = threading.Thread(target=self._worker)
            t.daemon = True
            t.start()
            self.threads.append(t)

    def add_task(self, task):
        self.queue.put(task)

    def _worker(self):
        while self.running:
            try:
                task = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            chapter_id = task['chapter_id']
            title = task['title']
            save_path = task['save_path']
            success = download_audio(chapter_id, title, save_path)
            message = "Success" if success else "Failed"
            if self.callback:
                self.callback(task, success, message)
            self.queue.task_done()

    def stop(self):
        self.running = False
        for t in self.threads:
            t.join(timeout=1)

class TingYouApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TingYou FM Downloader")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)

        self.download_dir = os.path.join(os.getcwd(), "downloads")
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)

        self.downloaded_ids = self.load_downloaded_ids()
        self.search_results = []
        self.worker = None

        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_ui(self):
        top_frame = ttk.Frame(self.root, padding="5")
        top_frame.pack(fill=tk.X)

        ttk.Label(top_frame, text="Keyword:").pack(side=tk.LEFT, padx=5)
        self.keyword_var = tk.StringVar()
        self.entry_keyword = ttk.Entry(top_frame, textvariable=self.keyword_var, width=40)
        self.entry_keyword.pack(side=tk.LEFT, padx=5)
        self.btn_search = ttk.Button(top_frame, text="Search", command=self.search)
        self.btn_search.pack(side=tk.LEFT, padx=5)

        ttk.Label(top_frame, text="Download Dir:").pack(side=tk.LEFT, padx=5)
        self.dir_var = tk.StringVar(value=self.download_dir)
        self.entry_dir = ttk.Entry(top_frame, textvariable=self.dir_var, width=30)
        self.entry_dir.pack(side=tk.LEFT, padx=5)
        self.btn_browse = ttk.Button(top_frame, text="Browse", command=self.browse_dir)
        self.btn_browse.pack(side=tk.LEFT, padx=5)

        columns = ("id", "title", "author", "teller", "count", "status")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings", height=12)
        self.tree.heading("id", text="ID")
        self.tree.heading("title", text="Title")
        self.tree.heading("author", text="Author")
        self.tree.heading("teller", text="Teller")
        self.tree.heading("count", text="Chapters")
        self.tree.heading("status", text="Status")
        self.tree.column("id", width=80)
        self.tree.column("title", width=300)
        self.tree.column("author", width=120)
        self.tree.column("teller", width=120)
        self.tree.column("count", width=60)
        self.tree.column("status", width=80)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.tree.bind("<<TreeviewSelect>>", self.on_album_select)

        btn_frame = ttk.Frame(self.root, padding="5")
        btn_frame.pack(fill=tk.X)
        self.btn_download = ttk.Button(btn_frame, text="Download Selected", command=self.download_album, state=tk.DISABLED)
        self.btn_download.pack(side=tk.LEFT, padx=5)
        self.btn_download_all = ttk.Button(btn_frame, text="Download All", command=self.download_all)
        self.btn_download_all.pack(side=tk.LEFT, padx=5)
        self.progress = ttk.Progressbar(btn_frame, mode='indeterminate')
        self.progress.pack(side=tk.LEFT, padx=10, fill=tk.X, expand=True)

        log_frame = ttk.LabelFrame(self.root, text="Log", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, state=tk.NORMAL)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)

    def log(self, msg):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.root.update_idletasks()

    def browse_dir(self):
        dir_path = filedialog.askdirectory(initialdir=self.download_dir)
        if dir_path:
            self.download_dir = dir_path
            self.dir_var.set(dir_path)
            self.log(f"Download directory set to: {dir_path}")

    def search(self):
        keyword = self.keyword_var.get().strip()
        if not keyword:
            messagebox.showwarning("Warning", "Please enter a keyword")
            return
        self.log(f"Searching: {keyword}")
        self.btn_search.config(state=tk.DISABLED)
        try:
            results = search(keyword)
            self.search_results = results
            for item in self.tree.get_children():
                self.tree.delete(item)
            for r in results:
                status_text = "Ongoing" if r['status'] == 1 else "Completed"
                self.tree.insert("", tk.END, values=(
                    r['id'], r['title'], r['author'], r['teller'], r['count'], status_text
                ))
            self.log(f"Search completed, found {len(results)} albums")
        except Exception as e:
            self.log(f"Search failed: {e}")
            messagebox.showerror("Error", f"Search failed: {e}")
        finally:
            self.btn_search.config(state=tk.NORMAL)

    def on_album_select(self, event):
        selected = self.tree.selection()
        self.btn_download.config(state=tk.NORMAL if selected else tk.DISABLED)

    def get_selected_album(self):
        selected = self.tree.selection()
        if not selected:
            return None
        item = self.tree.item(selected[0])
        values = item['values']
        for album in self.search_results:
            if album['id'] == values[0]:
                return album
        return None

    def download_album(self):
        album = self.get_selected_album()
        if not album:
            return
        self.log(f"Preparing to download album: {album['title']} (ID: {album['id']})")
        self.download_album_by_id(album)

    def download_all(self):
        if not self.search_results:
            messagebox.showinfo("Info", "No search results. Please search first.")
            return
        self.log("Starting download of all albums in search results...")
        for album in self.search_results:
            self.download_album_by_id(album)

    def download_album_by_id(self, album):
        album_dir = os.path.join(self.download_dir, f"{album['id']}_{self.safe_filename(album['title'])}")
        os.makedirs(album_dir, exist_ok=True)
        try:
            chapters = get_album_chapters(album['id'])
            self.log(f"Album {album['title']}: fetched {len(chapters)} chapters")
        except Exception as e:
            self.log(f"Album {album['title']} failed to fetch chapters: {e}")
            return
        tasks = []
        for ch in chapters:
            ch_id = ch['id']
            if ch_id in self.downloaded_ids:
                self.log(f"Chapter {ch['index']} already downloaded, skipping")
                continue
            safe_title = self.safe_filename(ch['title'])
            filename = f"{ch['index']:03d}_{safe_title}.mp3"
            save_path = os.path.join(album_dir, filename)
            if os.path.exists(save_path):
                self.log(f"File exists: {filename}, skipping")
                self.downloaded_ids.add(ch_id)
                continue
            tasks.append({
                "chapter_id": ch_id,
                "title": ch['title'],
                "save_path": save_path,
                "album_title": album['title']
            })
        if not tasks:
            self.log(f"Album {album['title']}: no new chapters to download")
            return
        self.log(f"Album {album['title']}: added {len(tasks)} download tasks")
        self.start_download(tasks)

    def start_download(self, tasks):
        if self.worker and self.worker.running:
            for task in tasks:
                self.worker.add_task(task)
        else:
            self.worker = DownloadWorker(callback=self.on_download_complete)
            self.worker.start()
            for task in tasks:
                self.worker.add_task(task)

    def on_download_complete(self, task, success, message):
        ch_id = task['chapter_id']
        if success:
            self.log(f"✓ {task['album_title']} - {task['title']}: {message}")
            self.downloaded_ids.add(ch_id)
            self.save_downloaded_ids()
        else:
            self.log(f"✗ {task['album_title']} - {task['title']}: {message}")

    def safe_filename(self, text):
        invalid = '<>:"/\\|?*'
        for ch in invalid:
            text = text.replace(ch, '_')
        text = text.strip().replace(' ', '_')
        if len(text) > 100:
            text = text[:100]
        return text

    def load_downloaded_ids(self):
        record_file = os.path.join(self.download_dir, DOWNLOADED_IDS_FILE)
        if os.path.exists(record_file):
            try:
                with open(record_file, 'r', encoding='utf-8') as f:
                    return set(json.load(f))
            except:
                pass
        return set()

    def save_downloaded_ids(self):
        record_file = os.path.join(self.download_dir, DOWNLOADED_IDS_FILE)
        with open(record_file, 'w', encoding='utf-8') as f:
            json.dump(list(self.downloaded_ids), f, ensure_ascii=False)

    def on_close(self):
        if self.worker:
            self.worker.stop()
        self.root.destroy()

def main():
    root = tk.Tk()
    app = TingYouApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
