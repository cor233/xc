import os
import re
import time
import threading
import requests
from urllib.parse import urljoin
from tkinter import Tk, ttk, messagebox, filedialog, StringVar, IntVar
from tkinter.scrolledtext import ScrolledText
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

BASE_URL = "https://m.i275.com"
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
RETRY_DELAY = 2

ILLEGAL_CHARS = {
'\': '＼', '/': '／', ':': '：', '*': '＊',
'?': '？', '"': '＂', '<': '＜', '>': '＞', '|': '｜'
}

def sanitize_filename(name):
for ch, sub in ILLEGAL_CHARS.items():
name = name.replace(ch, sub)
return name.strip().rstrip('.')

def search_books(keyword, session):
url = f"{BASE_URL}/search.php?q={keyword}"
resp = session.get(url, timeout=REQUEST_TIMEOUT)
resp.raise_for_status()
soup = BeautifulSoup(resp.text, 'html.parser')
items = []
for a in soup.select('a.flex.p-4.gap-4[href^="/book/"]'):
href = a.get('href')
full_url = urljoin(BASE_URL, href)
title_el = a.select_one('h3.text-base.font-bold.text-gray-800')
if not title_el:
continue
title = title_el.text.strip()
narrator = ""
author = ""
paras = a.select('p.text-xs.text-gray-500')
for p in paras:
text = p.get_text(strip=True)
if '演播' in text:
narrator = text.split('演播', 1)[-1].strip()
elif '作者' in text:
author = text.split('作者', 1)[-1].strip()
items.append({
'title': title,
'narrator': narrator,
'author': author,
'url': full_url
})
return items

def extract_chapters(book_url, session):
resp = session.get(book_url, timeout=REQUEST_TIMEOUT)
resp.raise_for_status()
soup = BeautifulSoup(resp.text, 'html.parser')
chapters = []
for a in soup.select('a[href^="/play/"]'):
href = a.get('href')
if not href or not re.match(r'/play/\d+/\d+.html', href):
continue
full_url = urljoin(BASE_URL, href)
index_span = a.select_one('span.text-xs.text-gray-400.w-10')
if index_span:
index_text = index_span.text.strip().rstrip('.')
else:
match = re.match(r'(\d+).', a.get_text(strip=True))
index_text = match.group(1) if match else '0'
title_span = a.select_one('span.text-sm.text-gray-700.truncate')
if title_span:
title = title_span.text.strip()
else:
title = a.get_text(strip=True).replace(f'{index_text}.', '', 1).strip()
if not title:
title = f"第{index_text}章"
chapters.append({
'index': int(index_text) if index_text.isdigit() else 0,
'title': title,
'url': full_url
})
chapters.sort(key=lambda x: x['index'])
seen = set()
unique = []
for ch in chapters:
if ch['url'] not in seen:
seen.add(ch['url'])
unique.append(ch)
return unique

def extract_audio_url(play_url, session):
resp = session.get(play_url, timeout=REQUEST_TIMEOUT)
resp.raise_for_status()
html = resp.text
match = re.search(r"url:\s*'"", html, re.IGNORECASE)
if match:
return match.group(1)
match = re.search(r"src="'", html, re.IGNORECASE)
if match:
return match.group(1)
raise ValueError("未找到音频链接")

def get_extension(url):
ext = re.search(r'.(mp3|m4a|aac|ogg|wav|flac)(?|$)', url, re.IGNORECASE)
return ext.group(1) if ext else 'm4a'

class DownloadManager:
def init(self, gui):
self.gui = gui
self.session = requests.Session()
self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
self.cancelled = False
self.executor = None

def cancel(self):
self.cancelled = True

def run(self, chapters, output_dir):
self.cancelled = False
total = len(chapters)
completed = 0
failed = 0
skipped = 0
failed_list = []
self.gui.status_var.set(f"准备下载 {total} 章...")
self.gui.log(f"开始下载，共 {total} 章\n")
self.executor = ThreadPoolExecutor(max_workers=self.gui.threads.get())
futures = {}
for ch in chapters:
if self.cancelled:
break
future = self.executor.submit(self.download_one, ch, output_dir)
futures[future] = ch
for future in as_completed(futures):
if self.cancelled:
break
ch = futures[future]
try:
result, msg = future.result()
if result == 'success':
completed += 1
self.gui.log(f"✔ 第{ch['index']}章 {ch['title']} 下载完成")
elif result == 'skipped':
skipped += 1
self.gui.log(f"○ 第{ch['index']}章 已存在，跳过")
else:
failed += 1
failed_list.append(ch)
self.gui.log(f"✘ 第{ch['index']}章 失败：{msg}")
except Exception as e:
failed += 1
failed_list.append(ch)
self.gui.log(f"✘ 第{ch['index']}章 异常：{str(e)}")
self.gui.status_var.set(f"进度 {completed+skipped+failed}/{total} 成功:{completed} 跳过:{skipped} 失败:{failed}")
self.gui.root.update_idletasks()
self.executor.shutdown(wait=False)
summary = f"下载结束：成功 {completed}，跳过 {skipped}，失败 {failed}"
self.gui.log("\n" + summary)
self.gui.status_var.set(summary)
if failed_list:
self.gui.log("失败章节：")
for ch in failed_list:
self.gui.log(f" {ch['index']}. {ch['title']}")

def download_one(self, chapter, output_dir):
filename = sanitize_filename(chapter['title']) + '.' + get_extension(chapter['url'])
filepath = os.path.join(output_dir, filename)
if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
return ('skipped', '已存在')
audio_url = None
for attempt in range(1, MAX_RETRIES + 1):
if self.cancelled:
return ('cancelled', '')
try:
audio_url = extract_audio_url(chapter['url'], self.session)
break
except Exception as e:
if attempt == MAX_RETRIES:
return ('failed', f"获取音频链接失败(重试{MAX_RETRIES}次): {str(e)}")
time.sleep(RETRY_DELAY * attempt)
for attempt in range(1, MAX_RETRIES + 1):
if self.cancelled:
return ('cancelled', '')
try:
resp = self.session.get(audio_url, stream=True, timeout=REQUEST_TIMEOUT * 3)
resp.raise_for_status()
with open(filepath, 'wb') as f:
for chunk in resp.iter_content(chunk_size=8192):
if self.cancelled:
f.close()
os.remove(filepath)
return ('cancelled', '')
f.write(chunk)
return ('success', filepath)
except Exception as e:
if attempt == MAX_RETRIES:
if os.path.exists(filepath):
os.remove(filepath)
return ('failed', f"下载失败(重试{MAX_RETRIES}次): {str(e)}")
time.sleep(RETRY_DELAY * attempt)

class Application:
def init(self):
self.root = Tk()
self.root.title("275听书网下载器 v3.0")
self.root.geometry("700x600")
self.root.resizable(True, True)
self.threads = IntVar(value=3)
self.manager = None
self.download_thread = None
self.session = requests.Session()
self.session.headers.update({'User-Agent': 'Mozilla/5.0'})
self.all_chapters = []
self.current_book_info = None
self.create_widgets()
self.root.protocol("WM_DELETE_WINDOW", self.on_close)

def create_widgets(self):
mainframe = ttk.Frame(self.root, padding="10")
mainframe.pack(fill='both', expand=True)

search_frame = ttk.Frame(mainframe)
search_frame.pack(fill='x', pady=(0, 5))
ttk.Label(search_frame, text="搜索书名/作者：").pack(side='left')
self.search_var = StringVar()
ttk.Entry(search_frame, textvariable=self.search_var, width=30).pack(side='left', padx=5)
ttk.Button(search_frame, text="搜索", command=self.do_search).pack(side='left')

self.result_tree = ttk.Treeview(mainframe, columns=('title', 'narrator'), show='headings', height=8)
self.result_tree.heading('title', text='书名')
self.result_tree.heading('narrator', text='演播')
self.result_tree.column('title', width=350)
self.result_tree.column('narrator', width=180)
self.result_tree.pack(fill='x', pady=5)
self.result_tree.bind('<Double-1>', self.on_book_select)

self.chapter_label = ttk.Label(mainframe, text="章节数：尚未解析")
self.chapter_label.pack(anchor='w', pady=2)

settings_frame = ttk.LabelFrame(mainframe, text="下载设置", padding="5")
settings_frame.pack(fill='x', pady=5)
ttk.Label(settings_frame, text="保存父目录：").grid(row=0, column=0, sticky='w')
self.dir_var = StringVar(value=os.getcwd())
ttk.Entry(settings_frame, textvariable=self.dir_var, width=40).grid(row=0, column=1, sticky='we', padx=5)
ttk.Button(settings_frame, text="浏览", command=self.browse_dir).grid(row=0, column=2, padx=5)
ttk.Label(settings_frame, text="并发数：").grid(row=1, column=0, sticky='w', pady=5)
ttk.Spinbox(settings_frame, from_=1, to=10, textvariable=self.threads, width=5).grid(row=1, column=1, sticky='w', pady=5)

range_frame = ttk.Frame(mainframe)
range_frame.pack(fill='x', pady=2)
ttk.Label(range_frame, text="下载范围：第").pack(side='left')
self.start_var = IntVar(value=1)
ttk.Entry(range_frame, textvariable=self.start_var, width=6).pack(side='left', padx=5)
ttk.Label(range_frame, text="章 ～ 第").pack(side='left')
self.end_var = IntVar(value=0)
ttk.Entry(range_frame, textvariable=self.end_var, width=6).pack(side='left', padx=5)
ttk.Label(range_frame, text="章 (0=全部)").pack(side='left')

self.status_var = StringVar(value="就绪")
ttk.Label(mainframe, textvariable=self.status_var).pack(anchor='w', pady=2)

btn_frame = ttk.Frame(mainframe)
btn_frame.pack(fill='x', pady=5)
self.download_btn = ttk.Button(btn_frame, text="开始批量下载", command=self.start_download)
self.download_btn.pack(side='left', padx=5)
self.stop_btn = ttk.Button(btn_frame, text="终止", command=self.stop_download, state='disabled')
self.stop_btn.pack(side='left', padx=5)

log_frame = ttk.LabelFrame(mainframe, text="日志", padding="5")
log_frame.pack(fill='both', expand=True, pady=5)
self.log_area = ScrolledText(log_frame, height=8, state='normal', wrap='word')
self.log_area.pack(fill='both', expand=True)

def log(self, msg):
self.log_area.insert('end', msg + '\n')
self.log_area.see('end')
self.log_area.update_idletasks()

def browse_dir(self):
path = filedialog.askdirectory()
if path:
self.dir_var.set(path)

def do_search(self):
keyword = self.search_var.get().strip()
if not keyword:
messagebox.showwarning("提示", "请输入搜索关键词")
return
for item in self.result_tree.get_children():
self.result_tree.delete(item)
self.all_chapters = []
self.current_book_info = None
self.chapter_label.config(text="章节数：尚未解析")
self.log(f"正在搜索：{keyword} ...")
try:
results = search_books(keyword, self.session)
if not results:
self.log("没有找到任何书籍")
return
for r in results:
self.result_tree.insert('', 'end',
values=(r['title'], r['narrator']),
tags=(r['url'], r['title'], r['narrator']))
self.log(f"找到 {len(results)} 本相关书籍，双击选择")
except Exception as e:
self.log(f"搜索失败：{str(e)}")

def on_book_select(self, event):
selection = self.result_tree.selection()
if not selection:
return
item = selection[0]
book_url = self.result_tree.item(item, 'tags')[0]
book_title = self.result_tree.item(item, 'tags')[1]
narrator = self.result_tree.item(item, 'tags')[2]
self.current_book_info = {'url': book_url, 'title': book_title, 'narrator': narrator}
self.log(f"正在解析章节：{book_title} ...")
try:
chapters = extract_chapters(book_url, self.session)
if not chapters:
self.log("未找到章节")
return
self.all_chapters = chapters
self.start_var.set(1)
self.end_var.set(len(chapters))
self.chapter_label.config(text=f"章节数：{len(chapters)}")
self.log(f"解析完成，共 {len(chapters)} 章")
except Exception as e:
self.log(f"章节解析失败：{str(e)}")

def start_download(self):
if not self.all_chapters:
messagebox.showwarning("警告", "请先选择一本书")
return
start = self.start_var.get()
end = self.end_var.get()
if end == 0:
end = len(self.all_chapters)
if start < 1:
start = 1
if end > len(self.all_chapters):
end = len(self.all_chapters)
chapters = [ch for ch in self.all_chapters if start <= ch['index'] <= end]
if not chapters:
messagebox.showwarning("警告", "没有符合范围的章节")
return
parent_dir = self.dir_var.get()
book_title = self.current_book_info['title'] if self.current_book_info else "未知书名"
narrator = self.current_book_info['narrator'] if self.current_book_info else ""
folder_name = f"{book_title}-{narrator}" if narrator else book_title
folder_name = sanitize_filename(folder_name)
output_dir = os.path.join(parent_dir, folder_name)
if not os.path.isdir(output_dir):
try:
os.makedirs(output_dir, exist_ok=True)
except Exception as e:
messagebox.showerror("错误", f"无法创建目录：{e}")
return
self.download_btn['state'] = 'disabled'
self.stop_btn['state'] = 'normal'
self.log_area.delete('1.0', 'end')
self.manager = DownloadManager(self)
self.download_thread = threading.Thread(target=self.manager.run, args=(chapters, output_dir), daemon=True)
self.download_thread.start()
self.monitor_thread()

def monitor_thread(self):
if self.download_thread and self.download_thread.is_alive():
self.root.after(500, self.monitor_thread)
else:
self.download_btn['state'] = 'normal'
self.stop_btn['state'] = 'disabled'
if self.manager and self.manager.cancelled:
self.status_var.set("已终止")
self.manager = None

def stop_download(self):
if self.manager:
self.manager.cancel()
self.status_var.set("正在终止...")

def on_close(self):
if self.manager and not self.manager.cancelled:
if messagebox.askokcancel("退出", "下载正在进行，确定退出吗？"):
self.manager.cancel()
self.root.destroy()
else:
self.root.destroy()

if name == "main":
app = Application()
app.root.mainloop()
