"""Hand a download to Internet Download Manager the way its browser extension does.

IDM's command line (IDMan.exe /d <url>) fetches the url cold: no cookies, no referer.
Anything gated on a session -- gofile, and most hosts that only serve a file to the
browser that asked for it -- then saves the "please log in" page instead of the file.
The extension does not hand IDM a url, it hands IDM the whole request, and that is the
entire difference. It does so over a websocket IDM listens on locally; this speaks the
same protocol, read out of IDM's own extension (IDMGCExt.crx, background.js):

  ws://127.0.0.1:1001, subprotocol plugin.v3.internetdownloadmanager.com, every message
  a binary frame holding utf-8 text:
      MSG#<id>#<type>#<a>#<b>[:<arg>...][,<key>=<value>...];
  an attribute is key=<number>, or key=<utf-8 byte length>:<text> for a string. Type 2
  is the hello, which IDM requires before it will accept anything from a client -- and
  its reply has to be read before sending, or the download is dropped. Type 14 is the
  download itself: 6=url, 7=page, 50=referer, 51=cookie header, 54=user agent.

Unofficial and version specific, so every failure here is a plain False and the caller
falls back to the command line.
"""
import base64
import os
import socket
import struct
import time

HOST = "127.0.0.1"
PORT = 1001
SUBPROTOCOL = "plugin.v3.internetdownloadmanager.com"
# IDM closes the connection unless Origin is one of its own two Chrome extension ids,
# or any moz-extension:// origin at all -- Firefox gives an extension a random uuid per
# install, so there is nothing there to whitelist. That last one is the gate to walk
# through: it is the only value IDM accepts that is not another product's identity.
# Who is really calling is said honestly in the hello itself, as the client name
ORIGIN = "moz-extension://f95checker"


def encode(msg_id: int, type_: int, a: int, b: int, args=(), attrs=None):
    out = [f"MSG#{msg_id}#{type_}#{a}#{b}"]
    for arg in args:
        out.append(f":{arg or 0}")
    for key, value in (attrs or {}).items():
        if value is None or value == "":
            continue
        if isinstance(value, int):
            out.append(f",{key}={value}")
        else:
            out.append(f",{key}={len(value.encode('utf-8'))}:{value}")
    out.append(";")
    return "".join(out)


def _frame(payload: str):
    data = payload.encode("utf-8")
    mask = os.urandom(4)
    n = len(data)
    if n < 126:
        header = bytes((0x82, 0x80 | n))
    elif n < 65536:
        header = b"\x82\xfe" + struct.pack(">H", n)
    else:
        header = b"\x82\xff" + struct.pack(">Q", n)
    return header + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data))


def _read_frame(sock, buf: bytearray):
    while True:
        if len(buf) >= 2:
            length, offset = buf[1] & 127, 2
            if length == 126:
                length, offset = struct.unpack(">H", buf[2:4])[0], 4
            elif length == 127:
                length, offset = struct.unpack(">Q", buf[2:10])[0], 10
            if buf[1] & 128:  # a server frame is never masked, but do not assume it
                offset += 4
            if len(buf) >= offset + length:
                del buf[:offset + length]
                return True
        chunk = sock.recv(65536)
        if not chunk:
            return False
        buf += chunk


def send_download(url: str, *, cookies: str = "", referer: str = "",
                  user_agent: str = "", timeout: float = 5.0):
    """Returns True once IDM has been handed the download, False for any failure at
    all -- IDM not running, protocol changed, anything. The caller falls back."""
    sock = None
    try:
        sock = socket.create_connection((HOST, PORT), timeout=timeout)
        sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((
            f"GET /?cid=0&rnd=0 HTTP/1.1\r\n"
            f"Host: {HOST}:{PORT}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: {SUBPROTOCOL}\r\n"
            f"Origin: {ORIGIN}\r\n\r\n"
        ).encode())
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                return False
            buf += chunk
        head, _, rest = bytes(buf).partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            return False
        buf = bytearray(rest)
        # The numbers are the extension's own protocol/browser constants, kept as they
        # are because IDM checks them; only the client name says who is really calling
        sock.sendall(_frame(encode(1, 2, 5, 0, (113, 93, 1031, 0, 16845059, 0, 15, 3), {
            112: "F95Checker", 113: "F95Checker", 116: "en-US", 125: "{}",
        })))
        if not _read_frame(sock, buf):  # IDM drops downloads sent before it replies
            return False
        sock.sendall(_frame(encode(2, 14, 1, 0, (1,), {
            6: url, 7: referer, 50: referer, 8: 4,
            51: cookies, 54: user_agent,
        })))
        # IDM reads the socket asynchronously and drops whatever a client left behind
        # when it disconnects, so closing right here silently loses the download -- it
        # took ~0.13s to act on one in testing. There is no ack to wait for, so just
        # stay long enough for it to have read the message
        time.sleep(0.5)
        return True
    except (OSError, struct.error):
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
