import os
import re
import sys
import time
import threading
import requests
import tkinter as tk
from tkinter import ttk
from concurrent.futures import ThreadPoolExecutor, as_completed


BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0')

DEFAULT_URL = 'https://api.365ting.com/tingyou/stream?album=8513553762&idx=721'


def make_headers(url):
    return {
        'User-Agent': BROWSER_UA,
        'Referer': url,
        'Accept': '*/*',
        'Accept-Encoding': 'identity',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }


def probe(url, timeout=(10, 20)):
    headers = make_headers(url)
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=timeout)
        try:
            ct = (r.headers.get('Content-Type') or '').lower()
            if r.status_code == 503 or 'json' in ct:
                return {'ok': False, 'reason': 'rate_limited_%s' % r.status_code}
            if r.status_code not in (200, 206):
                return {'ok': False, 'reason': 'bad_status_%s' % r.status_code}
            accept = 'bytes' in (r.headers.get('Accept-Ranges') or '').lower()
            total = None
            cl = r.headers.get('Content-Length')
            cr = r.headers.get('Content-Range') or ''
            m = re.search(r'/(\d+)\s*$', cr)
            if m:
                total = int(m.group(1))
            elif cl and cl.isdigit() and r.status_code == 200:
                total = int(cl)
            return {'ok': True, 'status': r.status_code, 'ct': ct,
                    'total': total, 'range': accept}
        finally:
            r.close()
    except Exception as e:
        return {'ok': False, 'reason': 'exception: %s' % e}


def test_single(url, stop_event, limit_bytes=None):
    headers = make_headers(url)
    t0 = time.time()
    bytes_done = 0
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=(10, 30))
        ct = (r.headers.get('Content-Type') or '').lower()
        if r.status_code in (429, 503) or 'json' in ct:
            return {'ok': False, 'reason': 'rate_limited_%s' % r.status_code,
                    'bytes': 0, 'sec': time.time() - t0, 'kbps': 0}
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=65536):
            if not chunk:
                continue
            if stop_event.is_set():
                r.close()
                return {'ok': False, 'reason': 'stopped', 'bytes': bytes_done,
                        'sec': time.time() - t0, 'kbps': 0}
            bytes_done += len(chunk)
            if limit_bytes and bytes_done >= limit_bytes:
                r.close()
                break
        sec = time.time() - t0
        kbps = (bytes_done / 1024) / sec if sec > 0 else 0
        return {'ok': True, 'bytes': bytes_done, 'sec': sec, 'kbps': kbps}
    except Exception as e:
        return {'ok': False, 'reason': str(e), 'bytes': bytes_done,
                'sec': time.time() - t0, 'kbps': 0}


def test_segments(url, total, seg_count, stop_event, limit_bytes_per_seg=None):
    headers = make_headers(url)
    if not total or total <= 0:
        return {'ok': False, 'reason': 'no_total'}
    seg_size = (total + seg_count - 1) // seg_count

    def dl_one(i):
        start = i * seg_size
        end = min(start + seg_size - 1, total - 1)
        if start > end:
            return {'i': i, 'ok': True, 'bytes': 0, 'sec': 0, 'kbps': 0, 'skipped': True}
        h = dict(headers)
        h['Range'] = 'bytes=%d-%d' % (start, end)
        t0 = time.time()
        bytes_done = 0
        try:
            r = requests.get(url, headers=h, stream=True, timeout=(10, 30))
            ct = (r.headers.get('Content-Type') or '').lower()
            if r.status_code in (429, 503) or 'json' in ct:
                return {'i': i, 'ok': False, 'reason': 'rate_limited_%s' % r.status_code,
                        'bytes': 0, 'sec': time.time() - t0, 'kbps': 0}
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                if stop_event.is_set():
                    r.close()
                    return {'i': i, 'ok': False, 'reason': 'stopped',
                            'bytes': bytes_done, 'sec': time.time() - t0, 'kbps': 0}
                bytes_done += len(chunk)
                if limit_bytes_per_seg and bytes_done >= limit_bytes_per_seg:
                    r.close()
                    break
            sec = time.time() - t0
            kbps = (bytes_done / 1024) / sec if sec > 0 else 0
            return {'i': i, 'ok': True, 'bytes': bytes_done, 'sec': sec, 'kbps': kbps}
        except Exception as e:
            return {'i': i, 'ok': False, 'reason': str(e), 'bytes': bytes_done,
                    'sec': time.time() - t0, 'kbps': 0}

    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=seg_count) as ex:
        futures = [ex.submit(dl_one, i) for i in range(seg_count)]
        for f in as_completed(futures):
            results.append(f.result())
    wall_sec = time.time() - t0
    total_bytes = sum(r['bytes'] for r in results)
    total_kbps = (total_bytes / 1024) / wall_sec if wall_sec > 0 else 0
    failed = [r for r in results if not r.get('ok') and not r.get('skipped')]
    return {
        'ok': len(failed) == 0,
        'results': results,
        'bytes': total_bytes,
        'sec': wall_sec,
        'kbps': total_kbps,
        'fail_reason': failed[0].get('reason') if failed else '',
    }


class SpeedTestApp:
    def __init__(self, root):
        self.root = root
        self.root.title('下载测速器 - Range 并发测试')
        self.root.geometry('960x680')
        self.root.minsize(800, 560)

        self.stop_event = threading.Event()
        self.testing = False
        self._build_ui()

    def _build_ui(self):
        top = ttk.Frame(self.root)
        top.pack(fill=tk.X, padx=10, pady=(10, 4))

        ttk.Label(top, text='测试URL:').pack(side=tk.LEFT)
        self.url_var = tk.StringVar(value=DEFAULT_URL)
        ttk.Entry(top, textvariable=self.url_var, width=90).pack(
            side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        cfg = ttk.Frame(self.root)
        cfg.pack(fill=tk.X, padx=10, pady=4)

        self.quick_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg, text='快速模式(只下前', variable=self.quick_var).pack(side=tk.LEFT)
        self.quick_mb = tk.StringVar(value='2')
        ttk.Entry(cfg, textvariable=self.quick_mb, width=5).pack(side=tk.LEFT, padx=3)
        ttk.Label(cfg, text='MB，省流量)').pack(side=tk.LEFT)

        ttk.Label(cfg, text='    测试间隔(秒):').pack(side=tk.LEFT)
        self.gap_var = tk.StringVar(value='10')
        ttk.Entry(cfg, textvariable=self.gap_var, width=5).pack(side=tk.LEFT, padx=3)

        btn = ttk.Frame(self.root)
        btn.pack(fill=tk.X, padx=10, pady=6)
        self.start_btn = ttk.Button(btn, text='开始综合测试', command=self.start_test)
        self.start_btn.pack(side=tk.LEFT, padx=4)
        self.stop_btn = ttk.Button(btn, text='停止', command=self.stop_test, state='disabled')
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        body = ttk.Frame(self.root)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.result = tk.Text(body, wrap=tk.WORD, font=('Consolas', 10))
        scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.result.yview)
        self.result.configure(yscrollcommand=scroll.set)
        self.result.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def log(self, msg):
        def _log():
            self.result.insert(tk.END, msg + '\n')
            self.result.see(tk.END)
        try:
            self.root.after(0, _log)
        except tk.TclError:
            pass

    def start_test(self):
        if self.testing:
            return
        self.testing = True
        self.stop_event.clear()
        self.start_btn.config(state='disabled')
        self.stop_btn.config(state='normal')
        self.result.delete('1.0', tk.END)
        threading.Thread(target=self.run_all_tests, daemon=True).start()

    def stop_test(self):
        self.stop_event.set()
        self.log('>>> 请求停止，等待当前步骤结束...')

    def _finish(self):
        self.testing = False
        self.stop_event.clear()
        try:
            self.start_btn.config(state='normal')
            self.stop_btn.config(state='disabled')
        except tk.TclError:
            pass

    def run_all_tests(self):
        try:
            url = self.url_var.get().strip()
            if not url:
                self.log('URL 为空')
                return
            quick = self.quick_var.get()
            try:
                quick_mb = float(self.quick_mb.get())
            except Exception:
                quick_mb = 2
            try:
                gap = float(self.gap_var.get())
            except Exception:
                gap = 10

            self.log('=' * 62)
            self.log('URL: %s' % url)
            self.log('模式: %s | 间隔: %.0f 秒' %
                     ('快速(前%.0fMB)' % quick_mb if quick else '完整', gap))
            self.log('=' * 62)

            self.log('\n[1/3] 探测服务器...')
            p = probe(url)
            if not p.get('ok'):
                self.log('探测失败: %s' % p.get('reason'))
                self.log('如果出现 rate_limited_503，说明已触发限流，请等 5~10 分钟再试')
                return
            total = p.get('total')
            self.log('状态=%s ct=%s 总大小=%s 支持Range=%s'
                     % (p['status'], p['ct'], total, p['range']))
            if not total:
                self.log('无法获取总大小，分段测试将跳过')
            if not p['range']:
                self.log('服务端未声明 Accept-Ranges，分段测试将跳过')

            limit_bytes = int(quick_mb * 1024 * 1024) if quick and quick_mb > 0 else None

            self.log('\n[2/3] 单连接测速...')
            res1 = test_single(url, self.stop_event, limit_bytes=limit_bytes)
            if res1.get('reason') == 'stopped':
                self.log('已停止')
                return
            if not res1.get('ok'):
                self.log('单连接失败: %s' % res1.get('reason'))
                return
            self.log('单连接: %.1f KB / %.1f 秒 = %.1f KB/s'
                     % (res1['bytes'] / 1024, res1['sec'], res1['kbps']))
            speed1 = res1['kbps']

            seg_results = {}
            if total and p['range']:
                for seg in (2, 4):
                    if self.stop_event.is_set():
                        break
                    self.log('\n等待 %.0f 秒避免限流...' % gap)
                    time.sleep(gap)
                    if self.stop_event.is_set():
                        break
                    self.log('[3/3] %d 段并发测试...' % seg)
                    seg_limit = None
                    if quick and limit_bytes:
                        seg_limit = max(65536, limit_bytes // seg)
                    r = test_segments(url, total, seg, self.stop_event,
                                      limit_bytes_per_seg=seg_limit)
                    if r.get('fail_reason') == 'stopped':
                        self.log('已停止')
                        return
                    if not r.get('ok'):
                        self.log('%d 段失败: %s' % (seg, r.get('fail_reason')))
                        seg_results[seg] = None
                        continue
                    self.log('%d 段: %.1f KB / %.1f 秒 = %.1f KB/s'
                             % (seg, r['bytes'] / 1024, r['sec'], r['kbps']))
                    seg_results[seg] = r['kbps']
            else:
                self.log('\n跳过分段测试(无总大小或不支持 Range)')

            self.log('\n' + '=' * 62)
            self.log('结论分析:')
            self.log('单连接基准: %.1f KB/s' % speed1)

            verdicts = {}
            for seg, kbps in seg_results.items():
                if kbps is None:
                    self.log('%d 段: 失败（限流或 limit_conn < %d）' % (seg, seg))
                    verdicts[seg] = 'fail'
                else:
                    ratio = kbps / speed1 if speed1 > 0 else 0
                    self.log('%d 段: %.1f KB/s (相对单连接 %.2fx)' % (seg, kbps, ratio))
                    if ratio < 1.3:
                        verdicts[seg] = 'ip_limit'
                    elif ratio < seg * 0.7:
                        verdicts[seg] = 'partial'
                    else:
                        verdicts[seg] = 'effective'

            self.log('\n建议:')
            if not seg_results:
                self.log('segments = 1（无分段数据）')
            elif all(v == 'ip_limit' for v in verdicts.values()):
                self.log('服务端为 IP/账户总限速，并发无收益')
                self.log('建议 segments = 1，max_workers = 1')
            elif any(v in ('effective', 'partial') for v in verdicts.values()):
                good = [s for s, v in verdicts.items() if v in ('effective', 'partial')]
                fails = [s for s, v in verdicts.items() if v == 'fail']
                best = max(good)
                safe = best
                if fails:
                    safe = max(1, min(fails) - 1)
                self.log('并发有效，推荐 segments = %d' % safe)
                if safe >= 4:
                    self.log('实际下载可试 4 段，遇到 503 时自动降为 1 段')
                else:
                    self.log('实际下载建议 segments = %d，配合 503 退避重试' % safe)
            else:
                self.log('数据不足，建议 segments = 1')

            self.log('=' * 62)
        except Exception as e:
            import traceback
            self.log('测试异常: %s' % e)
            self.log(traceback.format_exc())
        finally:
            try:
                self.root.after(0, self._finish)
            except tk.TclError:
                pass


def main():
    root = tk.Tk()
    SpeedTestApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
