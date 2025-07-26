# id_updater.py
#
# This is an upgraded, one-time HTTP server for receiving session info
# from Tampermonkey script based on user-selected mode
# (DirectChat or Battle), and updating it to config.jsonc.

import http.server
import socketserver
import json
import re
import threading
import os
import requests

# --- Configuration ---
HOST = "127.0.0.1"
PORT = 5103
CONFIG_PATH = 'config.jsonc'

def read_config():
    """Read and parse config.jsonc file, remove comments for parsing."""
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ Error: Config file '{CONFIG_PATH}' does not exist.")
        return None
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            # 正则表达式移除行注释和块注释
            content = re.sub(r'//.*', '', f.read())
            content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)
            return json.loads(content)
    except Exception as e:
        print(f"❌ Error reading or parsing '{CONFIG_PATH}': {e}")
        return None

def save_config_value(key, value):
    """
    Safely update a single key-value pair in config.jsonc, preserving original format and comments.
    Only suitable for string or number values.
    """
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()

    # Use regex to safely replace value
    # It finds "key": "any value" and replaces "any value"
        pattern = re.compile(rf'("{key}"\s*:\s*")[^"]*(")')
        new_content, count = pattern.subn(rf'\g<1>{value}\g<2>', content, 1)

        if count == 0:
            print(f"🤔 Warning: Could not find key '{key}' in '{CONFIG_PATH}'.")
            return False

        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    except Exception as e:
        print(f"❌ Error updating '{CONFIG_PATH}': {e}")
        return False

def save_session_ids(session_id, message_id):
    """Update new session ID to config.jsonc file."""
    print(f"\n📝 Attempting to write IDs to '{CONFIG_PATH}'...")
    res1 = save_config_value("session_id", session_id)
    res2 = save_config_value("message_id", message_id)
    if res1 and res2:
        print(f"✅ Successfully updated IDs.")
        print(f"   - session_id: {session_id}")
        print(f"   - message_id: {message_id}")
    else:
        print(f"❌ Failed to update IDs. Please check error messages above.")


class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path == '/update':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data)

                session_id = data.get('sessionId')
                message_id = data.get('messageId')

                if session_id and message_id:
                    print("\n" + "=" * 50)
                    print("🎉 Successfully captured ID from browser!")
                    print(f"  - Session ID: {session_id}")
                    print(f"  - Message ID: {message_id}")
                    print("=" * 50)

                    save_session_ids(session_id, message_id)

                    self.send_response(200)
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(b'{"status": "success"}')

                    print("\nTask complete, server will shut down automatically in 1 second.")
                    threading.Thread(target=self.server.shutdown).start()

                else:
                    self.send_response(400, "Bad Request")
                    self._send_cors_headers()
                    self.end_headers()
                    self.wfile.write(b'{"error": "Missing sessionId or messageId"}')
            except Exception as e:
                self.send_response(500, "Internal Server Error")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(f'{{"error": "Internal server error: {e}"}}'.encode('utf-8'))
        else:
            self.send_response(404, "Not Found")
            self._send_cors_headers()
            self.end_headers()

    def log_message(self, format, *args):
        return

def run_server():
    with socketserver.TCPServer((HOST, PORT), RequestHandler) as httpd:
        print("\n" + "="*50)
        print("  🚀 Session ID update listener started")
        print(f"  - Listening address: http://{HOST}:{PORT}")
        print("  - Please operate LMArena page in browser to trigger ID capture.")
        print("  - After successful capture, this script will close automatically.")
        print("="*50)
        httpd.serve_forever()

def notify_api_server():
    """Notify main API server that ID update process has started."""
    api_server_url = "http://127.0.0.1:5102/internal/start_id_capture"
    try:
        response = requests.post(api_server_url, timeout=3)
        if response.status_code == 200:
            print("✅ Successfully notified main server to activate ID capture mode.")
            return True
        else:
            print(f"⚠️ Failed to notify main server, status code: {response.status_code}.")
            print(f"   - Error message: {response.text}")
            return False
    except requests.ConnectionError:
        print("❌ Unable to connect to main API server. Please ensure api_server.py is running.")
        return False
    except Exception as e:
        print(f"❌ Unknown error occurred while notifying main server: {e}")
        return False

if __name__ == "__main__":
    config = read_config()
    if not config:
        exit(1)

    # --- Get user selection ---
    last_mode = config.get("id_updater_last_mode", "direct_chat")
    mode_map = {"a": "direct_chat", "b": "battle"}
    
    prompt = f"Please select mode [a: DirectChat, b: Battle] (default is last selected: {last_mode}): "
    choice = input(prompt).lower().strip()

    if not choice:
        mode = last_mode
    else:
        mode = mode_map.get(choice)
        if not mode:
            print(f"Invalid input, will use default: {last_mode}")
            mode = last_mode

    save_config_value("id_updater_last_mode", mode)
    print(f"Current mode: {mode.upper()}")
    
    if mode == 'battle':
        last_target = config.get("id_updater_battle_target", "A")
        target_prompt = f"Please select message to update [A (must select A for search model) or B] (default is last selected: {last_target}): "
        target_choice = input(target_prompt).upper().strip()

        if not target_choice:
            target = last_target
        elif target_choice in ["A", "B"]:
            target = target_choice
        else:
            print(f"Invalid input, will use default: {last_target}")
            target = last_target
        
        save_config_value("id_updater_battle_target", target)
        print(f"Battle target: Assistant {target}")
        print("Note: Whether you select A or B, the captured ID will update the main session_id and message_id.")

    # Notify main server before starting listener
    if notify_api_server():
        run_server()
        print("Server closed.")
    else:
        print("\nID update process interrupted due to failure to notify main server.")