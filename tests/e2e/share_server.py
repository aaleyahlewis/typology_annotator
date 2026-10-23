"""Local stand-in for chatgpt.com/share pages, used only by the end-to-end test.

python3 tests/e2e/share_server.py PORT
"""
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.helpers import fixtures as f  # noqa: E402

PAGES = {
    f.SHARE_ID: f.turbo_page(),                                          # current page format
    "11111111-0000-4000-8000-000000000001": f.next_data_page(),          # older page format
    "22222222-0000-4000-8000-000000000002": f.rendered_page(),           # no embedded data
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        share_id = self.path.rstrip("/").rsplit("/", 1)[-1]
        page = PAGES.get(share_id)
        self.send_response(200 if page else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write((page or "not found").encode("utf-8"))

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
