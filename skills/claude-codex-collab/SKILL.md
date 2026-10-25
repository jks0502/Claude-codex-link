---
name: claude-codex-collab
description: Delegate work from Claude Code to the Codex desktop app and collect results reliably (Windows). Use when handing a task to Codex, waiting for its reply, reviewing what Codex did, or when a [codex_ask] notice appears in context.
---

# Claude ⇄ Codex 协作

让 Claude 写方案、审查、查根因，让 Codex 在桌面版里执行；用户能在 Codex 桌面版实时看到过程。

## 1. 发任务：用 `scripts/codex_ask.py`

```bash
python -X utf8 <path>/codex_ask.py "<对话名或ID>" <消息文件|->
```

- 用 **后台运行**（run_in_background）；结束时收到通知，stdout 是 Codex 的最终回复。
- 退出码：0 完成 / 2 被停止（且宽限期内没人接着做）/ 3 发不出 / 4 超时（会打印接着等的命令 `... - --no-send --nonce X`）/ 5 Codex 报错 / 1 其它。
- **一次后台运行就够**，`--timeout` 设大（如 36000）。不要再手写 sleep+重发的定时任务，脚本已内置恢复逻辑（见下）。
- 没开 Codex 桌面版时消息留在队列，打开后自动送出。

### 不要这样做（都实测踩过）
- 不要 `codex queue --thread <对话名>`：按名字会报 `No active session`，要用对话 ID（脚本已自动把名字换成 ID）。
- 不要 `codex exec resume`：它在后台另起一个进程，和桌面版抢同一个对话。
- 不要靠 git 提交或网站变化判断 Codex 做完没有；脚本按唯一标记 `〔Claude#xxxxxx〕` 找到那一轮，等它的 `task_complete`。
- 不要用 npm 版 CLI 发送给较旧的桌面版：版本不一致时队列项会被桌面版悄悄忽略；脚本优先用桌面版自带的 `codex.exe`。
- 桌面版刚自动更新/重启后，排队消息要等用户在界面里点开那个对话才会被取走；排队超过一两分钟没动就请用户点开。

### 内置自动恢复
- 回合以 error 结束不当作“完成”：额度用完 → 等到 “try again at” 时间自动发一条续做消息（`--retries`，默认 3 次）；连接失败 → 1 分钟后重试；其它错误（如对话建在别的模型提供商上）→ 退出码 5 并输出错误文本。
- 回合被中止 → 再跟踪 `--grace` 秒（默认 900）：用户在 Codex 里接着做，就跟那一轮并报告它的回复（退出 0），而不是退出码 2 漏掉。
- 被取走 8 分钟还没开始回合 → 同一条消息自动再发一次。
- 离线自测：`python -X utf8 scripts/test_codex_ask.py`（7 个场景，假 rollout + 假队列）。

## 2. 回复不丢：hook 兜底

后台等待的完成通知绑定发起它的 claude 进程，会话重启后会丢。脚本会把每次请求记在 `~/.claude/codex-asks/<nonce>.json`，再用 hook 在每次会话启动/用户发言时补报：

```json
"hooks": {
  "SessionStart":     [{"hooks": [{"type": "command", "command": "python -X utf8 \"<path>/codex_ask.py\" --pending", "timeout": 15}]}],
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python -X utf8 \"<path>/codex_ask.py\" --pending", "timeout": 15}]}]
}
```

上下文里出现 `[codex_ask] 有 Codex 结果没有送达` 时：先审查那条回复，或按给出的命令后台接着等，再回答用户。用户直接在 Codex 里继续的回合（不经脚本）也会被 `--pending` 报告。

另有 `codex_ask.py --watch <对话>`：后台挂着，任何一轮结束（谁发起的都算）就退出并输出回复，用来唤醒 Claude；处理完要重新挂上。

## 3. 分工与写任务单

- **会话与 Codex 对话一一对应**：每个 Claude 会话派活前先确认自己绑定了哪个 Codex 对话 ID；没有就提醒用户新建一个，别沿用别的会话的对话。

- **Claude**：写方案、定位根因、审查。**Codex**：执行与收尾（版本记录、长测、提交、文档）。
- Codex 卡在硬件/驱动类难题、只会逐个试时，Claude 亲自查到根因，做最小验证，再把收尾交回 Codex。
- 新对话没有上下文：消息里写全背景、文档路径、验收标准。
- 独立的大任务优先在对应项目里**新开对话**，别挤在一个忙着的对话里（消息会并进当前一轮）。
- 任务单里写明“不要中途找用户”：用模拟器/截图/串口日志自己验收；需要提权（UAC）的步骤合并到最后一次；只有物理上必须本人做的事才一次说清。
- 看改动：`git status` 加 Codex 的回复，审查后再进行下一步。
- 烧录固件、部署上线等不可逆动作，先问用户（除非用户对具体设备已明确长期授权）。

## 4. 每日模型检查（可选）

每天第一次互相调用时，读一个记录日期的文件；不是今天就查 Claude 与 Codex 有无更新模型，写入今天日期；有新模型只问用户是否切换，不自动换。

## 5. 限额中断

脚本已自动处理（见“内置自动恢复”）。对没经过脚本的对话，可以自己写个小守护进程：发现以 `usage_limit_exceeded` 结束的回合，重置后用 `codex queue` 排一条“接着做”（不要用 `codex resume`，会和桌面版抢对话）。

## 依赖

Windows、Codex 桌面版、Python 3.10+（可选 `psutil` 以判断 claude 进程是否还活着）。依赖 Codex 本地文件格式（`~/.codex/state_5.sqlite`、`queue_1.sqlite`、`rollout-*.jsonl`），升级后可能失效。
