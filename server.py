import http.server
import socketserver

PORT = 8000
DIRECTORY = r"C:\Users\ALIENWARE\.gemini\antigravity\scratch\stock-trading-sim"

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

if __name__ == '__main__':
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), CustomHandler) as httpd:
        print(f"Serving PostgreEx on http://127.0.0.1:{PORT}")
        httpd.serve_forever()
