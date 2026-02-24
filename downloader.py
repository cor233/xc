#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import json
import requests
import argparse
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import colorama
from colorama import Fore, Style

colorama.init()

class TingShuDownloader:
    def __init__(self, max_workers=3, delay=1, output_dir="downloads"):
        self.max_workers = max_workers
        self.delay = delay
        self.output_dir = output_dir
        self.base_url = "https://m.i275.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://m.i275.com/'
        })
        
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
    
    def search_books(self, keyword):
        self.log(f"正在搜索: {keyword}", "info")
        
        search_url = f"{self.base_url}/search.php"
        params = {'q': keyword}
        
        try:
            response = self.session.get(search_url, params=params, timeout=15)
            response.encoding = 'utf-8'
            html = response.text
            
            books = []
            
            pattern = r'<a\s+href="(/book/\d+\.html)"[^>]*class="flex p-4 gap-4[^"]*"[^>]*>.*?</a>'
            
            for match in re.finditer(pattern, html, re.DOTALL):
                item_html = match.group(0)
                book_url = match.group(1)
                
                cover_match = re.search(r'<img[^>]*src="([^"]+)"[^>]*alt="([^"]*)"', item_html)
                cover = cover_match.group(1) if cover_match else ''
                
                title_match = re.search(r'<h3[^>]*class="[^"]*text-base font-bold[^"]*"[^>]*>([^<]+)</h3>', item_html)
                title = title_match.group(1).strip() if title_match else '未知书名'
                
                author = '未知'
                broadcaster_match = re.search(r'<span[^>]*class="bg-gray-100[^"]*"[^>]*>演播</span>\s*([^<]+)', item_html)
                if broadcaster_match:
                    author = broadcaster_match.group(1).strip()
                
                if author == '未知':
                    writer_match = re.search(r'<span[^>]*class="bg-gray-100[^"]*"[^>]*>作者</span>\s*([^<]+)', item_html)
                    if writer_match:
                        author = writer_match.group(1).strip()
                
                desc = ''
                desc_match = re.search(r'<p[^>]*class="text-xs text-gray-400[^"]*"[^>]*>([^<]+)</p>', item_html)
                if desc_match:
                    desc = desc_match.group(1).strip()
                
                books.append({
                    'title': title,
                    'url': urljoin(self.base_url, book_url),
                    'cover': cover,
                    'author': author,
                    'description': desc
                })
            
            count_match = re.search(r'“<span[^>]*class="text-purple-600[^"]*"[^>]*>([^<]+)</span>” 的结果 \((\d+)\)', html)
            if count_match:
                keyword_display = count_match.group(1)
                total = int(count_match.group(2))
                self.log(f"找到 {total} 个关于 '{keyword_display}' 的结果", "success")
            
            return books
            
        except Exception as e:
            self.log(f"搜索失败: {e}", "error")
            return []
    
    def get_hot_books(self, limit=10):
        self.log("获取热门书籍...", "info")
        
        try:
            response = self.session.get(self.base_url, timeout=15)
            response.encoding = 'utf-8'
            html = response.text
            
            books = []
            
            pattern = r'<a\s+href="(/book/\d+\.html)"[^>]*class="bg-white rounded-lg shadow[^"]*"[^>]*>.*?<img[^>]*src="([^"]+)"[^>]*>.*?<div[^>]*class="font-medium[^"]*"[^>]*>(.*?)</div>.*?<div[^>]*class="text-xs text-gray-500[^"]*"[^>]*>(.*?)</div>'
            
            matches = re.findall(pattern, html, re.DOTALL)
            
            for match in matches[:limit]:
                book_url, cover_img, title, author = match
                title = re.sub(r'<[^>]+>', '', title).strip()
                author = re.sub(r'<[^>]+>', '', author).strip()
                author = author.replace('演播', '').strip()
                
                books.append({
                    'title': title,
                    'url': urljoin(self.base_url, book_url),
                    'cover': cover_img if cover_img.startswith('http') else urljoin(self.base_url, cover_img),
                    'author': author
                })
            
            return books
            
        except Exception as e:
            self.log(f"获取热门书籍失败: {e}", "error")
            return []
    
    def extract_book_info(self, catalog_url):
        self.log(f"正在获取目录页: {catalog_url}")
        
        try:
            response = self.session.get(catalog_url, timeout=15)
            response.encoding = 'utf-8'
            html = response.text
            
            book_title = "未知书名"
            title_match = re.search(r'<h1[^>]*>(.*?)</h1>', html)
            if title_match:
                book_title = title_match.group(1).strip()
            
            author = "未知"
            author_match = re.search(r'演播[：:]\s*<span[^>]*class="[^"]*text-gray-800[^"]*"[^>]*>(.*?)</span>', html)
            if author_match:
                author = author_match.group(1).strip()
            
            cover = ""
            cover_match = re.search(r'<img[^>]*src="([^"]+)"[^>]*class="[^"]*object-cover[^"]*"[^>]*>', html)
            if cover_match:
                cover = cover_match.group(1)
            
            chapters = []
            
            pattern = r'<a[^>]*href="(/play/\d+/\d+\.html)"[^>]*>.*?<span[^>]*class="[^"]*text-xs[^"]*"[^>]*>(\d+)\.?</span>.*?<span[^>]*class="[^"]*text-sm[^"]*"[^>]*>(.*?)</span>'
            
            for match in re.finditer(pattern, html, re.DOTALL):
                url, num, title = match.groups()
                
                chapters.append({
                    'num': int(num),
                    'title': title.strip(),
                    'url': urljoin(catalog_url, url)
                })
            
            if not chapters:
                link_pattern = r'<a[^>]*href="(/play/\d+/\d+\.html)"[^>]*>.*?(\d+)\.\s*(.*?)</a>'
                for match in re.finditer(link_pattern, html, re.DOTALL):
                    url, num, title = match.groups()
                    chapters.append({
                        'num': int(num),
                        'title': title.strip(),
                        'url': urljoin(catalog_url, url)
                    })
            
            chapters.sort(key=lambda x: x['num'])
            
            self.log(f"找到书籍: {book_title}", "success")
            self.log(f"作者/演播: {author}", "info")
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
    
    def get_audio_url(self, chapter_url):
        try:
            response = self.session.get(chapter_url, timeout=15)
            html = response.text
            
            patterns = [
                r'url:\s*[\'"]([^\'"]+\.m4a[^\'"]*)[\'"]',
                r'<audio[^>]+src=["\']([^"\']+)["\']',
                r'(https?:)?//[^\'"\s]*\.xmcdn\.com[^\'"\s]*\.m4a[^\'"\s]*',
                r'audio: \[\{.*?url:\s*[\'"]([^\'"]+)[\'"]'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    url = match.group(1) if match.group(1) else match.group(0)
                    if url.startswith('//'):
                        url = 'https:' + url
                    return url
            
            return None
            
        except Exception as e:
            self.log(f"获取音频URL失败: {e}", "error")
            return None
    
    def download_audio(self, chapter, book_info):
        chapter_num = chapter['num']
        chapter_title = chapter['title']
        chapter_url = chapter['url']
        
        safe_title = re.sub(r'[\\/*?:"<>|]', '', chapter_title)
        filename = f"{str(chapter_num).zfill(4)}_{safe_title}.m4a"
        filepath = os.path.join(self.output_dir, book_info['title'], filename)
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            self.log(f"章节 {chapter_num} 已存在，跳过", "warning")
            return True
        
        self.log(f"获取章节 {chapter_num} 音频地址...", "info")
        audio_url = self.get_audio_url(chapter_url)
        
        if not audio_url:
            self.log(f"章节 {chapter_num} 获取音频地址失败", "error")
            return False
        
        try:
            self.log(f"下载章节 {chapter_num}: {chapter_title[:30]}...", "info")
            
            response = self.session.get(audio_url, stream=True, timeout=30)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            
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
            
            self.log(f"章节 {chapter_num} 下载完成", "success")
            return True
            
        except Exception as e:
            self.log(f"章节 {chapter_num} 下载失败: {e}", "error")
            if os.path.exists(filepath):
                os.remove(filepath)
            return False
    
    def download_all(self, catalog_url):
        self.log("=" * 60, "info")
        self.log("275听书网 批量下载器", "info")
        self.log(f"目标网址: {catalog_url}", "info")
        self.log(f"保存目录: {self.output_dir}", "info")
        self.log(f"并发数: {self.max_workers}", "info")
        self.log("=" * 60, "info")
        
        book_info = self.extract_book_info(catalog_url)
        
        if not book_info or not book_info['chapters']:
            self.log("没有找到任何章节，退出", "error")
            return
        
        chapters = book_info['chapters']
        
        book_dir = os.path.join(self.output_dir, book_info['title'])
        os.makedirs(book_dir, exist_ok=True)
        
        info_file = os.path.join(book_dir, "_info.json")
        with open(info_file, 'w', encoding='utf-8') as f:
            json.dump(book_info, f, ensure_ascii=False, indent=2)
        
        self.log(f"书籍信息已保存到: {info_file}", "success")
        
        success_count = 0
        fail_count = 0
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_chapter = {
                executor.submit(self.download_audio, chapter, book_info): chapter 
                for chapter in chapters
            }
            
            for future in tqdm(as_completed(future_to_chapter), 
                             total=len(chapters), 
                             desc="总体进度",
                             unit="章",
                             ncols=70):
                chapter = future_to_chapter[future]
                try:
                    if future.result():
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    self.log(f"章节 {chapter['num']} 处理异常: {e}", "error")
                    fail_count += 1
                
                time.sleep(self.delay)
        
        self.log("=" * 60, "info")
        self.log(f"下载完成！", "success")
        self.log(f"成功: {success_count} 章", "success")
        self.log(f"失败: {fail_count} 章", "error" if fail_count > 0 else "info")
        self.log(f"保存位置: {os.path.abspath(book_dir)}", "info")
        self.log("=" * 60, "info")
    
    def interactive_search(self):
        print(f"\n{Fore.CYAN}🔍 275听书网 搜索下载器{Style.RESET_ALL}")
        print("=" * 50)
        
        while True:
            print(f"\n{Fore.YELLOW}请选择操作:{Style.RESET_ALL}")
            print("1. 搜索书籍")
            print("2. 查看热门书籍")
            print("3. 直接输入URL下载")
            print("0. 退出")
            
            choice = input(f"\n{Fore.GREEN}请输入数字: {Style.RESET_ALL}").strip()
            
            if choice == '1':
                keyword = input(f"{Fore.CYAN}请输入搜索关键词: {Style.RESET_ALL}").strip()
                if not keyword:
                    continue
                
                books = self.search_books(keyword)
                
                if not books:
                    self.log("没有找到相关书籍", "warning")
                    continue
                
                print(f"\n{Fore.YELLOW}找到 {len(books)} 本书:{Style.RESET_ALL}")
                for i, book in enumerate(books, 1):
                    print(f"\n{i}. {Fore.GREEN}{book['title']}{Style.RESET_ALL}")
                    print(f"   作者/演播: {book['author']}")
                    if book.get('description'):
                        print(f"   简介: {book['description'][:50]}...")
                    print(f"   链接: {book['url']}")
                
                choice = input(f"\n{Fore.CYAN}选择要下载的书籍编号 (输入0返回): {Style.RESET_ALL}").strip()
                
                if choice.isdigit() and 1 <= int(choice) <= len(books):
                    selected = books[int(choice)-1]
                    self.download_all(selected['url'])
                    
            elif choice == '2':
                books = self.get_hot_books(15)
                
                if not books:
                    self.log("获取热门书籍失败", "error")
                    continue
                
                print(f"\n{Fore.YELLOW}热门书籍:{Style.RESET_ALL}")
                for i, book in enumerate(books, 1):
                    print(f"{i}. {Fore.GREEN}{book['title']}{Style.RESET_ALL} - {book['author']}")
                
                choice = input(f"\n{Fore.CYAN}选择要下载的书籍编号 (输入0返回): {Style.RESET_ALL}").strip()
                
                if choice.isdigit() and 1 <= int(choice) <= len(books):
                    selected = books[int(choice)-1]
                    self.download_all(selected['url'])
                    
            elif choice == '3':
                url = input(f"{Fore.CYAN}请输入书籍目录页URL: {Style.RESET_ALL}").strip()
                if url:
                    self.download_all(url)
                    
            elif choice == '0':
                print(f"{Fore.YELLOW}再见！{Style.RESET_ALL}")
                break

def main():
    parser = argparse.ArgumentParser(description='275听书网 批量下载器 (支持搜索)')
    parser.add_argument('url', nargs='?', help='书籍目录页URL (可选，不提供则进入交互模式)')
    parser.add_argument('-o', '--output', default='downloads', help='输出目录 (默认: downloads)')
    parser.add_argument('-w', '--workers', type=int, default=3, help='并发下载数 (默认: 3)')
    parser.add_argument('-d', '--delay', type=float, default=1.0, help='请求延迟秒数 (默认: 1)')
    parser.add_argument('-s', '--search', help='直接搜索关键词')
    
    args = parser.parse_args()
    
    downloader = TingShuDownloader(
        max_workers=args.workers,
        delay=args.delay,
        output_dir=args.output
    )
    
    if args.search:
        books = downloader.search_books(args.search)
        if books:
            print(f"\n{Fore.YELLOW}搜索结果:{Style.RESET_ALL}")
            for i, book in enumerate(books, 1):
                print(f"\n{i}. {book['title']}")
                print(f"   作者: {book['author']}")
                print(f"   链接: {book['url']}")
        return
    
    if args.url:
        downloader.download_all(args.url)
    else:
        downloader.interactive_search()

if __name__ == "__main__":
    main()
