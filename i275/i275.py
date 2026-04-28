import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import requests
import os
import threading
from tkinter import filedialog, messagebox
from urllib.parse import urlparse
import time

# 全局变量：控制下载线程状态
is_downloading = False
download_thread = None

class AudioBookDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title("有声书下载工具")
        self.root.geometry("800x600")
        self.root.resizable(False, False)
        
        # 设置主题（ttkbootstrap提供多种主题：cosmo, flatly, journal, minty, pulse等）
        self.style = ttk.Style(theme="flatly")
        
        # 构建UI
        self._build_ui()
    
    def _build_ui(self):
        # 1. 顶部输入区域
        top_frame = ttk.Frame(self.root, padding=20)
        top_frame.pack(fill=X, pady=10)
        
        # 有声书URL输入
        url_label = ttk.Label(top_frame, text="有声书URL：", font=("微软雅黑", 12))
        url_label.grid(row=0, column=0, sticky=W, padx=5, pady=5)
        
        self.url_var = tk.StringVar()
        url_entry = ttk.Entry(
            top_frame, textvariable=self.url_var, font=("微软雅黑", 12), width=50
        )
        url_entry.grid(row=0, column=1, padx=5, pady=5)
        
        # 保存路径选择
        path_label = ttk.Label(top_frame, text="保存路径：", font=("微软雅黑", 12))
        path_label.grid(row=1, column=0, sticky=W, padx=5, pady=5)
        
        self.path_var = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads"))
        path_entry = ttk.Entry(
            top_frame, textvariable=self.path_var, font=("微软雅黑", 12), width=50
        )
        path_entry.grid(row=1, column=1, padx=5, pady=5)
        
        path_btn = ttk.Button(
            top_frame, text="选择", command=self._select_save_path, bootstyle=PRIMARY
        )
        path_btn.grid(row=1, column=2, padx=5, pady=5)
        
        # 2. 中间控制区域
        control_frame = ttk.Frame(self.root, padding=10)
        control_frame.pack(fill=X, pady=10)
        
        # 下载按钮
        self.download_btn = ttk.Button(
            control_frame, text="开始下载", command=self._start_download, bootstyle=SUCCESS,
            font=("微软雅黑", 12), width=15
        )
        self.download_btn.pack(side=LEFT, padx=20)
        
        # 暂停/取消按钮
        self.cancel_btn = ttk.Button(
            control_frame, text="取消下载", command=self._cancel_download, bootstyle=DANGER,
            font=("微软雅黑", 12), width=15, state=DISABLED
        )
        self.cancel_btn.pack(side=LEFT, padx=20)
        
        # 3. 进度条区域
        progress_frame = ttk.Frame(self.root, padding=10)
        progress_frame.pack(fill=X, pady=10, padx=20)
        
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            progress_frame, variable=self.progress_var, maximum=100, bootstyle=SUCCESS
        )
        self.progress_bar.pack(fill=X, pady=5)
        
        self.progress_label = ttk.Label(
            progress_frame, text="进度：0%", font=("微软雅黑", 10)
        )
        self.progress_label.pack(side=RIGHT)
        
        # 4. 日志输出区域
        log_frame = ttk.Frame(self.root, padding=10)
        log_frame.pack(fill=BOTH, expand=True, pady=10, padx=20)
        
        log_label = ttk.Label(log_frame, text="下载日志：", font=("微软雅黑", 12))
        log_label.pack(anchor=W, pady=5)
        
        # 日志文本框（带滚动条）
        self.log_text = tk.Text(
            log_frame, font=("微软雅黑", 10), wrap=tk.WORD, bg="#f8f9fa", fg="#212529"
        )
        self.log_text.pack(side=LEFT, fill=BOTH, expand=True)
        
        scrollbar = ttk.Scrollbar(log_frame, orient=VERTICAL, command=self.log_text.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.log_text.config(yscrollcommand=scrollbar.set)
        
        # 禁用日志文本框编辑
        self.log_text.config(state=DISABLED)
    
    def _select_save_path(self):
        """选择保存路径"""
        path = filedialog.askdirectory(title="选择保存路径")
        if path:
            self.path_var.set(path)
            self._log(f"已选择保存路径：{path}")
    
    def _log(self, msg):
        """日志输出"""
        self.log_text.config(state=NORMAL)
        self.log_text.insert(END, f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        self.log_text.see(END)  # 自动滚动到最后
        self.log_text.config(state=DISABLED)
    
    def _update_progress(self, value):
        """更新进度条和进度文本"""
        self.progress_var.set(value)
        self.progress_label.config(text=f"进度：{int(value)}%")
    
    def _start_download(self):
        """启动下载（线程执行，避免GUI卡死）"""
        global is_downloading, download_thread
        
        # 校验输入
        url = self.url_var.get().strip()
        save_path = self.path_var.get().strip()
        
        if not url:
            messagebox.showerror("错误", "请输入有声书URL！")
            return
        if not os.path.exists(save_path):
            os.makedirs(save_path)
            self._log(f"创建保存路径：{save_path}")
        
        # 防止重复下载
        if is_downloading:
            messagebox.showwarning("提示", "正在下载中，请等待！")
            return
        
        # 更新UI状态
        is_downloading = True
        self.download_btn.config(state=DISABLED)
        self.cancel_btn.config(state=NORMAL)
        self._update_progress(0)
        self._log(f"开始下载：{url}")
        
        # 启动下载线程
        download_thread = threading.Thread(
            target=self._download_core, args=(url, save_path), daemon=True
        )
        download_thread.start()
    
    def _cancel_download(self):
        """取消下载"""
        global is_downloading
        if is_downloading:
            is_downloading = False
            self._log("已触发取消下载，等待线程终止...")
    
    def _download_core(self, url, save_path):
        """
        核心下载逻辑（需根据目标平台修改）
        示例：模拟下载 + 进度更新，实际需替换为解析音频URL、批量下载等逻辑
        """
        global is_downloading
        try:
            # 模拟解析有声书信息（实际需替换为平台解析逻辑）
            self._log("解析有声书信息中...")
            time.sleep(1)
            book_name = "示例有声书"
            chapter_count = 10  # 模拟10个章节
            
            # 模拟逐个章节下载
            for chapter in range(1, chapter_count + 1):
                if not is_downloading:  # 检测取消信号
                    raise Exception("用户取消下载")
                
                chapter_name = f"{book_name}_第{chapter}章.mp3"
                chapter_path = os.path.join(save_path, chapter_name)
                
                # 模拟下载（实际需替换为真实的音频下载请求）
                self._log(f"开始下载章节 {chapter}/{chapter_count}：{chapter_name}")
                for i in range(10):
                    if not is_downloading:
                        raise Exception("用户取消下载")
                    time.sleep(0.2)  # 模拟下载耗时
                    progress = (chapter - 1) * 10 + i + 1
                    self._update_progress(progress)
                
                # 模拟下载完成
                self._log(f"章节 {chapter} 下载完成：{chapter_path}")
            
            # 下载完成
            self._log(f"所有章节下载完成！保存路径：{save_path}")
            self._update_progress(100)
        
        except Exception as e:
            self._log(f"下载失败：{str(e)}")
        finally:
            # 恢复UI状态
            is_downloading = False
            self.root.after(0, lambda: self.download_btn.config(state=NORMAL))
            self.root.after(0, lambda: self.cancel_btn.config(state=DISABLED))

if __name__ == "__main__":
    root = ttk.Window()  # ttkbootstrap的Window替代tk.Tk
    app = AudioBookDownloader(root)
    root.mainloop()
