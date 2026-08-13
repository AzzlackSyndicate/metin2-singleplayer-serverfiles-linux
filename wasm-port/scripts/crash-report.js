// crash-report.js -- offer to send a crash somewhere it can be read.
//
// A browser client fails in a place nobody can look. When the wasm module traps
// the player sees a frozen screen, and the one line that explains it is in a
// console they will never open. That is not hypothetical: the bug that made
// every character with a friend unplayable was found in seconds once the client
// printed
//
//     PYEXC: OnLogout raised UnboundLocalError:
//            cannot access local variable 'list' where it is not associated with a value
//
// and was hunted for hours before that, from a stack trace that pointed at the
// wrong layer entirely. So the page asks. Once, when it breaks, with the trace
// AND the log visible and a box to say what was happening.
//
// Nothing is sent unless the button is pressed.
//
// ── WHAT IS SENT ──────────────────────────────────────────────────────────────
//
// The claim has to be true, not reassuring, so it is narrow and checkable:
//
//   the error and its stack trace       function names and byte offsets
//   every PYEXC line of the session     the client's own report of a script
//                                       error -- the most useful thing here
//   the tail of the console log         what the client printed just before
//   the character name                  published on purpose by the client, so
//                                       a report can be matched against the
//                                       server's logs for the same minute
//   what the player typed in the box    their words, their choice
//   client version, browser, server     which build, which browser, which host
//
// The ACCOUNT name and the password are not in this page's reach at all: they
// live inside the wasm heap and never cross into JavaScript. The character name
// does cross, because it was handed over deliberately for this -- and it is
// listed in the dialog for the same reason. The technical log CAN also contain
// in-game names -- a friend's character in a messenger event, for instance --
// and the dialog says so rather than claiming otherwise. "Show
// exactly what will be sent" prints the whole payload, so the player can read
// every byte before deciding. A promise that can be checked is worth more than
// one that has to be believed.
//
// ── WHY IT HOOKS FOUR THINGS AND NOT ONE ──────────────────────────────────────
//
// A wasm trap arrives as a plain `error' event; a failed fetch inside the
// streaming filesystem arrives as an unhandled promise rejection; an emscripten
// abort() calls Module.onAbort and may never reach either. Missing one of them
// is how a crash reporter comes to be trusted and silent.
//
// The console is wrapped rather than Module.print, for the same reason: the
// generated module installs its own print handlers after this file runs, and a
// wrapper placed on Module would be replaced. console.* is what everything ends
// up calling, whoever owns the pipe.
//
// Optional and unobtrusive: `?crashReport=off' disables it entirely, and
// `?crashReport=<url>' sends somewhere else. Failing to load this file at all
// costs the page nothing.

(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  var setting = params.get('crashReport') || '';
  if (setting === 'off' || setting === '0') return;

  // Default: the panel that served this page. /play/ is a directory, so '../'
  // lands on the panel root whether or not it sits at the domain root.
  var ENDPOINT = /^https?:\/\//.test(setting) ? setting
               : (setting || new URL('../crash-report', location.href).href);

  var LOG_LINES = 400;        // ring buffer depth
  var LOG_CHARS = 12000;      // and a hard ceiling on what is sent
  var PYEXC_MAX = 25;         // script errors kept in full, oldest dropped

  var log = [];
  var pyexc = [];
  var reported = false;
  var shown = false;

  // ── capture ─────────────────────────────────────────────────────────────────
  function remember(text) {
    if (!text) return;
    log.push(text);
    if (log.length > LOG_LINES) log.shift();
    // PYEXC is the client saying "a script raised, here is what". It is the
    // single most useful line in the file, so it is kept separately and never
    // pushed out of the ring buffer by ordinary chatter.
    if (text.indexOf('PYEXC:') !== -1) {
      pyexc.push(text);
      if (pyexc.length > PYEXC_MAX) pyexc.shift();
    }
  }

  function flatten(args) {
    var out = [];
    for (var i = 0; i < args.length; i++) {
      var a = args[i];
      try {
        out.push(typeof a === 'string' ? a
               : (a instanceof Error ? (a.message + '\n' + a.stack) : JSON.stringify(a)));
      } catch (e) { out.push(String(a)); }
    }
    return out.join(' ');
  }

  ['log', 'info', 'warn', 'error', 'debug'].forEach(function (name) {
    var original = console[name];
    if (typeof original !== 'function') return;
    console[name] = function () {
      try { remember(flatten(arguments)); } catch (e) {}
      return original.apply(console, arguments);
    };
  });

  function logTail() {
    var text = log.join('\n');
    return text.length > LOG_CHARS ? text.slice(text.length - LOG_CHARS) : text;
  }

  // ── payload ─────────────────────────────────────────────────────────────────
  function versions() {
    var v = {};
    try {
      var m = document.querySelector('meta[name="m2-version"]');
      if (m) v.client = m.content;
    } catch (e) {}
    return v;
  }

  function payload(description, err) {
    return {
      when: new Date().toISOString(),
      message: err && err.message ? String(err.message) : String(err || 'unknown'),
      stack: err && err.stack ? String(err.stack) : '(no stack trace)',
      description: description || '',
      // Published by the client on login (CPythonPlayer::SetName). It is what
      // lets a report be held against the server's own logs for the same
      // minute -- without it the trail ends at "somebody crashed".
      character: String(globalThis.__m2PlayerName || ''),
      pyexc: pyexc.join('\n'),
      log: logTail(),
      versions: versions(),
      userAgent: navigator.userAgent,
      page: location.pathname + location.search,
      serverHost: params.get('serverHost') || '',
      serverPort: params.get('serverPort') || ''
    };
  }

  // ── dialog ──────────────────────────────────────────────────────────────────
  function css() {
    return [
      '#m2crash{position:fixed;inset:0;z-index:2147483647;background:rgba(8,10,14,.86);',
      'display:flex;align-items:center;justify-content:center;padding:1.5rem;',
      'font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;color:#e8eaed}',
      '#m2crash .box{background:#171a20;border:1px solid #333a45;border-radius:10px;',
      'max-width:46rem;width:100%;max-height:90vh;overflow:auto;padding:1.4rem 1.6rem;',
      'box-shadow:0 18px 50px rgba(0,0,0,.55)}',
      '#m2crash h2{margin:0 0 .4rem;font-size:1.1rem}',
      '#m2crash p{margin:.5rem 0;opacity:.88}',
      '#m2crash ul{margin:.4rem 0 .8rem 1.1rem;padding:0;opacity:.88}',
      '#m2crash li{margin:.15rem 0}',
      '#m2crash textarea{width:100%;box-sizing:border-box;min-height:5.5rem;resize:vertical;',
      'background:#0e1117;color:#e8eaed;border:1px solid #333a45;border-radius:6px;',
      'padding:.6rem;font:inherit}',
      '#m2crash pre{background:#0e1117;border:1px solid #262c35;border-radius:6px;',
      'padding:.6rem;overflow:auto;max-height:14rem;font-size:12px;white-space:pre-wrap;',
      'word-break:break-word;margin:.5rem 0 0}',
      '#m2crash .row{display:flex;gap:.6rem;margin-top:1rem;flex-wrap:wrap;align-items:center}',
      '#m2crash button{font:inherit;padding:.55rem 1rem;border-radius:6px;cursor:pointer;',
      'border:1px solid #3a424f;background:#232833;color:#e8eaed}',
      '#m2crash button.primary{background:#2f6f4f;border-color:#3c8a63;font-weight:600}',
      '#m2crash button:disabled{opacity:.55;cursor:default}',
      '#m2crash .small{font-size:12px;opacity:.72}',
      '#m2crash details{margin-top:.6rem}',
      '#m2crash summary{cursor:pointer;opacity:.82}'
    ].join('');
  }

  function show(err) {
    if (shown) return;
    shown = true;

    var style = document.createElement('style');
    style.textContent = css();
    document.head.appendChild(style);

    var wrap = document.createElement('div');
    wrap.id = 'm2crash';
    wrap.innerHTML =
      '<div class="box" role="dialog" aria-modal="true">' +
      '<h2>The game stopped unexpectedly</h2>' +
      '<p>Sending this report helps get it fixed. It is the only way whoever ' +
      'maintains this server gets to see what actually went wrong &mdash; ' +
      'everything below stays in your browser otherwise.</p>' +
      '<p><strong>No login data is sent.</strong> Your account name and password ' +
      'are not available to this page at all. What is sent:</p>' +
      '<ul>' +
      '<li>the error and where in the program it happened</li>' +
      '<li>the client\'s own report of any script error (<code>PYEXC</code>)</li>' +
      '<li>the last lines the client printed while running</li>' +
      '<li>the name of the character you were playing, so the report can be ' +
      'matched with the server\'s own logs</li>' +
      '<li>what you write in the box below</li>' +
      '<li>which client version, browser and server you were using</li>' +
      '</ul>' +
      '<p class="small">The technical log can mention in-game names &mdash; a ' +
      'friend\'s character, for instance. Read the whole report below before you ' +
      'send it; nothing is hidden from you.</p>' +
      '<p><label for="m2crash-desc">What were you doing when it happened? ' +
      '(optional, but it helps a lot)</label></p>' +
      '<textarea id="m2crash-desc" placeholder="e.g. I logged in with a character who has a friend in the list"></textarea>' +
      '<details><summary>Show exactly what will be sent</summary><pre id="m2crash-json"></pre></details>' +
      '<div class="row">' +
      '<button class="primary" id="m2crash-send">Send Error Message</button>' +
      '<button id="m2crash-close">No thanks</button>' +
      '<span class="small" id="m2crash-status"></span>' +
      '</div>' +
      '<details open><summary>Technical details</summary><pre id="m2crash-stack"></pre></details>' +
      '</div>';
    document.body.appendChild(wrap);

    var desc = wrap.querySelector('#m2crash-desc');
    var json = wrap.querySelector('#m2crash-json');
    var status = wrap.querySelector('#m2crash-status');
    var sendBtn = wrap.querySelector('#m2crash-send');
    var closeBtn = wrap.querySelector('#m2crash-close');

    var p0 = payload('', err);
    wrap.querySelector('#m2crash-stack').textContent =
      p0.message + '\n\n' + p0.stack +
      (p0.pyexc ? '\n\n--- script errors ---\n' + p0.pyexc : '');

    function refresh() {
      json.textContent = JSON.stringify(payload(desc.value, err), null, 2);
    }
    refresh();
    desc.addEventListener('input', refresh);

    closeBtn.addEventListener('click', function () { wrap.remove(); });

    sendBtn.addEventListener('click', function () {
      if (reported) return;
      reported = true;
      sendBtn.disabled = true;
      status.textContent = 'sending...';

      fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload(desc.value, err))
      }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        status.textContent = 'Thank you — the report was sent.';
        sendBtn.textContent = 'Sent';
      }).catch(function (e) {
        // Never lose the report to a failed POST: the text is still on screen
        // and can be copied out of the box above.
        reported = false;
        sendBtn.disabled = false;
        status.textContent = 'Could not send it (' + e.message + '). ' +
                             'You can copy the details above instead.';
      });
    });
  }

  // ── what counts as a crash ───────────────────────────────────────────────────
  //
  // Not every error is one. Measured in the wild on the first build of this file:
  //
  //     Uncaught (in promise) SecurityError: Pointer lock cannot be acquired
  //     immediately after the user has exited the lock.
  //
  // That is the browser refusing to re-grab the mouse a fraction of a second
  // after it was released -- it happens while turning the camera, it is a policy
  // rule rather than a fault, and the game plays straight through it. The dialog
  // appeared anyway, and a reporter that interrupts play for things that are not
  // crashes gets dismissed on the one occasion it matters.
  //
  // So the test is narrow and positive: a WASM TRAP, or an explicit abort().
  // Those are the cases where the module is dead the moment it throws and no
  // amount of clicking brings it back. Everything else is left alone -- it still
  // goes into the log ring buffer through the console hook above, so if a real
  // crash follows, the harmless thing that preceded it travels with the report
  // as context.
  function isFatal(err) {
    if (!err) return false;
    try {
      if (typeof WebAssembly !== 'undefined' && WebAssembly.RuntimeError &&
          err instanceof WebAssembly.RuntimeError) return true;
    } catch (e) {}
    if (String(err.name || '') === 'RuntimeError') return true;
    return /unreachable|out of bounds|null function|signature mismatch|\babort\(/i
             .test(String(err.message || ''));
  }

  function handle(err, force) {
    if (!force && !isFatal(err)) return;
    try { show(err); } catch (e) { /* a broken reporter must not replace the bug */ }
  }

  window.addEventListener('error', function (ev) {
    handle(ev.error || new Error(ev.message || 'error'));
  });

  window.addEventListener('unhandledrejection', function (ev) {
    var r = ev.reason;
    handle(r instanceof Error ? r : new Error(String(r)));
  });

  // emscripten's abort() path, which does not always surface as an error event.
  var install = function () {
    if (!window.Module) return false;
    var prev = Module.onAbort;
    Module.onAbort = function (what) {
      // Forced: abort() IS the end of the module, whatever the text says.
      handle(new Error('abort: ' + what), true);
      if (typeof prev === 'function') prev(what);
    };
    return true;
  };
  if (!install()) {
    var tries = 0;
    var t = setInterval(function () {
      if (install() || ++tries > 600) clearInterval(t);
    }, 100);
  }
})();
