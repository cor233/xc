#!/usr/bin/env python3
import os
import re
import time
import threading
import requests
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from urllib.parse import urljoin
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': 'tingchina/1.9.1 (Linux;Android 16) ExoPlayerLib/2.19.1',
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
    'Referer': 'https://m.i275.com/',
    'Connection': 'keep-alive',
}

ILLEGAL_CHAR_MAP = {
    '/': '／', '\\': '＼', ':': '：', '*': '＊', '?': '？',
    '"': '＂', '<': '＜', '>': '＞', '|': '｜', '\0': '�'
}

def sanitize_filename(name: str) -> str:
    for old, new in ILLEGAL_CHAR_MAP.items():
        name = name.replace(old, new)
    name = name.strip('. ').strip()
    return name if name else "未命名"

def get_soup(url, session):
    resp = session.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    return BeautifulSoup(resp.text, 'html.parser')

def extract_audio_info(play_page_url, session):
    soup = get_soup(play_page_url, session)
    script_text = None
    for script in soup.find_all('script'):
        if script.string and 'new APlayer' in script.string:
            script_text = script.string
            break
    if not script_text:
        raise Exception("未找到APlayer初始化代码")
    
    url_match = re.search(r"url:\s*'([^']+)'", script_text)
    if not url_match:
        raise Exception("未找到音频URL")
    audio_url = url_match.group(1)
    
    name_match = re.search(r"name:\s*'([^']+)'", script_text)
    chapter_title = name_match.group(1) if name_match else "未知章节"
    
    artist_match = re.search(r"artist:\s*'([^']+)'", script_text)
    book_title = artist_match.group(1) if artist_match else "未知专辑"
    
    return audio_url, chapter_title, book_title

def download_audio(audio_url, save_path, session, progress_callback=None):
    if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
        return True
    
    headers = HEADERS.copy()
    headers['Referer'] = 'https://m.i275.com/'
    try:
        resp = session.get(audio_url, headers=headers, stream=True, timeout=30)
        resp.raise_for_status()
        total = int(resp.headers.get('content-length', 0))
        downloaded = 0
        with open(save_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total:
                        progress_callback(downloaded / total)
        return True
    except Exception as e:
        if os.path.exists(save_path):
            os.remove(save_path)
        raise e

def get_chapter_links(book_url, session):
    soup = get_soup(book_url, session)
    grid_div = soup.find('div', class_=re.compile(r'grid'))
    if not grid_div:
        raise Exception("未找到章节列表容器")
    
    chapters = []
    for a in grid_div.find_all('a', href=True):
        href = a['href']
        if href.startswith('/play/'):
            full_url = urljoin(book_url, href)
            chapter_name = a.get_text(strip=True)
            if not chapter_name:
                span = a.find('span', class_=re.compile(r'truncate'))
                if span:
                    chapter_name = span.get_text(strip=True)
            chapters.append((full_url, chapter_name))
    return chapters

def download_book(book_url, output_dir, log_callback, progress_callback):
    session = requests.Session()
    session.headers.update(HEADERS)
    
    try:
        soup = get_soup(book_url, session)
        title_tag = soup.find('h1', class_=re.compile(r'text-2xl'))
        book_name = title_tag.get_text(strip=True) if title_tag else "未知书籍"
        book_name = sanitize_filename(book_name)
        book_dir = os.path.join(output_dir, book_name)
        os.makedirs(book_dir, exist_ok=True)
        log_callback(f"开始下载《{book_name}》")
        
        chapters = get_chapter_links(book_url, session)
        total = len(chapters)
        log_callback(f"共找到 {total} 个章节")
        
        for idx, (chapter_url, chapter_title) in enumerate(chapters, 1):
            log_callback(f"[{idx}/{total}] 解析：{chapter_title}")
            try:
                audio_url, _, _ = extract_audio_info(chapter_url, session)
                safe_title = sanitize_filename(chapter_title)
                filename = f"{idx:03d}_{safe_title}.m4a"
                save_path = os.path.join(book_dir, filename)
                
                log_callback(f"正在下载：{filename}")
                def sub_progress(percent):
                    overall = (idx - 1 + percent) / total
                    progress_callback(int(overall * 100))
                download_audio(audio_url, save_path, session, sub_progress)
                log_callback(f"下载完成：{filename}\n")
            except Exception as e:
                log_callback(f"错误：{chapter_title} 下载失败 - {str(e)}\n")
            time.sleep(0.5)
        progress_callback(100)
        log_callback("所有章节处理完毕！")
    except Exception as e:
        log_callback(f"发生错误：{str(e)}")
    finally:
        session.close()

def download_single(play_url, output_dir, log_callback, progress_callback):
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        log_callback(f"解析播放页：{play_url}")
        audio_url, chapter_title, book_title = extract_audio_info(play_url, session)
        log_callback(f"专辑：{book_title}")
        log_callback(f"章节：{chapter_title}")
        
        book_name = sanitize_filename(book_title)
        save_dir = os.path.join(output_dir, book_name)
        os.makedirs(save_dir, exist_ok=True)
        safe_title = sanitize_filename(chapter_title)
        filename = f"{safe_title}.m4a"
        save_path = os.path.join(save_dir, filename)
        
        def progress_hook(percent):
            progress_callback(int(percent * 100))
        log_callback(f"开始下载：{filename}")
        download_audio(audio_url, save_path, session, progress_hook)
        progress_callback(100)
        log_callback(f"下载完成：{save_path}")
    except Exception as e:
        log_callback(f"错误：{str(e)}")
    finally:
        session.close()

class DownloaderGUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("275听书网音频下载器")
        self.window.geometry("700x550")
        self.window.resizable(True, True)
        
        mode_frame = tk.LabelFrame(self.window, text="下载模式", padx=10, pady=5)
        mode_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.mode_var = tk.IntVar(value=1)
        tk.Radiobutton(mode_frame, text="单集下载", variable=self.mode_var, value=1).pack(side=tk.LEFT, padx=10)
        tk.Radiobutton(mode_frame, text="整本书下载", variable=self.mode_var, value=2).pack(side=tk.LEFT, padx=10)
        
        url_frame = tk.LabelFrame(self.window, text="URL地址", padx=10, pady=5)
        url_frame.pack(fill=tk.X, padx=10, pady=5)
        self.url_entry = tk.Entry(url_frame, font=("Arial", 10))
        self.url_entry.pack(fill=tk.X, expand=True)
        
        out_frame = tk.LabelFrame(self.window, text="保存目录", padx=10, pady=5)
        out_frame.pack(fill=tk.X, padx=10, pady=5)
        self.out_path = tk.StringVar(value=os.path.join(os.getcwd(), "下载"))
        out_entry = tk.Entry(out_frame, textvariable=self.out_path)
        out_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(out_frame, text="浏览", command=self.select_dir).pack(side=tk.RIGHT, padx=5)
        
        self.progress = ttk.Progressbar(self.window, orient=tk.HORIZONTAL, length=100, mode='determinate')
        self.progress.pack(fill=tk.X, padx=10, pady=5)
        self.progress_label = tk.Label(self.window, text="就绪")
        self.progress_label.pack()
        
        log_frame = tk.LabelFrame(self.window, text="下载日志", padx=10, pady=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=15)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        
        btn_frame = tk.Frame(self.window)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        self.download_btn = tk.Button(btn_frame, text="开始下载", command=self.start_download, bg="#4CAF50", fg="white", font=("Arial", 11, "bold"))
        self.download_btn.pack(side=tk.LEFT, padx=5)
        self.clear_btn = tk.Button(btn_frame, text="清空日志", command=self.clear_log, bg="#f44336", fg="white")
        self.clear_btn.pack(side=tk.LEFT, padx=5)
        
        self.window.protocol("WM_DELETE_WINDOW", self.on_close)
        self.download_thread = None
        self.running = False
    
    def select_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.out_path.set(path)
    
    def log(self, message):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.window.update_idletasks()
    
    def update_progress(self, value):
        self.progress['value'] = value
        self.progress_label.config(text=f"下载进度 {value}%")
        self.window.update_idletasks()
    
    def clear_log(self):
        self.log_text.delete(1.0, tk.END)
    
    def on_close(self):
        if self.running:
            if messagebox.askyesno("确认", "下载进行中，确定要退出吗？"):
                self.running = False
                self.window.destroy()
        else:
            self.window.destroy()
    
    def start_download(self):
        if self.running:
            messagebox.showwarning("提示", "已有下载任务进行中，请稍后")
            return
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("错误", "请输入URL")
            return
        output = self.out_path.get()
        if not output:
            output = os.path.join(os.getcwd(), "下载")
            self.out_path.set(output)
        try:
            os.makedirs(output, exist_ok=True)
        except:
            messagebox.showerror("错误", "无法创建保存目录")
            return
        
        mode = self.mode_var.get()
        self.running = True
        self.download_btn.config(state=tk.DISABLED)
        self.update_progress(0)
        self.clear_log()
        
        def wrapper():
            try:
                if mode == 1:
                    download_single(url, output, self.log, self.update_progress)
                else:
                    download_book(url, output, self.log, self.update_progress)
            except Exception as e:
                self.log(f"严重错误：{str(e)}")
            finally:
                self.running = False
                self.download_btn.config(state=tk.NORMAL)
        
        self.download_thread = threading.Thread(target=wrapper, daemon=True)
        self.download_thread.start()
    
    def run(self):
        self.window.mainloop()

if __name__ == '__main__':
    app = DownloaderGUI()
    app.run()
