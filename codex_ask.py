"""Queue a message into a Codex desktop thread and wait for that exact turn to finish.

usage: python -X utf8 codex_ask.py <thread name|uuid> <message file|-> [--timeout 1800]
       python -X utf8 codex_ask.py <thread name|uuid> - --no-send --nonce X   (resume waiting for an earlier send)
Status lines go to stderr, the final reply to stdout.
Exit 0 complete, 2 aborted, 3 cannot send, 4 timeout, 1 other.

Tested 2026-10-22 against Codex desktop: `codex queue --thread <name>` fails with "No active session"
even when the app is open, so names are resolved to the uuid here. The queued item id is not the turn id;
the turn is found by a unique tag appended to the message. Queue works while the app is closed; the
message is delivered when the app reopens.
"""
import json, os, re, secrets, sqlite3, subprocess, sys, time

HOME = os.path.expanduser('~')
STATE = os.path.join(HOME, '.codex', 'state_5.sqlite')
QUEUE = os.path.join(HOME, '.codex', 'queue_1.sqlite')
CODEX = ['node', os.path.join(os.environ.get('APPDATA', ''), 'npm', 'node_modules', '@openai', 'codex', 'bin', 'codex.js')]
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


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


def main():
    a = sys.argv[1:]
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
    if not resume:
        r = subprocess.run(CODEX + ['queue', '--thread', tid, '--message', msg.rstrip() + '\n\n' + tag],
                           capture_output=True, text=True, encoding='utf-8')
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            log('发送失败:', out)
            return 3 if 'No active session' in out else 1
        m = re.search(r'Queued message (\S+)', out)
        item_id = m and m.group(1)
        log(f'已排队 {item_id}，标记 {tag}，对话 {tid}')
        if not app_running():
            log('Codex 桌面版没在运行：消息留在队列里，打开 Codex 后才会送出')
    turn, deadline, last_new, was_queued, last_app_check = None, time.time() + timeout, time.time(), None, time.time()
    while time.time() < deadline:
        new = False
        for o in tail.events():
            new = True
            p = o.get('payload', {})
            t = p.get('type')
            if turn is None and t == 'item_completed' and p.get('item', {}).get('type') == 'UserMessage' \
                    and tag in user_text(p['item']):
                turn = p.get('turn_id')
                if not resume or time.time() - last_new < 5:
                    log(f'Codex 已开始这一轮 {turn}')
            elif turn and p.get('turn_id') == turn and t == 'task_complete':
                print(p.get('last_agent_message') or '')
                log(f'完成，用时 {p.get("duration_ms", 0) / 1000:.0f} 秒')
                return 0
            elif turn and p.get('turn_id') == turn and t == 'turn_aborted':
                log(f'这一轮被中止: {p.get("reason")}')
                return 2
        if new:
            last_new = time.time()
        if item_id and turn is None:
            q = queued(item_id)
            if q != was_queued:
                log('在队列里等 Codex 取走' if q else '已被 Codex 取走')
                was_queued = q
            if q and time.time() - last_app_check > 60:
                last_app_check = time.time()
                if not app_running():
                    log('Codex 桌面版没在运行，消息还在队列里')
        if time.time() - last_new > 600:
            log('10 分钟没有新记录，可能卡住了，继续等')
            last_new = time.time()
        time.sleep(2)
    log(f'超时。之后继续等这一条：codex_ask.py {tid} - --no-send --nonce {nonce}')
    return 4


if __name__ == '__main__':
    sys.exit(main())
