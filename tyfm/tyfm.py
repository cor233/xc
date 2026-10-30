import os
import re
import sys
import json
import time
import uuid
import requests

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0'
CHAPTER_API = 'https://appp.fdhtbz.cn/api/h5/listening/chapters/{aid}?cache_meta=1'
PLAY_BASES = [
    'https://appp.fdhtbz.cn/api/h5/listening/play',
    'https://api-preview.toulaopao.cc/api/h5/listening/play',
]
PLAY_PARAMS = ['chapter_id', 'id', 'chapterId', 'cid']


def session_headers():
    return {
        'User-Agent': UA,
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://tingyou.fm/',
        'Origin': 'https://tingyou.fm',
        'x-listening-session': str(uuid.uuid4()),
        'x-listening-timezone': 'Asia/Shanghai',
    }


def find_chapters(obj, depth=0):
    if depth > 6:
        return None
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        keys = set(obj[0].keys())
        if keys & {'title', 'name', 'chapter_title', 'chapter_name'} and \
           keys & {'id', 'chapter_id', 'chapterId', 'cid', 'track_id'}:
            return obj
    if isinstance(obj, dict):
        for v in obj.values():
            r = find_chapters(v, depth + 1)
            if r:
                return r
    return None


def get_cid(chap):
    for k in ('chapter_id', 'chapterId', 'cid', 'id', 'track_id'):
        if k in chap and chap[k] is not None:
            return chap[k]
    return None


def probe_play(cid):
    for base in PLAY_BASES:
        for p in PLAY_PARAMS:
            try:
                r = requests.get(base, headers=session_headers(),
                                 params={p: cid}, timeout=15)
                if r.status_code == 200 and 'json' in r.headers.get('Content-Type', ''):
                    try:
                        j = r.json()
                        if j.get('play_url'):
                            print(f'[play] ✅ {base}?{p}={cid}')
                            return j['play_url']
                    except Exception:
                        pass
                print(f'[play] ✗ {base}?{p}={cid} -> {r.status_code} {r.text[:80]}')
            except Exception as e:
                print(f'[play] ✗ {base}?{p}={cid} -> {e}')
    return None


def main():
    aid = sys.argv[1] if len(sys.argv) > 1 else '8200752245'
    m = re.search(r'(\d{8,})', aid)
    if m:
        aid = m.group(1)
    print(f'专辑 ID: {aid}')

    print(f'\n[1] 章节列表')
    r = requests.get(CHAPTER_API.format(aid=aid), headers=session_headers(), timeout=20)
    print(f'    {r.status_code} {r.headers.get("Content-Type")} {len(r.content)} 字节')
    if r.status_code != 200:
        print(r.text[:300])
        return
    chapters = find_chapters(r.json())
    if not chapters:
        print('❌ 未定位章节数组')
        print(json.dumps(r.json(), ensure_ascii=False)[:800])
        return
    print(f'    找到 {len(chapters)} 章')
    print(f'    第 1 章原始字段: {json.dumps(chapters[0], ensure_ascii=False)[:400]}')

    cid = get_cid(chapters[0])
    print(f'\n[2] chapter_id = {cid}')

    print(f'\n[3] 探测 play 接口')
    url = probe_play(cid)
    if not url:
        print('❌ play 全部失败')
        return
    print(f'    play_url: {url[:100]}...')

    print(f'\n[4] 测试下载第 1 章')
    t0 = time.time()
    rr = requests.get(url, headers={'User-Agent': UA, 'Referer': 'https://tingyou.fm/'},
                      stream=True, timeout=60)
    rr.raise_for_status()
    total = 0
    with open('test_ch1.m4a', 'wb') as f:
        for chunk in rr.iter_content(65536):
            if chunk:
                f.write(chunk)
                total += len(chunk)
    sec = time.time() - t0
    print(f'    ✅ {total // 1024} KB / {sec:.1f} 秒 = {(total / 1024) / sec if sec > 0 else 0:.0f} KB/s')
    print(f'    已保存: {os.path.abspath("test_ch1.m4a")}')

    print(f'\n全部诊断通过 ✅')


if __name__ == '__main__':
    main()
