"""Tiny LAN transport: newline-delimited JSON over TCP, one reader thread per connection."""
import json
import queue
import socket
import threading

PORT = 5555


def _encode(msg):
    return json.dumps(msg, separators=(",", ":")).encode() + b"\n"


class Conn:
    def __init__(self, sock, cid, inbox):
        self.sock, self.cid, self.inbox, self.alive = sock, cid, inbox, True
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(None)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        buf = b""
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        self.inbox.put((self.cid, json.loads(line)))
                    except ValueError:
                        pass
        except OSError:
            pass
        self.alive = False
        self.inbox.put((self.cid, None))        # None == disconnected

    def send(self, msg):
        if not self.alive:
            return False
        try:
            self.sock.settimeout(0.25)
            self.sock.sendall(_encode(msg))
            return True
        except OSError:
            self.alive = False
            return False

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


class HostNet:
    def __init__(self, port=PORT):
        self.inbox = queue.Queue()
        self.conns = {}
        self._next = 1
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("", port))
        self.srv.listen(4)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                s, _ = self.srv.accept()
            except OSError:
                return
            cid = self._next
            self._next += 1
            self.conns[cid] = Conn(s, cid, self.inbox)

    def messages(self):
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def send(self, cid, msg):
        c = self.conns.get(cid)
        if c:
            c.send(msg)

    def broadcast(self, msg):
        for c in list(self.conns.values()):
            c.send(msg)

    def drop(self, cid):
        c = self.conns.pop(cid, None)
        if c:
            c.close()


class ClientNet:
    def __init__(self, host, port=PORT):
        self.inbox = queue.Queue()
        sock = socket.create_connection((host, port), timeout=5)
        self.conn = Conn(sock, 0, self.inbox)

    def messages(self):
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return [m for _, m in out if m is not None]

    def send(self, msg):
        return self.conn.send(msg)

    @property
    def alive(self):
        return self.conn.alive


def lan_ip():
    """The address other PCs on the LAN should use to reach this one."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))        # no packet is sent; this just picks the route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
