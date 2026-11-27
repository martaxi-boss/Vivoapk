"""Synthetic native probe regression: an admitted finite response needs >8s."""
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    harness = Path(os.environ['VIVOAPK_LOOPBACK_HARNESS'])
    ffprobe = shutil.which('ffprobe')
    if not harness.is_file() or not ffprobe:
        raise RuntimeError('Native fixture tools unavailable')
    with tempfile.TemporaryDirectory(prefix='vivoapk-finite-delay-') as temp:
        folder = Path(temp)
        media = folder / 'generated.mp4'
        subprocess.run([
            'ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=160x90:rate=25', '-f', 'lavfi', '-i',
            'sine=frequency=1000:sample_rate=48000', '-t', '2', '-c:v',
            'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-movflags', '+faststart', str(media),
        ], check=True, capture_output=True, timeout=30)
        body = media.read_bytes()
        if not 0 < len(body) < 2 * 1024 * 1024:
            raise RuntimeError('Generated fixture exceeds bound')
        route = '/vivoapk/' + secrets.token_hex(24) + '/file'
        delayed = threading.Event()
        first_request = [None]

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path != route:
                    self.send_error(404)
                    return
                first, last = 0, len(body) - 1
                header = self.headers.get('Range')
                if header:
                    match = re.fullmatch(r'bytes=([0-9]+)-([0-9]*)', header)
                    if not match:
                        self.send_error(416)
                        return
                    first = int(match[1])
                    last = min(int(match[2]) if match[2] else last, last)
                    if not 0 <= first <= last < len(body):
                        self.send_error(416)
                        return
                if not delayed.is_set():
                    delayed.set()
                    first_request[0] = time.monotonic()
                    time.sleep(9)
                self.send_response(206 if header else 200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(last - first + 1))
                if header:
                    self.send_header('Content-Range', f'bytes {first}-{last}/{len(body)}')
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(body[first:last + 1])

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = folder / 'fixture.json'
            config.write_text(json.dumps({
                'ffprobe': ffprobe,
                'file_probe_uri': f'http://127.0.0.1:{server.server_port}{route}',
            }))
            result = subprocess.run(['dotnet', str(harness), str(config), 'finite-probe'],
                                    capture_output=True, text=True, timeout=65)
            elapsed = time.monotonic() - first_request[0] if first_request[0] else 0
            if (result.returncode != 0 or elapsed < 9
                    or 'VIVOAPK_PRIVATE_DELAYED_FINITE_PROBE=PASS' not in result.stdout):
                raise RuntimeError('Native finite response above the old8sec deadline failed')
            print('VIVOAPK_SYNTHETIC_FINITE_DELAYED_HEADERS_9SEC=PASS', flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    main()
