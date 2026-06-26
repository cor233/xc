#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import random
import hashlib
import urllib.parse
from typing import Dict, List, Optional, Any

import requests

MARKET_URL = "https://caiyun.feixin.10086.cn/market"
MARKET_7071_URL = "https://caiyun.feixin.10086.cn:7071/market"
MOBILE_MARKET_URL = "https://m.mcloud.139.com/ycloud"
AI_YUN_URL = "https://ai.yun.139.com"

MARKET_CLIENT_VERSION = "12.5.4"
MARKET_SOURCE_ID = "1097"
MARKET_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; MI 8 Build/QKQ1.190828.002; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.146 "
    "Mobile Safari/537.36 MCloudApp/12.5.4 AppLanguage/zh-CN"
)
SHARE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
)

AI_CAMERA_SAMPLE_BASE64 = (
    "data:image/jpg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAkGBxAQEBUQEBAVFRUVFRUVFRUVFRUVFRUVFRUWFhUVFRUYHSggGBolHRUVITEhJSkrLi4uFx8zODMsNygtLisBCgoKDg0OGhAQGi0lHyUtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLf/AABEIAAEAAQMBIgACEQEDEQH/xAAXAAEBAQEAAAAAAAAAAAAAAAAAAQID/8QAFhEBAQEAAAAAAAAAAAAAAAAAABEh/9oADAMBAAIRAxEAPwHhAH//xAAZEAEBAQEBAQAAAAAAAAAAAAABEQIhMUH/2gAIAQEAAT8A2M4Kxqf/xAAWEQEBAQAAAAAAAAAAAAAAAAAAESH/2gAIAQIBAT8Ap//EABYRAQEBAAAAAAAAAAAAAAAAAAABEf/aAAgBAwEBPwCf/9k=="
)

def generate_tid() -> str:
    return str(int(time.time() * 1_000_000_000))

def random_hex(length: int = 8) -> str:
    return ''.join(random.choices('0123456789abcdef', k=length))

def bool_from_any(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ('1', 'true', 'yes')
    return False

def normalize_message(msg: str, message: str) -> str:
    if msg and msg.strip():
        return msg.strip()
    return message.strip() if message else ""

class CaiyunClient:
    def __init__(self, sso_token: str, jwt_token: str, device_id: Optional[str] = None):
        self.sso_token = sso_token
        self.jwt_token = jwt_token
        self.device_id = device_id or self._generate_device_id()
        self.session = requests.Session()
        self.session.headers.update({
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh,zh-CN;q=0.9",
        })

    @staticmethod
    def _generate_device_id() -> str:
        return ''.join(random.choices('0123456789ABCDEF', k=32))

    @staticmethod
    def login_with_password(phone: str, password: str) -> tuple:
        login_url = "https://m.mcloud.139.com/user/login"
        headers = {
            "User-Agent": MARKET_USER_AGENT,
            "Content-Type": "application/json",
            "Origin": "https://m.mcloud.139.com",
            "Referer": "https://m.mcloud.139.com/",
        }
        pwd_md5 = hashlib.md5(password.encode('utf-8')).hexdigest()
        payload = {
            "username": phone,
            "password": pwd_md5,
            "rememberMe": True,
            "clientType": "1",
        }
        session = requests.Session()
        resp = session.post(login_url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise Exception(f"登录失败: {data.get('msg', '未知错误')}")
        jwt_token = data.get("data", {}).get("jwtToken")
        if not jwt_token:
            raise Exception("登录响应未包含 jwtToken")
        sso_token = None
        for cookie in session.cookies:
            if cookie.name == "caiyun_token":
                sso_token = cookie.value
                break
        if not sso_token:
            sso_token = data.get("data", {}).get("token") or data.get("token")
            if not sso_token:
                raise Exception("登录响应未包含 caiyun_token (SSO_TOKEN)")
        return sso_token, jwt_token

    def _build_market_headers(self, extra_headers: Optional[Dict] = None, referer: Optional[str] = None) -> Dict:
        headers = {
            "User-Agent": MARKET_USER_AGENT,
            "Accept": "*/*",
            "Origin": "https://m.mcloud.139.com",
            "X-Requested-With": "com.chinamobile.mcloud",
        }
        if self.jwt_token:
            headers["jwtToken"] = self.jwt_token
            headers["jwttoken"] = self.jwt_token
        if referer is None:
            referer = self._build_market_page_url()
        headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _build_receive_headers(self) -> Dict:
        extra = {
            "showLoading": "true",
            "appVersion": f"{MARKET_CLIENT_VERSION}.0",
            "activityId": "sign_in_3",
        }
        headers = self._build_market_headers(extra)
        if self.device_id:
            headers["deviceId"] = self.device_id
        return headers

    def _build_market_page_url(self) -> str:
        return (
            f"https://m.mcloud.139.com/portal/mobilecloud/index.html"
            f"?path=newsignin&sourceid={MARKET_SOURCE_ID}&enableShare=1"
            f"&token={self.sso_token}&targetSourceId=001005"
        )

    def _post_journaling(self, keyword: str) -> None:
        payload = (
            f"module=uservisit&optkeyword={urllib.parse.quote(keyword)}"
            f"&sourceid={MARKET_SOURCE_ID}&marketName=sign_in_3"
        )
        headers = self._build_market_headers({
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"
        })
        try:
            resp = self.session.post(
                "https://m.mcloud.139.com/ycloud/visitlog/journaling",
                data=payload,
                headers=headers,
                timeout=10
            )
            resp.raise_for_status()
        except Exception:
            pass

    def _prepare_session(self, for_receive: bool = False) -> None:
        page_url = self._build_market_page_url()
        try:
            self.session.get(page_url, headers=self._build_market_headers(), timeout=10)
        except Exception:
            pass
        keywords = [
            "newsignin_index_pv",
            "newsignin_index_client",
            "newsignin_index_app_client",
            "newsignin_index_cookie_login",
            "newsignin_index_cookie",
            "newsignin_index_app_cookie_login",
        ]
        if for_receive:
            keywords.append("newsignin_index_receive_type")
        for kw in keywords:
            self._post_journaling(kw)

    def sign_in(self) -> Dict:
        self._prepare_session(False)
        url = f"{MOBILE_MARKET_URL}/signin/page/startSignIn?client=app"
        resp = self.session.get(url, headers=self._build_receive_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_cloud_info(self) -> Dict:
        self._prepare_session(False)
        url = f"{MOBILE_MARKET_URL}/signin/page/infoV3?client=app"
        resp = self.session.get(url, headers=self._build_receive_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def receive_pending_cloud_rewards(self) -> Dict:
        self._prepare_session(True)
        url = f"{MOBILE_MARKET_URL}/signin/page/receiveV2?client=app"
        resp = self.session.get(url, headers=self._build_receive_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def get_receive_summary(self) -> Dict:
        cloud_info = self.get_cloud_info()
        if not self._is_success(cloud_info):
            return {"code": -1, "msg": cloud_info.get("msg", "获取云朵信息失败")}
        prize_resp = self.get_prize_log_page(1, 15)
        if not self._is_success(prize_resp):
            return {"code": -1, "msg": prize_resp.get("msg", "获取奖品记录失败")}
        pending_names = self._get_pending_prize_names(prize_resp)
        result = {
            "todaySignIn": self._today_signed(cloud_info),
            "total": cloud_info.get("result", {}).get("total", 0),
            "toReceive": cloud_info.get("result", {}).get("toReceive", 0),
            "nextMonthGet": cloud_info.get("result", {}).get("nextMonthGet", 0),
            "pendingPrizeNames": pending_names,
            "pendingPrizeCount": len(pending_names),
        }
        msg_parts = [f"当前云朵{result['total']}"]
        if pending_names:
            msg_parts.append(f"待领奖品{len(pending_names)}项")
        return {"code": 0, "msg": "，".join(msg_parts), "success": True, "result": result}

    def receive_all(self) -> Dict:
        claim_resp = self.receive_pending_cloud_rewards()
        if not self._is_success(claim_resp):
            return claim_resp
        summary = self.get_receive_summary()
        if not self._is_success(summary):
            return summary
        if claim_resp.get("msg"):
            summary["msg"] = f"{claim_resp.get('msg', '')}，{summary.get('msg', '')}"
        return summary

    def get_prize_log_page(self, page: int = 1, size: int = 15) -> Dict:
        self._prepare_session(True)
        url = (
            f"https://m.mcloud.139.com/ycloud/prizeApi/checkPrize/getUserPrizeLogPageV2"
            f"?currPage={page}&pageSize={size}"
        )
        resp = self.session.get(url, headers=self._build_receive_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _get_pending_prize_names(self, prize_resp: Dict) -> List[str]:
        result = prize_resp.get("result", {})
        items = result.get("result") or result.get("records") or []
        pending = []
        for item in items:
            if item.get("flag") == 1 and item.get("prizeName", "").strip():
                pending.append(item["prizeName"].strip())
        return pending

    def _today_signed(self, cloud_info: Dict) -> bool:
        if cloud_info.get("result", {}).get("todaySignIn"):
            return True
        cal = cloud_info.get("result", {}).get("cal", [])
        for day in cal:
            if day.get("t") and bool_from_any(day.get("s")):
                return True
        return False

    def _is_success(self, resp: Dict) -> bool:
        code = resp.get("code")
        msg = normalize_message(resp.get("msg", ""), resp.get("message", ""))
        if isinstance(code, int):
            return code == 0
        if isinstance(code, str):
            return code == "0" or code == ""
        return resp.get("success", False) or msg.lower() == "success"

    def shake(self) -> Dict:
        url = f"{MARKET_7071_URL}/shake-server/shake/shakeIt?flag=1"
        resp = self.session.post(url, headers=self._build_market_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def sign_in_wx(self) -> Dict:
        url = f"{MARKET_URL}/playoffic/followSignInfo?isWx=true"
        resp = self.session.get(url, headers=self._build_market_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def wx_draw(self) -> Dict:
        url = f"{MARKET_URL}/playoffic/draw"
        resp = self.session.get(url, headers=self._build_market_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def obtain_msg_push_on(self) -> Dict:
        url = f"{MARKET_URL}/msgPushOn/task/obtain"
        resp = self.session.post(
            url,
            headers=self._build_market_headers({"Content-Type": "application/json"}),
            json={"type": 2},
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def receive_backup_gift(self) -> Dict:
        url = f"{MARKET_7071_URL}/backupgift/receive"
        resp = self.session.get(url, headers=self._build_market_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def cloud_multiple(self) -> Dict:
        self._prepare_session(False)
        url = f"{MOBILE_MARKET_URL}/signin/page/multiple"
        resp = self.session.get(url, headers=self._build_receive_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()

    def receive_revival_reward(self) -> Dict:
        self._prepare_session(True)
        url = f"{MOBILE_MARKET_URL}/signin/page/receiveRevivalReward"
        resp = self.session.post(
            url,
            headers=self._build_receive_headers(),
            json={},
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def get_task_list_v2(self, group: str = "") -> Dict:
        self._prepare_session(False)
        url = f"{MOBILE_MARKET_URL}/signin/task/taskListV2"
        payload = {
            "marketname": "sign_in_3",
            "clientVersion": MARKET_CLIENT_VERSION,
            "group": group,
        }
        resp = self.session.post(
            url,
            headers=self._build_receive_headers(),
            json=payload,
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()

    def do_task(self, key: str, task_id: str) -> bool:
        self._prepare_session(False)
        if not self.device_id:
            self.device_id = self._generate_device_id()
        url = f"{MOBILE_MARKET_URL}/signin/task/click?key={key}&id={task_id}"
        resp = self.session.get(url, headers=self._build_receive_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return self._is_success(data)

    def receive_task_reward(self, task_id: str) -> bool:
        self._prepare_session(True)
        url = f"{MOBILE_MARKET_URL}/signin/page/receiveTask?taskId={task_id}"
        resp = self.session.get(url, headers=self._build_receive_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return self._is_success(data)

    def process_all_tasks(self) -> None:
        resp = self.get_task_list_v2()
        if not self._is_success(resp):
            print(f"获取任务列表失败: {resp.get('msg', '')}")
            return
        result = resp.get("result", {})
        if not isinstance(result, dict):
            print("任务列表格式异常")
            return
        for group, tasks in result.items():
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                task_id = str(task.get("id", ""))
                if not task_id:
                    continue
                status = task.get("status", -1)
                if status == 0:
                    print(f"执行任务: {task.get('name')} (ID: {task_id})")
                    ok = self.do_task(group, task_id)
                    if ok:
                        print(f"任务执行成功，尝试领取奖励")
                        reward_ok = self.receive_task_reward(task_id)
                        print(f"领取奖励: {'成功' if reward_ok else '失败'}")
                    else:
                        print(f"任务执行失败（可能已执行）")
                elif status == 1:
                    print(f"任务已完成待领取: {task.get('name')} (ID: {task_id})")
                    reward_ok = self.receive_task_reward(task_id)
                    print(f"领取奖励: {'成功' if reward_ok else '失败'}")
                else:
                    print(f"任务状态 {status}，跳过: {task.get('name')}")

    def run_all(self) -> Dict:
        results = {}
        try:
            sign_resp = self.sign_in()
            results['sign_in'] = sign_resp
            print(f"签到结果: {sign_resp.get('msg', '')}")
        except Exception as e:
            results['sign_in'] = {'error': str(e)}
            print(f"签到异常: {e}")
        try:
            info = self.get_cloud_info()
            results['cloud_info'] = info
            total = info.get('result', {}).get('total', 0)
            print(f"当前云朵: {total}")
        except Exception as e:
            results['cloud_info'] = {'error': str(e)}
            print(f"获取云朵信息异常: {e}")
        try:
            receive_resp = self.receive_all()
            results['receive_all'] = receive_resp
            print(f"领取奖励: {receive_resp.get('msg', '')}")
        except Exception as e:
            results['receive_all'] = {'error': str(e)}
            print(f"领取奖励异常: {e}")
        try:
            shake_resp = self.shake()
            results['shake'] = shake_resp
            print(f"摇一摇: {shake_resp.get('message', '')}")
        except Exception as e:
            results['shake'] = {'error': str(e)}
            print(f"摇一摇异常: {e}")
        try:
            wx_sign = self.sign_in_wx()
            results['wx_sign'] = wx_sign
            print(f"微信签到: {wx_sign.get('msg', '')}")
        except Exception as e:
            results['wx_sign'] = {'error': str(e)}
            print(f"微信签到异常: {e}")
        try:
            wx_draw = self.wx_draw()
            results['wx_draw'] = wx_draw
            print(f"微信抽奖: {wx_draw.get('msg', '')}")
        except Exception as e:
            results['wx_draw'] = {'error': str(e)}
            print(f"微信抽奖异常: {e}")
        try:
            msg_push = self.obtain_msg_push_on()
            results['msg_push'] = msg_push
            print(f"消息推送奖励: {msg_push.get('msg', '')}")
        except Exception as e:
            results['msg_push'] = {'error': str(e)}
            print(f"消息推送奖励异常: {e}")
        try:
            backup = self.receive_backup_gift()
            results['backup_gift'] = backup
            print(f"备份好礼: {backup.get('msg', '')}")
        except Exception as e:
            results['backup_gift'] = {'error': str(e)}
            print(f"备份好礼异常: {e}")
        try:
            multiple = self.cloud_multiple()
            results['cloud_multiple'] = multiple
            print(f"云朵翻倍: {multiple.get('msg', '')}")
        except Exception as e:
            results['cloud_multiple'] = {'error': str(e)}
            print(f"云朵翻倍异常: {e}")
        try:
            revival = self.receive_revival_reward()
            results['revival'] = revival
            print(f"复活卡: {revival.get('msg', '')}")
        except Exception as e:
            results['revival'] = {'error': str(e)}
            print(f"复活卡异常: {e}")
        try:
            self.process_all_tasks()
            results['tasks'] = 'processed'
        except Exception as e:
            results['tasks'] = {'error': str(e)}
            print(f"任务处理异常: {e}")
        return results

def main():
    sso_token = os.environ.get("SSO_TOKEN")
    jwt_token = os.environ.get("JWT_TOKEN")
    if not sso_token or not jwt_token:
        phone = os.environ.get("PHONE")
        password = os.environ.get("PASSWORD")
        if not phone or not password:
            print("错误: 请设置环境变量 SSO_TOKEN/JWT_TOKEN 或 PHONE/PASSWORD")
            exit(1)
        print("正在使用手机号密码自动登录...")
        try:
            sso_token, jwt_token = CaiyunClient.login_with_password(phone, password)
            print("登录成功，已自动获取 Token")
        except Exception as e:
            print(f"自动登录失败: {e}")
            exit(1)
    device_id = os.environ.get("DEVICE_ID")
    client = CaiyunClient(sso_token, jwt_token, device_id)
    print("开始执行签到流程...")
    results = client.run_all()
    print("\n===== 执行摘要 =====")
    for key, value in results.items():
        if isinstance(value, dict):
            msg = value.get("msg", value.get("message", "无消息"))
            if "error" in value:
                msg = f"错误: {value['error']}"
            print(f"{key}: {msg}")
        else:
            print(f"{key}: {value}")

if __name__ == "__main__":
    main()
