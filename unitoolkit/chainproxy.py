#!/usr/bin/env python3
"""Local chaining proxy for the library browser: `python -m unitoolkit.chainproxy`.

Chrome cannot take user:pass in --proxy-server for https, so this listens on 127.0.0.1:8118
and adds the upstream Proxy-Authorization itself. Only needed where the library drops your
own IP; most students reach their library directly and never start it.

Upstream: CHAIN_UPSTREAM_HOST/PORT/USER (default Apify residential, sticky session, since
Koha binds a login to the client IP). Password from CHAIN_UPSTREAM_PASSWORD_FILE (default
~/.secrets/APIFY_PROXY_PASSWORD), read at start-up, never logged.
"""
import base64
import os
import select
import socket
import sys
import threading

LISTEN = ('127.0.0.1', int(os.environ.get('CHAIN_PORT', '8118')))
UP_HOST = os.environ.get('CHAIN_UPSTREAM_HOST', 'proxy.apify.com')
UP_PORT = int(os.environ.get('CHAIN_UPSTREAM_PORT', '8000'))
# Sticky session: Koha binds the login to the client IP, so every request in the flow
# must leave through the same residential exit. Apify keeps a session ~30 minutes.
UP_USER = os.environ.get('CHAIN_UPSTREAM_USER', 'groups-RESIDENTIAL,session-kohavakt')
PW_FILE = os.path.expanduser(os.environ.get('CHAIN_UPSTREAM_PASSWORD_FILE',
                                           '~/.secrets/APIFY_PROXY_PASSWORD'))
AUTH = ''          # set in main(): importing this module must not need the password


def _auth():
    with open(PW_FILE) as f:
        pw = f.read().strip()
    return 'Basic ' + base64.b64encode(f'{UP_USER}:{pw}'.encode()).decode()


def pipe(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 60)
            if not r:
                break
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


def handle(client):
    try:
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = client.recv(4096)
            if not chunk:
                return client.close()
            head += chunk
        lines = head.split(b'\r\n')
        method, target, version = lines[0].split(b' ')
        up = socket.create_connection((UP_HOST, UP_PORT), timeout=30)
        if method.upper() == b'CONNECT':
            req = (b'CONNECT ' + target + b' HTTP/1.1\r\nHost: ' + target +
                   b'\r\nProxy-Authorization: ' + AUTH.encode() + b'\r\n\r\n')
            up.sendall(req)
            resp = b''
            while b'\r\n\r\n' not in resp:
                chunk = up.recv(4096)
                if not chunk:
                    return client.close()
                resp += chunk
            status = resp.split(b'\r\n')[0]
            if b' 200' not in status:
                client.sendall(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
                print('upstream refused:', status[:60], file=sys.stderr)
                return client.close()
            client.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
            pipe(client, up)
        else:
            rest = head.split(b'\r\n\r\n', 1)[1]
            extra = b''.join(b'\r\n' + ln for ln in lines[1:] if ln and not ln.lower().startswith(b'proxy-'))
            up.sendall(lines[0] + extra + b'\r\nProxy-Authorization: ' + AUTH.encode() +
                       b'\r\n\r\n' + rest)
            pipe(client, up)
    except Exception as exc:
        print('handler error:', type(exc).__name__, file=sys.stderr)
        try:
            client.close()
        except Exception:
            pass


def main():
    global AUTH
    AUTH = _auth()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN)
    srv.listen(64)
    print('chaining proxy on %s:%d -> %s:%d (%s)' % (LISTEN[0], LISTEN[1], UP_HOST, UP_PORT, UP_USER))
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == '__main__':
    main()
