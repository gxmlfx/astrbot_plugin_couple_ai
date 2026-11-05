# 情侣 AI · 跨房间任务与日记

一个给 AstrBot 用的多会话插件：让不同私聊、群聊像同一个人一样记事、传话和写日记。

- **跨房间任务**：让另一个会话在指定时间收到一句话或提醒
- **目标语气生成**：传话由目标会话绑定的人格和模型生成，不是原样转发
- **共享事件日志**：记录用户和 Bot 在各会话的发言，标明说话人；其他会话按自己的水印看到「最近在别处做了什么」
- **每日日记**：按设定时间生成 Markdown 日记，严格区分「我」和「用户」说过的话，保存并发送到指定会话
- **WebUI**：创建、取消、重试任务，查看事件和日记，修改插件设置
- **模块化**：任务、事件、日记各自独立，方便继续扩展

## 适用场景

- 两个私聊 + 一个小群，希望它们共享一点上下文
- 想让 Bot 在 A 会话说的事，过一会儿在 B 会话被提起
- 想要每天一篇日记，而不是一堆零散聊天记录

## 安装

### 从链接安装

在 AstrBot WebUI 中打开「插件」→「安装插件」→「从链接安装」，填入：

```text
https://github.com/gxmlfx/astrbot_plugin_couple_ai
```

安装完成后重启 AstrBot。

### 手动安装

```bash
cp -r astrbot_plugin_couple_ai ~/astrbot/data/plugins/
docker restart astrbot
```

要求：

- AstrBot >= 4.24.0
- Python >= 3.10

## 快速开始

1. 打开 AstrBot WebUI → 插件 → 情侣 AI
2. 在「设置」里填写你的 QQ、对方 QQ、群号和称呼
3. 打开「只允许这三个会话」，或按需关闭
4. 保存后，直接在任意会话里说：

```text
跟对方说：今晚不回去吃饭了
明天早上八点提醒她带伞
把这句话带到小群：……
```

WebUI 新建任务时，目标只需填 QQ 号或群号：填设置里的小群号会自动识别成群聊，其他号码按私聊处理。

也可以直接在 WebUI 里创建任务。

## WebUI

插件页面包含：

- **设置**：功能开关、QQ/群号、称呼、事件与任务参数、日记设置
- **任务**：创建、取消、重试、标记完成
- **事件**：查看用户和 Bot 的发言、清空
- **日记**：生成、重写、查看、删除；列表显示字数

页面会跟随系统浅色/深色模式。

## 会话 UMO

AstrBot 用 UMO 标识一个会话：

```text
platform_id:MessageType:session_id
```

常见例子：

```text
default:FriendMessage:<QQ号>
default:GroupMessage:<群号>
```

在 AstrBot 的会话管理里可以直接复制 UMO。

## 模型工具

插件向模型注册了四个工具：

| 工具 | 作用 |
| --- | --- |
| `couple_task_create` | 创建跨会话任务 |
| `couple_task_list` | 查看当前会话相关任务 |
| `couple_task_cancel` | 取消未发送任务 |
| `couple_task_ack` | 确认已处理发给自己的任务 |

模型会在合适的时候自动调用，不需要手动输入工具名。

## 配置

大部分配置可以直接在 WebUI 的「设置」里改。

| 配置 | 说明 |
| --- | --- |
| `enabled` | 插件总开关 |
| `allow_sessions` | 允许的会话 UMO，每行一个；留空表示不限制 |
| `session_labels` | `UMO=别名`，用于显示 |
| `platform_id` | 平台 ID，默认 `default` |
| `owner_qq` / `partner_qq` / `group_id` | 用于生成三个会话的 UMO |
| `owner_name` / `partner_name` / `group_name` | 三个会话的显示称呼 |
| `restrict_sessions` | 是否只允许上面三个会话 |
| `event_log_enabled` | 是否记录事件 |
| `event_inject_count` | 每次模型请求注入最近几条事件，默认 5 |
| `event_max_chars` | 单条事件最大字数，默认 80 |
| `event_keep` | 事件环保留条数，默认 200 |
| `task_max_pending` | 单个会话待处理任务上限 |
| `task_max_chars` | 任务内容最大字数 |
| `check_interval_seconds` | 任务检查间隔 |
| `task_retry_seconds` | 任务失败重试间隔 |
| `task_max_attempts` | 任务最大尝试次数 |
| `deliver_prefix` | 任务消息前缀，默认空 |
| `disable_external_relay_tools` | 停用其他插件的跨房间转发工具，避免重复发送 |
| `diary_enabled` | 启用每日日记 |
| `diary_time` | 日记生成时间，如 `23:00` |
| `diary_target` | 日记接收会话的 UMO |
| `diary_max_chars` | 文件发送失败时的文本回退上限 |

## 说明

- 传话会用目标会话的人格和当前模型生成，但**不会携带目标会话的完整聊天历史**；如果需要更强上下文，可以再接 conversation manager
- 事件日志记录用户和 Bot 的发言摘要，不是完整聊天记录；旧版本只记录 Bot 发言
- 任务失败会按配置重试，成功后标记为完成，也可以用 `couple_task_ack` 手动确认
- 日记以 Markdown 文件发送；如果协议端访问不到 AstrBot 的文件服务，会自动回退为文本
- 任务默认不区分静默时段，需要的话可以在调用侧控制

## 目录结构

```text
astrbot_plugin_couple_ai/
├── metadata.yaml
├── main.py
├── _conf_schema.json
├── README.md
├── LICENSE
└── pages/couple/
    ├── index.html
    ├── app.js
    └── style.css
```

## License

MIT