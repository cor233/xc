import sys
import subprocess
import os

def install_playwright():
    print("正在安装 playwright...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
    print("正在安装浏览器...")
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
    print("安装完成")

if __name__ == "__main__":
    install_playwright()
