#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
import threading
import queue
import requests
from urllib.parse import urljoin
from tkinter import *
from tkinter import ttk, messagebox, scrolledtext
import colorama
from colorama import Fore, Style
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import undetected_chromedriver as uc

colorama.init()

class TingShuDownloader:
    def __init__(self, max_workers=3, delay=2, output_dir="downloads", headless=True):
        self.max_workers = max_workers
        self.delay = delay
        self.output_dir = output_dir
        self.base_url = "https://m.i275.com"
        self.driver = None
        self.headless = headless
        self.download_queue = queue.Queue()
        self.is_downloading = False
        self.current_book = None
        self.chapters = []
        
        os.makedirs(output_dir, exist_ok=True)
    
    def log(self, message, level="info"):
        timestr = time.strftime('%H:%M:%S')
        if level == "success":
            print(f"{Fore.GREEN}[{timestr}] ✓ {message}{Style.RESET_ALL}")
        elif level == "error":
            print(f"{Fore.RED}[{timestr}] ✗ {message}{Style.RESET_ALL}")
        elif level == "warning":
            print(f"{Fore.YELLOW}[{timestr}] ⚠ {message}{Style.RESET_ALL}")
        elif level == "info":
            print(f"{Fore.CYAN}[{timestr}] ℹ {message}{Style.RESET_ALL}")
        else:
            print(f"[{timestr}] {message}")
    
    def init_driver(self):
        if self.driver:
            return
        
        options = uc.ChromeOptions()
        if self.headless:
            options.add_argument('--headless=new')
        
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        options.add_argument('--disable-gpu')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        
        self.driver = uc.Chrome(options=options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    def close_driver(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
    
    def search_books(self, keyword):
        self.log(f"正在搜索: {keyword}", "info")
        
        try:
            self.init_driver()
            
            search_url = f"{self.base_url}/search.php?q={keyword}"
            self.driver.get(search_url)
            
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "divide-y"))
            )
            
            time.sleep(2)
            
            books = []
            
            book_elements = self.driver.find_elements(By.XPATH, '//a[contains(@href, "/book/") and contains(@class, "flex p-4")]')
            
            for element in book_elements:
                try:
                    html = element.get_attribute('outerHTML')
                    
                    book_url = element.get_attribute('href')
                    
                    img = element.find_element(By.TAG_NAME, 'img')
                    cover = img.get_attribute('src') if img else ''
                    
                    title_elem = element.find_element(By.XPATH, './/h3[contains(@class, "font-bold")]')
                    title = title_elem.text.strip() if title_elem else '未知书名'
                    
                    author = '未知'
                    try:
                        author_elem = element.find_element(By.XPATH, './/p[contains(@class, "text-gray-500")]')
                        author = author_elem.text.strip()
                    except:
                        pass
                    
                    desc = ''
                    try:
                        desc_elem = element.find_element(By.XPATH, './/p[contains(@class, "text-gray-400")]')
                        desc = desc_elem.text.strip()
                    except:
                        pass
                    
                    books.append({
                        'title': title,
                        'url': book_url,
                        'cover': cover,
                        'author': author,
                        'description': desc
                    })
                except Exception as e:
                    continue
            
            self.log(f"找到 {len(books)} 个结果", "success")
            return books
            
        except Exception as e:
            self.log(f"搜索失败: {e}", "error")
            return []
    
    def get_hot_books(self, limit=20):
        self.log("获取热门书籍...", "info")
        
        try:
            self.init_driver()
            
            self.driver.get(self.base_url)
            
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "grid"))
            )
            
            time.sleep(2)
            
            books = []
            
            book_elements = self.driver.find_elements(By.XPATH, '//a[contains(@href, "/book/") and contains(@class, "bg-white")]')
            
            for element in book_elements[:limit]:
                try:
                    book_url = element.get_attribute('href')
                    
                    img = element.find_element(By.TAG_NAME, 'img')
                    cover = img.get_attribute('src') if img else ''
                    
                    title_elem = element.find_element(By.XPATH, './/div[contains(@class, "font-medium")]')
                    title = title_elem.text.strip()
                    
                    author_elem = element.find_element(By.XPATH, './/div[contains(@class, "text-gray-500")]')
                    author = author_elem.text.strip().replace('演播', '').strip()
                    
                    books.append({
                        'title': title,
                        'url': book_url,
                        'cover': cover,
                        'author': author
                    })
                except:
                    continue
            
            return books
            
        except Exception as e:
            self.log(f"获取热门书籍失败: {e}", "error")
            return []
    
    def extract_book_info(self, catalog_url):
        self.log(f"正在获取目录页: {catalog_url}")
        
        try:
            self.init_driver()
            
            self.driver.get(catalog_url)
            
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "grid"))
            )
            
            time.sleep(2)
            
            title_elem = self.driver.find_element(By.XPATH, '//h1')
            book_title = title_elem.text.strip() if title_elem else '未知书名'
            
            author = '未知'
            try:
                author_span = self.driver.find_element(By.XPATH, '//p[contains(text(), "演播")]/span')
                author = author_span.text.strip()
            except:
                pass
            
            cover = ''
            try:
                cover_img = self.driver.find_element(By.XPATH, '//img[contains(@class, "object-cover")]')
                cover = cover_img.get_attribute('src')
            except:
                pass
            
            chapters = []
            
            chapter_links = self.driver.find_elements(By.XPATH, '//a[contains(@href, "/play/") and not(contains(@class, "bg-purple-600"))]')
            
            for link in chapter_links:
                try:
                    href = link.get_attribute('href')
                    
                    text = link.text.strip()
                    
                    num_match = re.search(r'(\d+)\.', text)
                    if num_match:
                        num = int(num_match.group(1))
                    else:
                        num = len(chapters) + 1
                    
                    title = re.sub(r'^\d+\.\s*', '', text).strip()
                    
                    chapters.append({
                        'num': num,
                        'title': title,
                        'url': href
                    })
                except:
                    continue
            
            chapters.sort(key=lambda x: x['num'])
            
            self.log(f"找到书籍: {book_title}", "success")
            self.log(f"发现 {len(chapters)} 个章节", "success")
            
            return {
                'title': book_title,
                'author': author,
                'cover': cover,
                'url': catalog_url,
                'chapters': chapters
            }
            
        except Exception as e:
            self.log(f"获取目录页失败: {e}", "error")
            return None
    
    def get_audio_url_selenium(self, chapter_url):
        try:
            self.driver.get(chapter_url)
            
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.ID, "aplayer"))
            )
            
            time.sleep(3)
            
            page_source = self.driver.page_source
            
            patterns = [
                r'url:\s*[\'"]([^\'"]+\.m4a[^\'"]*)[\'"]',
                r'<audio[^>]+src=["\']([^"\']+)["\']',
                r'(https?:)?//[^\'"\s]*\.(xmcdn|tingshijie)\.com[^\'"\s]*\.m4a[^\'"\s]*',
                r'audio: \[\{.*?url:\s*[\'"]([^\'"]+)[\'"]'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, page_source, re.DOTALL)
                if match:
                    url = match.group(1) if match.group(1) else match.group(0)
                    if url.startswith('//'):
                        url = 'https:' + url
                    return url
            
            audio_elements = self.driver.find_elements(By.TAG_NAME, 'audio')
            for audio in audio_elements:
                src = audio.get_attribute('src')
                if src:
                    return src
            
            return None
            
        except Exception as e:
            self.log(f"获取音频URL失败: {e}", "error")
            return None
    
    def download_audio_with_requests(self, audio_url, filepath, chapter_num):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': 'https://m.i275.com/',
                'Origin': 'https://m.i275.com',
                'Accept': 'audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,application/ogg;q=0.7,video/*;q=0.6,*/*;q=0.5',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Range': 'bytes=0-',
                'Connection': 'keep-alive',
            }
            
            session = requests.Session()
            
            self.driver.execute_script("window.open('');")
            self.driver.switch_to.window(self.driver.window_handles[-1])
            self.driver.get(audio_url)
            time.sleep(2)
            cookies = self.driver.get_cookies()
            self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])
            
            for cookie in cookies:
                session.cookies.set(cookie['name'], cookie['value'])
            
            response = session.get(audio_url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            
            from tqdm import tqdm
            with open(filepath, 'wb') as f, tqdm(
                desc=f"章节 {chapter_num}",
                total=total_size,
                unit='B',
                unit_scale=True,
                leave=False,
                ncols=60
            ) as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
            
            return True
            
        except Exception as e:
            self.log(f"下载失败: {e}", "error")
            return False
    
    def download_chapter(self, chapter, book_info, gui_log_callback=None):
        chapter_num = chapter['num']
        chapter_title = chapter['title']
        chapter_url = chapter['url']
        
        safe_title = re.sub(r'[\\/*?:"<>|]', '', chapter_title)
        filename = f"{str(chapter_num).zfill(4)}_{safe_title}.m4a"
        filepath = os.path.join(self.output_dir, book_info['title'], filename)
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            msg = f"章节 {chapter_num} 已存在，跳过"
            self.log(msg, "warning")
            if gui_log_callback:
                gui_log_callback(msg)
            return True
        
        msg = f"获取章节 {chapter_num} 音频地址..."
        self.log(msg, "info")
        if gui_log_callback:
            gui_log_callback(msg)
        
        max_retries = 3
        for retry in range(max_retries):
            try:
                audio_url = self.get_audio_url_selenium(chapter_url)
                
                if not audio_url:
                    msg = f"章节 {chapter_num} 获取音频地址失败"
                    self.log(msg, "error")
                    if gui_log_callback:
                        gui_log_callback(msg)
                    return False
                
                msg = f"下载章节 {chapter_num}: {chapter_title[:30]}..."
                self.log(msg, "info")
                if gui_log_callback:
                    gui_log_callback(msg)
                
                success = self.download_audio_with_requests(audio_url, filepath, chapter_num)
                
                if success:
                    msg = f"章节 {chapter_num} 下载完成"
                    self.log(msg, "success")
                    if gui_log_callback:
                        gui_log_callback(msg)
                    return True
                else:
                    raise Exception("下载失败")
                    
            except Exception as e:
                if retry < max_retries - 1:
                    wait_time = 5 * (retry + 1)
                    msg = f"章节 {chapter_num} 下载失败，{wait_time}秒后重试 ({retry+2}/{max_retries})"
                    self.log(msg, "warning")
                    if gui_log_callback:
                        gui_log_callback(msg)
                    time.sleep(wait_time)
                else:
                    msg = f"章节 {chapter_num} 下载失败: {e}"
                    self.log(msg, "error")
                    if gui_log_callback:
                        gui_log_callback(msg)
                    return False
        
        return False
    
    def download_all(self, catalog_url, gui_log_callback=None, gui_progress_callback=None):
        self.log("=" * 60, "info")
        self.log("275听书网 批量下载器", "info")
        self.log(f"目标网址: {catalog_url}", "info")
        self.log("=" * 60, "info")
        
        if gui_log_callback:
            gui_log_callback("开始获取书籍信息...")
        
        book_info = self.extract_book_info(catalog_url)
        
        if not book_info or not book_info['chapters']:
            msg = "没有找到任何章节，退出"
            self.log(msg, "error")
            if gui_log_callback:
                gui_log_callback(msg)
            return
        
        chapters = book_info['chapters']
        self.current_book = book_info
        self.chapters = chapters
        
        book_dir = os.path.join(self.output_dir, book_info['title'])
        os.makedirs(book_dir, exist_ok=True)
        
        info_file = os.path.join(book_dir, "_info.json")
        with open(info_file, 'w', encoding='utf-8') as f:
            json.dump(book_info, f, ensure_ascii=False, indent=2)
        
        msg = f"书籍信息已保存到: {info_file}"
        self.log(msg, "success")
        if gui_log_callback:
            gui_log_callback(msg)
        
        if gui_progress_callback:
            gui_progress_callback(0, len(chapters))
        
        success_count = 0
        fail_count = 0
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_chapter = {}
            
            for chapter in chapters:
                future = executor.submit(self.download_chapter, chapter, book_info, gui_log_callback)
                future_to_chapter[future] = chapter
                time.sleep(self.delay)
            
            for i, future in enumerate(as_completed(future_to_chapter)):
                chapter = future_to_chapter[future]
                try:
                    if future.result():
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    self.log(f"章节 {chapter['num']} 处理异常: {e}", "error")
                    if gui_log_callback:
                        gui_log_callback(f"章节 {chapter['num']} 处理异常: {e}")
                    fail_count += 1
                
                if gui_progress_callback:
                    gui_progress_callback(i + 1, len(chapters))
        
        self.log("=" * 60, "info")
        self.log(f"下载完成！", "success")
        self.log(f"成功: {success_count} 章", "success")
        self.log(f"失败: {fail_count} 章", "error" if fail_count > 0 else "info")
        self.log("=" * 60, "info")
        
        if gui_log_callback:
            gui_log_callback(f"下载完成！成功: {success_count} 章，失败: {fail_count} 章")
        
        self.close_driver()

class DownloaderGUI:
    def __init__(self):
        self.root = Tk()
        self.root.title("275听书网下载器")
        self.root.geometry("800x600")
        self.root.resizable(True, True)
        
        self.downloader = None
        self.current_books = []
        
        self.setup_ui()
        
    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=BOTH, expand=True)
        
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=BOTH, expand=True)
        
        search_frame = ttk.Frame(notebook)
        notebook.add(search_frame, text="搜索下载")
        
        hot_frame = ttk.Frame(notebook)
        notebook.add(hot_frame, text="热门推荐")
        
        settings_frame = ttk.Frame(notebook)
        notebook.add(settings_frame, text="设置")
        
        self.setup_search_tab(search_frame)
        self.setup_hot_tab(hot_frame)
        self.setup_settings_tab(settings_frame)
        
        log_frame = ttk.LabelFrame(main_frame, text="运行日志", padding="5")
        log_frame.pack(fill=BOTH, expand=True, pady=(10, 0))
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, wrap=WORD)
        self.log_text.pack(fill=BOTH, expand=True)
        
        self.log("程序已启动，请选择操作")
    
    def setup_search_tab(self, parent):
        input_frame = ttk.Frame(parent)
        input_frame.pack(fill=X, pady=10)
        
        ttk.Label(input_frame, text="搜索关键词:").pack(side=LEFT, padx=5)
        
        self.search_entry = ttk.Entry(input_frame, width=30)
        self.search_entry.pack(side=LEFT, padx=5)
        self.search_entry.bind('<Return>', lambda e: self.do_search())
        
        ttk.Button(input_frame, text="搜索", command=self.do_search).pack(side=LEFT, padx=5)
        
        result_frame = ttk.LabelFrame(parent, text="搜索结果", padding="5")
        result_frame.pack(fill=BOTH, expand=True, pady=10)
        
        columns = ('序号', '书名', '作者/演播')
        self.search_tree = ttk.Treeview(result_frame, columns=columns, show='tree headings', height=12)
        
        self.search_tree.heading('序号', text='序号')
        self.search_tree.heading('书名', text='书名')
        self.search_tree.heading('作者/演播', text='作者/演播')
        
        self.search_tree.column('序号', width=50, anchor='center')
        self.search_tree.column('书名', width=300)
        self.search_tree.column('作者/演播', width=200)
        
        scrollbar = ttk.Scrollbar(result_frame, orient=VERTICAL, command=self.search_tree.yview)
        self.search_tree.configure(yscrollcommand=scrollbar.set)
        
        self.search_tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
        
        self.search_tree.bind('<Double-Button-1>', lambda e: self.download_selected(self.search_tree))
        
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=X, pady=5)
        
        ttk.Button(btn_frame, text="下载选中书籍", 
                  command=lambda: self.download_selected(self.search_tree)).pack(side=LEFT, padx=5)
    
    def setup_hot_tab(self, parent):
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=X, pady=10)
        
        ttk.Button(btn_frame, text="刷新热门书籍", command=self.load_hot_books).pack(side=LEFT, padx=5)
        
        result_frame = ttk.LabelFrame(parent, text="热门书籍", padding="5")
        result_frame.pack(fill=BOTH, expand=True, pady=10)
        
        columns = ('序号', '书名', '作者/演播')
        self.hot_tree = ttk.Treeview(result_frame, columns=columns, show='tree headings', height=15)
        
        self.hot_tree.heading('序号', text='序号')
        self.hot_tree.heading('书名', text='书名')
        self.hot_tree.heading('作者/演播', text='作者/演播')
        
        self.hot_tree.column('序号', width=50, anchor='center')
        self.hot_tree.column('书名', width=350)
        self.hot_tree.column('作者/演播', width=200)
        
        scrollbar = ttk.Scrollbar(result_frame, orient=VERTICAL, command=self.hot_tree.yview)
        self.hot_tree.configure(yscrollcommand=scrollbar.set)
        
        self.hot_tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
        
        self.hot_tree.bind('<Double-Button-1>', lambda e: self.download_selected(self.hot_tree))
        
        btn_frame2 = ttk.Frame(parent)
        btn_frame2.pack(fill=X, pady=5)
        
        ttk.Button(btn_frame2, text="下载选中书籍", 
                  command=lambda: self.download_selected(self.hot_tree)).pack(side=LEFT, padx=5)
        
        self.load_hot_books()
    
    def setup_settings_tab(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill=BOTH, expand=True, pady=20)
        
        ttk.Label(frame, text="并发下载数:").grid(row=0, column=0, sticky=W, pady=5, padx=10)
        self.workers_var = StringVar(value="3")
        ttk.Entry(frame, textvariable=self.workers_var, width=10).grid(row=0, column=1, sticky=W, pady=5)
        
        ttk.Label(frame, text="请求延迟(秒):").grid(row=1, column=0, sticky=W, pady=5, padx=10)
        self.delay_var = StringVar(value="2")
        ttk.Entry(frame, textvariable=self.delay_var, width=10).grid(row=1, column=1, sticky=W, pady=5)
        
        ttk.Label(frame, text="输出目录:").grid(row=2, column=0, sticky=W, pady=5, padx=10)
        self.output_var = StringVar(value="downloads")
        ttk.Entry(frame, textvariable=self.output_var, width=30).grid(row=2, column=1, sticky=W, pady=5)
        
        self.headless_var = BooleanVar(value=True)
        ttk.Checkbutton(frame, text="无头模式(不显示浏览器窗口)", 
                       variable=self.headless_var).grid(row=3, column=0, columnspan=2, sticky=W, pady=5, padx=10)
        
        ttk.Button(frame, text="保存设置", command=self.save_settings).grid(row=4, column=0, columnspan=2, pady=20)
    
    def log(self, message):
        self.log_text.insert(END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(END)
        self.root.update()
    
    def save_settings(self):
        try:
            workers = int(self.workers_var.get())
            delay = float(self.delay_var.get())
            output = self.output_var.get()
            headless = self.headless_var.get()
            
            self.log(f"设置已保存: 并发={workers}, 延迟={delay}, 输出目录={output}")
            messagebox.showinfo("成功", "设置已保存")
        except:
            messagebox.showerror("错误", "请输入有效的数字")
    
    def do_search(self):
        keyword = self.search_entry.get().strip()
        if not keyword:
            messagebox.showwarning("警告", "请输入搜索关键词")
            return
        
        self.log(f"开始搜索: {keyword}")
        
        def search_task():
            try:
                downloader = TingShuDownloader()
                books = downloader.search_books(keyword)
                downloader.close_driver()
                
                self.root.after(0, lambda: self.display_search_results(books))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("错误", f"搜索失败: {e}"))
        
        threading.Thread(target=search_task, daemon=True).start()
    
    def display_search_results(self, books):
        for item in self.search_tree.get_children():
            self.search_tree.delete(item)
        
        self.current_books = books
        
        for i, book in enumerate(books, 1):
            self.search_tree.insert('', 'end', values=(i, book['title'], book['author']))
        
        self.log(f"找到 {len(books)} 个结果")
    
    def load_hot_books(self):
        self.log("加载热门书籍...")
        
        def hot_task():
            try:
                downloader = TingShuDownloader()
                books = downloader.get_hot_books(20)
                downloader.close_driver()
                
                self.root.after(0, lambda: self.display_hot_books(books))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("错误", f"加载失败: {e}"))
        
        threading.Thread(target=hot_task, daemon=True).start()
    
    def display_hot_books(self, books):
        for item in self.hot_tree.get_children():
            self.hot_tree.delete(item)
        
        for i, book in enumerate(books, 1):
            self.hot_tree.insert('', 'end', values=(i, book['title'], book['author']))
        
        self.log(f"加载了 {len(books)} 本热门书籍")
    
    def download_selected(self, tree):
        selection = tree.selection()
        if not selection:
            messagebox.showwarning("警告", "请先选中一本书")
            return
        
        item = tree.item(selection[0])
        values = item['values']
        
        if not values or len(values) < 2:
            return
        
        index = int(values[0]) - 1
        
        if tree == self.search_tree:
            if index >= len(self.current_books):
                return
            book = self.current_books[index]
        else:
            book = self.current_hot_books[index] if hasattr(self, 'current_hot_books') else None
            if not book:
                return
        
        result = messagebox.askyesno("确认", f"确定要下载《{book['title']}》吗？")
        if not result:
            return
        
        self.log(f"开始下载: {book['title']}")
        
        def download_task():
            try:
                workers = int(self.workers_var.get())
                delay = float(self.delay_var.get())
                output = self.output_var.get()
                headless = self.headless_var.get()
                
                downloader = TingShuDownloader(
                    max_workers=workers,
                    delay=delay,
                    output_dir=output,
                    headless=headless
                )
                
                def log_callback(msg):
                    self.root.after(0, lambda: self.log(msg))
                
                def progress_callback(current, total):
                    self.root.after(0, lambda: self.log(f"进度: {current}/{total}"))
                
                downloader.download_all(
                    book['url'],
                    gui_log_callback=log_callback,
                    gui_progress_callback=progress_callback
                )
                
                self.root.after(0, lambda: messagebox.showinfo("完成", "下载完成！"))
                
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("错误", f"下载失败: {e}"))
        
        threading.Thread(target=download_task, daemon=True).start()
    
    def run(self):
        self.root.mainloop()

def main():
    app = DownloaderGUI()
    app.run()

if __name__ == "__main__":
    main()
