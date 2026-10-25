# Claude-codex-link

让 Claude Code（或任何脚本）把任务发进 **Codex 桌面版**里已有的对话，并**准确等到这一轮结束**，拿到 Codex 的最终回复。人可以在 Codex 桌面版里实时看到全过程，也可以随时插话或点停止。

Send a message into an existing **Codex desktop** thread and wait for *that exact turn* to finish, then print Codex's final reply. You keep watching (and steering) the run live in the desktop app.

## 为什么需要它

`codex queue` 只负责把消息排进桌面版的队列，发完立刻返回，不会等 Codex 回复。自己去盯对话记录时，很容易踩这几个坑（都是实测踩到的）：

- `codex queue` 返回的 “Queued message …” 编号**不是**这一轮的编号，拿它去对 `task_complete` 永远对不上。
- `codex queue --thread <对话名>` 即使桌面版开着也可能报 `No active session`，用对话 ID 才稳定。
- 对话记录在 Windows 上被追加时，文件修改时间不更新，不能按 mtime 判断有没有新内容。
- npm 的 `codex.CMD` 经过 cmd.exe，消息里的换行、引号、`%`、`^` 可能被改掉。
- 你在 Codex 干活时插话，会并进当前这一轮；点停止后 Codex 还会自动补跑一轮。

这个脚本的做法：

1. 把对话名换成对话 ID（读 `~/.codex/state_5.sqlite`）。
2. 在消息末尾加一个唯一标记 `〔Claude#xxxxxx〕`，用 `node …/codex.js queue --thread <ID>` 发送。
3. 从对话记录 `rollout-*.jsonl` 里按字节位置往下读，找到原文带这个标记的那条消息，取它所在这一轮的编号。
4. 等这一轮的 `task_complete`（输出最终回复）或 `turn_aborted`（被停止）。
5. 排队期间读 `~/.codex/queue_1.sqlite` 报告“在队列里”“已被取走”；Codex 没开时提示消息会在打开后送出。

## 用法

```bash
python -X utf8 skills/claude-codex-collab/scripts/codex_ask.py "<对话名或ID>" message.txt
echo "请只回复：收到" | python -X utf8 skills/claude-codex-collab/scripts/codex_ask.py "<对话名或ID>" -
```

- 进度写到 stderr，Codex 的最终回复写到 stdout。
- `--timeout 秒数`：默认 1800。
- 超时后会打印一条接着等的命令：`codex_ask.py <ID> - --no-send --nonce <标记>`。

| 退出码 | 含义 |
| --- | --- |
| 0 | 这一轮完成，stdout 是最终回复 |
| 2 | 这一轮被停止 |
| 3 | 发不出去，或对话记录不存在（先在桌面版打开这个对话） |
| 4 | 超时 |
| 5 | Codex 报错（重试用尽或不可恢复，stdout 是错误文本） |
| 1 | 其它错误 |

## 自动恢复（2026-10-25 新增）

- 额度用完：等到 “try again at” 时间自动发续做消息（`--retries`，默认 3）；连接失败：1 分钟后重试。
- 回合被中止：再跟踪 `--grace` 秒（默认 900），用户在 Codex 里接着做就报告那一轮的回复。
- 被取走 8 分钟没开始：自动再发一次。
- `--pending`（配合 SessionStart / UserPromptSubmit hook）补报会话重启后丢失的回复；`--watch <对话>` 在任何一轮结束时退出，用来唤醒 Claude。
- 离线测试：`python -X utf8 skills/claude-codex-collab/scripts/test_codex_ask.py`。

在 Claude Code 里建议后台运行，结束时会收到通知。

## 环境

- Windows，已安装 Codex 桌面版，以及 npm 版 Codex CLI（脚本调用 `%APPDATA%\\npm\\node_modules\\@openai\\codex\\bin\\codex.js`）。
- Python 3.10+，只用标准库。
- 2026-10-22 在 Codex 桌面版 + codex-cli 0.163.0 上测试：简单回复、特殊字符原样送达、30 秒带命令的长任务、连发两条（第二条排队）、中途点停止、中途插话、桌面版退出时发送（重开后自动送出）、超时后接着等，均通过。

依赖的是 Codex 的本地文件格式（`state_5.sqlite`、`queue_1.sqlite`、`rollout-*.jsonl`），不是公开接口，Codex 升级后可能失效。

## Claude Code Skill

`skills/claude-codex-collab/` 是把这套协作经验整理成的 Claude Code skill（含最新版 `codex_ask.py`、hook 兜底、分工与踩坑清单）。复制到 `~/.claude/skills/` 即可使用。

## License

MIT
