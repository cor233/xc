## 环境变量

- `yunpan`（必填）：账号信息，格式：`Authorization#手机号#昵称`，多账号用 `&` 分隔
- `yunpan_device_id`（可选）：指定 deviceId，不填则自动生成并缓存
- `YDYP_DEVICE_ID`（可选）：`yunpan_device_id` 的备用变量名
- `yunpan_storage_dir`（可选）：自定义缓存目录，默认脚本所在目录
- `YDYP_STORAGE_DIR`（可选）：`yunpan_storage_dir` 的备用变量名
- `BARK_URL`（可选）：Bark 推送地址
- `BARK_PUSH`（可选）：`BARK_URL` 的备用变量名
- `SENDKEY`（可选）：Server酱 SendKey
- `PUSHPLUS_TOKEN`（可选）：PushPlus Token
- `TG_BOT_TOKEN`（可选）：Telegram Bot Token，需与 `TG_CHAT_ID` 同时配置
- `TG_CHAT_ID`（可选）：Telegram Chat ID，需与 `TG_BOT_TOKEN` 同时配置
- `WEBHOOK_URL`（可选）：通用 Webhook 地址，POST JSON `{title, content}`
