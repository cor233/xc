import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import requests
import threading
import os
import re
from urllib.parse import urljoin, quote
from bs4 import BeautifulSoup

# 网站核心配置（从网页源码提取）
BASE_DOMAIN = "https://m.i275.com"  # 移动端域名（更易解析）
SEARCH_URL = f"{BASE_DOMAIN}/search.php"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT, "Referer": BASE_DOMAIN}

class I275AudioDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title("275听书网音频下载器")
        self.root.geometry("1000x700")
        self.root.minsize(800, 600)

        # 全局变量
        self.download_path = tk.StringVar(value=os.path.expanduser("~/Downloads/275听书"))
        self.current_book_url = ""  # 当前选中书籍的详情页URL
        self.book_list = []  # 搜索结果：[(book_title, book_url, cover), ...]
        self.chapter_list = []  # 章节列表：[(chapter_title, chapter_url), ...]

        # ========== 1. 顶部搜索区域 ==========
        search_frame = ttk.Frame(root, padding="10 10 10 5")
        search_frame.pack(fill=tk.X, anchor=tk.N)

        ttk.Label(search_frame, text="搜索关键词：", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)
        self.search_entry = ttk.Entry(search_frame, width=40, font=("微软雅黑", 10))
        self.search_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.search_btn = ttk.Button(search_frame, text="搜索书籍", command=self.start_search)
        self.search_btn.pack(side=tk.LEFT, padx=5)

        # ========== 2. 书籍列表区域 ==========
        book_frame = ttk.LabelFrame(root, text="搜索结果（书籍）", padding="10 5 10 10")
        book_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.book_tree = ttk.Treeview(book_frame, columns=("title",), show="headings", height=8)
        self.book_tree.heading("title", text="书籍名称")
        self.book_tree.column("title", width=800)
        self.book_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        book_scroll = ttk.Scrollbar(book_frame, orient=tk.VERTICAL, command=self.book_tree.yview)
        self.book_tree.configure(yscrollcommand=book_scroll.set)
        book_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.book_tree.bind("<<TreeviewSelect>>", self.on_book_select)

        # ========== 3. 章节列表区域 ==========
        chapter_frame = ttk.LabelFrame(root, text="书籍章节", padding="10 5 10 10")
        chapter_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.chapter_tree = ttk.Treeview(chapter_frame, columns=("title",), show="headings", height=15)
        self.chapter_tree.heading("title", text="章节名称")
        self.chapter_tree.column("title", width=800)
        self.chapter_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        chapter_scroll = ttk.Scrollbar(chapter_frame, orient=tk.VERTICAL, command=self.chapter_tree.yview)
        self.chapter_tree.configure(yscrollcommand=chapter_scroll.set)
        chapter_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ========== 4. 下载配置区域 ==========
        download_frame = ttk.Frame(root, padding="10 5 10 10")
        download_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(download_frame, text="下载路径：", font=("微软雅黑", 10)).pack(side=tk.LEFT, padx=5)
        path_entry = ttk.Entry(download_frame, textvariable=self.download_path, width=50, font=("微软雅黑", 10))
        path_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(download_frame, text="选择路径", command=self.choose_download_path).pack(side=tk.LEFT, padx=5)

        # ========== 5. 操作&状态区域 ==========
        action_frame = ttk.Frame(root, padding="10 0 10 10")
        action_frame.pack(fill=tk.X, padx=10)

        self.download_btn = ttk.Button(action_frame, text="下载选中章节", command=self.start_download)
        self.download_btn.pack(side=tk.LEFT, padx=5)
        self.status_label = ttk.Label(action_frame, text="状态：就绪", font=("微软雅黑", 10))
        self.status_label.pack(side=tk.LEFT, padx=20)

        # 初始化创建下载目录
        os.makedirs(self.download_path.get(), exist_ok=True)

    def choose_download_path(self):
        """选择下载文件夹"""
        path = filedialog.askdirectory(title="选择音频下载文件夹")
        if path:
            self.download_path.set(path)
            os.makedirs(path, exist_ok=True)

    def start_search(self):
        """启动书籍搜索（多线程避免GUI卡死）"""
        keyword = self.search_entry.get().strip()
        if not keyword:
            messagebox.showwarning("提示", "请输入搜索关键词！")
            return

        self.search_btn.config(state=tk.DISABLED)
        self.status_label.config(text="状态：正在搜索书籍...")
        # 清空原有列表
        self.clear_tree(self.book_tree)
        self.clear_tree(self.chapter_tree)
        self.book_list = []
        self.chapter_list = []

        # 多线程执行搜索
        search_thread = threading.Thread(target=self.search_books, args=(keyword,))
        search_thread.daemon = True
        search_thread.start()

    def search_books(self, keyword):
        """执行书籍搜索（子线程）"""
        try:
            # 构造搜索请求
            params = {"q": quote(keyword)}
            response = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=15)
            response.raise_for_status()
            response.encoding = "utf-8"

            # 解析搜索结果
            soup = BeautifulSoup(response.text, "html.parser")
            book_items = soup.select("div.divide-y > a[href^='/book/']")  # 书籍链接节点
            if not book_items:
                self.root.after(0, lambda: self.status_label.config(text="状态：未找到相关书籍"))
                self.root.after(0, lambda: messagebox.showinfo("提示", "未找到相关书籍！"))
                return

            # 提取书籍信息
            for item in book_items:
                book_title = item.select_one("h3.text-base").get_text(strip=True) if item.select_one("h3.text-base") else "未知书籍"
                book_url = urljoin(BASE_DOMAIN, item["href"])
                self.book_list.append((book_title, book_url))

            # 主线程更新书籍列表
            self.root.after(0, self.update_book_list)
            self.root.after(0, lambda: self.status_label.config(text="状态：搜索完成，共找到{}本书籍".format(len(self.book_list))))

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("搜索错误", f"书籍搜索失败：{str(e)}"))
            self.root.after(0, lambda: self.status_label.config(text="状态：搜索失败"))
        finally:
            self.root.after(0, lambda: self.search_btn.config(state=tk.NORMAL))

    def update_book_list(self):
        """更新书籍列表（主线程）"""
        for book_title, _ in self.book_list:
            self.book_tree.insert("", tk.END, values=(book_title,))

    def on_book_select(self, event):
        """选中书籍后加载章节列表"""
        selected_items = self.book_tree.selection()
        if not selected_items:
            return

        # 获取选中书籍的URL
        idx = self.book_tree.index(selected_items[0])
        self.current_book_url = self.book_list[idx][1]
        book_title = self.book_list[idx][0]

        # 清空章节列表
        self.clear_tree(self.chapter_tree)
        self.chapter_list = []
        self.status_label.config(text=f"状态：正在加载《{book_title}》的章节...")

        # 多线程加载章节
        chapter_thread = threading.Thread(target=self.load_chapters, args=(self.current_book_url,))
        chapter_thread.daemon = True
        chapter_thread.start()

    def load_chapters(self, book_url):
        """加载书籍章节（子线程）"""
        try:
            response = requests.get(book_url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            response.encoding = "utf-8"
            soup = BeautifulSoup(response.text, "html.parser")

            # 解析章节列表（适配网页的章节节点）
            chapter_items = soup.select("div.grid > a[href^='/play/']")
            if not chapter_items:
                self.root.after(0, lambda: self.status_label.config(text="状态：未找到章节列表"))
                self.root.after(0, lambda: messagebox.showinfo("提示", "该书籍暂无章节！"))
                return

            # 提取章节信息
            for item in chapter_items:
                chapter_title = item.select_one("span.text-sm").get_text(strip=True) if item.select_one("span.text-sm") else "未知章节"
                chapter_url = urljoin(BASE_DOMAIN, item["href"])
                self.chapter_list.append((chapter_title, chapter_url))

            # 主线程更新章节列表
            self.root.after(0, self.update_chapter_list)
            self.root.after(0, lambda: self.status_label.config(text=f"状态：加载完成，共{len(self.chapter_list)}个章节"))

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("章节加载错误", f"加载章节失败：{str(e)}"))
            self.root.after(0, lambda: self.status_label.config(text="状态：章节加载失败"))

    def update_chapter_list(self):
        """更新章节列表（主线程）"""
        for chapter_title, _ in self.chapter_list:
            self.chapter_tree.insert("", tk.END, values=(chapter_title,))

    def start_download(self):
        """下载选中章节（多线程）"""
        selected_items = self.chapter_tree.selection()
        if not selected_items:
            messagebox.showwarning("提示", "请先选择要下载的章节！")
            return
        if not self.current_book_url:
            messagebox.showwarning("提示", "请先选择书籍！")
            return

        # 获取选中章节信息
        idx = self.chapter_tree.index(selected_items[0])
        chapter_title, chapter_url = self.chapter_list[idx]
        save_dir = self.download_path.get()

        self.download_btn.config(state=tk.DISABLED)
        self.status_label.config(text=f"状态：正在下载《{chapter_title}》...")

        # 多线程下载
        download_thread = threading.Thread(target=self.download_audio, args=(chapter_title, chapter_url, save_dir))
        download_thread.daemon = True
        download_thread.start()

    def download_audio(self, chapter_title, chapter_url, save_dir):
        """下载音频（子线程）"""
        try:
            # 1. 访问播放页，提取音频URL
            response = requests.get(chapter_url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            response.encoding = "utf-8"

            # 正则提取APlayer中的音频URL（从网页源码的audio.url字段）
            audio_url_pattern = re.compile(r"url:\s*'([^']+\.m4a[^']*)'")
            match = audio_url_pattern.search(response.text)
            if not match:
                raise Exception("未提取到音频下载地址（可能网站接口变更）")
            audio_url = match.group(1)

            # 2. 下载音频文件
            audio_filename = f"{chapter_title}.m4a".replace("/", "").replace("\\", "").replace(":", "").replace("*", "").replace("?", "").replace('"', "").replace("<", "").replace(">", "").replace("|", "")
            save_path = os.path.join(save_dir, audio_filename)

            # 流式下载（避免大文件占用内存）
            audio_response = requests.get(audio_url, headers=HEADERS, stream=True, timeout=30)
            audio_response.raise_for_status()
            total_size = int(audio_response.headers.get("Content-Length", 0))
            downloaded_size = 0

            with open(save_path, "wb") as f:
                for chunk in audio_response.iter_content(chunk_size=1024*1024):  # 1MB块
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        # 更新下载进度
                        progress = f"{(downloaded_size/total_size)*100:.1f}%" if total_size > 0 else "未知进度"
                        self.root.after(0, lambda: self.status_label.config(text=f"状态：下载中 {progress}..."))

            # 下载完成
            self.root.after(0, lambda: self.status_label.config(text=f"状态：《{chapter_title}》下载完成"))
            self.root.after(0, lambda: messagebox.showinfo("下载成功", f"音频已保存至：\n{save_path}"))

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("下载错误", f"章节下载失败：{str(e)}"))
            self.root.after(0, lambda: self.status_label.config(text="状态：下载失败"))
        finally:
            self.root.after(0, lambda: self.download_btn.config(state=tk.NORMAL))

    @staticmethod
    def clear_tree(tree):
        """清空Treeview列表"""
        for item in tree.get_children():
            tree.delete(item)

if __name__ == "__main__":
    # 解决Windows下tkinter高清屏模糊问题
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass

    # 启动GUI
    root = tk.Tk()
    app = I275AudioDownloader(root)
    root.mainloop()
