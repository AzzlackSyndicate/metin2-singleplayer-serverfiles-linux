#!/usr/bin/env python3
"""cdp2.py - cdp.py plus a timed action script, so the browser client can be driven
(clicks, typing) the way a player would drive it.

  cdp2.py --out DIR --secs 120 --do "20:shot;22:click:455,340;25:click:794,647;30:shot"

Actions (separated by ';', each "SECONDS:VERB[:ARG]"):
  shot                 capture a screenshot
  click:X,Y            move + press + release, left button, at CSS px X,Y
  dclick:X,Y           the same with clickCount=2
  move:X,Y             mouseMoved only
  type:TEXT            char-by-char keyDown/char/keyUp (what an <input>-less canvas needs)
  key:NAME             one named key: Enter, Tab, Escape, Backspace
  eval:EXPR            Runtime.evaluate, result logged
"""
import argparse, base64, hashlib, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import WS, http_json, arg_str          # noqa: E402

KEYS = {
    'Enter':     dict(key='Enter', code='Enter', windowsVirtualKeyCode=13, text='\r'),
    'Tab':       dict(key='Tab', code='Tab', windowsVirtualKeyCode=9),
    'Escape':    dict(key='Escape', code='Escape', windowsVirtualKeyCode=27),
    'Backspace': dict(key='Backspace', code='Backspace', windowsVirtualKeyCode=8),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--secs', type=float, default=120)
    ap.add_argument('--do', default='')
    ap.add_argument('--port', type=int, default=9222)
    ap.add_argument('--tag', default='drive')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    logf = open(os.path.join(args.out, args.tag + '.log'), 'w', buffering=1,
                encoding='utf-8')

    def w(s):
        logf.write(s + '\n')

    tgts = http_json(f'http://127.0.0.1:{args.port}/json')
    page = next((t for t in tgts if t['type'] == 'page' and 'index.html' in t['url']), None)
    if not page:
        w('NO PAGE: ' + json.dumps([t['url'] for t in tgts]))
        return 2
    w('== target ' + page['url'])
    ws = WS(page['webSocketDebuggerUrl'])
    mid = [0]
    pending = {}

    def send(method, params=None):
        mid[0] += 1
        ws.send({'id': mid[0], 'method': method, 'params': params or {}})
        return mid[0]

    send('Runtime.enable'); send('Log.enable'); send('Page.enable'); send('Network.enable')

    acts = []
    for item in args.do.split(';'):
        item = item.strip()
        if not item:
            continue
        t, rest = item.split(':', 1)
        verb, _, arg = rest.partition(':')
        acts.append([float(t), verb, arg])
    acts.sort(key=lambda a: a[0])
    shot_n = [0]
    t0 = time.time()

    def click(x, y, n=1):
        x, y = int(x), int(y)
        send('Input.dispatchMouseEvent', dict(type='mouseMoved', x=x, y=y, button='none'))
        for i in range(1, n + 1):
            send('Input.dispatchMouseEvent', dict(type='mousePressed', x=x, y=y,
                                                  button='left', buttons=1, clickCount=i))
            send('Input.dispatchMouseEvent', dict(type='mouseReleased', x=x, y=y,
                                                  button='left', buttons=0, clickCount=i))

    def do(verb, arg, el):
        if verb == 'shot':
            shot_n[0] += 1
            i = send('Page.captureScreenshot', {'format': 'png'})
            pending[i] = os.path.join(args.out,
                                      f'{args.tag}-{shot_n[0]:02d}-t{int(el)}s.png')
        elif verb in ('click', 'dclick'):
            x, y = arg.split(',')
            click(x, y, 2 if verb == 'dclick' else 1)
            w(f'[{el:7.1f}s] == {verb} {x},{y}')
        elif verb in ('press', 'release'):
            # A press and a release seconds apart, so the client sees them in DIFFERENT
            # frames -- the shape a human click has at 1 fps, and the one thing a
            # back-to-back synthetic click cannot reproduce.
            x, y = arg.split(',')
            send('Input.dispatchMouseEvent',
                 dict(type='mousePressed' if verb == 'press' else 'mouseReleased',
                      x=int(x), y=int(y), button='left',
                      buttons=1 if verb == 'press' else 0, clickCount=1))
            w(f'[{el:7.1f}s] == {verb} {x},{y}')
        elif verb == 'move':
            x, y = arg.split(',')
            send('Input.dispatchMouseEvent',
                 dict(type='mouseMoved', x=int(x), y=int(y), button='none'))
            w(f'[{el:7.1f}s] == move {x},{y}')
        elif verb == 'type':
            for ch in arg:
                send('Input.dispatchKeyEvent',
                     dict(type='keyDown', text=ch, unmodifiedText=ch, key=ch,
                          code=('Key' + ch.upper()) if ch.isalpha() else
                               ('Digit' + ch if ch.isdigit() else ''),
                          windowsVirtualKeyCode=ord(ch.upper())))
                send('Input.dispatchKeyEvent', dict(type='char', text=ch,
                                                    unmodifiedText=ch, key=ch))
                send('Input.dispatchKeyEvent',
                     dict(type='keyUp', key=ch,
                          code=('Key' + ch.upper()) if ch.isalpha() else
                               ('Digit' + ch if ch.isdigit() else ''),
                          windowsVirtualKeyCode=ord(ch.upper())))
                time.sleep(0.05)
            w(f'[{el:7.1f}s] == typed {len(arg)} chars')
        elif verb == 'key':
            k = KEYS[arg]
            send('Input.dispatchKeyEvent', dict(type='keyDown', **k))
            if 'text' in k:
                send('Input.dispatchKeyEvent', dict(type='char', **k))
            send('Input.dispatchKeyEvent', dict(type='keyUp', **{
                kk: vv for kk, vv in k.items() if kk != 'text'}))
            w(f'[{el:7.1f}s] == key {arg}')
        elif verb in ('eval', 'evalfile'):
            # evalfile takes a PATH: JS with ';' and ':' in it cannot survive the --do
            # action syntax, and the interesting probes are all several statements long.
            if verb == 'evalfile':
                arg = open(arg, encoding='utf-8').read()
            i = send('Runtime.evaluate', {'expression': arg, 'returnByValue': True,
                                          'awaitPromise': True})
            pending[i] = ('EVAL', arg)
            w(f'[{el:7.1f}s] == eval {arg[:100]}')

    while time.time() - t0 < args.secs:
        el = time.time() - t0
        while acts and acts[0][0] <= el:
            a = acts.pop(0)
            do(a[1], a[2], el)
        m = ws.recv()
        if m is None:
            continue
        el = time.time() - t0
        meth = m.get('method')
        if meth == 'Runtime.consoleAPICalled':
            txt = ' '.join(arg_str(x) for x in m['params'].get('args', []))
            if 'getInternalformatParameter' in txt:
                continue
            w(f'[{el:7.1f}s][{m["params"].get("type","log")}] {txt}')
        elif meth == 'Log.entryAdded':
            e = m['params']['entry']
            t = e.get('text', '')
            if 'getInternalformatParameter' in t or 'AudioContext was not allowed' in t:
                continue
            w(f'[{el:7.1f}s][browser:{e.get("level")}] {t}')
        elif meth == 'Runtime.exceptionThrown':
            d = m['params']['exceptionDetails']
            w(f'[{el:7.1f}s][EXCEPTION] {d.get("text")} '
              f'{(d.get("exception") or {}).get("description","")}')
        elif meth == 'Network.loadingFailed':
            w(f'[{el:7.1f}s][netfail] {m["params"].get("errorText")}')
        elif 'id' in m and m['id'] in pending:
            what = pending.pop(m['id'])
            if isinstance(what, tuple):
                w(f'[{el:7.1f}s][evalresult] {json.dumps(m.get("result"))[:3000]}')
            else:
                r = m.get('result') or {}
                if 'data' in r:
                    raw = base64.b64decode(r['data'])
                    open(what, 'wb').write(raw)
                    w(f'[{el:7.1f}s] == shot {os.path.basename(what)} '
                      f'({len(raw)} B, md5 {hashlib.md5(raw).hexdigest()[:10]})')
                else:
                    w(f'[{el:7.1f}s] == shot FAILED {json.dumps(m)[:200]}')
    # drain
    end = time.time() + 10
    while time.time() < end and pending:
        m = ws.recv()
        if not m:
            continue
        if 'id' in m and m['id'] in pending:
            what = pending.pop(m['id'])
            r = m.get('result') or {}
            if isinstance(what, tuple):
                w('[drain][evalresult] ' + json.dumps(r)[:3000])
            elif 'data' in r:
                raw = base64.b64decode(r['data'])
                open(what, 'wb').write(raw)
                w('[drain] shot ' + os.path.basename(what))
    logf.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
