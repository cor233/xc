import os
import json
import time
import random
import requests

PHONE = os.environ.get("PHONE")
PASSWORD = os.environ.get("PASSWORD")

if not PHONE or not PASSWORD:
    raise ValueError("PHONE and PASSWORD must be set")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; MI 8 Build/QKQ1.190828.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.146 Mobile Safari/537.36 MCloudApp/12.5.4 AppLanguage/zh-CN",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh,zh-CN;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "X-Requested-With": "com.chinamobile.mcloud",
})

def login():
    url = "https://m.mcloud.139.com/mcloud/oauth/token"
    data = {
        "grant_type": "password",
        "username": PHONE,
        "password": PASSWORD,
        "app_id": "3002",
        "source": "1000"
    }
    resp = SESSION.post(url, data=data, timeout=30)
    resp.raise_for_status()
    token_data = resp.json()
    if "access_token" not in token_data:
        raise Exception("Login failed: " + str(token_data))
    access_token = token_data["access_token"]
    user_domain_id = token_data.get("userDomainId", PHONE)
    SESSION.headers.update({
        "jwtToken": access_token,
        "jwttoken": access_token,
        "Authorization": "Bearer " + access_token,
        "x-yun-tid": str(int(time.time() * 1000)),
        "x-yun-client-info": f"4||1|12.5.4||MI 8|{int(time.time()*1000)}||android 10|||||"
    })
    return access_token, user_domain_id

def get_device_id():
    return "".join(random.choices("0123456789abcdef", k=32))

def ensure_device():
    device_id = get_device_id()
    SESSION.headers["deviceId"] = device_id
    SESSION.headers["x-yun-device-id"] = device_id
    return device_id

def prepare_signin_session():
    ensure_device()
    page_url = "https://m.mcloud.139.com/portal/mobilecloud/index.html?path=newsignin&sourceid=1097&enableShare=1&token=&targetSourceId=001005"
    SESSION.get(page_url, timeout=30)
    keywords = [
        "newsignin_index_pv",
        "newsignin_index_client",
        "newsignin_index_app_client",
        "newsignin_index_cookie_login",
        "newsignin_index_cookie",
        "newsignin_index_app_cookie_login",
    ]
    for kw in keywords:
        SESSION.post(
            "https://m.mcloud.139.com/ycloud/visitlog/journaling",
            data=f"module=uservisit&optkeyword={kw}&sourceid=1097&marketName=sign_in_3",
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=30
        )

def sign_in():
    prepare_signin_session()
    url = "https://m.mcloud.139.com/ycloud/signin/page/startSignIn?client=app"
    resp = SESSION.get(url, timeout=30)
    return resp.json()

def get_cloud_info():
    prepare_signin_session()
    url = "https://m.mcloud.139.com/ycloud/signin/page/infoV3?client=app"
    resp = SESSION.get(url, timeout=30)
    return resp.json()

def receive_pending():
    prepare_signin_session()
    url = "https://m.mcloud.139.com/ycloud/signin/page/receiveV2?client=app"
    resp = SESSION.get(url, timeout=30)
    return resp.json()

def shake():
    url = "https://caiyun.feixin.10086.cn:7071/shake-server/shake/shakeIt?flag=1"
    resp = SESSION.post(url, timeout=30)
    return resp.json()

def sign_in_wx():
    url = "https://caiyun.feixin.10086.cn/market/playoffic/followSignInfo?isWx=true"
    resp = SESSION.get(url, timeout=30)
    return resp.json()

def wx_draw():
    url = "https://caiyun.feixin.10086.cn/market/playoffic/draw"
    resp = SESSION.get(url, timeout=30)
    return resp.json()

def obtain_msg_push():
    url = "https://caiyun.feixin.10086.cn/market/msgPushOn/task/obtain"
    resp = SESSION.post(url, json={"type": 2}, timeout=30)
    return resp.json()

def receive_backup_gift():
    url = "https://caiyun.feixin.10086.cn:7071/backupgift/receive"
    resp = SESSION.get(url, timeout=30)
    return resp.json()

def cloud_multiple():
    prepare_signin_session()
    url = "https://m.mcloud.139.com/ycloud/signin/page/multiple"
    resp = SESSION.get(url, timeout=30)
    return resp.json()

def receive_revival():
    prepare_signin_session()
    url = "https://m.mcloud.139.com/ycloud/signin/page/receiveRevivalReward"
    resp = SESSION.post(url, json={}, timeout=30)
    return resp.json()

def complete_ai_camera(user_domain_id):
    tid = str(int(time.time() * 1000))
    headers = {
        "x-yun-api-version": "v1",
        "x-yun-tid": tid,
        "sec-ch-ua": '"Android WebView";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "X-Requested-With": "com.chinamobile.mcloud",
        "Origin": "https://frontend.mcloud.139.com",
        "Referer": "https://frontend.mcloud.139.com/",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh,zh-CN;q=0.9,en-US;q=0.8,en;q=0.7",
        "x-DeviceInfo": f"||36|12.5.4||MI 8|{tid}||android 10|||||"
    }
    SESSION.headers.update(headers)

    recognize_body = {
        "channelId": "101",
        "userId": user_domain_id,
        "recognizeType": "1",
        "base64": "data:image/jpg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAkGBxAQEBUQEBAVFRUVFRUVFRUVFRUVFRUVFRUWFhUVFRUYHSggGBolHRUVITEhJSkrLi4uFx8zODMsNygtLisBCgoKDg0OGhAQGi0lHyUtLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLf/AABEIAAEAAQMBIgACEQEDEQH/xAAXAAEBAQEAAAAAAAAAAAAAAAAAAQID/8QAFhEBAQEAAAAAAAAAAAAAAAAAABEh/9oADAMBAAIRAxEAPwHhAH//xAAZEAEBAQEBAQAAAAAAAAAAAAABEQIhMUH/2gAIAQEAAT8A2M4Kxqf/xAAWEQEBAQAAAAAAAAAAAAAAAAAAESH/2gAIAQIBAT8Ap//EABYRAQEBAAAAAAAAAAAAAAAAAAABEf/aAAgBAwEBPwCf/9k=",
        "sendType": "2",
        "imageExt": "jpg",
        "uploadToCloud": True,
        "timeout": 30000
    }
    resp = SESSION.post("https://ai.yun.139.com/api/image/aiRecognize", json=recognize_body, timeout=30)
    recognize_data = resp.json()
    if not recognize_data.get("success"):
        raise Exception("AI recognize failed: " + str(recognize_data))
    file_id = recognize_data["data"]["fileId"]
    if not file_id:
        raise Exception("No fileId in AI recognize response")

    now = time.strftime("%Y-%m-%dT%H:%M:%S.000+08:00", time.localtime())
    chat_body = {
        "userId": user_domain_id,
        "sessionId": "",
        "applicationType": "chat",
        "applicationId": "",
        "sourceChannel": "101",
        "dialogueInput": {
            "dialogue": "？",
            "prompt": "",
            "inputTime": now,
            "enableForceLlm": False,
            "enableForceNetworkSearch": True,
            "enableModelThinking": False,
            "enableAllNetworkSearch": False,
            "enableKnowledgeAndNetworkSearch": False,
            "enableRegenerate": False,
            "versionInfo": {"h5Version": "2.7.6"},
            "extInfo": "{}",
            "sortInfo": {},
            "toolSetting": {"imageToolSetting": {"enableLlmDescribe": True}},
            "attachment": {
                "attachmentTypeList": [3],
                "fileList": [{"fileId": file_id, "name": f"{int(time.time()*1000)}.jpeg"}]
            }
        }
    }
    headers_chat = {
        "Accept": "text/event-stream",
        "x-yun-client-info": f"4||1|12.5.4||MI 8|{tid}||android 10|||||",
        "x-yun-app-channel": "101",
        "Content-Type": "application/json",
        "x-yun-tid": tid,
        "x-yun-api-version": "v1"
    }
    SESSION.headers.update(headers_chat)
    resp = SESSION.post("https://ai.yun.139.com/api/outer/assistant/chat/v2/add", json=chat_body, stream=True, timeout=60)
    for line in resp.iter_lines():
        if not line:
            continue
        line = line.decode('utf-8')
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                data = json.loads(payload)
                if data.get("success") or data.get("code") == "0000":
                    return True
            except:
                pass
    raise Exception("AI chat failed")

def main():
    access_token, user_domain_id = login()
    print("Login success")

    print("Sign in:", sign_in())
    print("Cloud info:", get_cloud_info())
    print("Receive pending:", receive_pending())

    try:
        print("Shake:", shake())
    except Exception as e:
        print("Shake error:", e)

    try:
        print("Wx sign in:", sign_in_wx())
    except Exception as e:
        print("Wx sign error:", e)

    try:
        print("Wx draw:", wx_draw())
    except Exception as e:
        print("Wx draw error:", e)

    try:
        print("Msg push:", obtain_msg_push())
    except Exception as e:
        print("Msg push error:", e)

    try:
        print("Backup gift:", receive_backup_gift())
    except Exception as e:
        print("Backup gift error:", e)

    try:
        print("Cloud multiple:", cloud_multiple())
    except Exception as e:
        print("Multiple error:", e)

    try:
        print("Revival reward:", receive_revival())
    except Exception as e:
        print("Revival error:", e)

    try:
        print("AI camera task:", complete_ai_camera(user_domain_id))
    except Exception as e:
        print("AI camera error:", e)

if __name__ == "__main__":
    main()
