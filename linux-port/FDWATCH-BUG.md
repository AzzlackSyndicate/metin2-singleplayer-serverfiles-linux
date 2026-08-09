# The bug that blocked login — and why it was so hard to see

**Status: fixed, confirmed by a real client logging in, and in the shipped
patch.** This is an account of what happened, written while it was fresh; the
one-line fix it ends with is live in
[`patches/0001-r40250-linux-port.patch`](patches/0001-r40250-linux-port.patch).

This is the single most instructive defect of the whole port, so it gets its own
write-up. Anyone porting kqueue code to epoll can hit exactly this.

## Symptom

A real client connected to the ported server and:

1. TCP connected ✓
2. Received the `PHASE` packet ✓
3. Completed the time-sync handshake ✓ (`Handshake: client_time 0 server_time N`, then `AUTH_PHASE`)
4. Received the 261-byte key agreement ✓
5. **Then went silent forever.** The client showed *"You will be connected to the
   server…"*, timed out after 16-45 s, reconnected, and looped.

The server logged **no errors at all**. It simply waited and closed on ping timeout.

## Why the obvious suspect was wrong

Everything pointed at the crypto: 40250 enables `_IMPROVED_PACKET_ENCRYPTION_`
(the other Metin2 fork we ported first has it off, so this path had never been
exercised), and the failure happened exactly at the key agreement. The natural
theory was a bad 32-bit Crypto++ build.

That theory was **disproved, not assumed away**:

- `cipher.cpp`, `cipher.h`, `desc.cpp`, `input_auth.cpp` and `common/service.h`
  are byte-identical to pristine — the port never touched them.
- A two-party Diffie-Hellman harness through the server's own code derived
  **identical 256-byte shared secrets, 5 rounds out of 5**.
- Crypto++ 8.4.0 built 32-bit passes its full `cryptest v` validation suite with
  zero failures. (The only failing item is the RDSEED hardware-entropy wrapper,
  which a 64-bit control build on the same CPU also exercises differently —
  and `AutoSeededRandomPool` uses `/dev/urandom`, never RDSEED.)

## The actual root cause

`libthecore/src/fdwatch.c` — the Linux `fdwatch_get_buffer_size()` returned **0**
whenever the current epoll round had no *write* event for that descriptor.

The semantic gap:

| | behaviour |
|---|---|
| **kqueue (FreeBSD)** | `kqrevents[]` is **not cleared between passes**. The last `EVFILT_WRITE` record survives, so the function keeps answering a positive `sbspace()`. |
| **epoll (our port)** | `fdwrevents[]` is **rebuilt every pass**. With no write event this round, there is nothing to read — so it answered 0. |

Callers treat 0 as *"the socket cannot take anything"*. `DESC::ProcessOutput()`
returns immediately at its `buffer_left <= 0` guard. That silently turned this
deliberate flush in `game/src/input.cpp:648` into a no-op:

```cpp
d->SendKeyAgreementCompleted();
// Flush socket output before going encrypted
d->ProcessOutput();          // <-- no-op on Linux
```

And that flush **always** runs while handling a *read* event — i.e. exactly when
no write event exists. So it never did anything.

## The consequence, visible in strace

`HEADER_GC_KEY_AGREEMENT_COMPLETED` stayed queued and left the machine coalesced
with the first ciphertext behind it:

```
ours (broken):
send(17, "\373\0\1\0\1\206\366e\276...", 261, 0) = 261   <- 0xfb key agreement
send(17, "\372\0\0\0\357\247", 6, 0) = 6                  <- 0xfa + encrypted PHASE, ONE segment

production (FreeBSD, working):
send(...) = 261
send(...) = 4
send(...) = 2
```

The client decrypts a whole socket read at once — the same way the server's own
`DESC::ProcessInput` does. So it took the ciphertext in **while its cipher was
still inactive**, kept it as plaintext, and stalled on the garbage forever.

## The fix

`libthecore/src/fdwatch.c:502` — fall back to the kernel's own figure instead of 0:

```c
return fdwatch_sndbuf_left(fd);
```

That helper already existed and already supplies the `data` field of write
events, so nothing downstream sees a value it would not otherwise get. It still
goes to 0 exactly when the send buffer is genuinely full — which matters, because
`socket_write()` spins on `EAGAIN` (`total -= 0`) if the answer is optimistic.

Because a one-line fix in a 500-line file is easy to lose in a merge, the game
image's builder stage refuses to compile without it:

```dockerfile
RUN grep -q 'return fdwatch_sndbuf_left(fd);' libthecore/src/fdwatch.c || exit 1
```

A silent regression here costs a day of "the client connects and then hangs",
which is exactly what it cost the first time.

## Verification

- **Controlled A/B**, one line different, same binary otherwise:
  - `return 0;` → `RAW 6 bytes: fa 00 00 00 96 db` → client sees header `0x96`, stalls
  - `return fdwatch_sndbuf_left(fd);` → `RAW 4 bytes` then `RAW 2 bytes` → PHASE_AUTH decrypts correctly
- A **real protocol client** built from a verbatim copy of `cipher.cpp`, including
  a mode that models the retail client's decrypt-at-recv-time behaviour:
  **24/24 successful encrypted handshakes** across auth (11000) and all three game
  cores (13000-13002).
- **Production parity**: the same harness completes against the live FreeBSD
  server with byte-identical framing.
- **A real Windows client logged in.**

## FreeBSD path provably unaffected

`unifdef -U__linux__` on the ported `fdwatch.c` versus pristine differs **only by
the four `LINUX-BLOCK-BEGIN/END` marker comments** — zero code.
`fdwatch_sndbuf_left` appears 0 times in the FreeBSD view and 0 times in pristine,
4 times in the Linux view. The hunk sits between `#if defined(__linux__)` and
`#else /* !__linux__ */`.

## The lesson

The first port (of a different Metin2 fork) had already flagged this exact
function as having *"no exact epoll equivalent"* and deliberately chose to
**under-report rather than over-report**, because an optimistic answer drives
`socket_write()` into an infinite `EAGAIN` spin. That instinct was right — but
under-reporting went one step too far, all the way to 0, and 0 does not mean
"a little space" to the callers. It means "stop".

A porting note that says *"this has no exact equivalent"* is a warning that the
semantics, not just the API, need checking against every caller.
