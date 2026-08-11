#!/usr/bin/env python3
"""cdp.py - attach to a running Chrome over the DevTools protocol, mirror the page's
console + browser log + exceptions + failed requests into a file, and take screenshots
at intervals.

Pure stdlib: this WSL has no node and no `websockets` package, so the WebSocket client
is implemented here (RFC 6455 client framing is ~60 lines and needs no dependency).

  cdp.py --out /root/kscripts/out/run1 --secs 240 --shot-every 30
"""
import argparse, base64, json, os, socket, struct, sys, time, urllib.request, hashlib, re


def http_json(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode())


class WS:
    def __init__(self, url, timeout=1.0):
        m = re.match(r'ws://([^:/]+):(\d+)(/.*)', url)
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.s = socket.create_connection((host, port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            buf += self.s.recv(4096)
        if b'101' not in buf.split(b'\r\n')[0]:
            raise RuntimeError('ws handshake failed: %r' % buf[:200])
        self.rest = buf.split(b'\r\n\r\n', 1)[1]
        self.s.settimeout(timeout)

    def _recv_exact(self, n):
        while len(self.rest) < n:
            try:
                d = self.s.recv(65536)
            except socket.timeout:
                return None
            if not d:
                raise ConnectionError('ws closed')
            self.rest += d
        out, self.rest = self.rest[:n], self.rest[n:]
        return out

    def send(self, obj):
        payload = json.dumps(obj).encode()
        n = len(payload)
        hdr = b'\x81'
        mask = os.urandom(4)
        if n < 126:
            hdr += struct.pack('!B', 0x80 | n)
        elif n < 65536:
            hdr += struct.pack('!BH', 0x80 | 126, n)
        else:
            hdr += struct.pack('!BQ', 0x80 | 127, n)
        hdr += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.s.sendall(hdr + masked)

    def recv(self):
        """Return one decoded JSON message, or None on timeout."""
        frames = b''
        while True:
            h = self._recv_exact(2)
            if h is None:
                return None
            fin = h[0] & 0x80
            op = h[0] & 0x0f
            ln = h[1] & 0x7f
            if ln == 126:
                ln = struct.unpack('!H', self._recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack('!Q', self._recv_exact(8))[0]
            data = self._recv_exact(ln) if ln else b''
            if data is None:
                return None
            if op == 0x8:
                raise ConnectionError('ws close frame')
            if op == 0x9:      # ping -> pong
                continue
            frames += data
            if fin:
                break
        try:
            return json.loads(frames.decode('utf-8', 'replace'))
        except Exception:
            return None


def arg_str(a):
    if a is None:
        return ''
    if 'value' in a:
        v = a['value']
        return v if isinstance(v, str) else json.dumps(v)
    if 'description' in a:
        return a['description']
    if 'preview' in a:
        return json.dumps(a['preview'])
    return a.get('type', '?')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--secs', type=float, default=180)
    ap.add_argument('--shot-every', type=float, default=30)
    ap.add_argument('--port', type=int, default=9222)
    ap.add_argument('--move-mouse', action='store_true',
                    help='dispatch a mouseMoved over the canvas once the module is up '
                         '(GetAspect NaN insurance / focus)')
    ap.add_argument('--eval-at', default='', help='SECS:JSEXPR, repeatable with ;;')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    logf = open(os.path.join(args.out, 'console.log'), 'w', buffering=1, encoding='utf-8')

    def w(line):
        logf.write(line + '\n')

    tgts = http_json(f'http://127.0.0.1:{args.port}/json')
    page = next((t for t in tgts if t['type'] == 'page' and 'index.html' in t['url']), None)
    if page is None:
        page = next((t for t in tgts if t['type'] == 'page'), None)
    if page is None:
        w('NO PAGE TARGET: ' + json.dumps([t['url'] for t in tgts]))
        return 2
    w('== target: ' + page['url'])
    ws = WS(page['webSocketDebuggerUrl'])
    mid = [0]

    def send(method, params=None):
        mid[0] += 1
        ws.send({'id': mid[0], 'method': method, 'params': params or {}})
        return mid[0]

    send('Runtime.enable')
    send('Log.enable')
    send('Page.enable')
    send('Network.enable')

    t0 = time.time()
    next_shot = args.shot_every
    shot_n = 0
    pending_shot = {}
    moved = False
    evals = []
    for item in filter(None, args.eval_at.split(';;')):
        s, expr = item.split(':', 1)
        evals.append([float(s), expr])
    netfail = 0
    counters = {'console': 0, 'log': 0, 'exception': 0}

    while time.time() - t0 < args.secs:
        el = time.time() - t0
        if el >= next_shot:
            shot_n += 1
            i = send('Page.captureScreenshot', {'format': 'png'})
            pending_shot[i] = os.path.join(args.out, f'shot-{shot_n:02d}-t{int(el)}s.png')
            next_shot += args.shot_every
        if args.move_mouse and not moved and el > 8:
            moved = True
            for (x, y) in ((640, 450), (700, 470), (660, 430)):
                send('Input.dispatchMouseEvent',
                     {'type': 'mouseMoved', 'x': x, 'y': y, 'button': 'none',
                      'clickCount': 0})
            w(f'[{el:7.1f}s] == dispatched mouseMoved over the canvas')
        for e in evals:
            if e[0] is not None and el >= e[0]:
                i = send('Runtime.evaluate',
                         {'expression': e[1], 'returnByValue': True, 'awaitPromise': True})
                pending_shot[i] = ('EVAL', e[1])
                w(f'[{el:7.1f}s] == eval: {e[1][:120]}')
                e[0] = None
        m = ws.recv()
        if m is None:
            continue
        el = time.time() - t0
        meth = m.get('method')
        if meth == 'Runtime.consoleAPICalled':
            counters['console'] += 1
            txt = ' '.join(arg_str(a) for a in m['params'].get('args', []))
            w(f'[{el:7.1f}s][{m["params"].get("type","log")}] {txt}')
        elif meth == 'Log.entryAdded':
            counters['log'] += 1
            e = m['params']['entry']
            w(f'[{el:7.1f}s][browser:{e.get("level")}] {e.get("text")} {e.get("url","")}')
        elif meth == 'Runtime.exceptionThrown':
            counters['exception'] += 1
            d = m['params']['exceptionDetails']
            w(f'[{el:7.1f}s][EXCEPTION] {d.get("text")} '
              f'{(d.get("exception") or {}).get("description","")}')
        elif meth == 'Network.loadingFailed':
            netfail += 1
            if netfail < 40:
                w(f'[{el:7.1f}s][netfail] {m["params"].get("errorText")} '
                  f'{m["params"].get("type")}')
        elif meth == 'Page.frameStoppedLoading':
            w(f'[{el:7.1f}s] == frame stopped loading')
        elif 'id' in m and m['id'] in pending_shot:
            what = pending_shot.pop(m['id'])
            if isinstance(what, tuple):
                w(f'[{el:7.1f}s][evalresult] {json.dumps(m.get("result"))[:4000]}')
            else:
                r = m.get('result') or {}
                if 'data' in r:
                    raw = base64.b64decode(r['data'])
                    open(what, 'wb').write(raw)
                    w(f'[{el:7.1f}s] == screenshot {os.path.basename(what)} '
                      f'({len(raw)} bytes, md5 {hashlib.md5(raw).hexdigest()[:12]})')
                else:
                    w(f'[{el:7.1f}s] == screenshot FAILED: {json.dumps(m)[:300]}')

    # final shot + a state dump
    i = send('Page.captureScreenshot', {'format': 'png'})
    pending_shot[i] = os.path.join(args.out, 'shot-final.png')
    i2 = send('Runtime.evaluate', {'returnByValue': True, 'expression': """
      (function(){
        var c = document.getElementById('canvas');
        var gl = null, info = {};
        try { gl = c.getContext('webgl2') || c.getContext('webgl'); } catch(e){}
        info.status = (document.getElementById('status')||{}).textContent;
        info.canvas = c ? (c.width + 'x' + c.height + ' css ' + c.clientWidth + 'x' + c.clientHeight) : 'none';
        info.gateHidden = (document.getElementById('gate')||{}).className;
        info.webgl = gl ? gl.getParameter(gl.VERSION) : 'no context via getContext (already taken)';
        try {
          var d = gl && gl.getExtension('WEBGL_debug_renderer_info');
          info.renderer = gl && d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : '';
        } catch(e){}
        info.heap = performance.memory ? (performance.memory.usedJSHeapSize/1048576).toFixed(1)+' MB' : '';
        try { info.wasmMem = (Module.HEAPU8.length/1048576).toFixed(1)+' MB'; } catch(e){ info.wasmMem=''; }
        try { info.webfs = {chunks: METIN2_WEBFS_STATE ? 1 : 0}; } catch(e){}
        try { info.fs = window.M2WEBFS ? Object.keys(window.M2WEBFS) : null; } catch(e){}
        info.reqs = performance.getEntriesByType('resource').length;
        var tb = 0; performance.getEntriesByType('resource').forEach(function(e){ tb += e.transferSize||0; });
        info.transferMB = (tb/1048576).toFixed(1);
        return info;
      })()
    """})
    pending_shot[i2] = ('EVAL', 'final state')
    end = time.time() + 12
    while time.time() < end and pending_shot:
        m = ws.recv()
        if m is None:
            continue
        if 'id' in m and m['id'] in pending_shot:
            what = pending_shot.pop(m['id'])
            if isinstance(what, tuple):
                w('[final] ' + json.dumps(m.get('result'))[:4000])
            else:
                r = m.get('result') or {}
                if 'data' in r:
                    raw = base64.b64decode(r['data'])
                    open(what, 'wb').write(raw)
                    w(f'[final] screenshot {os.path.basename(what)} ({len(raw)} bytes)')
    w('== counters: ' + json.dumps(counters) + ' netfail=' + str(netfail))
    logf.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
