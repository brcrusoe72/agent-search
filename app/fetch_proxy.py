"""Small validating forward proxy for API and browser outbound traffic.

The proxy resolves a destination, rejects every non-global answer, and opens
the tunnel to the validated IP. In Tor mode it resolves through Tor's SOCKS
RESOLVE extension and then asks Tor to connect to that validated IP.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import struct
from urllib.parse import urlsplit

logger = logging.getLogger("agentsearch.fetch_proxy")
LISTEN_HOST = os.getenv("FETCH_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("FETCH_PROXY_PORT", "8888"))
TOR_PROXY = os.getenv("FETCH_PROXY_TOR", "").strip()
MAX_HEADER_BYTES = int(os.getenv("FETCH_PROXY_MAX_HEADER_BYTES", "65536"))
CONNECT_TIMEOUT = float(os.getenv("FETCH_PROXY_CONNECT_TIMEOUT", "15"))
MAX_CONCURRENT = max(1, int(os.getenv("FETCH_PROXY_MAX_CONCURRENT", "64")))
_capacity = asyncio.Semaphore(MAX_CONCURRENT)


class UnsafeURLError(ValueError):
    pass


def _split_host_port(value: str, default_port: int) -> tuple[str, int]:
    raw = value.strip()
    if raw.startswith("["):
        end = raw.find("]")
        if end < 0:
            raise UnsafeURLError("Malformed IPv6 authority")
        host = raw[1:end]
        port = int(raw[end + 2 :]) if raw[end + 1 :].startswith(":") else default_port
        return host, port
    if raw.count(":") == 1:
        host, port_text = raw.rsplit(":", 1)
        return host, int(port_text)
    return raw, default_port


def _validated_ip(address: str) -> str:
    parsed = ipaddress.ip_address(address)
    if (not parsed.is_global or parsed.is_multicast or parsed.is_reserved
            or parsed.is_unspecified or "%" in address):
        raise UnsafeURLError(f"Non-global proxy destination blocked: {parsed}")
    return parsed.compressed


def _resolve_direct(host: str, port: int) -> list[str]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            rows = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except (socket.gaierror, OSError) as exc:
            raise UnsafeURLError(f"DNS resolution failed for {host}") from exc
        addresses: list[str] = []
        for row in rows:
            address = _validated_ip(row[4][0])
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise UnsafeURLError(f"DNS returned no addresses for {host}")
        return addresses
    return [_validated_ip(literal.compressed)]


async def _read_socks_reply(reader: asyncio.StreamReader) -> str:
    head = await reader.readexactly(4)
    if head[0] != 5 or head[1] != 0:
        raise OSError(f"SOCKS request failed with code {head[1]}")
    atyp = head[3]
    if atyp == 1:
        address = socket.inet_ntop(socket.AF_INET, await reader.readexactly(4))
    elif atyp == 4:
        address = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
    elif atyp == 3:
        size = (await reader.readexactly(1))[0]
        address = (await reader.readexactly(size)).decode("ascii")
    else:
        raise OSError("Unsupported SOCKS address type")
    await reader.readexactly(2)
    return address


async def _open_tor_control() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    tor_host, tor_port = _split_host_port(TOR_PROXY, 9050)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(tor_host, tor_port), CONNECT_TIMEOUT
    )
    writer.write(b"\x05\x01\x00")
    await writer.drain()
    if await reader.readexactly(2) != b"\x05\x00":
        writer.close()
        raise OSError("Tor SOCKS authentication negotiation failed")
    return reader, writer


async def _tor_resolve(host: str) -> str:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return _validated_ip(literal.compressed)
    reader, writer = await _open_tor_control()
    try:
        encoded = host.encode("idna")
        writer.write(b"\x05\xf0\x00\x03" + bytes([len(encoded)]) + encoded + b"\x00\x00")
        await writer.drain()
        return _validated_ip(await _read_socks_reply(reader))
    finally:
        writer.close()
        await writer.wait_closed()


async def _open_tor_ip(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await _open_tor_control()
    parsed = ipaddress.ip_address(_validated_ip(address))
    atyp = b"\x01" if parsed.version == 4 else b"\x04"
    packed = parsed.packed
    writer.write(b"\x05\x01\x00" + atyp + packed + struct.pack("!H", port))
    await writer.drain()
    await _read_socks_reply(reader)
    return reader, writer


async def _open_destination(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    if TOR_PROXY:
        address = await _tor_resolve(host)
        return await _open_tor_ip(address, port)
    last_error: Exception | None = None
    for address in _resolve_direct(host, port):
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(address, port), CONNECT_TIMEOUT
            )
        except Exception as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        # The peer closed or the tunnel was cancelled; close the writer below.
        pass
    finally:
        if not writer.is_closing():
            writer.close()


async def _reject(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    safe_reason = reason.replace("\r", " ").replace("\n", " ")[:200]
    body = safe_reason.encode("utf-8")
    writer.write(
        f"HTTP/1.1 {status} Proxy Error\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()
    writer.close()


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        header = await reader.readuntil(b"\r\n\r\n")
        if len(header) > MAX_HEADER_BYTES:
            raise UnsafeURLError("Proxy request headers too large")
        lines = header.split(b"\r\n")
        method, target, version = lines[0].decode("latin-1").split(" ", 2)
        if method.upper() == "GET" and target == "/live":
            body = b'{"status":"alive"}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
            return
        if method.upper() == "CONNECT":
            host, port = _split_host_port(target, 443)
            upstream_reader, upstream_writer = await _open_destination(host, port)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            parsed = urlsplit(target)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise UnsafeURLError("Forward proxy requires an absolute HTTP URL")
            if parsed.scheme == "https":
                raise UnsafeURLError("HTTPS proxy requests must use CONNECT")
            host, port = parsed.hostname, parsed.port or 80
            upstream_reader, upstream_writer = await _open_destination(host, port)
            path = parsed.path or "/"
            if parsed.query:
                path += f"?{parsed.query}"
            lines[0] = f"{method} {path} {version}".encode("latin-1")
            upstream_writer.write(b"\r\n".join(lines))
            await upstream_writer.drain()

        await asyncio.gather(
            _pipe(reader, upstream_writer),
            _pipe(upstream_reader, writer),
        )
    except (ValueError, OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        logger.warning("Proxy request blocked: %s", str(exc)[:200])
        if not writer.is_closing():
            await _reject(writer, 403, str(exc))
    finally:
        if upstream_writer is not None and not upstream_writer.is_closing():
            upstream_writer.close()
        if not writer.is_closing():
            writer.close()


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Reject excess proxy work promptly instead of building an unbounded queue."""
    acquired = False
    try:
        await asyncio.wait_for(_capacity.acquire(), timeout=0.1)
        acquired = True
        await _handle_client(reader, writer)
    except asyncio.TimeoutError:
        await _reject(writer, 503, "Fetch proxy is at capacity")
    finally:
        if acquired:
            _capacity.release()


async def main() -> None:
    server = await asyncio.start_server(
        handle_client,
        LISTEN_HOST,
        LISTEN_PORT,
        limit=MAX_HEADER_BYTES + 1,
    )
    logger.info("Validating fetch proxy listening on %s:%s", LISTEN_HOST, LISTEN_PORT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
