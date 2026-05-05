import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import uuid

def generate():
    try:
        n = int(entry_count.get())
        if n <= 0:
            raise ValueError
    except ValueError:
        messagebox.showerror("输入错误", "请输入一个有效的正整数")
        return
    result_text.delete(1.0, tk.END)
    uuids = [str(uuid.uuid4()) for _ in range(n)]
    result_text.insert(tk.END, "\n".join(uuids))

def copy_all():
    content = result_text.get(1.0, tk.END).strip()
    if content:
        root.clipboard_clear()
        root.clipboard_append(content)
        messagebox.showinfo("已复制", "所有 UUID 已复制到剪贴板")
    else:
        messagebox.showwarning("无内容", "请先生成 UUID")

root = tk.Tk()
root.title("UUID v4 生成器")
root.geometry("650x450")
root.minsize(500, 350)

style = ttk.Style()
style.theme_use("clam")

style.configure("TLabel", font=("Segoe UI", 11))
style.configure("TButton", font=("Segoe UI", 10, "bold"), padding=6)
style.configure("TEntry", font=("Segoe UI", 11), padding=4)
style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"), foreground="#2c3e50")
style.configure("Accent.TButton", foreground="#ffffff", background="#3498db")
style.map("Accent.TButton",
          background=[("active", "#2980b9"), ("pressed", "#21618c")],
          foreground=[("active", "#ffffff")])
style.configure("Copy.TButton", foreground="#2c3e50", background="#ecf0f1")
style.map("Copy.TButton",
          background=[("active", "#d5dbdb"), ("pressed", "#bdc3c7")])

main_frame = ttk.Frame(root, padding=(20, 15))
main_frame.pack(fill=tk.BOTH, expand=True)

title_label = ttk.Label(main_frame, text="UUID v4 批量生成", style="Title.TLabel")
title_label.pack(anchor=tk.W, pady=(0, 10))

control_frame = ttk.Frame(main_frame)
control_frame.pack(fill=tk.X, pady=(0, 10))

ttk.Label(control_frame, text="生成个数：").pack(side=tk.LEFT)
entry_count = ttk.Entry(control_frame, width=10, font=("Segoe UI", 11))
entry_count.pack(side=tk.LEFT, padx=(5, 0))
entry_count.insert(0, "5")

btn_generate = ttk.Button(control_frame, text="开始生成", style="Accent.TButton", command=generate)
btn_generate.pack(side=tk.LEFT, padx=10)

btn_copy = ttk.Button(control_frame, text="复制全部", style="Copy.TButton", command=copy_all)
btn_copy.pack(side=tk.LEFT)

separator = ttk.Separator(main_frame, orient=tk.HORIZONTAL)
separator.pack(fill=tk.X, pady=5)

result_frame = ttk.Frame(main_frame)
result_frame.pack(fill=tk.BOTH, expand=True)

result_text = scrolledtext.ScrolledText(
    result_frame,
    wrap=tk.WORD,
    font=("Consolas", 10),
    bg="#f9f9f9",
    fg="#2c3e50",
    insertbackground="#2c3e50",
    relief=tk.FLAT,
    borderwidth=1,
    highlightthickness=1,
    highlightcolor="#bdc3c7",
    highlightbackground="#ecf0f1"
)
result_text.pack(fill=tk.BOTH, expand=True)

root.mainloop()