import os
import json
import re
import threading
import requests
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
    'Referer': 'https://tingyou.fm/',
}

# 默认API模板（需要根据实际抓包修改）
DEFAULT_API_URL = 'https://tingyou.fm/api/audio/{chapter_id}'

def find_keys_recursive(obj, key_substring):
    """递归查找包含特定子串的键，返回第一个匹配的值"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if key_substring in k:
                return v
            res = find_keys_recursive(v, key_substring)
            if res is not None:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_keys_recursive(item, key_substring)
            if res is not None:
                return res
    return None

class Downloader:
    def __init__(self, album_url, save_dir, api_url_template=DEFAULT_API_URL):
        self.album_url = album_url
        self.save_dir = save_dir
        self.api_url_template = api_url_template
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def get_album_info(self):
        """获取专辑信息和章节列表"""
        try:
            resp = self.session.get(self.album_url, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            raise Exception(f"获取专辑页面失败: {e}")

        html = resp.text
        # 使用BeautifulSoup解析
        soup = BeautifulSoup(html, 'html.parser')
        script_tag = soup.find('script', id='__NUXT_DATA__')
        if not script_tag:
            # 调试：保存页面源码
            with open('debug.html', 'w', encoding='utf-8') as f:
                f.write(html)
            raise Exception("未找到 __NUXT_DATA__ 脚本标签，页面源码已保存至 debug.html")
        json_str = script_tag.string
        data = json.loads(json_str)

        # 可选：保存调试数据
        with open('debug.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        album_info = find_keys_recursive(data, 'album-detail-')
        chapters_data = find_keys_recursive(data, 'album-chapters-')
        if not album_info or not chapters_data:
            raise Exception("无法从数据中提取专辑信息或章节列表，请检查 debug.json 结构")

        chapters = chapters_data.get('chapters', [])
        title = album_info.get('title', '未知专辑')
        return title, chapters

    def get_audio_url(self, chapter_id):
        """根据章节ID获取音频URL"""
        url = self.api_url_template.format(chapter_id=chapter_id)
        try:
            resp = self.session.get(url, timeout=10)
            resp.raise_for_status()
            # 假设API返回JSON，包含 'url' 字段
            data = resp.json()
            return data.get('url')
        except Exception as e:
            raise Exception(f"获取音频URL失败 (章节ID: {chapter_id}): {e}")

    def download_audio(self, chapter, progress_callback):
        """下载单个章节"""
        chapter_id = chapter['id']
        chapter_title = chapter['title']
        safe_title = re.sub(r'[\\/*?:"<>|]', '', chapter_title)
        filename = f"{chapter['index']:03d} - {safe_title}.mp3"
        filepath = os.path.join(self.save_dir, filename)

        audio_url = self.get_audio_url(chapter_id)
        if not audio_url:
            raise Exception(f"未获取到音频URL: {chapter_title}")

        resp = self.session.get(audio_url, stream=True, timeout=30)
        resp.raise_for_status()
        total_size = int(resp.headers.get('content-length', 0))
        downloaded = 0
        with open(filepath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total_size)
        return filepath

# 以下GUI部分保持不变，但需要导入新增的模块
class GUI:
    # ... （同之前的GUI代码）
    pass

if __name__ == "__main__":
    gui = GUI()
    gui.run()
