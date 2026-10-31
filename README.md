# 情侣 AI · 跨房间任务与日记（astrbot_plugin_couple_ai）

让 AstrBot 的多个会话像同一个人：

- **跨房间任务**：在一个会话里让另一个会话在指定时间收到一句话/提醒
- **目标语气生成**：所有传话强制由目标会话的人格用自己的语气生成，不再提供原样直发
- **共享事件日志 + 水印**：Bot 每次发言进入事件环，其他会话按自己的 watermark 增量看到「我最近在别处做了什么」
- **每日日记**：到点生成 Markdown 日记，保存并发送到指定会话
- **WebUI**：创建/取消/重试任务，查看与清空事件，生成/查看日记
- **可扩展**：`events`、`tasks`、`diaries`、`webui` 分层

## 安装

### 方式一：AstrBot WebUI（推荐）

1. 把本目录打包成 zip（或上传到 GitHub 后填仓库地址）
2. AstrBot WebUI → 插件 → 安装插件 → 从文件/URL 安装
3. 重启 AstrBot

### 方式二：手动

```bash
cp -r astrbot-plugin-couple-ai ~/astrbot/data/plugins/astrbot_plugin_couple_ai
docker restart astrbot
```

## WebUI 设置

插件页面顶部有「设置」：

- 启用插件、记录事件、只允许三个会话
- 平台 ID、我的 QQ、对方 QQ、小群号、三个称呼
- 事件注入条数/字数/保留条数
- 任务内容上限/待处理上限/检查间隔/消息前缀
- 每日日记开关、时间、目标 UMO
- 停用外部跨房间工具（保存后需重启 AstrBot 生效）

保存后会同步生成 `session_labels`、`allow_sessions` 和 `diary_target`，不需要手写 UMO。

页面同时跟随系统浅色/深色模式。

## 使用

### 1. 让模型创建任务

在任意会话里说：

> 跟对方说：今晚不回去吃饭了  
> 明天早上八点提醒她带伞  
> 把这句话带到小群：……

模型会调用 `couple_task_create`：

```json
{
  "to_session": "default:FriendMessage:<目标QQ>",
  "text": "今晚不回去吃饭了",
  "due_at": "",
  "kind": "notify"
}
```

### 2. 会话 UMO

AstrBot 的会话 ID 格式是：

```
platform_id:MessageType:session_id
```

例如：

```
default:FriendMessage:<你的QQ>
default:FriendMessage:<对方QQ>
default:GroupMessage:<群号>
```

在 WebUI「会话管理」里可以直接复制 UMO。

### 3. WebUI

AstrBot WebUI → 插件 → 情侣 AI → 打开页面：

- 新建任务：填目标 UMO、内容、时间（留空立即）
- 任务列表：取消 / 重试 / 标记完成
- 事件日志：查看 / 清空

## 配置

在 AstrBot 插件配置里：

| 配置 | 说明 |
| --- | --- |
| `enabled` | 总开关 |
| `event_log_enabled` | 记录事件 |
| `event_inject_count` | 每次 LLM 请求注入最近几条，默认 5 |
| `event_max_chars` | 单条事件最大字数，默认 80 |
| `event_keep` | 事件环保留条数，默认 200 |
| `allow_sessions` | 允许的会话 UMO，每行一个；留空不限制 |
| `session_labels` | `UMO=别名`，用于显示 |
| `task_max_pending` | 目标会话待处理上限 |
| `task_max_chars` | 任务内容最大字数 |
| `check_interval_seconds` | 任务轮询间隔 |
| `deliver_prefix` | 任务消息前缀，默认空 |
| `disable_external_relay_tools` | 停用 private_companion 的 pc_send_to_group / pc_relay_message 等，避免和本插件抢路 |
| `task_retry_seconds` | 任务失败重试间隔 |
| `task_max_attempts` | 任务最大尝试次数 |
| `diary_enabled` | 启用每日日记 |
| `diary_time` | 日记生成时间，如 `23:00` |
| `diary_target` | 日记接收 UMO |
| `diary_max_chars` | 文件发送失败时的文本回退上限 |

## 日记文件发送（重要）

AstrBot 的 `File` 组件走 `callback_api_base` 的 file service，而不是把本地路径直接丢给协议端。

在 AstrBot 配置里设置：

```json
"callback_api_base": "http://<协议端能访问的 AstrBot 地址>:6185"
```

- Docker 同机：`http://172.17.0.1:6185`（Linux docker0 网关）
- 其他部署：填 AstrBot WebUI 的公网/内网地址
- 没配或协议端访问不到：插件会自动回退成文本发送，不会卡住

这样不同用户不需要共享卷，也不需要把 AstrBot 文件目录挂给协议端。

## 当前边界

- `target_voice` 用目标会话的人格 + 当前 provider 生成，**不携带目标会话的完整聊天历史**；需要更强上下文可以后续接 conversation manager
- 任务失败会按 `task_retry_seconds` 重试，最多 `task_max_attempts` 次；成功即 done（也可用 `couple_task_ack` 手动确认）
- 事件日志记录 Bot 的**发言摘要**，不是聊天记录；按会话 watermark 增量注入
- 日记文件发送使用 AstrBot 的 **file service**：需要把 `callback_api_base` 配成协议端能访问的地址
  （例如 `http://172.17.0.1:6185`），不需要给每个用户加共享卷；为空或协议端访问不到时自动回退文本
- 任务默认不过滤静默时段

## 上传到 AstrBot 插件库

1. 建 GitHub 仓库（名字建议 `astrbot_plugin_couple_ai`）
2. 推到仓库，确认根目录有 `metadata.yaml`、`main.py`、`_conf_schema.json`、`pages/`
3. 到 AstrBot 插件市场/仓库提交你的仓库地址
4. 之后用户可以在 AstrBot WebUI 里直接搜索/安装

仓库结构：

```
astrbot_plugin_couple_ai/
├── metadata.yaml
├── main.py
├── _conf_schema.json
├── README.md
├── LICENSE
└── pages/couple/index.html
    ├── app.js
    └── style.css
```

## License

MIT
