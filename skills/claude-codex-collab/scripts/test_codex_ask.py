"""Offline scenarios for codex_ask.py: fake rollout + stubbed queue, events appended from a thread."""
import importlib.util, io, json, os, sys, tempfile, threading, time, contextlib

spec = importlib.util.spec_from_file_location('ca', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'codex_ask.py'))
ca = importlib.util.module_from_spec(spec); spec.loader.exec_module(ca)
ca.time.sleep = lambda s, _real=time.sleep: _real(min(s, 0.05))  # fast clock for waits
ca.app_running = lambda: True
ca.RECS = tempfile.mkdtemp()
ca.claude_owner = lambda: (None, None)

def ev(path, typ, **kw):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'timestamp': '2026-10-25T00:00:00Z', 'payload': dict(type=typ, **kw)}) + '\n')

def user(path, turn, text):
    ev(path, 'item_completed', turn_id=turn, item={'type': 'UserMessage', 'content': [{'type': 'text', 'text': text}]})

def run(script, args, sends):
    path = tempfile.mktemp(suffix='.jsonl'); open(path, 'w').close()
    ca.resolve = lambda t: ('tid', path)
    queue = set()
    def fake_send(tid, text):
        iid = f'item{len(sends)}'; sends.append(text); queue.add(iid)
        threading.Thread(target=script, args=(path, text, queue, iid, len(sends)), daemon=True).start()
        return iid
    ca.send = fake_send
    ca.queued = lambda iid: iid in queue
    msgf = tempfile.mktemp(); open(msgf, 'w', encoding='utf-8').write('do it')
    sys.argv = ['x', 'tid', msgf, '--nonce', 'abc123'] + (args if '--timeout' in args else args + ['--timeout', '30'])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = ca.main()
    return code, out.getvalue().strip()

def tagged(text): return '〔Claude#abc123〕' in text

# 1 normal
def s1(path, text, q, iid, n):
    time.sleep(0.2); q.discard(iid); user(path, 't1', text); ev(path, 'task_complete', turn_id='t1', last_agent_message='ok done')
sends = []; r = run(s1, [], sends); assert r == (0, 'ok done'), r; print('1 normal OK')

# 2 usage limit then success on continue
def s2(path, text, q, iid, n):
    time.sleep(0.2); q.discard(iid); user(path, f't{n}', text)
    if n == 1: ev(path, 'task_complete', turn_id='t1', last_agent_message=None,
                  error={'message': "You've hit your usage limit. try again at 11:59 PM.", 'codex_error_info': 'usage_limit_exceeded'})
    else: ev(path, 'task_complete', turn_id=f't{n}', last_agent_message='resumed ok')
sends = []; r = run(s2, [], sends); assert r == (0, 'resumed ok') and len(sends) == 2 and '自动续做' in sends[1] and tagged(sends[1]), (r, sends); print('2 limit->continue OK')

# 3 fatal error (model unsupported)
def s3(path, text, q, iid, n):
    time.sleep(0.2); q.discard(iid); user(path, 't1', text)
    ev(path, 'task_complete', turn_id='t1', error={'message': "The 'deepseek' model is not supported", 'codex_error_info': {}})
sends = []; r = run(s3, [], sends); assert r[0] == 5 and 'not supported' in r[1] and len(sends) == 1, r; print('3 fatal OK')

# 4 aborted, then user continues in app
def s4(path, text, q, iid, n):
    time.sleep(0.2); q.discard(iid); user(path, 't1', text); ev(path, 'turn_aborted', turn_id='t1', reason='interrupted')
    time.sleep(0.3); user(path, 'u1', '继续做'); ev(path, 'task_complete', turn_id='u1', last_agent_message='user turn result')
sends = []; r = run(s4, [], sends); assert r[0] == 0 and 'user turn result' in r[1], r; print('4 abort->user continues OK')

# 5 aborted, nobody continues (grace 1s)
def s5(path, text, q, iid, n):
    time.sleep(0.2); q.discard(iid); user(path, 't1', text); ev(path, 'turn_aborted', turn_id='t1', reason='interrupted')
sends = []; r = run(s5, ['--grace', '1'], sends); assert r[0] == 2, r; print('5 abort->nobody OK')

# 6 network error retried
def s6(path, text, q, iid, n):
    time.sleep(0.2); q.discard(iid); user(path, f't{n}', text)
    if n == 1: ev(path, 'task_complete', turn_id='t1', error={'message': 'Error running remote compact task: Connection failed: error sending request', 'codex_error_info': {'http_connection_failed': {}}})
    else: ev(path, 'task_complete', turn_id=f't{n}', last_agent_message='after network')
sends = []; r = run(s6, [], sends); assert r == (0, 'after network'), r; print('6 network retry OK')

# 7 picked up but never started -> requeue once (time-skip via monkeypatched threshold)
real_time = ca.time.time; offset = [0]
ca.time.time = lambda: real_time() + offset[0]
def s7(path, text, q, iid, n):
    time.sleep(0.2); q.discard(iid)
    if n == 1: time.sleep(0.2); offset[0] += 500  # pretend 8+ minutes passed, never starts
    else: user(path, 't2', text); ev(path, 'task_complete', turn_id='t2', last_agent_message='second try')
sends = []; r = run(s7, ['--timeout', '3000'], sends); assert r == (0, 'second try') and len(sends) == 2 and sends[0] == sends[1], (r, len(sends)); print('7 stuck pickup -> requeue OK')
ca.time.time = real_time

k, w = ca.error_kind({'message': 'usage limit ... try again at 7:37 PM.', 'codex_error_info': 'usage_limit_exceeded'})
assert k == 'limit' and w >= 60, (k, w); print('parse OK', k, round(w))
print('ALL PASS')
