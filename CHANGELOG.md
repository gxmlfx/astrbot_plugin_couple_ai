# Changelog

## 0.4.3

- 事件上下文改用 `extra_user_content_parts` + `mark_as_temp()` 注入，不再修改 system prompt，恢复提示词缓存
- 最低 AstrBot 版本提高到 4.24.0

## 0.4.2

- 日记提示与导出文件名改为通用文案，移除个人称呼

## 0.4.1

- 修复插件页面在 bridge SDK 之后加载导致的空白 UI

## 0.4.0

- WebUI 增加功能开关、QQ/群号映射、任务/事件/日记配置
- WebUI 跟随系统浅色/深色模式

## 0.3.0

- 传话强制使用目标语气生成（移除 direct 模式）
- 默认配置、示例和包内文档移除真实 QQ/群号与个人称呼

## 0.2.2

- 可选停用 private_companion 的 pc_send_to_group / pc_relay_message 等外部跨房间工具
- 避免模型绕开 couple_task_create 被其他插件的权限拦住

## 0.2.1

- 文档：日记文件发送改用 AstrBot file service，不再需要共享卷
- 人格：补情侣模式说明

## 0.2.0

- 任务默认由目标会话的人格用自己的语气生成（`task_mode=target_voice`）
- 任务失败自动重试，`attempts` / `next_attempt_at` 持久化
- `couple_task_ack` 工具
- 事件注入按会话 watermark 增量，不再重复注入旧事件
- 每日日记模块：生成 Markdown、保存、发送，WebUI 查看/生成/删除
- WebUI 增加日记面板、任务方式/尝试次数

## 0.1.0

- 跨房间任务、事件日志、WebUI
