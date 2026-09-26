#!/usr/bin/env python3
"""Serve the app locally: python3 tools/serve.py [port] [dir]  (default 8765, the app folder).
Plain http.server plus the right MIME types for the manifest and service worker.
Open http://localhost:PORT/ (service workers need localhost or https)."""
import functools, http.server, os, sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      '.webmanifest': 'application/manifest+json', '.js': 'text/javascript',
                      '.json': 'application/json', '.mp3': 'audio/mpeg', '.woff2': 'font/woff2'}
    def log_message(self, fmt, *args):
        if os.environ.get('OYE_QUIET') != '1':
            super().log_message(fmt, *args)

    def end_headers(self):
        if self.path.split('?')[0].endswith(('sw.js', 'version.json')):
            self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    root = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else APP
    srv = http.server.ThreadingHTTPServer(('0.0.0.0', port), functools.partial(Handler, directory=root))
    print(f'Oye on http://localhost:{port}/  (Ctrl+C to stop)', flush=True)
    srv.serve_forever()
