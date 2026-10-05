"""A guard that records and refuses every Python-level way to open a connection.

Used by the synthetic zero-socket test (task 19.2, Requirements 4.1 and 11.5). A
synthetic run must make no outbound connection of any kind, DNS included. The guard
patches the points a connection, a name lookup, or a network-tool process starts from:

* ``socket``: creation of an inet socket, ``connect``, ``connect_ex``, ``sendto``,
  ``sendmsg``, ``send``, ``sendall``, ``create_connection``, ``getaddrinfo``,
  ``gethostbyname``, ``gethostbyname_ex``, ``gethostbyaddr``, ``getnameinfo``;
* ``ssl.SSLContext.wrap_socket``;
* the asyncio loop: ``create_connection``, ``create_datagram_endpoint``,
  ``getaddrinfo``, ``sock_connect``, and ``asyncio.open_connection``;
* the HTTP stacks: ``httpx`` clients, ``http.client``, ``urllib.request``, ``urllib3``;
* the C module itself: ``_socket.socket`` (replaced by a guarded subclass, since the C
  type cannot be patched in place) and ``_socket.getaddrinfo`` and its siblings;
* processes: ``subprocess.Popen``, ``os.system``, ``os.fork``, ``os.forkpty``,
  ``os.exec*``, ``os.posix_spawn*`` (``os.spawn*`` and ``multiprocessing`` start through
  these), ``_posixsubprocess.fork_exec``, and the asyncio subprocess helpers (any
  process start is a violation, whatever it runs).

What it allows, on purpose: AF_UNIX sockets (the event loop's own ``socketpair``) and
a socket wrapped around an existing descriptor, and SQLite file I/O (it is not a socket
at all; so is ``bind``, which opens no outbound connection). The patches are global,
so they hold in threads and in the event loop's executor. It does not see ``ctypes`` or
a native extension calling ``connect(2)`` below Python, nor a child process once it has
started (a start is refused, so none runs).

Every violation is recorded and also raised, so an adapter that swallows the error is
still caught by ``assert_clean``. In a live run (``guard_for_mode``) nothing is refused:
each attempt is recorded and stopped short of the wire, so a test can show the guard
applies to synthetic mode only, with no real network.
"""

import _posixsubprocess
import _socket
import asyncio
import contextlib
import http.client
import inspect
import multiprocessing
import os
import socket
import ssl
import subprocess
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import httpx
import pytest
import urllib3.connection

from leadforge.lead_ingestion.models import DataMode

__all__ = ["CHANNELS", "SocketGuard", "SocketGuardError", "guard_for_mode"]

Probe = Callable[[], Awaitable[None]]

# The C socket type as imported, before any guard replaces ``_socket.socket``.
_RAW_SOCKET = _socket.socket


class SocketGuardError(AssertionError):
    """A synthetic run tried to open a connection, resolve a name, or spawn."""


def inet_socket() -> socket.socket:
    """An unconnected inet socket, made without ``socket.socket.__init__``.

    Lets a probe reach ``connect`` and friends without tripping the creation channel.
    """
    raw = _RAW_SOCKET(socket.AF_INET, socket.SOCK_DGRAM)
    return socket.socket(fileno=raw.detach())


# -------------------------------------------------------------------------- probes
# One probe per channel: it does only that, against a name that never resolves.

_HOST = "example.invalid"


async def _socket_create() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
        pass


def _on_inet_socket(action: Callable[[socket.socket], object]) -> Probe:
    async def probe() -> None:
        with contextlib.closing(inet_socket()) as sock:
            action(sock)

    return probe


def _send_with_sync_httpx() -> None:
    with httpx.Client() as client:
        client.send(httpx.Request("GET", f"https://{_HOST}/"))


def _call(action: Callable[[], object]) -> Probe:
    async def probe() -> None:
        action()

    return probe


async def _create_connection() -> None:
    socket.create_connection((_HOST, 80))


async def _wrap_socket() -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with contextlib.closing(inet_socket()) as sock:
        context.wrap_socket(sock, server_hostname=_HOST)


async def _loop_create_connection() -> None:
    await asyncio.get_running_loop().create_connection(asyncio.Protocol, _HOST, 80)


async def _loop_datagram() -> None:
    await asyncio.get_running_loop().create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=(_HOST, 9)
    )


async def _loop_getaddrinfo() -> None:
    await asyncio.get_running_loop().getaddrinfo(_HOST, 80)


async def _loop_sock_connect() -> None:
    with contextlib.closing(inet_socket()) as sock:
        sock.setblocking(False)
        # Bounded, so that a missing guard fails the test instead of waiting on a
        # blackholed documentation address.
        await asyncio.wait_for(
            asyncio.get_running_loop().sock_connect(sock, ("192.0.2.1", 9)), 2
        )


async def _open_connection() -> None:
    await asyncio.open_connection(_HOST, 80)


async def _httpx_async() -> None:
    async with httpx.AsyncClient() as client:
        await client.send(httpx.Request("GET", f"https://{_HOST}/"))


async def _http_client() -> None:
    http.client.HTTPConnection(_HOST).connect()


async def _urllib3() -> None:
    urllib3.connection.HTTPConnection(_HOST).connect()


async def _async_subprocess() -> None:
    await asyncio.create_subprocess_exec("true")


async def _raw_socket_create() -> None:
    _socket.socket(socket.AF_INET, socket.SOCK_STREAM).close()  # no context manager


def _fork() -> None:
    try:
        pid = os.fork()
    except BlockingIOError:  # a live run's guard stopped it
        return
    if pid == 0:  # the guard is missing: do not let the child run the suite
        os._exit(0)
    os.waitpid(pid, 0)


def _forkpty() -> None:
    try:
        pid, _fd = os.forkpty()
    except BlockingIOError:
        return
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)


async def _multiprocessing_spawn() -> None:
    try:
        multiprocessing.get_context("spawn").Process(target=int).start()
    except BlockingIOError:
        return


def _exec_absent() -> None:
    # An exec that the guard let through would replace this process, so it names a
    # path that does not exist and fails harmlessly.
    os.execv("/nonexistent/probe", ["probe"])


_ADDR = (_HOST, 80)

CHANNELS: Mapping[str, Probe] = {
    "socket.socket": _socket_create,
    "socket.connect": _on_inet_socket(lambda s: s.connect(_ADDR)),
    "socket.connect_ex": _on_inet_socket(lambda s: s.connect_ex(_ADDR)),
    "socket.sendto": _on_inet_socket(lambda s: s.sendto(b"x", _ADDR)),
    "socket.sendmsg": _on_inet_socket(lambda s: s.sendmsg([b"x"])),
    "socket.send": _on_inet_socket(lambda s: s.send(b"x")),
    "socket.sendall": _on_inet_socket(lambda s: s.sendall(b"x")),
    "socket.create_connection": _create_connection,
    "socket.getaddrinfo": _call(lambda: socket.getaddrinfo(_HOST, 80)),
    "socket.gethostbyname": _call(lambda: socket.gethostbyname(_HOST)),
    "socket.gethostbyname_ex": _call(lambda: socket.gethostbyname_ex(_HOST)),
    "socket.gethostbyaddr": _call(lambda: socket.gethostbyaddr("192.0.2.1")),
    "socket.getnameinfo": _call(lambda: socket.getnameinfo(("192.0.2.1", 80), 0)),
    "ssl.wrap_socket": _wrap_socket,
    "asyncio.create_connection": _loop_create_connection,
    "asyncio.create_datagram_endpoint": _loop_datagram,
    "asyncio.getaddrinfo": _loop_getaddrinfo,
    "asyncio.sock_connect": _loop_sock_connect,
    "asyncio.open_connection": _open_connection,
    "httpx.AsyncClient": _httpx_async,
    "httpx.Client": _call(_send_with_sync_httpx),
    "http.client": _http_client,
    "urllib.request": _call(lambda: urllib.request.urlopen(f"https://{_HOST}/")),
    "urllib3": _urllib3,
    "subprocess.Popen": _call(lambda: subprocess.Popen(["true"])),
    "os.system": _call(lambda: os.system("true")),
    "os.posix_spawn": _call(lambda: os.posix_spawn("/bin/true", ["true"], {})),
    "asyncio.subprocess": _async_subprocess,
    "_socket.socket": _raw_socket_create,
    "_socket.getaddrinfo": _call(lambda: _socket.getaddrinfo(_HOST, 80)),
    "_socket.gethostbyname": _call(lambda: _socket.gethostbyname(_HOST)),
    "_socket.gethostbyname_ex": _call(lambda: _socket.gethostbyname_ex(_HOST)),
    "_socket.gethostbyaddr": _call(lambda: _socket.gethostbyaddr("192.0.2.1")),
    "_socket.getnameinfo": _call(lambda: _socket.getnameinfo(("192.0.2.1", 80), 0)),
    "os.fork": _call(_fork),
    "os.forkpty": _call(_forkpty),
    "os.execv": _call(_exec_absent),
    "os.posix_spawnp": _call(lambda: os.posix_spawnp("true", ["true"], {})),
    "_posixsubprocess.fork_exec": _multiprocessing_spawn,
}


# ------------------------------------------------------------------------- targets


@dataclass(frozen=True)
class _Target:
    channel: str
    owner: Any
    attribute: str
    # The value a call returns when it is recorded but not refused (a live run).
    # ``None`` here means "let the call through" and applies to creation only.
    neutral: Callable[[], object] | None
    local_ok: bool = False  # AF_UNIX and wrap-an-existing-fd are not outbound


def _targets() -> list[_Target]:
    sock = socket.socket
    loop = asyncio.base_events.BaseEventLoop
    selector_loop = asyncio.selector_events.BaseSelectorEventLoop
    nothing: Callable[[], object] = lambda: None  # noqa: E731
    zero: Callable[[], object] = lambda: 0  # noqa: E731

    def refuse() -> object:
        # A process start that is stopped, not faked: a fake pid of 0 would read as
        # the child, and any other would be a pid nothing owns.
        raise BlockingIOError("process start stopped by the socket guard")

    refused: Callable[[], object] = refuse
    return [
        _Target("socket.socket", sock, "__init__", None, local_ok=True),
        _Target("socket.connect", sock, "connect", nothing, local_ok=True),
        _Target("socket.connect_ex", sock, "connect_ex", zero, local_ok=True),
        _Target("socket.sendto", sock, "sendto", zero, local_ok=True),
        _Target("socket.sendmsg", sock, "sendmsg", zero, local_ok=True),
        _Target("socket.send", sock, "send", zero, local_ok=True),
        _Target("socket.sendall", sock, "sendall", nothing, local_ok=True),
        _Target("socket.create_connection", socket, "create_connection", nothing),
        _Target("socket.getaddrinfo", socket, "getaddrinfo", list),
        _Target("socket.gethostbyname", socket, "gethostbyname", lambda: "0.0.0.0"),
        _Target("socket.gethostbyname_ex", socket, "gethostbyname_ex", nothing),
        _Target("socket.gethostbyaddr", socket, "gethostbyaddr", nothing),
        _Target("socket.getnameinfo", socket, "getnameinfo", nothing),
        _Target("ssl.wrap_socket", ssl.SSLContext, "wrap_socket", nothing),
        _Target("asyncio.create_connection", loop, "create_connection", nothing),
        _Target(
            "asyncio.create_datagram_endpoint",
            loop,
            "create_datagram_endpoint",
            nothing,
        ),
        _Target("asyncio.getaddrinfo", loop, "getaddrinfo", list),
        _Target(
            "asyncio.sock_connect",
            selector_loop,
            "sock_connect",
            nothing,
            local_ok=True,
        ),
        _Target("asyncio.open_connection", asyncio, "open_connection", nothing),
        _Target(
            "asyncio.open_connection",
            asyncio.streams,
            "open_connection",
            nothing,
        ),
        _Target("httpx.AsyncClient", httpx.AsyncClient, "send", nothing),
        _Target("httpx.Client", httpx.Client, "send", nothing),
        _Target("http.client", http.client.HTTPConnection, "connect", nothing),
        _Target("urllib.request", urllib.request.OpenerDirector, "open", nothing),
        _Target("urllib3", urllib3.connection.HTTPConnection, "_new_conn", nothing),
        _Target("subprocess.Popen", subprocess.Popen, "__init__", nothing),
        _Target("os.system", os, "system", zero),
        _Target("os.posix_spawn", os, "posix_spawn", zero),
        _Target("_socket.getaddrinfo", _socket, "getaddrinfo", list),
        _Target("_socket.gethostbyname", _socket, "gethostbyname", lambda: "0.0.0.0"),
        _Target("_socket.gethostbyname_ex", _socket, "gethostbyname_ex", nothing),
        _Target("_socket.gethostbyaddr", _socket, "gethostbyaddr", nothing),
        _Target("_socket.getnameinfo", _socket, "getnameinfo", nothing),
        _Target("os.fork", os, "fork", refused),
        _Target("os.forkpty", os, "forkpty", refused),
        _Target("os.execv", os, "execv", nothing),
        _Target("os.execve", os, "execve", nothing),
        _Target("os.posix_spawnp", os, "posix_spawnp", zero),
        _Target("_posixsubprocess.fork_exec", _posixsubprocess, "fork_exec", refused),
        _Target("asyncio.subprocess", asyncio, "create_subprocess_exec", nothing),
        _Target("asyncio.subprocess", asyncio, "create_subprocess_shell", nothing),
        _Target(
            "asyncio.subprocess", asyncio.subprocess, "create_subprocess_exec", nothing
        ),
        _Target(
            "asyncio.subprocess", asyncio.subprocess, "create_subprocess_shell", nothing
        ),
    ]


def _is_local(target: _Target, args: tuple[Any, ...], kwargs: dict[str, Any]) -> bool:
    """AF_UNIX, or a socket built around an existing descriptor, is not outbound."""
    if not target.local_ok:
        return False
    if target.attribute == "__init__":
        family = args[1] if len(args) > 1 else kwargs.get("family", -1)
        fileno = args[4] if len(args) > 4 else kwargs.get("fileno")
        return fileno is not None or family == socket.AF_UNIX
    # The socket is the receiver of a socket method, or an argument of a loop method.
    sock = next((a for a in args if isinstance(a, socket.socket)), None)
    return sock is not None and sock.family == socket.AF_UNIX


class SocketGuard:
    """Install with ``install``; read ``violations``/``attempts``; ``assert_clean``.

    A non-enforcing guard (a live run) stops every attempt itself and also installs an
    enforcing backstop beneath it. Should a stop have a hole, the call falls to the
    backstop, which raises: no test of a live run can reach the wire.
    """

    def __init__(self, *, enforce: bool, allow_creation: bool = False) -> None:
        self.enforce = enforce
        self.allow_creation = allow_creation  # an unconnected socket is no contact
        self.violations: list[str] = []
        self.attempts: list[str] = []
        self.backstop: SocketGuard | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if not self.enforce:
            self.backstop = SocketGuard(enforce=True, allow_creation=True)
            self.backstop.install(monkeypatch)
        for target in _targets():
            original = getattr(target.owner, target.attribute)
            monkeypatch.setattr(
                target.owner, target.attribute, self._wrap(target, original)
            )
        monkeypatch.setattr(_socket, "socket", self._raw_socket_class())

    def _raw_socket_class(self) -> type:
        """A ``_socket.socket`` whose use is gated like ``socket.socket``'s.

        The C type takes no attribute assignment, so code that builds one directly,
        bypassing the ``socket`` module, is caught by swapping in this subclass.
        """
        base: type = _socket.socket  # the original, or an earlier guard's subclass
        methods = {
            target.attribute: self._wrap(
                replace(target, channel="_socket.socket", owner=base),
                getattr(base, target.attribute),
            )
            for target in _targets()
            if target.owner is socket.socket
        }
        gated_init = methods["__init__"]

        def init(self: Any, *args: Any, **kwargs: Any) -> None:
            # ``socket.socket.__init__`` calls this one; it has been gated already.
            if isinstance(self, socket.socket):
                base.__init__(self, *args, **kwargs)  # type: ignore[misc]
            else:
                gated_init(self, *args, **kwargs)

        methods["__init__"] = init
        return type("GuardedRawSocket", (base,), methods)

    def assert_clean(self) -> None:
        if self.backstop is not None:
            self.backstop.assert_clean()  # a live stop that leaked
        if self.violations:
            raise SocketGuardError(
                "a synthetic run opened a connection; first violation: "
                f"{self.violations[0]} ({len(self.violations)} in all)"
            )

    def _gate(
        self, target: _Target, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> bool:
        """True when the call must go through to the original."""
        if _is_local(target, args, kwargs):
            return True
        if self.allow_creation and target.attribute == "__init__":
            return target.channel in {"socket.socket", "_socket.socket"}
        shown = args if inspect.ismodule(target.owner) else args[1:]  # not ``self``
        detail = ", ".join(repr(a) for a in shown if not callable(a))[:60]
        entry = f"{target.channel}({detail})"
        if self.enforce:
            self.violations.append(entry)
            raise SocketGuardError(f"a synthetic run opened a connection: {entry}")
        self.attempts.append(entry)
        return target.neutral is None

    def _neutral(self, target: _Target, args: tuple[Any, ...]) -> object:
        assert target.neutral is not None
        if target.attribute == "__init__" and target.channel == "subprocess.Popen":
            # Popen.__del__ reads this; without it the stopped call warns at collection.
            args[0]._child_created = False
        return target.neutral()

    def _wrap(
        self, target: _Target, original: Callable[..., Any]
    ) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(original):

            async def async_guarded(*args: Any, **kwargs: Any) -> Any:
                if self._gate(target, args, kwargs):
                    return await original(*args, **kwargs)
                return self._neutral(target, args)

            return async_guarded

        def guarded(*args: Any, **kwargs: Any) -> Any:
            if self._gate(target, args, kwargs):
                return original(*args, **kwargs)
            return self._neutral(target, args)

        return guarded


def guard_for_mode(mode: DataMode) -> SocketGuard:
    """Enforcing for a synthetic run; record-only for a live one."""
    return SocketGuard(enforce=mode is DataMode.SYNTHETIC)
