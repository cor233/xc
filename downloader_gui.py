import os
import re
import time
import random
import threading
import queue
import urllib.parse
import json
from tkinter import *
from tkinter import scrolledtext, messagebox, filedialog
from tkinter import ttk
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup

class DownloadWorker:
    def __init__(self, album_url, max_workers, request_delay, retry_times, timeout, save_dir, log_queue):
        self.album_url = album_url
        self.max_workers = max_workers
        self.request_delay = request_delay
        self.retry_times = retry_times
        self.timeout = timeout
        self.save_dir = save_dir
        self.log_queue = log_queue
        self.stop_flag = False
        self.pause_flag = False
        self.pause_cond = threading.Condition()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        })
        self.downloaded_file = os.path.join(save_dir, '.downloaded.json')
        self.load_downloaded()
        self.executor = None
        self.failed_chapters = []
        self.total_chapters = 0
        self.completed = 0
        self.skipped = 0
        self.failed = 0

    def log(self, msg):
        self.log_queue.put(msg)

    def stats_log(self):
        self.log_queue.put(f"STATS:{self.completed}:{self.skipped}:{self.failed}:{self.total_chapters}")

    def fail_log(self, chap_num, chap_title):
        self.log_queue.put(f"FAIL:{chap_num}:{chap_title}")

    def clear_fail_log(self):
        self.log_queue.put("FAIL_CLEAR")

    def load_downloaded(self):
        if os.path.exists(self.downloaded_file):
            with open(self.downloaded_file, 'r', encoding='utf-8') as f:
                self.downloaded = set(json.load(f))
        else:
            self.downloaded = set()

    def save_downloaded(self):
        with open(self.downloaded_file, 'w', encoding='utf-8') as f:
            json.dump(list(self.downloaded), f, ensure_ascii=False)

    def check_pause(self):
        with self.pause_cond:
            while self.pause_flag and not self.stop_flag:
                self.pause_cond.wait()

    def fetch_url(self, url, referer=None, timeout=None, retries=None):
        if timeout is None:
            timeout = self.timeout
        if retries is None:
            retries = self.retry_times
        for attempt in range(retries):
            if self.stop_flag:
                return None
            self.check_pause()
            try:
                headers = {}
                if referer:
                    headers['Referer'] = referer
                else:
                    headers['Referer'] = self.album_url
                resp = self.session.get(url, headers=headers, timeout=timeout)
                resp.encoding = 'utf-8'
                if resp.status_code == 200:
                    return resp
                else:
                    self.log(f"请求失败，状态码 {resp.status_code}，重试 {attempt+1}/{retries}")
            except Exception as e:
                self.log(f"请求异常: {e}，重试 {attempt+1}/{retries}")
            time.sleep(random.uniform(*self.request_delay) * 2)
        return None

    def get_album_title(self):
        resp = self.fetch_url(self.album_url)
        if not resp:
            return "未知专辑"
        soup = BeautifulSoup(resp.text, 'html.parser')
        h1 = soup.find('h1')
        if h1:
            title = h1.get_text(strip=True)
        else:
            title_tag = soup.find('title')
            title = title_tag.get_text(strip=True) if title_tag else "未知专辑"
            title = re.sub(r'[-|]\s*275听书网.*$', '', title).strip()
        title = re.sub(r'[\\/*?:"<>|]', '_', title)
        return title

    def get_chapter_links(self):
        resp = self.fetch_url(self.album_url)
        if not resp:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        chapter_links = []
        for a in soup.find_all('a', href=re.compile(r'^/play/\d+/\d+\.html')):
            href = a.get('href')
            full_url = requests.compat.urljoin(self.album_url, href)
            a_id = a.get('id', '')
            chap_num = 0
            if a_id and a_id.startswith('chapter-pos-'):
                try:
                    chap_num = int(a_id.replace('chapter-pos-', ''))
                except:
                    pass
            title_span = a.find('span', class_='text-sm')
            if title_span:
                chap_title = title_span.get_text(strip=True)
            else:
                chap_title = a.get_text(strip=True) or f"第{chap_num}章"
            if "立即开始收听" in chap_title or "继续播放" in chap_title:
                continue
            chapter_links.append({
                'url': full_url,
                'num': chap_num,
                'title': chap_title
            })
        chapter_links.sort(key=lambda x: x['num'])
        return chapter_links

    def get_audio_url(self, play_url):
        resp = self.fetch_url(play_url, referer=play_url)
        if not resp:
            return None
        text = resp.text
        soup = BeautifulSoup(text, 'html.parser')
        title_tag = soup.find('title')
        page_title = title_tag.get_text() if title_tag else ""
        if "正在播放" not in page_title and "贺岁剧" not in page_title:
            self.log(f"警告：可能不是播放页，标题为：{page_title[:50]}")
        patterns = [
            r"url:\s*'([^']+)'",
            r'"url"\s*:\s*"([^"]+)"',
            r'<audio[^>]+src="([^"]+)"',
            r'var\s+audioUrl\s*=\s*[\'"]([^\'"]+)[\'"]',
            r'audioUrl\s*:\s*[\'"]([^\'"]+)[\'"]',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                url = match.group(1)
                url = url.replace('\\/', '/')
                return url
        self.log(f"无法从页面提取音频URL，响应片段：{text[:300]}")
        return None

    def download_audio(self, audio_url, save_path):
        try:
            headers = {'Referer': self.album_url}
            with self.session.get(audio_url, headers=headers, stream=True, timeout=self.timeout) as r:
                if r.status_code == 200:
                    with open(save_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if self.stop_flag:
                                return False
                            self.check_pause()
                            f.write(chunk)
                    return True
                else:
                    self.log(f"下载失败，状态码 {r.status_code}")
                    return False
        except Exception as e:
            self.log(f"下载异常: {e}")
            return False

    def sanitize_filename(self, name):
        map = {
            '\\': '＼', '/': '／', ':': '：', '*': '＊',
            '?': '？', '"': '＂', '<': '＜', '>': '＞', '|': '｜'
        }
        return re.sub(r'[\\/:*?"<>|]', lambda m: map[m.group(0)], name).strip()

    def process_chapter(self, chapter, download_dir):
        chap_num = chapter['num']
        chap_title = chapter['title']
        play_url = chapter['url']
        if play_url in self.downloaded:
            self.skipped += 1
            self.stats_log()
            self.log(f"[{chap_num}] 已下载，跳过")
            return True
        self.log(f"[{chap_num}] 开始处理：{chap_title}")
        time.sleep(random.uniform(*self.request_delay))
        audio_url = self.get_audio_url(play_url)
        if not audio_url:
            self.failed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 获取音频URL失败，跳过")
            self.fail_log(chap_num, chap_title)
            return False
        safe_title = self.sanitize_filename(chap_title)
        filename = f"{safe_title}.m4a"
        filepath = os.path.join(download_dir, filename)
        success = self.download_audio(audio_url, filepath)
        if success:
            self.downloaded.add(play_url)
            self.save_downloaded()
            self.completed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 下载完成 -> {filename}")
            return True
        else:
            self.failed += 1
            self.stats_log()
            self.log(f"[{chap_num}] 下载失败")
            self.fail_log(chap_num, chap_title)
            return False

    def start_download(self, download_dir):
        self.clear_fail_log()
        self.completed = 0
        self.skipped = 0
        self.failed = 0
        self.log("开始获取专辑信息...")
        self.fetch_url("https://m.i275.com/", referer="https://m.i275.com/")
        album_title = self.get_album_title()
        self.log(f"专辑标题：{album_title}")
        os.makedirs(download_dir, exist_ok=True)
        self.log(f"保存目录：{download_dir}")
        self.log("正在获取章节列表...")
        chapters = self.get_chapter_links()
        if not chapters:
            self.log("未找到任何章节，请检查URL或网络。")
            return
        self.total_chapters = len(chapters)
        self.stats_log()
        self.log(f"共找到 {self.total_chapters} 个章节")
        self.failed_chapters = []
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers)
        future_to_chap = {
            self.executor.submit(self.process_chapter, chap, download_dir): chap
            for chap in chapters
        }
        try:
            for future in as_completed(future_to_chap):
                if self.stop_flag:
                    break
                self.check_pause()
                chap = future_to_chap[future]
                try:
                    future.result()
                except Exception as e:
                    self.log(f"[{chap['num']}] 处理异常: {e}")
                    self.failed_chapters.append(chap)
        finally:
            if self.stop_flag:
                self.executor.shutdown(wait=False, cancel_futures=True)
            else:
                self.executor.shutdown(wait=True)
        if not self.stop_flag:
            self.log(f"下载完成！成功 {self.completed} 跳过 {self.skipped} 失败 {self.failed}")
            if self.failed_chapters:
                self.log(f"失败 {len(self.failed_chapters)} 章，可点击重试")
        else:
            self.log("下载已停止")

    def retry_failed(self, download_dir):
        if not self.failed_chapters:
            self.log("没有失败章节需要重试")
            return
        self.log(f"开始重试 {len(selflen(self.failed.failed_chapters)} _chapters)} 个失败个失败章节")
        self章节")
        self.clear_f.clear_fail_logail_log()
        self.com()
        self.completed = 0
       pleted = 0
        self.s self.skipped = kipped = 0
0
        self.failed        self = .failed = 0
0
        self        self.total_ch.total_chapters = len(self.failed_chapters)
        self.stats_log()
       apters = len(self.failed_chapters)
        self.stats_log()
        new_failed = new_failed = []
        []
        self.executor = self.executor = ThreadPool ThreadPoolExecutor(maxExecutor(max_workers_workers=self.max=self.max_workers_workers)
       )
        future_to future_to_chap_chap = {
 = {
            self            self.executor.executor.submit(self.process_chapter.submit, chap(self.process_chapter, download_dir):, chap, download_dir): chap
 chap
            for            for chap in chap in self.f self.failed_chapters
ailed_chapters
        }
        }
        try:
                   try:
            for future for future in as in as_completed_completed(future(future_to_ch_to_chap):
ap):
                if                if self.stop_flag:
                    break
 self.stop_flag:
                    break
                self.check_p                self.check_pause()
               ause()
                chap = chap = future_to future_to_chap_chap[future[future]
               ]
                try:
 try:
                    future                    future.result()
.result()
                except Exception as                except Exception as e:
 e:
                    self.log(f"[{chap['num']}]                     self.log(f"[{chap['num']}] 重试异常: {e重试异常:}")
                    {e}")
                    new_f new_failed.appendailed.append(chap(chap)
       )
        finally:
 finally:
            if            if self.stop self.stop_flag:
_flag:
                self.executor                self.shutdown.executor(wait=False,.shutdown(wait cancel_f=False,utures=True)
 cancel_futures=True)
            else            else:
               :
                self.executor.shutdown(w self.executor.shutdown(wait=Trueait=True)
       )
        self.f self.failed_chapters =ailed_chapters = new_f new_failed
ailed
        if        if not self not self.stop_flag.stop_flag:
           :
            if self.failed if self.failed_chapters_chapters:
               :
                for chap for chap in self in self.failed_chapters.failed_chapters:
                   :
                    self.fail_log self.fail_log(chap['num(chap['num'], chap'], chap['title['title'])
           '])
            self.log self.log("重("重试完成试完成")
       ")
        else:
 else:
            self            self.log(".log("重试重试已停止已停止")

    def pause(self):
")

    def pause        with(self):
 self.p        withause_cond:
 self.pause_            selfcond:
            self.pause_flag =.pause_flag = True

 True

    def    def resume(self resume(self):
        with self):
        with self.pause.pause_cond_cond:
           :
            self.pause_flag self.pause = False_flag = False
           
            self self.pause_cond.not.pause_cond.notify_allify_all()

class DownloaderApp()

class DownloaderApp(Tk):
    def(Tk):
    def __init __init__(self__(self):
       ):
        super().__init__ super().__init__()
       ()
        self self.title("有声.title("有声小说搜索小说搜索下载器")
       下载器")
        self.geometry(" self.geometry("950x950x800")
800")
        self        self.resizable.resizable(True(True, True, True)
       )
        self.search self.search_var =_var = StringVar()
        self.max StringVar()
        self.max_workers_workers_var =_var = IntVar IntVar(value=(value=5)
5)
        self.delay_min_var = Double        self.delay_min_var = DoubleVar(value=0.5)
Var(value=0.5)
               self.d self.delay_maxelay_max_var =_var = DoubleVar DoubleVar(value=(value=1.5)
1.5)
        self        self.retry_var = Int.retry_varVar(value = IntVar(value=3=3)
       )
        self.time self.timeout_varout_var = Int = IntVar(valueVar(value=15=15)
       )
        self.save self.save_path_var_path_var = StringVar()
 = StringVar()
        self.selected_book_url = None
        self.selected_book        self.selected_book_url = None
        self.selected_book_title_title = None
        self = None
.log_queue        self = queue.Queue.log_queue = queue()
       .Queue()
        self. self.worker_threadworker_thread = None = None
        self.worker =
        self. None
worker = None
        self        self.running.running = False = False
        self.create
        self.create_widget_widgets()
s()
        self        self.update_log.update_log()

    def create()

   _widgets(self def create_widget):
       s(self):
        main_frame = Frame main_frame = Frame(self, padx(self, padx=10=10, p, pady=ady=10)
10)
        main_frame.p        main_frame.pack(fack(fill=BOTH,ill=BOTH, expand=True expand=True)

       )

        search_frame = Frame search_frame = Frame(main(main_frame)
        search_frame)
_frame.p        search_frame.pack(fill=Xack(fill=X, p, pady=ady=5)
5)
               Label Label(search_frame(search_frame, text, text="关键词="关键词：").：").pack(spack(side=ide=LEFT)
LEFT)
        Entry        Entry(search_frame, textvariable=self.search_var, width(search_frame, textvariable=self.search_var, width=50).pack=50).pack(side(side=LEFT=LEFT, pad, padx=x=5,5, expand=True, fill expand=True, fill=X)
=X)
        Button(search_frame, text="        Button(search_frame, text搜索", command=self.search="搜索", command=self.search, bg, bg="blue="blue", f", fg="g="white",white", width=10). width=10).pack(spack(side=LEFT,ide=LEFT, padx=5 padx=5)

        result_frame)

        result_frame = LabelFrame(main_frame = LabelFrame(main_frame, text, text="搜索结果="搜索结果")
       ")
        result_frame result_frame.pack.pack(fill(fill=BOTH, expand=BOTH, expand=True,=True, pady pady=5)
        columns ==5)
        columns = ('书名 ('书名', '', '演播演播', '', '作者')
作者')
        self        self.tree.tree = t = ttk.Treeview(result_frametk.Treeview(result_frame, columns, columns=columns=columns, show, show='tree='tree headings', headings', height= height=6)
        self6)
        self.tree.tree.heading.heading('#0', text('#0', text='序号')
       ='序号')
        self.t self.tree.headree.heading('书名',ing('书名', text=' text='书名')
书名')
        self.tree        self.tree.heading.heading('演('演播', text='演播播', text='演播')
       ')
        self.t self.tree.headree.heading('作者',ing('作者', text=' text='作者')
作者')
        self        self.tree.column.tree.column('#0('#0', width', width=50=50)
        self.t)
        self.tree.column('ree.column('书名',书名', width= width=300)
300)
        self        self.tree.tree.column('演.column播',('演播', width=150)
 width=150)
        self        self.tree.tree.column.column('作者('作者', width', width=150=150)
       )
        self.tree.p self.tree.pack(sack(side=ide=LEFT,LEFT, fill=B fill=BOTH,OTH, expand=True expand=True)
       )
        scrollbar scrollbar = ttk.Sc = ttk.Scrollbarrollbar(result_frame(result_frame, orient, orient=VERT=VERTICAL, command=selfICAL, command=self.tree.tree.yview.yview)
       )
        scrollbar scrollbar.pack(side.pack(side=RIGHT, fill=RIGHT=Y, fill=Y)
       )
        self.t self.tree.configureree.configure(ys(yscrollcrollcommand=command=scrollbarscrollbar.set)
.set)
        self        self.tree.bind('.tree.bind('<<Tree<<TreeviewSelectviewSelect>>',>>', self.on self.on_select)

_select)

        config        config_frame =_frame = Frame(m Frame(main_frame)
       ain_frame config_frame)
        config_frame.pack(fill.pack=X,(fill=X, pady pady=5=5)
       )
        Label(config Label(config_frame,_frame, text=" text="并发数并发数：").：").pack(spack(side=LEFT)
ide=LEFT)
        Spinbox(config        Spinbox(config_frame,_frame, from_ from_=1=1, to, to=20=20, text, textvariable=self.max_variable=self.max_workers_varworkers_var, width, width==44).pack).pack(side(side=LEFT, pad=LEFT, padx=x=2)
2)
        Label(config_frame, text        Label(config_frame, text="重="重试试：").pack：").pack(side=LEFT(side, padx=(=LEFT, padx=(10,10,0))
0))
        Spin        Spinbox(configbox(config_frame_frame, from_, from_=0=0, to=10, text, to=10variable=self, text.retry_varvariable=self.ret, widthry_var, width=4=4).pack).pack(side(side=LEFT, pad=LEFT, padx=x=2)
2)
        Label        Label(config_frame(config_frame, text, text="超="超时时：：").pack").pack(side(side=LEFT=LEFT, pad, padx=(x=(10,10,0))
        Spin0))
box(config        Spinbox(config_frame,_frame, from_ from_=5, to=60, text=5, to=60, textvariable=selfvariable=self.timeout.timeout_var,_var, width= width=4).4).pack(spack(side=ide=LEFT,LEFT, padx padx=2)
       =2)
        Label(config_frame, text="延迟范围：").pack(side= Label(config_frame, text="延迟范围：").pack(side=LEFT,LEFT, padx=(10 padx=(10,0))
       ,0))
        Entry(config_frame, textvariable Entry(config_frame, textvariable=self.d=self.delay_minelay_min_var,_var, width= width=4).4).pack(spack(side=ide=LEFT)
LEFT)
        Label        Label(config_frame, text(config_frame="-")., text="-").pack(side=pack(side=LEFT)
        EntryLEFT)
        Entry(config_frame, textvariable=self(config_frame, textvariable=self.delay.delay_max_var_max_var, width, width=4=4).pack).pack(side(side=LEFT=LEFT)

       )

        dir_frame dir_frame = Frame(main = Frame_frame)
(main        dir_frame.p_frame)
        dir_frame.pack(fack(fill=X, pill=X, pady=ady=5)
5)
        Label        Label(dir_frame(dir_frame, text, text="保存目录：").pack="保存目录：(side").pack(side=LEFT=LEFT)
       )
        Entry(dir Entry(dir_frame,_frame, textvariable textvariable=self.save=self.save_path_var_path_var, width, width=60=60).pack).pack(side(side=LEFT=LEFT, padx=, pad5,x=5, expand=True expand=True, fill, fill=X)
=X)
        Button(dir_frame        Button, text="浏览(dir_frame, text", command="浏览=self.select_save", command=self.select_dir)._save_dir).pack(spack(side=LEFT)

        statside=LEFT)

        stats_frame =_frame = Frame(m Frame(main_frameain_frame)
       )
        stats_frame stats_frame.pack.pack(fill(fill=X=X,, pady pady=5=5)
       )
        Label(st Label(stats_frameats_frame, text, text="完成="完成:":").pack).pack(side(side=LEFT=LEFT)
       )
        self.com self.completed_labelpleted_label = Label(stats_frame, text="0", fg="green = Label(stats_frame, text="0", fg="green", font", font=("=("Arial", Arial", 10,10, "bold "bold"))
       "))
        self.com self.completed_labelpleted_label.pack(s.pack(side=LEFTide=, padx=(LEFT, pad0,x=(0,10))
        Label10))
        Label(stats(stats_frame,_frame, text=" text="跳过:"跳过:").pack).pack(side(side=LEFT=LEFT)
       )
        self.skipped self.skipped_label =_label = Label(st Label(stats_frameats_frame, text, text="0", f="0", fg="g="orange", font=orange", font=("A("Arial", 10rial", 10, ", "bold"))
bold"))
        self        self.skipped_label.pack.skipped_label.pack(side(side=LEFT=LEFT, padx=(, padx=(0,10))
0,        Label10))
        Label(stats(stats_frame,_frame, text=" text="失败:"失败:").pack).pack(side(side=LEFT=LEFT)
       )
        self.failed_label = Label(stats_frame, text=" self.failed_label = Label(stats_frame, text="0", fg="red0", fg="red", font", font=("=("ArialArial", ", 10, "bold10, "bold"))
       "))
        self.failed_label.pack self.failed_label.pack(side(side=LEFT, pad=LEFT, padx=(x=(0,10))
        Label0,10))
        Label(stats_frame,(stats text="_frame, text="总章总章:").pack:").pack(side=LEFT)
       (side=LEFT)
 self.total        self.total_label =_label = Label(st Label(stats_frameats_frame, text="0", font, text=("="0", fontArial", =("10,Arial", 10, "bold"))
 "bold"))
               self.total self.total_label.pack(side=_label.pack(sLEFT)

ide=        progress_frame =LEFT)

        progress_frame = Frame(m Frame(main_frame)
       ain_frame)
        progress_frame.pack(f progress_frameill=X,.pack(fill=X, pady pady=5=5)
       )
        self.pro self.progress = ttkgress = ttk.Progress.Progressbar(progress_framebar(progress_frame, orient, orient=HORIZ=HONTALORIZONTAL, length, length=100, mode=100, mode='determ='determinate')
inate')
        self.pro        self.progressgress.pack.pack(fill=X(fill=X, expand, expand=True)

        c=True)

        ctrl_frametrl_frame = Frame = Frame(main_frame)
        ctrl_frame.pack(main_frame)
        ctrl_frame.pack(fill(fill=X, pady=X, pady=5)
        self.start_btn=5)
        self.start_btn = Button = Button(ctrl_frame,(ctrl_frame, text=" text="开始下载选中书籍", command开始下载选中书籍", command=self.start=self.start_download, bg_download, bg="green="green", f", fg="white", width=g="white", width=20)
        self.start_20)
        self.start_btn.pack(side=btn.pack(side=LEFT,LEFT, padx=2 padx=2)
       )
        self.pause_btn = self.pause_btn = Button(ctrl Button(ctrl_frame, text_frame, text="暂停="暂停", command=self.p", command=self.pause_dause_download, bg="ownload, bg="orange", fg="white", width=8orange", fg="white", width=8, state=DIS, state=DISABLEDABLED)
       )
        self.p self.pause_ause_btn.pack(sbtn.pack(side=ide=LEFT,LEFT, padx padx=2=2)
       )
        self.res self.resume_btn = Button(ume_btn = Button(ctrl_framectrl_frame, text, text="继续="继续", command", command=self.res=self.resume_dume_download,ownload, bg=" bg="blue",blue", fg fg="white="white", width", width=8, state=DISABLED)
        self.res=8, state=DISABLED)
        self.resume_ume_btn.pbtn.pack(sack(side=ide=LEFT,LEFT, padx padx=2=2)
       )
        self.stop self.stop_btn_btn = Button = Button(ctrl_frame,(ctrl_frame, text=" text="停止",停止", command=self command=self.stop_d.stop_download, state=ownload, state=DISABDISABLED,LED, bg=" bg="red",red", fg fg="white="white", width", width=8=8)
       )
        self.stop self.stop__btn.packbtn.pack(side(side=LEFT=LEFT, pad, padx=2)
        selfx=2)
        self.ret.retry_btn = Button(ry_btn = Button(ctrl_frame, text="重试失败章节", command=self.retry_failed,ctrl_frame, text="重试失败章节", command=self.retry_failed, bg="purple bg="purple", fg="", fwhite",g=" width=15,white", width= state=15,DISABLED)
 state=DISABLED)
        self        self.ret.retry_btn.pry_btn.pack(sack(side=LEFT,ide=LEFT, padx padx=2=2)

       )

        log_frame log_frame = Label = LabelFrame(main_frameFrame(main_frame, text="下载日志")
, text="下载日志")
        log        log_frame.pack(f_frame.pack(fill=BOTHill=B,OTH, expand=True expand=True, p, pady=5)
        selfady=5)
        self.log_text = scrolledtext.Scrolled.log_text = scrolledtext.ScrolledText(logText(log_frame,_frame, wrap= wrap=WORD, height=WORD, height=6)
        self6)
        self.log_text.log_text.pack.pack(fill(fill=BOTH=BOTH, expand, expand=True)

=True)

        fail_frame = LabelFrame(main        fail_frame = LabelFrame(main_frame,_frame, text=" text="失败日志失败日志")
        fail_frame")
        fail_frame.pack(f.pack(fill=BOTH, expandill=BOTH, expand=True,=True, pady pady=5=5)
       )
        self.f self.fail_textail_text = sc = scrolledtext.ScrolledrolledtextText(fail.ScrolledText(fail_frame_frame, wrap, wrap=WORD=WORD, height, height=4=4,, f fg="red")
g="red")
        self.fail        self.fail_text.p_text.pack(fack(fill=Bill=BOTH,OTH, expand=True)

    expand=True)

    def select_save_dir(self def select_save):
       _dir(self):
        dir_path dir_path = filed = filedialog.ialog.askdirectoryaskdirectory(title="(title="选择保存选择保存目录")
目录")
        if        if dir_path dir_path:
           :
            self.save self.save_path_var.set(dir_path_var.set(dir_path)

_path)

    def    def search(self search(self):
       ):
        keyword = keyword = self.search_var.get self.search_var.get().strip().strip()
        if()
        if not not keyword:
 keyword:
            message            messagebox.showwarning("提示",box.showwarning("提示", "请输入搜索关键词")
            "请输入搜索关键词")
            return
 return
        self        self.log(f.log(f"正在"正在搜索：搜索：{keyword}")
        for item{keyword}")
        for item in self in self.tree.tree.get_.get_children():
children():
            self            self.tree.tree.delete(item.delete(item)
       )
        search_url search_url = f = f"https://m.i275.com/search"https://m.i275.php?.com/searchq={urllib.php?q={urllib.parse..parse.quote(keyquote(keyword)}word)}"
       "
        try:
 try:
            headers            headers = {" = {"User-Agent": "MUser-Agent":ozilla/ "Mozilla/5.5.0 (0 (Windows NT 10Windows NT 10.0.0; Win; Win64;64; x64 x64) Apple) AppleWebKitWebKit/537/537.36.36 (KHTML, (K like Gecko)HTML, like Ge Chrome/cko)120. Chrome/120.0.0.0.0.0 Safari0 Safari/537/537.36"}
           .36"}
            resp = resp = requests.get requests.get(search_url(search_url, headers, headers=headers, timeout=15)
           =headers, timeout=15)
            resp. resp.encoding =encoding = 'utf 'utf-8-8'
           '
            if resp if resp.status_code.status_code != 200 != 200:
                self:
                self.log(f"搜索.log(f"搜索请求失败请求失败，状态，状态码 {码 {resp.statusresp.status_code}")
_code}")
                return                return
            soup = BeautifulSoup
            soup =(resp BeautifulSoup(resp.text,.text, 'html 'html.parser')
            items =.parser')
            items = soup.find soup.find_all('a',_all('a', href= href=re.compilere.compile(r'(r'^/^/book/\d+\.html'))
           book/\d+\.html'))
            if not if not items:
 items:
                self                self.log(".log("未找到相关结果未找到相关结果")
               ")
                return
            index return
 =             index1
            for a in items:
                href = a = 1
            for a in items:
                href = a.get('.get('href')
href')
                book                book_url = requests.com_url = requests.compat.urlpat.urljoin("join("https://https://m.im.i275.com275.com/",/", href)
 href)
                title                title_div =_div = a.find a.find('h('h3') or a.find('3') or a.find('div',div', class_ class_='font-medium')
='font-medium')
                if                if not title not title_div:
                    continue_div:
                    continue
                title = title_div
                title = title_div.get_text(strip.get_text(strip=True)
                if not title=True)
                if not title or title == "未知书名":
                    continue
 or title == "未知书名":
                                   info continue
                info_spans_spans = = a.find_all a.find_all('p('p', class', class_='text-xs_='text-xs')
                announ')
                announcer =cer = ""
                ""
                author = ""
                author = ""
                for p for p in info_spans in info:
                   _spans:
                    text = text = p.get p.get_text(st_text(strip=Truerip=True)
                   )
                    if if "演播 "演播" in" in text:
                        ann text:
ouncer                        announcer = text = text.replace(".replace("演播演播", """, "").strip).strip()
                   ()
                    elif "作者" elif "作者" in text in text:
                        author =:
                        author = text.replace text.replace("作者("作者", """, "").strip).strip()
                self.tree.insert()
                self.tree.insert('',('', 'end', text=str(index 'end', text=str(index), values=(title), values=(title, ann, announcer, authorouncer, author), tags), tags=(book_url,=(book_url, title))
 title))
                index +=                 index += 1
1
            self            self.log(f.log(f"搜索"搜索完成，完成，共找到 {index共找到 {index-1-1} 条结果")
        except Exception} 条结果")
        except Exception as e as e:
           :
            self.log(f"搜索异常 self.log(f"搜索异常: {: {e}")

   e}")

    def on def on_select(self, event_select(self, event):
       ):
        selected = self.t selected = self.tree.seree.selection()
        iflection()
        if selected:
 selected:
            item            item = selected[0 = selected[0]
           ]
            self.selected_book_url self.selected_book_url = self.tree.item(item = self.tree.item(item, 'tags')[0]
, 'tags')[0]
            self            self.selected_book.selected_book_title = self.t_title = self.tree.item(item, 'tagsree.item(item, 'tags')[1')[1]
            self.log]
            self.log(f"(f"已选中：已选中：{{self.selectedself.selected_book_title_book_title}")

    def}")

    def start_d start_download(self):
       ownload(self):
        if self if self.running.running:
            messagebox:
            messagebox.showwarning.showwarning("("提示", "已有任务在运行")
           提示", "已有任务在运行")
            return
 return
        if        if not self not self.selected_book.selected_book_url:
            message_url:
            messagebox.shbox.showerror("错误", "请先在owerror("错误", "请先在搜索结果中选择一本小说搜索结果中选择一本小说")
           ")
            return
        save return
        save_dir =_dir = self.save_path_var self.save_path_var.get()..get().strip()
        ifstrip()
        if not save_dir:
            save_dir = not save_dir:
            save_dir = os.get os.getcwd()
           cwd()
            self.save self.save_path_var.set(s_path_var.set(save_dirave_dir)
        book_dir)
        book_dir = os = os.path.join(save.path.join(save_dir,_dir, self.selected_book_title self.selected_book_title)
       )
        self. self.worker = DownloadWorkerworker = DownloadWorker(
            album_url=self.selected(
            album_url=self.selected_book_url_book_url,
           ,
            max_ max_workers=selfworkers=self.max_workers_var.max_.get(),
workers_var.get(),
            request            request_delay=(self_delay=(self.delay.delay_min_var.get(),_min_var.get(), self.delay_max_var.get self.d()),
           elay_max_var.get()),
            retry retry_times_times=self.=self.retryretry_var.get(),
           _var.get(),
            timeout=self.timeout_var.get timeout=self.timeout_var.get(),
           (),
            save_dir= save_dir=book_dir,
book_dir,
            log            log_queue=self.log_queue_queue=self.log_queue
       
        )
        self.r )
        self.running =unning = True
        self True
        self.start_.start_btn.config(state=btn.config(state=DISABDISABLED)
LED)
        self.pause        self.pause_btn_btn.config(state=NORMAL.config(state=NORMAL)
       )
        self.resume_ self.resume_btn.config(state=DISABLED)
btn.config(state=DISABLED)
        self        self.stop_btn.config(state=NORMAL)
        self.stop_btn.config(state=NORMAL)
        self.ret.retry_ry_btn.configbtn.config(state=DISAB(state=DISABLED)
        selfLED)
        self.fail.fail_text.delete_text.delete(1(1.0, END.0, END)
       )
        self. self.worker_thread = threadingworker_thread = threading.Thread(target.Thread(target=self.=self.worker.startworker.start_download_download, args, args=(book_dir,=(book_dir,))
       ))
        self.worker_thread self.worker_thread.da.daemon =emon = True
 True
        self        self.worker.worker_thread.start()

   _thread.start()

    def pause def pause_download(self):
_download(self):
        if        if self.worker self.worker:
            self:
            self.worker.pause.worker()
           .pause()
            self.pause_ self.pause_btn.configbtn.config(state=(state=DISABLED)
DISABLED)
            self            self.resume_btn.resume_btn.config(state.config(state=NORMAL)
           =NORMAL)
            self.log self.log("下载("下载已暂停已暂停")

   ")

    def resume_download def resume(self):
_download(self):
        if self.        ifworker:
 self.            self.workerworker:
            self.worker.resume.resume()
           ()
            self.p self.pause_btn.config(state=NORMAL)
ause_btn.config(state=NORMAL)
            self            self.resume.resume_btn_btn.config(state=DIS.config(state=DISABLEDABLED)
            self.log)
            self.log("下载("下载已继续已继续")

    def stop_download(self):
        if self.")

    def stop_download(self):
        if self.worker:
worker:
            self            self.worker.worker.stop_flag.stop_flag = True
            = True
            self. self.worker.resume()
        self.runningworker.resume()
        self.running = False = False
       
        self.start self.start_btn_btn.config(state.config(state=NORMAL)
       =NORMAL)
        self.p self.pause_btn.configause_btn.config(state=(state=DISABLED)
        selfDISABLED)
        self.resume.resume_btn_btn.config(state.config(state=DIS=DISABLEDABLED)
       )
        self.stop self.stop_btn_btn.config(state.config(state=DIS=DISABLED)
       ABLED)
        self.retry_btn.config(state self.retry_btn.config(state=NORMAL=NORMAL if hasattr(self if hasattr(self.worker.worker, 'failed_ch, 'failed_chapters')apters') and self.worker and self.worker.failed.failed_chapters_chapters else DISABLED)
        else DISABLED)
        self.log("用户 self.log("用户请求停止请求停止...")

   ...")

 def retry    def retry_failed(self):
        if not self_failed(self):
        if not self.worker:
           .worker:
            return
        if return
 self.running:
        if self.running:
            message            messagebox.showwarning("box.showwarning("提示",提示", "请 "请等待当前任务完成等待当前任务完成")
           ")
            return
        self return
        self.running.running = True
        self.start = True
        self.start_btn_btn.config(state=DIS.config(state=DISABLEDABLED)
        self.p)
        self.pause_ause_btn.config(state=NORMAL)
btn.config(state=NORMAL)
        self        self.resume_btn.resume_btn.config(state.config(state=DISABLED=DISABLED)
       )
        self.stop_btn self.stop_btn.config(state.config(state=NORMAL=NORMAL)
       )
        self. self.retryretry_btn_btn.config(state=DIS.config(state=DISABLED)
       ABLED)
        self.f self.fail_text.delete(1.ail_text.delete(1.0, END)
0,        self.worker_thread = threading.Thread END)
        self.worker_thread = threading.Thread(target=self(target=self.worker.ret.workerry_f.retry_failed, args=(ailed,os.path args=(os.path.join(self.join(self.save_path.save_path_var.get_var.get().strip().strip() or() or os.get os.getcwdcwd(), self.selected_book_title),))
       (), self.selected_book_title),))
        self. self.worker_thread.daworker_thread.daemon = True
emon =        self.worker_thread.start True
        self.worker_thread.start()

   ()

    def update def update_log(self_log(self):
        while True):
        while True:
            try:
:
            try:
                msg                msg = self.log_queue = self.get_now.log_queue.get_nowait()
ait()
                if msg.start                if msg.startswith("FAIL:"swith("FAIL:"):
                   ):
                    parts = parts = msg msg.split(':',.split(':', 2 2)
                    if len)
                   (parts if len(parts) == 3) ==:
                        3:
                        self.f self.fail_text.insert(ail_text.insert(END,END, f" f"第第 { {partsparts[1]} 章[1]}：{ 章：{parts[2]}\nparts[2]}\n")
                        self.f")
                        self.fail_textail_text..see(ENDsee(END)
               )
                elif msg elif msg == " == "FAIL_CFAIL_CLEARLEAR":
                   ":
                    self.f self.fail_textail_text.delete(1..delete(1.0,0, END)
 END)
                elif                elif msg.start msg.startswith("swith("STATS:"):
STATS                    parts:"):
                    parts = msg = msg.split(':.split(':')
                   ')
                    if len(parts) == if len(parts) == 5 5:
:
                        completed = int(parts                        completed = int(parts[1])
                        skipped = int[1])
                        skipped = int(parts[2])
(parts[2])
                        failed = int(                        failed =parts int(parts[3])
                        total[3])
 = int                        total = int(parts(parts[4[4])
                        self.com])
                        self.completed_labelpleted_label.config(text.config(text=str(completed))
=str(                        self.skicompleted))
                        selfpped_label.skipped_label.config(text.config(text=str(s=str(skippedkipped))
                        self.failed_label))
                        self.f.config(textailed_label.config(text=str(failed))
=str(f                        selfailed))
                        self.total_label.total_label.config(text.config(text=str(total))
                        done=str(total))
                        = completed + done = skipped
                        if completed + total > skipped
                        if 0 total > :
                            percent =0:
                            percent = (done (done / total) * 100 / total) * 100
                           
                            self.pro self.progress['gress['value']value'] = percent = percent
               
                else:
                    self else:
                    self.log_text.log_text.insert(.insert(END, msg +END, "\n msg + "\n")
                    self.log")
                   _text. self.log_text.see(see(END)
            exceptEND)
            except queue.Empty queue.Empty:
               :
                break
 break
        if        if self. self.worker_thread and notworker_thread and not self.worker_thread.is_al self.worker_thread.is_alive():
ive():
            if            if self.r self.running:
unning:
                self                self.running.running = False = False
               
                self.start_btn self.start_btn.config(state.config(state=NORMAL)
               =NORMAL)
                self.p self.pause_ause_btn.configbtn.config(state=DISAB(state=DISABLED)
LED)
                self                self.resume.resume_btn_btn.config(state=DISABLED)
                self.stop_btn.config(state=DIS.config(state=DISABLED)
                self.stop_btn.config(state=DISABLEDABLED)
                self.)
                self.retryretry_btn.config(state_btn.config(state=NORMAL=NORMAL if hasattr(self if hasattr(self.worker.worker, 'failed_ch, 'failed_chapters')apters') and self and self.worker.worker.failed_chapters.failed_chapters else DIS else DISABLEDABLED)
               )
                self.log self.log("下载线程已("下载线程已结束")
        self结束")
        self.after.after(100, self(100.update_log, self.update_log)

   )

    def log def log(self,(self, msg):
 msg):
        self        self.log_queue.log_queue.put(msg.put(msg)

if)

if __name __name__ ==__ == "__main__":
    "__main__":
    app = app = Downloader DownloaderApp()
App()
    app    app.mainloop.mainloop()
```()
