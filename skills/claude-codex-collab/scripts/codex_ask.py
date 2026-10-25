"""Queue a message into a Codex desktop thread and wait for that exact turn to finish.

usage: python -X utf8 codex_ask.py <thread name|uuid> <message file|-> [--timeout 1800]
       python -X utf8 codex_ask.py <thread name|uuid> - --no-send --nonce X   (resume waiting for an earlier send)
       python -X utf8 codex_ask.py --pending      (hook: report replies Claude has not seen; prints nothing if none)
       python -X utf8 codex_ask.py --show X       (print the recorded reply for nonce X)
       python -X utf8 codex_ask.py --watch <thread> [--timeout s]  (background: exit when any turn ends)
Status lines go to stderr, the final reply to stdout.
Exit 0 complete, 2 aborted, 3 cannot send, 4 timeout, 5 Codex error, 1 other.

Recovery (added 2026-10-25 after a day of manual babysitting):
- A turn that ends with an error is not "done": usage limit -> wait until the "try again at" time and send a
  continue message (up to --retries, default 3); connection failure -> retry after a minute; other errors
  (e.g. a thread created on another model provider) -> exit 5 with the error text.
- Aborted turn -> keep following the thread for --grace seconds (default 900): if the user continues in the
  Codex app, follow that turn and report its reply (exit 0) instead of exiting 2 and missing it.
- Picked up from the queue but no turn after 8 minutes -> queue the same message once more.

Every ask is recorded in ~/.claude/codex-asks/<nonce>.json. The reply only reaches Claude through the
background-task notification, which is lost when the Claude process that started the wait has exited
(session restart) by the time Codex finishes. So at the end the waiter checks that Claude process is still
alive; if not, or if the waiter itself was killed, the record stays undelivered and the SessionStart /
UserPromptSubmit hook (`--pending`) puts the reply, or a resume command, into the next turn's context.

Tested 2026-10-22 against Codex desktop: `codex queue --thread <name>` fails with "No active session"
even when the app is open, so names are resolved to the uuid here. The queued item id is not the turn id;
the turn is found by a unique tag appended to the message. Queue works while the app is closed; the
message is delivered when the app reopens.
"""
import json, os, re, secrets, sqlite3, subprocess, sys, time

HOME = os.path.expanduser('~')
STATE = os.path.join(HOME, '.codex', 'state_5.sqlite')
QUEUE = os.path.join(HOME, '.codex', 'queue_1.sqlite')
def codex_command():
    """The desktop app's own codex.exe (newest install dir): a newer npm CLI writes queue items that an older
    desktop app silently never picks up (seen 2026-10-23: npm 0.164 vs app 0.163). npm CLI only as fallback."""
    root = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'OpenAI', 'Codex', 'bin')
    try:
        exes = [os.path.join(root, d, 'codex.exe') for d in os.listdir(root)]
        exes = [e for e in exes if os.path.isfile(e)]
        if exes:
            return [max(exes, key=os.path.getmtime)]
    except OSError:
        pass
    return ['node', os.path.join(os.environ.get('APPDATA', ''), 'npm', 'node_modules', '@openai', 'codex', 'bin', 'codex.js')]


CODEX = codex_command()
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
RECS = os.path.join(HOME, '.claude', 'codex-asks')
KEEP_DAYS = 3  # --pending ignores records older than this


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, file=sys.stderr, flush=True)


def ro(path):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)


def resolve(thread):
    """name/title/uuid -> (uuid, rollout_path). Newest non-archived match wins."""
    with ro(STATE) as c:
        if UUID.match(thread):
            rows = c.execute('select id, rollout_path from threads where id=?', (thread,)).fetchall()
        else:
            rows = c.execute('select id, rollout_path from threads where archived=0 and (name=? or title=?) '
                             'order by updated_at_ms desc', (thread, thread)).fetchall()
    if not rows:
        raise SystemExit(f'找不到对话: {thread}')
    if len(rows) > 1:
        log(f'有 {len(rows)} 个同名对话，用最新的 {rows[0][0]}')
    tid, path = rows[0]
    return tid, path.replace('\\\\?\\', '')


def app_running():
    r = subprocess.run(['powershell', '-NoProfile', '-Command',
                        "Get-Process codex -ErrorAction SilentlyContinue | ? { $_.Path -like '*OpenAI*Codex*bin*' } | select -First 1 -Expand Id"],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def queued(item_id):
    with ro(QUEUE) as c:
        return c.execute('select 1 from queued_items where id=?', (item_id,)).fetchone() is not None


class Tail:
    """Read a growing jsonl by byte offset (mtime is unreliable on Windows)."""
    def __init__(self, path, start_at_end=True):
        self.path, self.buf = path, b''
        self.pos = os.path.getsize(path) if start_at_end else 0

    def events(self):
        with open(self.path, 'rb') as f:
            f.seek(self.pos)
            data = f.read()
        self.pos += len(data)
        self.buf += data
        *lines, self.buf = self.buf.split(b'\n')
        for l in lines:
            try:
                yield json.loads(l)
            except ValueError:
                pass


def user_text(item):
    return ''.join(c.get('text', '') for c in item.get('content', []) if isinstance(c, dict))


def rec_path(nonce):
    return os.path.join(RECS, nonce + '.json')


def load_rec(nonce):
    try:
        with open(rec_path(nonce), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_rec(nonce, **fields):
    os.makedirs(RECS, exist_ok=True)
    r = load_rec(nonce)
    r.update(fields)
    tmp = rec_path(nonce) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    os.replace(tmp, rec_path(nonce))
    return r


def claude_owner():
    """(pid, create_time) of the nearest claude.exe ancestor: the session that will get the notification."""
    try:
        import psutil
        p = psutil.Process().parent()
        while p:
            if p.name().lower() in ('claude.exe', 'claude'):
                return p.pid, p.create_time()
            p = p.parent()
    except Exception:
        pass
    return None, None


def alive(pid, created):
    if not pid:
        return False
    try:
        import psutil
        return abs(psutil.Process(pid).create_time() - created) < 1
    except Exception:
        return False


def finish(nonce, status, reply=''):
    """Record the outcome; delivered only if the Claude session that started the wait can still be notified."""
    r = load_rec(nonce)
    save_rec(nonce, status=status, reply=reply, finished_at=time.time(),
             delivered=alive(r.get('claude_pid'), r.get('claude_created')))


def send(tid, text):
    """Queue text into the thread -> queued item id, or None on failure (logged)."""
    r = subprocess.run(CODEX + ['queue', '--thread', tid, '--message', text],
                       capture_output=True, text=True, encoding='utf-8')
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        log('发送失败:', out)
        return None
    m = re.search(r'Queued message (\S+)', out)
    return m and m.group(1)


def error_kind(err):
    """task_complete.error -> ('limit', seconds to wait) | ('network', 60) | ('fatal', 0)."""
    text = str(err.get('message', err)) if isinstance(err, dict) else str(err)
    info = json.dumps(err.get('codex_error_info', '')) if isinstance(err, dict) else ''
    if 'usage_limit' in info or 'usage limit' in text:
        m = re.search(r'try again at (\d{1,2}):(\d{2})\s*([AP]M)', text)
        if not m:
            return 'limit', 3600
        h, mi = int(m.group(1)) % 12 + (12 if m.group(3) == 'PM' else 0), int(m.group(2))
        now = time.localtime()
        target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, h, mi, 0, 0, 0, -1))
        if target < time.time() - 60:
            target += 86400
        return 'limit', max(60, target - time.time() + 90)
    if 'http_connection_failed' in info or 'Connection failed' in text or 'error sending request' in text:
        return 'network', 60
    return 'fatal', 0


def scan_rollout(path, tag):
    """For a waiter that died: find the tagged turn in the whole rollout -> (status, reply)."""
    turn = None
    for o in Tail(path, start_at_end=False).events():
        p = o.get('payload', {})
        t = p.get('type')
        if turn is None and t == 'item_completed' and p.get('item', {}).get('type') == 'UserMessage' \
                and tag in user_text(p['item']):
            turn = p.get('turn_id')
        elif turn and p.get('turn_id') == turn and t in ('task_complete', 'turn_aborted'):
            try:
                from datetime import datetime
                ts = datetime.fromisoformat(o['timestamp'].replace('Z', '+00:00')).timestamp()
            except (KeyError, ValueError):
                ts = time.time()
            if t == 'task_complete':
                err = p.get('error')
                if err:
                    return 'error', (err.get('message') if isinstance(err, dict) else str(err)) or '', ts
                return 'done', p.get('last_agent_message') or '', ts
            return 'aborted', p.get('reason') or '', ts
    return ('running' if turn else 'queued'), '', None


def tagged_turns(rollout, turns, window=8 * 1024 * 1024):
    """Which of these turn ids were started by a 〔Claude#…〕-tagged prompt (searching the last few MB)."""
    tail = Tail(rollout)
    tail.pos = max(0, tail.pos - window)
    found = set()
    for o in tail.events():
        p = o.get('payload', {})
        if p.get('type') == 'item_completed' and p.get('turn_id') in turns \
                and p.get('item', {}).get('type') == 'UserMessage' and '〔Claude#' in user_text(p['item']):
            found.add(p.get('turn_id'))
    return found


def user_driven_turns(threads):
    """Turns the user started in the Codex app itself (not via this script) end with no notification at all.
    Keep a byte watermark per thread and report the final message of any such turn finished since."""
    marks_path = os.path.join(RECS, '_threads.json')
    try:
        with open(marks_path, encoding='utf-8') as f:
            marks = json.load(f)
    except (OSError, ValueError):
        marks = {}
    out = []
    for tid, (name, rollout) in threads.items():
        try:
            size = os.path.getsize(rollout)
        except OSError:
            continue
        if tid not in marks or marks[tid] > size:
            marks[tid] = size  # first sight: don't replay history
            continue
        tail = Tail(rollout)
        tail.pos = marks[tid]
        ours, seen, done_by_turn = set(), set(), []
        for o in tail.events():
            p = o.get('payload', {})
            if p.get('type') == 'item_completed' and p.get('item', {}).get('type') == 'UserMessage':
                seen.add(p.get('turn_id'))
                if '〔Claude#' in user_text(p['item']):
                    ours.add(p.get('turn_id'))
            elif p.get('type') == 'task_complete':
                done_by_turn.append((p.get('turn_id'), p.get('last_agent_message') or ''))
        marks[tid] = tail.pos - len(tail.buf)  # don't skip a half-written last line
        unknown_starts = {turn for turn, _ in done_by_turn if turn not in seen}
        if unknown_starts:
            # The turn may have started before the watermark (a --watch exit moves it): look its prompt up.
            ours |= tagged_turns(rollout, unknown_starts)
        done = [reply for turn, reply in done_by_turn if turn not in ours]
        if done:
            out.append(f'Codex 对话「{name}」里有 {len(done)} 轮不是你发起的（用户直接在 Codex 里继续的），'
                       f'已经结束，你还没看过。最后一轮的回复：\n{done[-1]}')
    os.makedirs(RECS, exist_ok=True)
    with open(marks_path + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(marks, f)
    os.replace(marks_path + '.tmp', marks_path)
    return out


def pending():
    if not os.path.isdir(RECS):
        return 0
    out, cutoff = [], time.time() - KEEP_DAYS * 86400
    threads = {}  # tid -> (name, rollout) of threads Claude has asked recently
    for name in sorted(os.listdir(RECS)):
        if not name.endswith('.json') or name.startswith('_'):
            continue
        nonce = name[:-5]
        r = load_rec(nonce)
        if r.get('tid') and r.get('rollout') and r.get('started_at', 0) >= cutoff:
            threads[r['tid']] = (r.get('thread'), r['rollout'])
        if not r or r.get('delivered') or r.get('started_at', 0) < cutoff:
            continue
        head = f'Codex 对话「{r.get("thread")}」的任务〔Claude#{nonce}〕'
        if r.get('status') == 'waiting':
            if alive(r.get('waiter_pid'), r.get('waiter_created')):
                if alive(r.get('claude_pid'), r.get('claude_created')):
                    continue  # still being waited on by a live session
                out.append(f'{head}还在进行，原来等待它的会话已经结束，完成通知不会到达。'
                           f'用后台命令接着等：python -X utf8 {__file__} {r["tid"]} - --no-send --nonce {nonce}')
                save_rec(nonce, delivered=True)
                continue
            try:
                status, reply, ts = scan_rollout(r['rollout'], r['tag'])
            except OSError:
                status, reply, ts = 'queued', '', None
            if status in ('running', 'queued'):
                out.append(f'{head}还没做完（{status}），等待进程已经退出。用后台命令接着等：'
                           f'python -X utf8 {__file__} {r["tid"]} - --no-send --nonce {nonce}')
                save_rec(nonce, delivered=True)
                continue
            r = save_rec(nonce, status=status, reply=reply, finished_at=ts)
        when = time.strftime('%H:%M', time.localtime(r.get('finished_at', time.time())))
        out.append(f'{head}已于 {when} 结束（{r.get("status")}），这条回复你还没看到：\n{r.get("reply", "")}')
        save_rec(nonce, delivered=True)
    out += user_driven_turns(threads)
    if out:
        print('[codex_ask] 有 Codex 结果没有送达，先处理它们：\n\n' + '\n\n---\n\n'.join(out))
    return 0


def watch(thread, timeout):
    """Block until any turn of the thread finishes (whoever started it), print its reply and exit 0.
    Run it in the background: its exit is what wakes Claude. Re-run it after handling each reply."""
    tid, path = resolve(thread)
    marks_path = os.path.join(RECS, '_threads.json')
    try:
        with open(marks_path, encoding='utf-8') as f:
            marks = json.load(f)
    except (OSError, ValueError):
        marks = {}
    tail = Tail(path)
    if 0 <= marks.get(tid, -1) <= tail.pos:
        tail.pos = marks[tid]  # catch a turn that ended between two watches
    started = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        for o in tail.events():
            p = o.get('payload', {})
            if p.get('type') == 'item_completed' and p.get('item', {}).get('type') == 'UserMessage':
                started[p.get('turn_id')] = user_text(p['item'])[:80].replace('\n', ' ')
            elif p.get('type') in ('task_complete', 'turn_aborted'):
                marks[tid] = tail.pos - len(tail.buf)
                os.makedirs(RECS, exist_ok=True)
                with open(marks_path + '.tmp', 'w', encoding='utf-8') as f:
                    json.dump(marks, f)
                os.replace(marks_path + '.tmp', marks_path)
                asked = started.get(p.get('turn_id'), '')
                log(f'Codex 一轮结束（{p.get("type")}），这一轮的提问：{asked or "（开始于监听之前）"}')
                print(p.get('last_agent_message') or p.get('reason') or '')
                return 0 if p.get('type') == 'task_complete' else 2
        time.sleep(2)
    log('监听超时，没有新的回合结束')
    return 4


def main():
    a = sys.argv[1:]
    if a and a[0] == '--pending':
        return pending()
    if a and a[0] == '--watch':
        return watch(a[1], int(a[a.index('--timeout') + 1]) if '--timeout' in a else 86400)
    if a and a[0] == '--show':
        print(load_rec(a[1]).get('reply', ''))
        return 0
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    thread, src = a[0], a[1]
    timeout = int(opt('--timeout', 1800))
    msg = sys.stdin.read() if src == '-' else open(src, encoding='utf-8').read()
    tid, path = resolve(thread)
    if not os.path.exists(path):
        log(f'对话记录不在 {path}（可能还是压缩状态），请先在 Codex 桌面版打开这个对话')
        return 3
    resume = '--no-send' in a
    tail = Tail(path, start_at_end=not resume)
    nonce = opt('--nonce') or secrets.token_hex(3)
    tag = f'〔Claude#{nonce}〕'
    item_id = None
    first = msg.rstrip() + '\n\n' + tag
    if not resume:
        item_id = send(tid, first)
        if not item_id:
            return 3
        log(f'已排队 {item_id}，标记 {tag}，对话 {tid}')
        if not app_running():
            log('Codex 桌面版没在运行：消息留在队列里，打开 Codex 后才会送出')
    cpid, ccreated = claude_owner()
    try:
        import psutil
        wcreated = psutil.Process().create_time()
    except Exception:
        wcreated = None
    save_rec(nonce, thread=thread, tid=tid, rollout=path, tag=tag, status='waiting', delivered=False,
             started_at=load_rec(nonce).get('started_at') or time.time(),
             waiter_pid=os.getpid(), waiter_created=wcreated, claude_pid=cpid, claude_created=ccreated)
    retries, grace = int(opt('--retries', 3)), int(opt('--grace', 900))
    turn, deadline, last_new, was_queued, last_app_check = None, time.time() + timeout, time.time(), None, time.time()
    picked_at, requeued, aborted_at, user_turn = None, False, None, None

    def resend(text, why):
        nonlocal item_id, turn, was_queued, picked_at, requeued
        item_id = send(tid, text)
        turn, was_queued, picked_at, requeued = None, None, None, False
        log(f'{why}：已重新排队 {item_id}')
        return item_id is not None

    while time.time() < deadline:
        new = False
        for o in tail.events():
            new = True
            p = o.get('payload', {})
            t = p.get('type')
            if t == 'item_completed' and p.get('item', {}).get('type') == 'UserMessage':
                if turn is None and tag in user_text(p['item']):
                    turn, aborted_at, user_turn = p.get('turn_id'), None, None
                    if not resume or time.time() - last_new < 5:
                        log(f'Codex 已开始这一轮 {turn}')
                elif aborted_at and user_turn is None and '〔Claude#' not in user_text(p['item']):
                    user_turn = p.get('turn_id')
                    log(f'用户在 Codex 里接着做了，跟进这一轮 {user_turn}')
            elif user_turn and p.get('turn_id') == user_turn and t in ('task_complete', 'turn_aborted'):
                reply = p.get('last_agent_message') or p.get('reason') or ''
                finish(nonce, 'done' if t == 'task_complete' and not p.get('error') else 'aborted', reply)
                print('（这是用户在 Codex 里接着做的那一轮的回复）\n' + reply)
                log('用户接着做的那一轮结束了')
                return 0 if t == 'task_complete' and not p.get('error') else 2
            elif turn and p.get('turn_id') == turn and t == 'task_complete':
                err = p.get('error')
                if not err:
                    reply = p.get('last_agent_message') or ''
                    finish(nonce, 'done', reply)
                    print(reply)
                    log(f'完成，用时 {p.get("duration_ms", 0) / 1000:.0f} 秒')
                    return 0
                kind, wait = error_kind(err)
                text = err.get('message', str(err)) if isinstance(err, dict) else str(err)
                if kind != 'fatal' and retries > 0:
                    retries -= 1
                    log(f'这一轮出错（{kind}），{wait / 60:.0f} 分钟后自动续做，还剩 {retries} 次：{text[:120]}')
                    save_rec(nonce, status='waiting', note=f'{kind} retry at {time.ctime(time.time() + wait)}')
                    deadline = max(deadline, time.time() + wait + timeout)
                    turn = None
                    time.sleep(wait)  # events written meanwhile are read on the next pass
                    cont =(f'（来自 Claude 的自动续做）上一轮因{"额度用完" if kind == "limit" else "网络错误"}中断。'
                            f'请从断点接着完成本对话里带 {tag} 的那条任务，最后一条消息用中文写，结论放前面。\n\n{tag}')
                    if not resend(cont, '自动续做'):
                        finish(nonce, 'error', text)
                        return 3
                    break
                finish(nonce, 'error', text)
                print(text)
                log(f'这一轮出错，没有自动重试：{text[:200]}')
                return 5
            elif turn and p.get('turn_id') == turn and t == 'turn_aborted':
                log(f'这一轮被中止: {p.get("reason")}，再看 {grace // 60} 分钟用户是否在 Codex 里接着做')
                aborted_at, turn = time.time(), None
        if new:
            last_new = time.time()
        if aborted_at and user_turn is None and time.time() - aborted_at > grace:
            finish(nonce, 'aborted', 'interrupted')
            log('被中止后没有人接着做')
            return 2
        if item_id and turn is None and not aborted_at:
            q = queued(item_id)
            if q != was_queued:
                log('在队列里等 Codex 取走' if q else '已被 Codex 取走')
                was_queued = q
                picked_at = None if q else time.time()
            if q and time.time() - last_app_check > 60:
                last_app_check = time.time()
                if not app_running():
                    log('Codex 桌面版没在运行，消息还在队列里')
            if picked_at and not requeued and time.time() - picked_at > 480:
                log('被取走 8 分钟还没开始，可能卡住了，同一条消息再发一次')
                if not resend(first, '取走后没开始'):
                    return 3
                requeued = True
        if time.time() - last_new > 600:
            log('10 分钟没有新记录，可能卡住了，继续等')
            last_new = time.time()
        time.sleep(2)
    log(f'超时。之后继续等这一条：codex_ask.py {tid} - --no-send --nonce {nonce}')
    # Leave the record 'waiting' with a dead waiter: --pending will check the rollout or say how to resume.
    return 4


if __name__ == '__main__':
    sys.exit(main())
