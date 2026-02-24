import os
import re
import json
import time
import threading
import requests
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright

class DownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("275听书网下载器")
        self.root.geometry("800x600")
        self.root.resizable(True, True)
        self.base_url = "https://m.i275.com"
        self.output_dir = "downloads"
        os.makedirs(self.output_dir, exist_ok=True)
        self.create_widgets()
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.download_queue = []
        self.is_downloading = False
        self.init_browser()

    def init_browser(self):
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=True)
            self.context = self.browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            self.page = self.context.new_page()
            self.log("浏览器初始化成功")
        except Exception as e:
            self.log(f"浏览器初始化失败: {e}")
            messagebox.showerror("错误", f"浏览器初始化失败: {e}")

    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(main_frame)
        top_frame.pack(fill=tk.X, pady=5)

        ttk.Label(top_frame, text="关键词:").pack(side=tk.LEFT)
        self.keyword_entry = ttk.Entry(top_frame, width=30)
        self.keyword_entry.pack(side=tk.LEFT, padx=5)
        self.keyword_entry.bind("<Return>", lambda e: self.search_books())

        self.search_btn = ttk.Button(top_frame, text="搜索", command=self.search_books)
        self.search_btn.pack(side=tk.LEFT, padx=5)

        self.hot_btn = ttk.Button(top_frame, text="热门书籍", command=self.get_hot_books)
        self.hot_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(top_frame, text="停止下载", command=self.stop_download, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        mid_frame = ttk.Frame(main_frame)
        mid_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        list_frame = ttk.LabelFrame(mid_frame, text="书籍列表", padding="5")
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.book_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set, height=15)
        self.book_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.book_listbox.yview)
        self.book_listbox.bind('<<ListboxSelect>>', self.on_book_select)

        detail_frame = ttk.LabelFrame(mid_frame, text="书籍详情", padding="5", width=300)
        detail_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        detail_frame.pack_propagate(False)

        self.detail_text = scrolledtext.ScrolledText(detail_frame, wrap=tk.WORD, height=12)
        self.detail_text.pack(fill=tk.BOTH, expand=True)

        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, pady=5)

        ttk.Label(bottom_frame, text="保存目录:").pack(side=tk.LEFT)
        self.dir_entry = ttk.Entry(bottom_frame, width=40)
        self.dir_entry.insert(0, self.output_dir)
        self.dir_entry.pack(side=tk.LEFT, padx=5)

        self.download_btn = ttk.Button(bottom_frame, text="下载选中书籍", command=self.start_download)
        self.download_btn.pack(side=tk.RIGHT)

        progress_frame = ttk.Frame(main_frame)
        progress_frame.pack(fill=tk.X, pady=5)

        self.progress = ttk.Progressbar(progress_frame, mode='determinate')
        self.progress.pack(fill=tk.X)

        self.status_label = ttk.Label(main_frame, text="就绪")
        self.status_label.pack()

        self.log_area = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, height=8)
        self.log_area.pack(fill=tk.BOTH, expand=True)

    def log(self, msg, level="info"):
        timestamp = time.strftime("%H:%M:%S")
        self.log_area.insert(tk.END, f"[{timestamp}] {msg}\n")
        self.log_area.see(tk.END)
        self.root.update_idletasks()

    def clear_list(self):
        self.book_listbox.delete(0, tk.END)

    def add_book_to_list(self, title, url, author=""):
        display = f"{title} - {author}"
        self.book_listbox.insert(tk.END, display)
        self.book_listbox.see(tk.END)

    def search_books(self):
        keyword = self.keyword_entry.get().strip()
        if not keyword:
            messagebox.showwarning("提示", "请输入关键词")
            return
        self.clear_list()
        threading.Thread(target=self._search_thread, args=(keyword,), daemon=True).start()

    def _search_thread(self, keyword):
        self.log(f"开始搜索: {keyword}")
        try:
            if not self.page:
                self.log("浏览器未初始化", "error")
                return
            search_url = f"{self.base_url}/search.php?q={keyword}"
            self.page.goto(search_url, timeout=30000)
            self.page.wait_for_selector('a[href^="/book/"]', timeout=10000)
            items = self.page.query_selector_all('a[href^="/book/"].flex')
            books = []
            for item in items:
                html = item.inner_html()
                title_match = re.search(r'<h3[^>]*>([^<]+)</h3>', html)
                title = title_match.group(1) if title_match else "未知"
                href = item.get_attribute('href')
                url = urljoin(self.base_url, href)
                author = "未知"
                author_match = re.search(r'<span[^>]*class="bg-gray-100[^"]*"[^>]*>演播</span>\s*([^<]+)', html)
                if author_match:
                    author = author_match.group(1).strip()
                else:
                    author_match = re.search(r'<span[^>]*class="bg-gray-100[^"]*"[^>]*>作者</span>\s*([^<]+)', html)
                    if author_match:
                        author = author_match.group(1).strip()
                books.append((title, url, author))
            if books:
                self.root.after(0, lambda: self._update_book_list(books))
                self.log(f"找到 {len(books)} 本书")
            else:
                self.log("未找到相关书籍", "warning")
        except Exception as e:
            self.log(f"搜索出错: {e}", "error")

    def _update_book_list(self, books):
        for title, url, author in books:
            self.book_listbox.insert(tk.END, f"{title} - {author}")
            self.book_listbox.see(tk.END)

    def get_hot_books(self):
        self.clear_list()
        threading.Thread(target=self._hot_thread, daemon=True).start()

    def _hot_thread(self):
        self.log("获取热门书籍...")
        try:
            self.page.goto(self.base_url, timeout=30000)
            self.page.wait_for_selector('a[href^="/book/"]', timeout=10000)
            items = self.page.query_selector_all('a[href^="/book/"]')
            books = []
            seen = set()
            for item in items:
                href = item.get_attribute('href')
                if href in seen:
                    continue
                seen.add(href)
                title_elem = item.query_selector('.font-medium')
                title = title_elem.inner_text().strip() if title_elem else "未知"
                author_elem = item.query_selector('.text-gray-500')
                author = author_elem.inner_text().strip() if author_elem else ""
                url = urljoin(self.base_url, href)
                books.append((title, url, author))
            if books:
                self.root.after(0, lambda: self._update_book_list(books[:20]))
                self.log(f"获取 {len(books[:20])} 本热门书籍")
            else:
                self.log("未获取到热门书籍", "warning")
        except Exception as e:
            self.log(f"获取热门出错: {e}", "error")

    def on_book_select(self, event):
        selection = self.book_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        text = self.book_listbox.get(index)
        url = None
        try:
            with open("temp_books.json", "r") as f:
                books = json.load(f)
                if index < len(books):
                    url = books[index]["url"]
        except:
            pass
        if not url:
            self.log("无法获取书籍详情", "warning")
            return
        threading.Thread(target=self._fetch_detail, args=(url,), daemon=True).start()

    def _fetch_detail(self, url):
        try:
            self.page.goto(url, timeout=30000)
            self.page.wait_for_selector('h1', timeout=10000)
            title = self.page.query_selector('h1').inner_text().strip()
            author = "未知"
            author_elem = self.page.query_selector('span.text-gray-800')
            if author_elem:
                author = author_elem.inner_text().strip()
            chapters = []
            chapter_links = self.page.query_selector_all('a[href^="/play/"]')
            seen = set()
            for link in chapter_links:
                href = link.get_attribute('href')
                if href in seen:
                    continue
                seen.add(href)
                num_span = link.query_selector('span.text-xs')
                num = num_span.inner_text().replace('.','').strip() if num_span else str(len(chapters)+1)
                title_span = link.query_selector('span.text-sm')
                chap_title = title_span.inner_text().strip() if title_span else f"第{num}集"
                chap_url = urljoin(self.base_url, href)
                chapters.append({"num": int(num), "title": chap_title, "url": chap_url})
            chapters.sort(key=lambda x: x["num"])
            info = {"title": title, "author": author, "url": url, "chapters": chapters}
            with open("temp_books.json", "w") as f:
                json.dump([info], f)
            self.root.after(0, lambda: self._show_detail(info))
        except Exception as e:
            self.log(f"获取详情出错: {e}", "error")

    def _show_detail(self, info):
        self.detail_text.delete(1.0, tk.END)
        self.detail_text.insert(tk.END, f"书名: {info['title']}\n")
        self.detail_text.insert(tk.END, f"作者: {info['author']}\n")
        self.detail_text.insert(tk.END, f"章节: {len(info['chapters'])}\n\n")
        for ch in info['chapters'][:10]:
            self.detail_text.insert(tk.END, f"{ch['num']}. {ch['title']}\n")
        if len(info['chapters']) > 10:
            self.detail_text.insert(tk.END, "...")

    def start_download(self):
        try:
            with open("temp_books.json", "r") as f:
                books = json.load(f)
                if not books:
                    messagebox.showwarning("提示", "请先选择一本书")
                    return
                book_info = books[0]
        except:
            messagebox.showwarning("提示", "请先选择一本书")
            return
        output = self.dir_entry.get().strip()
        if output:
            self.output_dir = output
        self.is_downloading = True
        self.download_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        threading.Thread(target=self._download_thread, args=(book_info,), daemon=True).start()

    def stop_download(self):
        self.is_downloading = False
        self.log("停止下载...")

    def _download_thread(self, book_info):
        self.log(f"开始下载: {book_info['title']}")
        book_dir = os.path.join(self.output_dir, book_info['title'])
        os.makedirs(book_dir, exist_ok=True)
        chapters = book_info['chapters']
        total = len(chapters)
        self.progress['maximum'] = total
        success = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {}
            for ch in chapters:
                if not self.is_downloading:
                    break
                future = executor.submit(self.download_one, ch, book_dir)
                futures[future] = ch
            for future in as_completed(futures):
                if not self.is_downloading:
                    break
                ch = futures[future]
                try:
                    if future.result():
                        success += 1
                        self.log(f"章节 {ch['num']} 完成")
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                    self.log(f"章节 {ch['num']} 失败: {e}", "error")
                self.progress['value'] = success + failed
                self.root.update_idletasks()
                time.sleep(1)
        self.is_downloading = False
        self.root.after(0, self._download_finished, success, failed)

    def download_one(self, chapter, book_dir):
        num = chapter['num']
        title = chapter['title']
        url = chapter['url']
        safe_title = re.sub(r'[\\/*?:"<>|]', '', title)
        filename = f"{str(num).zfill(4)}_{safe_title}.m4a"
        filepath = os.path.join(book_dir, filename)
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return True
        try:
            self.page.goto(url, timeout=30000)
            self.page.wait_for_load_state("networkidle")
            audio_url = None
            scripts = self.page.query_selector_all('script')
            for script in scripts:
                content = script.inner_html()
                if content and 'audio' in content and 'url' in content:
                    match = re.search(r'url:\s*[\'"]([^\'"]+\.m4a[^\'"]*)[\'"]', content)
                    if match:
                        audio_url = match.group(1)
                        break
            if not audio_url:
                audio_url = self.page.evaluate('''() => {
                    const audio = document.querySelector('audio');
                    return audio ? audio.src : null;
                }''')
            if not audio_url:
                self.log(f"章节 {num} 未找到音频地址", "error")
                return False
            if audio_url.startswith('//'):
                audio_url = 'https:' + audio_url
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': self.base_url,
                'Origin': self.base_url,
                'Accept': 'audio/webm,audio/ogg,audio/*'
            }
            resp = requests.get(audio_url, headers=headers, stream=True, timeout=30)
            resp.raise_for_status()
            total = int(resp.headers.get('content-length', 0))
            with open(filepath, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception as e:
            self.log(f"下载章节 {num} 异常: {e}", "error")
            if os.path.exists(filepath):
                os.remove(filepath)
            return False

    def _download_finished(self, success, failed):
        self.download_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.progress['value'] = 0
        messagebox.showinfo("完成", f"下载完成\n成功: {success}\n失败: {failed}")

    def on_closing(self):
        if self.is_downloading:
            if not messagebox.askyesno("确认", "下载正在进行，确定退出？"):
                return
        try:
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except:
            pass
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = DownloaderGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
