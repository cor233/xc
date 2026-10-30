import base64
import hashlib
import json
import os
import random
import re
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from os import path
from pathlib import Path
from urllib.parse import unquote

from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import requests

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except ImportError:
    AES = None
    pad = None

SCRIPT_VERSION = '5.1.5'

DEVICE_ID_STORAGE_FILENAME = 'yunpan_device_ids.json'
JS_CACHE_DIR = 'data'
JS_CACHE_FILE = 'yunpan_token_cache.json'

ua = 'Mozilla/5.0 (Linux; Android 11; M2012K10C Build/RP1A.200720.011; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/90.0.4430.210 Mobile Safari/537.36 MCloudApp/10.0.1'
market_ua = random.choice([
    'Mozilla/5.0 (Linux; Android 14; 23127HN0CC Build/UKQ1.230917.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.146 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
    'Mozilla/5.0 (Linux; Android 14; 24053PY09C Build/UP1A.231005.007; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/142.0.6522.118 Mobile Safari/537.36 MCloudApp/13.0.0 AppLanguage/zh-CN',
])

_SM_PUBLIC_KEY = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC8KHAcHbkCn5rxGgGJE+07tY+pt86D/oZ7sA51FaEBv2jgno2TI9zHJVYKJynmiKpixgwUcv93EfWIrU/p/UCs5Vu+odS3I4UBp3R7IZ1A0W01FkumAHYW2PQpMm8ueQKPLUq/idkpG/9b2JDv/qU+Ks36nbUPwlW4CjdfrV+V9QIDAQAB"
_SM_ORGANIZATION = "FXlyfmWg2AzwbrxDKSv5"
_SM_ANDROID_MODELS = [
    {'model': '23127HN0CC', 'build': 'UKQ1.230917.001', 'android': '14', 'chrome': '143.0.7499.146'},
    {'model': '24053PY09C', 'build': 'UP1A.231005.007', 'android': '14', 'chrome': '142.0.6522.118'},
]
_SM_SCREENS = [
    {'w': 1080, 'h': 2340, 'dpr': 2.625},
    {'w': 1080, 'h': 2400, 'dpr': 2.75},
]
_SM_RSA_KEY = RSA.import_key(base64.b64decode(_SM_PUBLIC_KEY))


def _sm_rsa_encrypt(plaintext):
    cipher = PKCS1_v1_5.new(_SM_RSA_KEY)
    return base64.b64encode(cipher.encrypt(plaintext.encode('utf-8'))).decode('ascii')


def _sm_get_smid(uid):
    now = datetime.now()
    ts = now.strftime('%Y%m%d%H%M%S')
    md5_uid = hashlib.md5(uid.encode('utf-8')).hexdigest()
    base_str = ts + md5_uid + '00'
    check = hashlib.md5(('smsk_web_' + base_str).encode('utf-8')).hexdigest()[:14]
    return base_str + check + '0'


def _generate_device_profile():
    phone = random.choice(_SM_ANDROID_MODELS)
    screen = random.choice(_SM_SCREENS)
    ua_str = (f'Mozilla/5.0 (Linux; Android {phone["android"]}; {phone["model"]} '
              f'Build/{phone["build"]}; wv) AppleWebKit/537.36 (KHTML, like Gecko) '
              f'Version/4.0 Chrome/{phone["chrome"]} Mobile Safari/537.36 '
              f'MCloudApp/13.0.0 AppLanguage/zh-CN')
    sw, sh = screen['w'], screen['h']
    avail_h = sh - random.randint(48, 128)
    uid = str(uuid.uuid4())
    ep = _sm_rsa_encrypt(uid)
    smid = _sm_get_smid(uid)
    now_ts = int(time.time() * 1000)
    start_time = now_ts - random.randint(1800000, 5400000)
    now_cst = datetime.now(timezone(timedelta(hours=8)))
    env = {
        'protocol': 242, 'organization': _SM_ORGANIZATION, 'appId': 'default',
        'os': 'web', 'version': '3.0.0', 'sdkver': '3.0.0', 'box': '',
        'rtype': 'all', 'smid': smid, 'subVersion': '1.0.0',
        'time': now_ts - start_time,
        'cdp': 0, 'maxTouchPoints': 5, 'connectionRtt': 0, 'cpucount': 8,
        'battery': {'charging': 0, 'level': round(0.6 + random.random() * 0.35, 2)},
        'dg': '5.0 ' + ua_str[len('Mozilla/'):],
        'gj': 'zh-CN', 'rr': 'Google Inc.', 'sv': 'Netscape', 'qc': 'Mozilla',
        'ye': 8, 'jq': 8, 'lo': [], 'bw': '', 'lr': 'Etc/GMT-8',
        'nr': 1, 'no': 0, 'br': 1, 'ra': 0,
        'gt': sw, 'wy': sw, 'cj': avail_h, 'wt': random.randint(100, 180),
        'hu': ['chrome'], 'documentExist': 1, 'yi': ['location'], 'dx': 'UTF-8',
        'ig': now_cst.strftime('%a %b %d %Y %H:%M:%S ') + '(GMT+08:00)',
        'ii': 1, 'fs': 0, 'ga': 0, 'tk': 0, 'rm': 0, 'kr': 0, 'nk': 0,
        'by': 'srgb', 'ar': 0, 'or': 0, 'et': 0, 'zc': 0, 'fj': 0, 'dc': 0, 'vd': 0,
        'ni': '', 'hn': '',
        'hv': '48000_2_1_0_2_explicit_speakers|______',
        'de': hashlib.md5(uid.encode('utf-8')).hexdigest()[:16] + '|10011011111000111100001100101101111100110101001110000000000100000',
        'xt': 1, 'vh': 0, 'xc': {'red': '0'},
        'pm': {
            'default': round(120.5 + random.random() * 20, 1),
            'apple': round(120.5 + random.random() * 20, 1),
            'serif': round(100 + random.random() * 20, 1),
            'sans': round(120.5 + random.random() * 20, 1),
            'mono': round(100 + random.random() * 20, 1),
            'min': round(10 + random.random() * 2, 1),
            'system': round(120.5 + random.random() * 20, 1),
        },
        'ob': {'maxTouchPoints': 5, 'touchEvent': True, 'touchStart': True},
        'incognito': {
            'getDirectoryExist': 0, 'getDirectoryIncognito': 0, 'maxTouchPointsExist': 1,
            'indexedDBIncognito': 0, 'openDatabaseExist': 0, 'openDatabaseIncognito': 0,
            'localStorageExist': 1, 'localStorageIncognito': 0, 'promiseExist': 1,
            'promiseAllSettledExist': 1, 'queryUsageAndQuotaIncognito': 0,
            'webkitRequestFileSystemIncognito': 0, 'serviceWorkerExist': 1,
            'indexedDBExist': 1, 'browserName': 'Chrome',
        },
        't': now_cst.strftime('%a %b %d %Y %H:%M:%S GMT+0800 (GMT+08:00)'),
        'collectTime': random.randint(50, 130),
    }
    data_b64 = base64.b64encode(json.dumps(env, separators=(',', ':')).encode('utf-8')).decode('ascii')
    return json.dumps({
        'appId': 'default', 'organization': _SM_ORGANIZATION,
        'ep': ep, 'data': data_b64,
        'os': 'web', 'encode': 1, 'compress': 0,
    }, separators=(',', ':'))


def fetch_device_id():
    url = 'https://slw.h5cmpassport.com:9090/deviceprofile/v4'
    headers = {
        'User-Agent': market_ua,
        'Content-Type': 'application/json;charset=UTF-8',
        'Origin': 'https://m.mcloud.139.com',
        'Referer': 'https://m.mcloud.139.com/portal/mobilecloud/index.html?path=newsignin',
    }
    payload_str = _generate_device_profile()
    try:
        time.sleep(random.uniform(0.5, 1.5))
        resp = requests.post(url, data=payload_str, headers=headers, timeout=15)
        result = resp.json()
        if result.get('code') == 1100 and result.get('detail', {}).get('deviceId'):
            return 'B' + result['detail']['deviceId']
    except Exception:
        pass
    return None

cloud_file_dummy_content = b'0'
cloud_file_dummy_hash = hashlib.sha256(cloud_file_dummy_content).hexdigest()

TOKEN_VALID_TIME = 21600000
FIVE_DAYS_MS = 5 * 24 * 60 * 60 * 1000
REFRESH_TOKEN_AES_KEY = 'c7lXOigXahPnTViq'
TOKEN_EXPIRE_SECONDS_FALLBACK = 2592000

err_accounts = ''
all_logs = ''
user_amount = ''
GLOBAL_DEBUG = False


def current_millis():
    return int(time.time() * 1000)


def normalize_authorization(token):
    token = (token or '').strip()
    if token and not token.startswith('Basic '):
        return f'Basic {token}'
    return token


def random_string(length=16):
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return ''.join(random.choice(chars) for _ in range(length))


def generate_uuid():
    return ''.join([
        random_string(8), '-',
        random_string(4), '-4',
        random_string(3), '-',
        random.choice('89ab'), random_string(3), '-',
        random_string(12)
    ])


def aes_encrypt(data, key):
    if AES is None or pad is None:
        raise ImportError('未安装 pycryptodome，无法执行加密')
    key_bytes = key.encode('utf-8')
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    cipher = AES.new(key_bytes, AES.MODE_ECB)
    encrypted = cipher.encrypt(pad(data.encode('utf-8'), AES.block_size))
    return base64.b64encode(encrypted).decode('utf-8')


def extract_raw_token(token, account):
    token = normalize_authorization(token)
    if token.startswith('Basic '):
        token = token[6:]
    try:
        decoded = base64.b64decode(token).decode('utf-8')
        parts = decoded.split(':')
        if len(parts) >= 3:
            return parts[2]
    except Exception:
        pass
    return token


def parse_token_key(authorization):
    try:
        token = normalize_authorization(authorization)
        if not token:
            return {'type': 'unknown', 'phone': '', 'expireAt': 0}
        if token.startswith('Basic '):
            token = token[6:]
        decoded = base64.b64decode(token).decode('utf-8')
        if not decoded:
            return {'type': 'unknown', 'phone': '', 'expireAt': 0}
        parts = decoded.split(':')
        token_type = parts[0] if parts else 'unknown'
        phone = parts[1] if len(parts) > 1 else ''
        pipe_parts = decoded.split('|')
        expire_at = int(pipe_parts[3]) if len(pipe_parts) > 3 and pipe_parts[3].isdigit() else 0
        return {'type': token_type, 'phone': phone, 'expireAt': expire_at}
    except Exception:
        return {'type': 'unknown', 'phone': '', 'expireAt': 0}


def is_token_expired(expire_at):
    if not expire_at:
        return False
    return expire_at <= current_millis()


def is_token_expiring_soon(expire_at):
    if not expire_at:
        return False
    diff = expire_at - current_millis()
    return 0 < diff <= FIVE_DAYS_MS


def build_authorization(account, raw_token):
    return f"Basic {base64.b64encode(f'mobile:{account}:{raw_token}'.encode()).decode()}"


def parse_expire_time_to_millis(expire_time):
    try:
        expire_seconds = int(float(expire_time))
    except (TypeError, ValueError):
        expire_seconds = TOKEN_EXPIRE_SECONDS_FALLBACK
    if expire_seconds <= 0:
        expire_seconds = TOKEN_EXPIRE_SECONDS_FALLBACK
    return current_millis() + expire_seconds * 1000


def get_env_device_id():
    return (os.getenv('yunpan_device_id') or os.getenv('YDYP_DEVICE_ID') or '').strip()


def normalize_market_device_input(device_id):
    raw = (device_id or '').strip().strip('"').strip("'")
    if not raw:
        return '', ''
    match = re.search(r'(?:^|;\s*)\.thumbcache_[^=;]*=([^;]+)', raw)
    if match:
        raw = match.group(1).strip()
    elif raw.lower().startswith('deviceid='):
        raw = raw.split('=', 1)[1].strip()
    raw = raw.strip().strip('"').strip("'")
    header_value = unquote(raw)
    cookie_value = raw if raw != header_value else (header_value[1:] if header_value.startswith('B') else header_value)
    return header_value, cookie_value


def get_storage_base_dir():
    custom_storage_dir = (os.environ.get('MCLD_DATA_DIR')
                          or os.environ.get('yunpan_storage_dir')
                          or os.environ.get('YDYP_STORAGE_DIR') or '').strip()
    if custom_storage_dir:
        base_dir = Path(custom_storage_dir)
    else:
        base_dir = Path(path.abspath(path.dirname(__file__)))
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base_dir


def get_storage_file_path(filename):
    return get_storage_base_dir() / filename


def get_js_cache_path():
    try:
        return get_storage_base_dir() / JS_CACHE_DIR / JS_CACHE_FILE
    except Exception:
        return get_storage_base_dir() / JS_CACHE_FILE


def load_js_cache():
    cache_path = get_js_cache_path()
    try:
        if cache_path.exists():
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault('accounts', {})
                    return data
    except Exception:
        pass
    return {'accounts': {}}


def save_js_cache(data):
    cache_path = get_js_cache_path()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_token_info(account):
    js_cache = load_js_cache()
    entry = (js_cache.get('accounts') or {}).get(account, {})
    if entry and entry.get('authorization'):
        return {
            'token': entry['authorization'],
            'expiresAt': entry.get('expireAt', 0),
            'lastRefreshAt': entry.get('updatedAt', 0),
        }
    return {}


def update_cache_authorization(account, authorization):
    parsed = parse_token_key(authorization)
    cache = load_js_cache()
    accounts = cache.setdefault('accounts', {})
    existing = accounts.get(account, {})
    existing.update({
        'authorization': normalize_authorization(authorization),
        'phone': account,
        'type': parsed.get('type', 'unknown'),
        'expireAt': parsed.get('expireAt', 0) or int(existing.get('expireAt', 0)),
        'updatedAt': current_millis(),
    })
    accounts[account] = existing
    save_js_cache(cache)


def get_device_id_storage_path():
    return get_storage_file_path(DEVICE_ID_STORAGE_FILENAME)


def load_device_id_storage():
    storage_path = get_device_id_storage_path()
    if not storage_path.exists():
        return {}
    try:
        with open(storage_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_device_id(device_id, account):
    storage_path = get_device_id_storage_path()
    data = load_device_id_storage()
    account_data = data.get(account, {})
    normalized_device_id, _ = normalize_market_device_input(device_id)
    account_data['deviceId'] = normalized_device_id or device_id
    data[account] = account_data
    try:
        with open(storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def save_token_info(account, token, expires_at, last_refresh_at):
    update_cache_authorization(account, token)


def ensure_account_storage_entry(account, token=''):
    storage_path = get_device_id_storage_path()
    data = load_device_id_storage()
    account_data = data.get(account)
    if not isinstance(account_data, dict):
        account_data = {}
    changed = False
    if 'deviceId' not in account_data:
        account_data['deviceId'] = ''
        changed = True
    if changed or account not in data:
        data[account] = account_data
        try:
            with open(storage_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def get_device_id(account):
    data = load_device_id_storage()
    return data.get(account, {}).get('deviceId', '')


def print_device_id_notice():
    if get_env_device_id():
        print("已检测到 yunpan_device_id，将优先使用环境变量 deviceId")
        return
    cached_data = load_device_id_storage()
    cached_accounts = [account for account, info in cached_data.items()
                       if isinstance(info, dict) and info.get('deviceId')]
    if cached_accounts:
        print(f"已发现本地缓存 deviceId {len(cached_accounts)} 个账号")
        return
    print("未检测到 yunpan_device_id；脚本将自动生成并保存 deviceId")


def load_send():
    cur_path = path.abspath(path.dirname(__file__))
    notify_file = cur_path + "/notify.py"
    if path.exists(notify_file):
        try:
            from notify import send
            print("加载通知服务成功！")
            return send
        except ImportError:
            pass
    return False


class YP:
    def __init__(self, cookie):
        try:
            self.auth_token = None
            self.click_num = 15
            self.client_version = '13.0.0'
            self.market_base_url = 'https://caiyun.feixin.10086.cn'
            self.user_domain_id = ''
            self.market_device_id = ''
            self.market_headers = {}
            self.session = requests.Session()
            self.user_log_lines = []
            self.cloud_total = 0
            self.signin_ok = False
            self.login_ok = False

            self.timestamp = str(int(round(time.time() * 1000)))

            parts = cookie.split("#")
            if len(parts) < 2:
                raise ValueError(f"⚠️ 变量值格式错误: {cookie}")

            self.Authorization = normalize_authorization(parts[0])
            self.account = parts[1].strip()
            self.refresh_capable = AES is not None and pad is not None
            self.load_persisted_authorization()
            self.load_or_create_market_device_profile()
            self.auth_token = extract_raw_token(self.Authorization, self.account)

            if len(self.account) >= 11:
                self.encrypt_account = self.account[:3] + "****" + self.account[-4:]
            else:
                self.encrypt_account = self.account

            self.jwtHeaders = {
                'User-Agent': ua,
                'Accept': 'application/json, text/plain, */*',
                'Host': 'caiyun.feixin.10086.cn',
                'Authorization': self.Authorization,
            }

        except Exception as e:
            print(f"{e}")
            self.Authorization = None

    def get_storage_record(self):
        js_cache = load_js_cache()
        entry = (js_cache.get('accounts') or {}).get(self.account, {})
        if entry and entry.get('authorization'):
            return {
                'token': entry['authorization'],
                'expiresAt': entry.get('expireAt', 0),
                'lastRefreshAt': entry.get('updatedAt', 0),
            }
        return {}

    def load_persisted_authorization(self):
        env_parsed = parse_token_key(self.Authorization)
        cached = self.get_storage_record()
        cached_token = normalize_authorization(cached.get('token', ''))
        if not cached_token:
            return
        cache_parsed = parse_token_key(cached_token)
        if cache_parsed.get('phone') and cache_parsed['phone'] != self.account:
            return
        env_expire = env_parsed.get('expireAt', 0) or int(cached.get('expiresAt') or 0)
        cache_expire = cache_parsed.get('expireAt', 0) or int(cached.get('expiresAt') or 0)
        if is_token_expired(env_expire) and not is_token_expired(cache_expire):
            self.Authorization = cached_token
            self.log(f'-Token缓存命中: env已过期，使用缓存Token')
            return
        if cache_expire > env_expire:
            self.Authorization = cached_token
            self.log(f'-Token缓存命中: 缓存Token更新')
            return
        if not env_expire and not cache_expire:
            self.Authorization = cached_token
            self.log('-Token缓存命中: 使用缓存Token')
            return
        if env_expire:
            save_token_info(self.account, self.Authorization, env_expire, current_millis())

    def load_or_create_market_device_profile(self):
        ensure_account_storage_entry(self.account, self.Authorization)
        fetched_id = fetch_device_id()
        if fetched_id:
            self.market_device_id = fetched_id
            save_device_id(fetched_id, self.account)
            return
        env_device_id = get_env_device_id()
        if env_device_id:
            device_id, _ = normalize_market_device_input(env_device_id)
            self.market_device_id = device_id
            return
        stored_device_id = get_device_id(self.account)
        if stored_device_id:
            device_id, _ = normalize_market_device_input(stored_device_id)
            self.market_device_id = device_id
            return
        self.market_device_id = str(uuid.uuid4()).replace('-', '')
        save_device_id(self.market_device_id, self.account)

    def save_authorization_record(self, token=None):
        token = normalize_authorization(token or self.Authorization)
        update_cache_authorization(self.account, token)

    def should_refresh_authorization(self, force=False):
        if force:
            return True, '强制刷新'
        parsed = parse_token_key(self.Authorization)
        expire_at = parsed.get('expireAt', 0)
        if not expire_at:
            stored = self.get_storage_record()
            expire_at = int(stored.get('expiresAt') or 0)
        if not expire_at:
            return True, '无法解析Token过期时间'
        if is_token_expired(expire_at):
            return True, 'Token已过期'
        if is_token_expiring_soon(expire_at):
            days = (expire_at - current_millis()) / 86400000
            return True, f'将在 {days:.1f} 天后过期'
        days = (expire_at - current_millis()) / 86400000
        return False, f'Token 状态良好（剩余 {days:.1f} 天）'

    def refresh_authorization_token(self, force=False):
        need_refresh, reason = self.should_refresh_authorization(force=force)
        if not need_refresh:
            self.log(f'-Authorization状态: {reason}')
            return True
        if not self.refresh_capable:
            self.log('-未安装 pycryptodome，跳过Authorization自动刷新')
            return True

        refresh_headers = {
            'Content-Type': 'application/json',
            'User-Agent': ua,
            'x-yun-tid': generate_uuid(),
            'Authorization': self.Authorization,
            'x-yun-api-version': 'v1',
            'x-yun-module-type': '100',
            'x-yun-op-type': '1',
            'x-yun-app-channel': '10214200',
            'x-yun-client-info': '||8||||||||||||',
            'hcy-cool-flag': '1',
        }
        encrypted_data = aes_encrypt({'phoneNumber': self.account}, REFRESH_TOKEN_AES_KEY)
        refresh_data = self.request_json('https://user-njs.yun.139.com/user/auth/refreshToken',
                                         headers=refresh_headers,
                                         data={'data': encrypted_data},
                                         method='POST', retries=1)
        if not refresh_data:
            self.log(f'-Authorization刷新失败: 接口无响应 ({reason})')
            return True
        code = str(refresh_data.get('code', ''))
        data = refresh_data.get('data')
        success = refresh_data.get('success', False)
        is_success = (code in ('0', '00', '000', '0000')) or bool(success) or (code.startswith('0') and len(code) <= 4)
        if not is_success or not isinstance(data, dict):
            self.log(f"-Authorization刷新失败: {refresh_data.get('message') or refresh_data.get('msg', '未知错误')}")
            return True
        raw_token = data.get('token')
        if not raw_token:
            self.log('-Authorization刷新失败: 响应缺少token')
            return True
        expires_at = parse_expire_time_to_millis(data.get('expireTime'))
        self.Authorization = build_authorization(self.account, raw_token)
        self.auth_token = raw_token
        self.save_authorization_record(token=self.Authorization)
        update_cache_authorization(self.account, self.Authorization)
        self.log(f'-Authorization自动刷新成功: {reason}')
        return True

    def sync_token_storage(self):
        stored = self.get_storage_record()
        expires_at = stored.get('expiresAt', current_millis() + TOKEN_VALID_TIME)
        last_refresh_at = stored.get('lastRefreshAt', 0)
        save_token_info(self.account, self.Authorization, expires_at, last_refresh_at)
        if self.market_device_id:
            save_device_id(self.market_device_id, self.account)
        self.save_authorization_record(token=self.Authorization)

    def log(self, content):
        print(content)
        self.user_log_lines.append(content)

    @staticmethod
    def catch_errors(func):
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                err_str = f"错误: {str(e)}"
                print(err_str)
                self.user_log_lines.append(err_str)
            return None
        return wrapper

    @catch_errors
    def run(self):
        self.log(f'账号: {self.encrypt_account}')
        self.refresh_authorization_token()
        try:
            self.signin_status()
        except Exception as e:
            self.log(f'-签到异常: {e}')
        try:
            self.get_cloud_tasklist_v2()
        except Exception as e:
            self.log(f'-任务异常: {e}')
        try:
            self.receive()
        except Exception as e:
            self.log(f'-云朵异常: {e}')
        self.sync_token_storage()
        global all_logs
        all_logs += f"用户【{self.encrypt_account}】日志:\n" + "\n".join(self.user_log_lines) + "\n\n"

    @catch_errors
    def send_request(self, url, headers=None, cookies=None, data=None, json_data=None, params=None, method='GET', debug=None, retries=3):
        debug = debug if debug is not None else GLOBAL_DEBUG
        request_headers = dict(headers or {})
        request_cookies = dict(cookies or {})
        if json_data is not None:
            request_args = {'json': json_data}
        elif isinstance(data, dict):
            request_args = {'json': data}
        else:
            request_args = {'data': data}
        for attempt in range(retries):
            try:
                response = self.session.request(method, url, params=params, headers=request_headers or None,
                                                cookies=request_cookies or None, timeout=15, **request_args)
                if 400 <= response.status_code < 500:
                    return None
                response.raise_for_status()
                return response
            except requests.HTTPError:
                return None
            except (requests.ConnectionError, TimeoutError, ConnectionError):
                if attempt >= retries - 1:
                    return None
                time.sleep(1)

    def request_json(self, url, headers=None, cookies=None, data=None, json_data=None, params=None, method='GET', debug=None, retries=5):
        response = self.send_request(url, headers=headers, cookies=cookies, data=data, json_data=json_data,
                                     params=params, method=method, debug=debug, retries=retries)
        if response is None:
            return None
        try:
            return response.json()
        except ValueError:
            text = response.text
            if '<BaseResult>' in text:
                try:
                    root = ET.fromstring(text)
                    success = root.find('success').text == 'true' if root.find('success') is not None else False
                    code = root.find('code').text if root.find('code') is not None else ''
                    msg = root.find('message').text if root.find('message') is not None else ''
                    return {'code': code, 'success': success, 'msg': msg, 'result': {}}
                except Exception:
                    pass
            preview = (text or '')[:120].replace('\n', ' ')
            self.log(f'[诊断] 解析失败 | HTTP {response.status_code} | Content-Type: {response.headers.get("content-type", "")} | 预览: {preview}')
            return None

    @staticmethod
    def get_today_sign_state(result):
        today_sign_in = result.get('todaySignIn')
        if isinstance(today_sign_in, bool):
            return today_sign_in
        for day in result.get('cal') or []:
            if day.get('t'):
                return bool(day.get('s'))
        return None

    def build_market_headers(self, extra_headers=None, referer=None):
        headers = dict(self.market_headers)
        if referer:
            headers['Referer'] = referer
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def request_market_json(self, url, params=None, data=None, method='GET', debug=None, retries=5, headers=None, json_body=False):
        extra = dict(headers) if headers else {}
        if json_body and isinstance(data, dict):
            send_data = None
            request_json_data = data
        elif json_body:
            extra.setdefault('Content-Type', 'application/json;charset=UTF-8')
            send_data = json.dumps(data, ensure_ascii=False, separators=(',', ':')) if data is not None else data
            request_json_data = None
        else:
            send_data = data
            request_json_data = None
        return self.request_json(url, headers=self.build_market_headers(extra), data=send_data,
                                 json_data=request_json_data, params=params, method=method, debug=debug, retries=retries)

    def click_task(self, task_id, key='task'):
        return self.request_market_json(f'{self.market_base_url}/market/signin/task/click?key={key}&id={task_id}')

    @catch_errors
    def signin_status(self):
        self.sleep()
        check_url = f'{self.market_base_url}/market/signin/page/infoV3'
        check_data = self.request_market_json(check_url, params={'client': 'app'})
        if not check_data:
            self.log('查询签到失败: 接口无响应')
            return
        if str(check_data.get('code')) not in ('0', '0000', '200'):
            self.log(f"查询签到失败: {check_data.get('msg', '未知错误')}")
            return
        if self.get_today_sign_state(check_data.get('result', {})):
            self.signin_ok = True
            self.log('✅已签到')
            return
        self.sleep(1, 2)
        signin_data = self.request_market_json(f'{self.market_base_url}/market/signin/page/startSignIn', params={'client': 'app'})
        if not signin_data:
            self.log('签到失败: 接口无响应')
            return
        if str(signin_data.get('code')) in ('0', '200') and self.get_today_sign_state(signin_data.get('result', {})):
            self.signin_ok = True
            self.log('✅签到成功')
            return
        self.log(f"签到失败: {signin_data.get('msg', '未知错误')}")

    def get_cloud_task_groups(self):
        return [
            ('cloudEmail', '\n📮 联动任务'),
            ('time', '\n✨ 新版热门任务'),
            ('day', '\n📆 云盘每日任务'),
            ('month', '\n📆 云盘每月任务'),
        ]

    def get_cloud_tasklist_v2(self):
        for group, title in self.get_cloud_task_groups():
            return_data = self.request_market_json(f'{self.market_base_url}/market/signin/task/taskListV2',
                                                   json_body=True, method='POST', data={
                'marketname': 'sign_in_3',
                'clientVersion': self.client_version,
                'group': group,
            })
            if not return_data:
                self.log(f'获取任务列表失败: {group}')
                continue
            if str(return_data.get('code')) not in ('0', '0000', '200'):
                self.log(f"获取任务列表失败: {group} {return_data.get('msg', '未知错误')}")
                continue
            tasks = return_data.get('result', {}).get(group, [])
            if not tasks:
                continue
            self.log(title)
            for task in tasks:
                self.handle_cloud_v2_task(group, task)

    def handle_cloud_v2_task(self, group, task):
        task_id = task.get('id')
        task_name = re.sub(r'<[^>]+>', '', task.get('name', ''))
        if task.get('state') == 'FINISH':
            print(f'-已完成: {task_name}')
            return
        if task_id in [106, 434, 522]:
            self.log(f'-去完成: {task_name} (专项逻辑未触发，需手动处理)')
            return
        task_keys = []
        if 'click' in set(task.get('stepTypeSet') or []):
            task_keys = ['task']
        if task_keys:
            self.log(f'-去完成: {task_name}')
            for task_key in task_keys:
                click_data = self.click_task(task_id, task_key)
                if click_data and str(click_data.get('code')) in ('0', '200'):
                    continue
                msg = click_data.get('msg', '未知错误') if click_data else '接口无响应'
                self.log(f'-任务登记失败: {task_name} {msg}')
                return
            self.log(f'-已登记任务: {task_name}')
            return
        self.log(f'-需手动完成: {task_name}')

    def sleep(self, min_delay=1, max_delay=1.5):
        time.sleep(random.uniform(min_delay, max_delay))

    @catch_errors
    def receive(self):
        info_data = self.request_market_json(f'{self.market_base_url}/market/signin/page/infoV3', params={'client': 'app'})
        if not info_data:
            self.log('查询云朵失败: 接口无响应')
            return
        if str(info_data.get('code')) not in ('0', '0000', '200'):
            self.log(f"查询云朵失败: {info_data.get('msg', '未知错误')}")
            return
        info_result = info_data.get('result', {})
        pending_amount = info_result.get('toReceive', 0)
        total_amount = info_result.get('total', 0)
        if pending_amount:
            receive_data = self.request_market_json(f'{self.market_base_url}/market/signin/page/receiveV3', params={'client': 'app'}, json_body=True, method='POST', data={})
            if receive_data and str(receive_data.get('code')) in ('0', '200'):
                receive_result = receive_data.get('result', {})
                self.log(f'-领取云朵:{receive_result.get("receive", pending_amount)}云朵')
                total_amount = receive_result.get('total', total_amount)
            else:
                self.log(f"领取云朵失败: {receive_data.get('msg', '未知错误') if receive_data else '接口无响应'}")
        self.log(f'-当前云朵数量:{total_amount}云朵')
        self.cloud_total = total_amount if isinstance(total_amount, int) else 0
        global user_amount
        user_amount += f'用户【{self.encrypt_account}】:{total_amount}云朵\n'


if __name__ == "__main__":
    env_name = 'yunpan'
    token = os.getenv(env_name)
    if not token:
        print(f'⛔️未获取到ck变量：请检查变量 {env_name} 是否填写')
        exit(0)

    cookies = re.split(r'[&]', token)
    print(f"共获取到 {len(cookies)} 个账号")
    print_device_id_notice()

    for i, account_info in enumerate(cookies, start=1):
        print(f"\n======== ▷ 第 {i} 个账号 ◁ ========")
        yp_instance = YP(account_info)
        if not yp_instance.Authorization:
            print(f"⛔️ 账号 {i} 无效，跳过执行")
            continue
        yp_instance.run()
        print("\n准备进行下一个账号")
        time.sleep(random.uniform(1, 3))

    print("\n================ 运行总结 ================")
    if err_accounts:
        print(f"❌ 失效账号:\n{err_accounts}")
    if user_amount:
        print(f"☁️ 云朵汇总:\n{user_amount}")

    send_func = load_send()
    if send_func:
        msg = f"任务详情:\n{all_logs}\n云朵汇总:\n{user_amount}"
        send_func('中国移动云盘任务信息', msg)
    else:
        print("未找到通知服务，跳过推送")
