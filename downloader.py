import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import requests
from bs4 import BeautifulSoup
import os
import urllib.parse
import re

class DownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("275听书下载器")
        self.root.geometry("800x600")
        self.base_url = "https://m.i275.com"
        self.download_path = ""
        self.current_book_url = ""
        self.chapters = []
        self.search_results = []
        
        self.create_widgets()
        
    def create_widgets(self):
        search_frame = tk.Frame(self.root)
        search_frame.pack(pady=10, fill=tk.X, padx=10)
        
        tk.Label(search_frame, text="搜索:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(search_frame, textvariable=self.search_var, width=50)
        self.search_entry.pack(side=tk.LEFT, padx=5)
        self.search_btn = tk.Button(search_frame, text="搜索", command=self.start_search)
        self.search_btn.pack(side=tk.LEFT)
        
        path_frame = tk.Frame(self.root)
        path_frame.pack(pady=5, fill=tk.X, padx=10)
        tk.Label(path_frame, text="下载位置:").pack(side=tk.LEFT)
        self.path_var = tk.StringVar()
        self.path_entry = tk.Entry(path_frame, textvariable=self.path_var, width=50)
        self.path_entry.pack(side=tk.LEFT, padx=5)
        self.browse_btn = tk.Button(path_frame, text="浏览", command=self.select_path)
        self.browse_btn.pack(side=tk.LEFT)
        
        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        left_frame = tk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0,5))
        tk.Label(left_frame, text="搜索结果", font=('Arial', 12, 'bold')).pack(anchor=tk.W)
        self.result_listbox = tk.Listbox(left_frame)
        self.result_listbox.pack(fill=tk.BOTH, expand=True)
        self.result_listbox.bind('<<ListboxSelect>>', self.on_result_select)
        
        right_frame = tk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5,0))
        tk.Label(right_frame, text="章节列表", font=('Arial', 12, 'bold')).pack(anchor=tk.W)
        scrollbar = tk.Scrollbar(right_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.chapter_listbox = tk.Listbox(right_frame, yscrollcommand=scrollbar.set)
        self.chapter_listbox.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.chapter_listbox.yview)
        
        download_frame = tk.Frame(self.root)
        download_frame.pack(pady=10)
        self.download_btn = tk.Button(download_frame, text="下载选中章节", command=self.start_download, state=tk.DISABLED)
        self.download_btn.pack()
        
        self.status_var = tk.StringVar()
        self.status_var.set("就绪")
        status_bar = tk.Label(self.root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
    def select_path(self):
        path = filedialog.askdirectory()
        if path:
            self.path_var.set(path)
            self.download_path = path
            
    def start_search(self):
        query = self.search_var.get().strip()
        if not query:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return
        self.result_listbox.delete(0, tk.END)
        self.chapter_listbox.delete(0, tk.END)
        self.download_btn.config(state=tk.DISABLED)
        self.status_var.set("搜索中...")
        threading.Thread(target=self.search, args=(query,), daemon=True).start()
        
    def search(self, query):
        try:
            url = f"{self.base_url}/search.php?q={urllib.parse.quote(query)}"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
            items = soup.find_all('a', href=re.compile(r'^/book/\d+\.html$'))
            results = []
            for item in items:
                title_tag = item.find('h3')
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                author = "未知"
                performer = "未知"
                p_tags = item.find_all('p', class_='text-xs')
                for p in p_tags:
                    text = p.get_text()
                    if '演播' in text:
                        span = p.find('span', class_='text-gray-800')
                        performer = span.get_text(strip=True) if span else text.replace('演播','').strip()
                    elif '作者' in text:
                        span = p.find('span', class_='text-gray-800')
                        author = span.get_text(strip=True) if span else text.replace('作者','').strip()
                url = item['href']
                full_url = urllib.parse.urljoin(self.base_url, url)
                results.append((title, author, performer, full_url))
            self.search_results = results
            self.root.after(0, self.display_results)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("错误", f"搜索失败: {str(e)}"))
            self.root.after(0, lambda: self.status_var.set("搜索失败"))
    
    def display_results(self):
        self.result_listbox.delete(0, tk.END)
        for title, author, performer, url in self.search_results:
            self.result_listbox.insert(tk.END, f"{title} - {performer}")
        self.status_var.set(f"找到 {len(self.search_results)} 条结果")
        
    def on_result_select(self, event):
        selection = self.result_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        _, _, _, url = self.search_results[idx]
        self.current_book_url = url
        self.chapter_listbox.delete(0, tk.END)
        self.download_btn.config(state=tk.DISABLED)
        self.status_var.set("加载章节...")
        threading.Thread(target=self.load_chapters, args=(url,), daemon=True).start()
        
    def load_chapters(self, url):
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
            chapters = []
            chapter_links = soup.find_all('a', href=re.compile(r'^/play/\d+/\d+\.html$'))
            for link in chapter_links:
                span = link.find('span', class_='text-sm')
                chapter_title = span.get_text(strip=True) if span else link.get_text(strip=True)
                chapter_url = urllib.parse.urljoin(self.base_url, link['href'])
                chapters.append((chapter_title, chapter_url))
            self.chapters = chapters
            self.root.after(0, self.display_chapters)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("错误", f"加载章节失败: {str(e)}"))
            self.root.after(0, lambda: self.status_var.set("加载章节失败"))
    
    def display_chapters(self):
        self.chapter_listbox.delete(0, tk.END)
        for title, _ in self.chapters:
            self.chapter_listbox.insert(tk.END, title)
        if self.chapters:
            self.download_btn.config(state=tk.NORMAL)
        self.status_var.set(f"共 {len(self.chapters)} 章")
    
    def start_download(self):
        selection = self.chapter_listbox.curselection()
        if not selection:
            messagebox.showwarning("提示", "请选择要下载的章节")
            return
        idx = selection[0]
        chapter_title, chapter_url = self.chapters[idx]
        if not self.download_path:
            messagebox.showwarning("提示", "请先选择下载位置")
            return
        self.status_var.set(f"正在解析音频链接: {chapter_title}")
        self.download_btn.config(state=tk.DISABLED)
        threading.Thread(target=self.download_chapter, args=(chapter_title, chapter_url), daemon=True).start()
        
    def download_chapter(self, title, url):
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.encoding = 'utf-8'
            match = re.search(r'url:\s*[\'"]([^\'"]+\.m4a[^\'"]*)[\'"]', resp.text)
            if not match:
                match = re.search(r'url:\s*[\'"]([^\'"]+\.mp3[^\'"]*)[\'"]', resp.text)
            if not match:
                self.root.after(0, lambda: messagebox.showerror("错误", "未找到音频链接"))
                self.root.after(0, lambda: self.status_var.set("下载失败"))
                self.root.after(0, lambda: self.download_btn.config(state=tk.NORMAL))
                return
            audio_url = match.group(1)
            if audio_url.startswith('//'):
                audio_url = 'https:' + audio_url
            elif not audio_url.startswith('http'):
                audio_url = urllib.parse.urljoin(self.base_url, audio_url)
            
            ext = os.path.splitext(audio_url.split('?')[0])[1] or '.m4a'
            filename = f"{title}{ext}"
            invalid_chars = '<>:"/\\|?*'
            for ch in invalid_chars:
                filename = filename.replace(ch, '_')
            filepath = os.path.join(self.download_path, filename)
            
            audio_resp = requests.get(audio_url, headers=headers, stream=True)
            audio_resp.raise_for_status()
            total_size = int(audio_resp.headers.get('content-length', 0))
            downloaded = 0
            with open(filepath, 'wb') as f:
                for chunk in audio_resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            percent = downloaded / total_size * 100
                            self.root.after(0, lambda p=percent: self.status_var.set(f"下载中: {p:.1f}%"))
            self.root.after(0, lambda: messagebox.showinfo("完成", f"下载完成: {filename}"))
            self.root.after(0, lambda: self.status_var.set("下载完成"))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("错误", f"下载失败: {str(e)}"))
            self.root.after(0, lambda: self.status_var.set("下载失败"))
        finally:
            self.root.after(0, lambda: self.download_btn.config(state=tk.NORMAL))

def main():
    root = tk.Tk()
    app = DownloaderApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
