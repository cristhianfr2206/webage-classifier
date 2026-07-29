import threading
from collections import Counter
from contextlib import suppress
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class FixtureServer:
    def __init__(self) -> None:
        self.hits: Counter[str] = Counter()
        self.request_seen = threading.Event()
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def url(self, path: str) -> str:
        return f"{self.origin}{path}"

    def start(self) -> "FixtureServer":
        self._thread.start()
        return self

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def count(self, path: str) -> int:
        with self._lock:
            return self.hits[path]

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *args: object) -> None:
                del args

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlsplit(self.path)
                with fixture._lock:
                    fixture.hits[parsed.path] += 1
                fixture.request_seen.set()
                route = parsed.path
                if route == "/redirect-private":
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header("Location", "http://127.0.0.1:1/private")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if route == "/loop":
                    number = int(parse_qs(parsed.query).get("n", ["0"])[0])
                    location = f"/loop?n={number + 1}"
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if route == "/large":
                    self._bytes(b"x" * 250_000, "text/plain")
                    return
                if route == "/download-file":
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", 'attachment; filename="unsafe.bin"')
                    self.send_header("Content-Length", "7")
                    self.end_headers()
                    self.wfile.write(b"payload")
                    return
                if route in {"/pixel", "/style.css"}:
                    media = "image/png" if route == "/pixel" else "text/css"
                    self._bytes(b"x", media)
                    return
                if route == "/popup-target":
                    self._html("<h1>popup visited</h1>")
                    return
                pages = {
                    "/js": """
                        <title>static shell</title><div id="root"></div>
                        <script>
                          document.title = "Rendered title";
                          root.innerHTML =
                            "<h1>Rendered games</h1><p>Meaningful dynamic content.</p>";
                        </script>
                    """,
                    "/blocked-subresources": """
                        <h1>Safe main content</h1>
                        <iframe src="http://127.0.0.1:1/secret"></iframe>
                        <img src="http://127.0.0.1:1/image">
                        <link rel="stylesheet" href="http://127.0.0.1:1/style.css">
                        <script>
                          fetch("http://127.0.0.1:1/fetch").then(r => r.text())
                            .then(value => document.body.dataset.leak = value).catch(() => {});
                          const xhr = new XMLHttpRequest();
                          xhr.open("GET", "http://127.0.0.1:1/xhr"); xhr.send();
                          try { new WebSocket("ws://127.0.0.1:1/socket"); } catch (_) {}
                        </script>
                    """,
                    "/popup": """
                        <h1>Popup test</h1>
                        <script>window.open("/popup-target", "_blank");</script>
                    """,
                    "/download": """
                        <h1>Download test</h1><a id="d" download href="/download-file">download</a>
                        <script>d.click()</script>
                    """,
                    "/endless": "<script>while (true) { Math.random(); }</script>",
                    "/resources": """
                        <h1>Resource flood</h1><script>
                        for (let i=0; i<100; i++) {
                          const img=document.createElement("img"); img.src="/pixel?n="+i;
                          document.body.appendChild(img);
                        }</script>
                    """,
                    "/oversized": """
                        <title>Bounded</title><div id="root"></div><script>
                        root.append("T".repeat(5000));
                        for(let i=0;i<20;i++) {
                          root.insertAdjacentHTML("beforeend",
                            `<h2>heading-${i}</h2><a>link-${i}</a><button>button-${i}</button>`);
                        }</script>
                    """,
                    "/xss": """
                        <title>&lt;script&gt;titleAttack()&lt;/script&gt;</title>
                        <h1>&lt;img src=x onerror=headingAttack()&gt;</h1>
                        <button>&lt;script&gt;buttonAttack()&lt;/script&gt;</button>
                        <p>&lt;svg onload=evidenceAttack()&gt;</p>
                    """,
                    "/unsafe-subframes": """
                        <h1>Unsafe schemes</h1><script>
                        for (const value of [
                          "file:///etc/passwd", "ftp://127.0.0.1/file",
                          "gopher://127.0.0.1/", "data:text/html,unsafe",
                          "blob:http://127.0.0.1/unsafe"
                        ]) {
                          const frame=document.createElement("iframe"); frame.src=value;
                          document.body.appendChild(frame);
                        }</script>
                    """,
                }
                body = pages.get(route)
                if body is None:
                    self._bytes(b"not found", "text/plain", HTTPStatus.NOT_FOUND)
                else:
                    self._html(body)

            def _html(self, body: str) -> None:
                self._bytes(
                    f"<!doctype html><html><body>{body}</body></html>".encode(),
                    "text/html; charset=utf-8",
                )

            def _bytes(
                self,
                body: bytes,
                media_type: str,
                status: HTTPStatus = HTTPStatus.OK,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", media_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                with suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(body)

        return Handler
