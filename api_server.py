# api_server.py
# Next-generation LMArena bridge backend service

import asyncio
import json
import logging
import os
import sys
import subprocess
import time
import uuid
import re
import threading
import random
import mimetypes
from datetime import datetime
from contextlib import asynccontextmanager

import uvicorn
import requests
from packaging.version import parse as parse_version
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response

# --- Import custom modules ---
from modules import image_generation

# --- Basic configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Global state and configuration ---
CONFIG = {} # Stores configuration loaded from config.jsonc
# browser_ws stores the WebSocket connection to a single Tampermonkey script.
# Note: This architecture assumes only one browser tab is active.
# To support multiple concurrent tabs, extend this to a dictionary managing multiple connections.
browser_ws: WebSocket | None = None
# response_channels stores the response queue for each API request.
# Key is request_id, value is asyncio.Queue.
response_channels: dict[str, asyncio.Queue] = {}
last_activity_time = None # Records the last activity time
idle_monitor_thread = None # Idle monitor thread
main_event_loop = None # Main event loop

# --- Model mapping ---
MODEL_NAME_TO_ID_MAP = {}
MODEL_ENDPOINT_MAP = {} # Added: stores model to session/message ID mapping
DEFAULT_MODEL_ID = None # Default model: Claude 3.5 Sonnet

def load_model_endpoint_map():
    """Load model to endpoint mapping from model_endpoint_map.json."""
    global MODEL_ENDPOINT_MAP
    try:
        with open('model_endpoint_map.json', 'r', encoding='utf-8') as f:
            content = f.read()
            # Allow empty file
            if not content.strip():
                MODEL_ENDPOINT_MAP = {}
            else:
                MODEL_ENDPOINT_MAP = json.loads(content)
        logger.info(f"Successfully loaded {len(MODEL_ENDPOINT_MAP)} model endpoint mappings from 'model_endpoint_map.json'.")
    except FileNotFoundError:
        MODEL_ENDPOINT_MAP = {}
        logger.warning("'model_endpoint_map.json' file not found. Using empty mapping.")
    except json.JSONDecodeError as err:
        MODEL_ENDPOINT_MAP = {}
        logger.error(f"Failed to load or parse 'model_endpoint_map.json': {err}. Using empty mapping.")

def load_config():
    """Load configuration from config.jsonc, handling JSONC comments."""
    global CONFIG
    try:
        with open('config.jsonc', 'r', encoding='utf-8') as f:
            content = f.read()
            # Remove // line comments and /* */ block comments
            json_content = re.sub(r'//.*', '', content)
            json_content = re.sub(r'/\*.*?\*/', '', json_content, flags=re.DOTALL)
            CONFIG = json.loads(json_content)
        logger.info("Successfully loaded configuration from 'config.jsonc'.")
        # Print key configuration states
        logger.info(f"  - Tavern Mode: {'✅ Enabled' if CONFIG.get('tavern_mode_enabled') else '❌ Disabled'}")
        logger.info(f"  - Bypass Mode: {'✅ Enabled' if CONFIG.get('bypass_enabled') else '❌ Disabled'}")
    except (FileNotFoundError, json.JSONDecodeError) as err:
        CONFIG = {}
        logger.error(f"Failed to load or parse 'config.jsonc': {err}. Using default configuration.")

def load_model_map():
    """Load model mapping from models.json."""
    global MODEL_NAME_TO_ID_MAP
    try:
        with open('models.json', 'r', encoding='utf-8') as f:
            MODEL_NAME_TO_ID_MAP = json.load(f)
        logger.info(f"Successfully loaded {len(MODEL_NAME_TO_ID_MAP)} models from 'models.json'.")
    except (FileNotFoundError, json.JSONDecodeError) as err:
        MODEL_NAME_TO_ID_MAP = {}
        logger.error(f"Failed to load 'models.json': {err}. Using empty model list.")

 # --- Update check ---
GITHUB_REPO = "Lianues/LMArenaBridge"

def download_and_extract_update(version):
    """Download and extract the latest version to a temporary folder."""
    update_dir = "update_temp"
    if not os.path.exists(update_dir):
        os.makedirs(update_dir)

    try:
        zip_url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"
        logger.info(f"Downloading new version from {zip_url} ...")
        response = requests.get(zip_url, timeout=60)
        response.raise_for_status()

        # Need to import zipfile and io
        import zipfile
        import io
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            z.extractall(update_dir)

        logger.info(f"New version successfully downloaded and extracted to '{update_dir}' folder.")
        return True
    except requests.RequestException as e:
        logger.error(f"Failed to download update: {e}")
    except Exception as e:
        # Handle zipfile.BadZipFile and other exceptions
        if 'zipfile' in str(type(e)):
            logger.error("Downloaded file is not a valid zip archive.")
        else:
            logger.error(f"Unknown error occurred while extracting update: {e}")
    return False

def check_for_updates():
    """Check for new version from GitHub."""
    if not CONFIG.get("enable_auto_update", True):
        logger.info("Auto-update is disabled, skipping check.")
        return

    current_version = CONFIG.get("version", "0.0.0")
    logger.info(f"Current version: {current_version}. Checking for updates from GitHub...")

    try:
        config_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/config.jsonc"
        response = requests.get(config_url, timeout=10)
        response.raise_for_status()

        jsonc_content = response.text
        json_content = re.sub(r'//.*', '', jsonc_content)
        json_content = re.sub(r'/\*.*?\*/', '', json_content, flags=re.DOTALL)
        remote_config = json.loads(json_content)
        
        remote_version_str = remote_config.get("version")
        if not remote_version_str:
            logger.warning("No version found in remote config file, skipping update check.")
            return

        if parse_version(remote_version_str) > parse_version(current_version):
            logger.info("="*60)
            logger.info(f"🎉 New version found! 🎉")
            logger.info(f"  - Current version: {current_version}")
            logger.info(f"  - Latest version: {remote_version_str}")
            if download_and_extract_update(remote_version_str):
                logger.info("Preparing to apply update. Server will shut down in 5 seconds and start update script.")
                time.sleep(5)
                update_script_path = os.path.join("modules", "update_script.py")
                # Use Popen to start a separate process
                subprocess.Popen([sys.executable, update_script_path])
                # Gracefully exit the current server process
                os._exit(0)
            else:
                logger.error(f"Auto-update failed. Please visit https://github.com/{GITHUB_REPO}/releases/latest to download manually.")
            logger.info("="*60)
        else:
            logger.info("Your program is already up to date.")

    except requests.RequestException as e:
        logger.error(f"Failed to check for updates: {e}")
    except json.JSONDecodeError:
        logger.error("Failed to parse remote config file.")
    except Exception as e:
        logger.error(f"Unknown error occurred while checking for updates: {e}")

# --- Model Updates ---
def extract_models_from_html(html_content):
    """
    Extract model data from HTML content using a more robust parsing method.
    """
    script_contents = re.findall(r'<script>(.*?)</script>', html_content, re.DOTALL)
    
    for script_content in script_contents:
        if 'self.__next_f.push' in script_content and 'initialState' in script_content and 'publicName' in script_content:
            match = re.search(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', script_content, re.DOTALL)
            if not match:
                continue
            
            full_payload = match.group(1)
            
            payload_string = full_payload.split('\\n')[0]
            
            json_start_index = payload_string.find(':')
            if json_start_index == -1:
                continue
            
            json_string_with_escapes = payload_string[json_start_index + 1:]
            json_string = json_string_with_escapes.replace('\\"', '"')
            
            try:
                data = json.loads(json_string)
                
                def find_initial_state(obj):
                    if isinstance(obj, dict):
                        for key, value in obj.items():
                            if key == 'initialState' and isinstance(value, list):
                                if value and isinstance(value[0], dict) and 'publicName' in value[0]:
                                    return value
                            result = find_initial_state(value)
                            if result is not None:
                                return result
                    elif isinstance(obj, list):
                        for item in obj:
                            result = find_initial_state(item)
                            if result is not None:
                                return result
                    return None

                models = find_initial_state(data)
                if models:
                    logger.info(f"Successfully extracted {len(models)} models from the script block.")
                    return models
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing the extracted JSON string: {e}")
                continue

    logger.error("Error: No script block containing valid model data found in HTML response.")
    return None

def compare_and_update_models(new_models_list, models_path):
    """
    Compare new and old model lists, print differences, and update local models.json file with new list.
    """
    try:
        with open(models_path, 'r', encoding='utf-8') as f:
            old_models = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        old_models = {}

    new_models_dict = {model['publicName']: model for model in new_models_list if 'publicName' in model}
    old_models_set = set(old_models.keys())
    new_models_set = set(new_models_dict.keys())

    added_models = new_models_set - old_models_set
    removed_models = old_models_set - new_models_set
    
    logger.info("--- Model list update check ---")
    has_changes = False

    if added_models:
        has_changes = True
        logger.info("\n[+] New models added:")
        for name in sorted(list(added_models)):
            model = new_models_dict[name]
            logger.info(f"  - Name: {name}, ID: {model.get('id')}, Organization: {model.get('organization', 'N/A')}")

    if removed_models:
        has_changes = True
        logger.info("\n[-] Models deleted:")
        for name in sorted(list(removed_models)):
            logger.info(f"  - Name: {name}, ID: {old_models.get(name)}")

    logger.info("\n[*] Common model check:")
    changed_models = 0
    for name in sorted(list(new_models_set.intersection(old_models_set))):
        new_id = new_models_dict[name].get('id')
        old_id = old_models.get(name)
        if new_id != old_id:
            has_changes = True
            changed_models += 1
            logger.info(f"  - ID changed: '{name}' old ID: {old_id} -> new ID: {new_id}")
    
    if changed_models == 0:
        logger.info("  - No change in common model IDs.")

    if not has_changes:
        logger.info("\nConclusion: No changes in the model list, no need to update the file.")
        logger.info("--- Check complete ---")
        return

    logger.info("\nConclusion: Model changes detected, updating 'models.json'...")
    updated_model_map = {model['publicName']: model.get('id') for model in new_models_list if 'publicName' in model and 'id' in model}
    try:
        with open(models_path, 'w', encoding='utf-8') as f:
            json.dump(updated_model_map, f, indent=4, ensure_ascii=False)
        logger.info(f"'{models_path}' successfully updated, containing {len(updated_model_map)} models.")
        load_model_map()
    except IOError as e:
        logger.error(f"Error writing to '{models_path}' file: {e}")
    
    logger.info("--- Check and update complete ---")

# --- Auto-restart Logic ---
def restart_server():
    """Gracefully notify client to refresh, then restart server."""
    logger.warning("="*60)
    logger.warning("Server idle timeout detected, preparing to restart automatically...")
    logger.warning("="*60)
    
    # 1. (Async) Notify browser to refresh
    async def notify_browser_refresh():
        if browser_ws:
            try:
                # Send 'reconnect' command first so frontend knows this is a planned restart
                await browser_ws.send_text(json.dumps({"command": "reconnect"}, ensure_ascii=False))
                logger.info("'reconnect' command sent to browser.")
            except Exception as e:
                logger.error(f"Failed to send 'reconnect' command: {e}")
    
    # Run async notification function in main event loop
    # Use `asyncio.run_coroutine_threadsafe` to ensure thread safety
    if browser_ws and browser_ws.client_state.name == 'CONNECTED' and main_event_loop:
        asyncio.run_coroutine_threadsafe(notify_browser_refresh(), main_event_loop)
    
    # 2. Delay a few seconds to ensure message is sent
    time.sleep(3)
    
    # 3. Execute restart
    logger.info("Restarting server...")
    os.execv(sys.executable, ['python'] + sys.argv)

def idle_monitor():
    """Run in background thread to monitor if server is idle."""
    global last_activity_time
    
    # Wait until last_activity_time is first set
    while last_activity_time is None:
        time.sleep(1)
        
    logger.info("Idle monitoring thread started.")
    
    while True:
        if CONFIG.get("enable_idle_restart", False):
            timeout = CONFIG.get("idle_restart_timeout_seconds", 300)
            
            # If timeout is set to -1, disable restart check
            if timeout == -1:
                time.sleep(10) # Still need to sleep to avoid busy loop
                continue

            idle_time = (datetime.now() - last_activity_time).total_seconds()
            
            if idle_time > timeout:
                logger.info(f"Server idle time ({idle_time:.0f}s) has exceeded threshold ({timeout}s).")
                restart_server()
                break # Exit loop because process is about to be replaced
                
        # Check every 10 seconds
        time.sleep(10)

# --- FastAPI Lifecycle Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle function run at server startup."""
    global idle_monitor_thread, last_activity_time, main_event_loop
    main_event_loop = asyncio.get_running_loop() # Get main event loop
    load_config() # Load configuration first
    
    # --- Print current operation mode ---
    mode = CONFIG.get("id_updater_last_mode", "direct_chat")
    target = CONFIG.get("id_updater_battle_target", "A")
    logger.info("="*60)
    logger.info(f"  Current operation mode: {mode.upper()}")
    if mode == 'battle':
        logger.info(f"  - Battle mode target: Assistant {target}")
    logger.info("  (Mode can be changed by running id_updater.py)")
    logger.info("="*60)

    check_for_updates() # Check for program updates
    load_model_map() # Load model IDs from models.json
    load_model_endpoint_map() # Load model endpoint mapping
    logger.info("Server started. Waiting for Tampermonkey script to connect...")

    # Mark activity time starting point after model update
    last_activity_time = datetime.now()
    
    # Start idle monitoring thread
    if CONFIG.get("enable_idle_restart", False):
        idle_monitor_thread = threading.Thread(target=idle_monitor, daemon=True)
        idle_monitor_thread.start()
        
    # --- Initialize custom modules ---
    image_generation.initialize_image_module(
        app_logger=logger,
        channels=response_channels,
        app_config=CONFIG,
        model_map=MODEL_NAME_TO_ID_MAP,
        default_model_id=DEFAULT_MODEL_ID
    )

    yield
    logger.info("Server is shutting down.")

app = FastAPI(lifespan=lifespan)

# --- CORS Middleware Configuration ---
# Allow all origins, all methods, all headers, which is safe for local development tools.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Helper Functions ---
def save_config():
    """Write the current CONFIG object back to config.jsonc, preserving comments."""
    try:
        # Read original file to preserve comments etc.
        with open('config.jsonc', 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # Use regex to safely replace values
        def replacer(key, value, content):
            # This regex will find the key, then match its value part until comma or right brace
            pattern = re.compile(rf'("{key}"\s*:\s*").*?("?)(,?\s*)$', re.MULTILINE)
            replacement = rf'\g<1>{value}\g<2>\g<3>'
            if not pattern.search(content): # If key doesn't exist, add to end of file (simplified handling)
                 content = re.sub(r'}\s*$', f'  ,"{key}": "{value}"\n}}', content)
            else:
                 content = pattern.sub(replacement, content)
            return content

        content_str = "".join(lines)
        content_str = replacer("session_id", CONFIG["session_id"], content_str)
        content_str = replacer("message_id", CONFIG["message_id"], content_str)
        
        with open('config.jsonc', 'w', encoding='utf-8') as f:
            f.write(content_str)
        logger.info("✅ Successfully updated session info to config.jsonc.")
    except Exception as e:
        logger.error(f"❌ Error writing to config.jsonc: {e}", exc_info=True)


def _process_openai_message(message: dict) -> dict:
    """
    Process OpenAI messages, separate text and attachments.
    - Decompose multimodal content list into pure text and attachments.
    - Ensure empty content for user role is replaced with a space to avoid LMArena errors.
    - Generate basic structure for attachments.
    """
    content = message.get("content")
    role = message.get("role")
    attachments = []
    text_content = ""

    if isinstance(content, list):
        
        text_parts = []
        for part in content:
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif part.get("type") == "image_url":
                image_url_data = part.get("image_url", {})
                url = image_url_data.get("url")

                # New logic: allow client to pass original filename via detail field
                # detail field is part of OpenAI Vision API, we reuse it here
                original_filename = image_url_data.get("detail")

                if url and url.startswith("data:"):
                    try:
                        content_type = url.split(';')[0].split(':')[1]
                        
                        # If client provides original filename, use it directly
                        if original_filename and isinstance(original_filename, str):
                            file_name = original_filename
                            logger.info(f"Successfully processed an attachment (using original filename): {file_name}")
                        else:
                            # Otherwise, fallback to old UUID-based naming logic
                            main_type, sub_type = content_type.split('/') if '/' in content_type else ('application', 'octet-stream')
                            
                            if main_type == "image": prefix = "image"
                            elif main_type == "audio": prefix = "audio"
                            else: prefix = "file"
                            
                            guessed_extension = mimetypes.guess_extension(content_type)
                            if guessed_extension:
                                file_extension = guessed_extension.lstrip('.')
                            else:
                                file_extension = sub_type if len(sub_type) < 20 else 'bin'
                            
                            file_name = f"{prefix}_{uuid.uuid4()}.{file_extension}"
                            logger.info(f"Successfully processed an attachment (generated filename): {file_name}")

                        attachments.append({
                            "name": file_name,
                            "contentType": content_type,
                            "url": url
                        })
                    except (IndexError, ValueError) as e:
                        logger.warning(f"Unable to parse base64 data URI: {url[:60]}... Error: {e}")

        text_content = "\n\n".join(text_parts)
    elif isinstance(content, str):
        text_content = content

    
    if role == "user" and not text_content.strip():
        text_content = " "

    return {
        "role": role,
        "content": text_content,
        "attachments": attachments
    }

def convert_openai_to_lmarena_payload(openai_data: dict, session_id: str, message_id: str, mode_override: str = None, battle_target_override: str = None) -> dict:
    """
    Convert OpenAI request body to simplified payload required by Tampermonkey script, and apply Tavern mode, Bypass mode, and Battle mode.
    Added mode override parameter to support model-specific session modes.
    """
    # 1. Normalize roles and process messages
    #    - Convert non-standard 'developer' role to 'system' for better compatibility.
    #    - Separate text and attachments.
    messages = openai_data.get("messages", [])
    for msg in messages:
        if msg.get("role") == "developer":
            msg["role"] = "system"
            logger.info("Message role normalization: convert 'developer' to 'system'.")
            
    processed_messages = [_process_openai_message(msg.copy()) for msg in messages]

    # 2. Apply Tavern Mode
    if CONFIG.get("tavern_mode_enabled"):
        system_prompts = [msg['content'] for msg in processed_messages if msg['role'] == 'system']
        other_messages = [msg for msg in processed_messages if msg['role'] != 'system']
        
        merged_system_prompt = "\n\n".join(system_prompts)
        final_messages = []
        
        if merged_system_prompt:
            # System messages should not have attachments
            final_messages.append({"role": "system", "content": merged_system_prompt, "attachments": []})
        
        final_messages.extend(other_messages)
        processed_messages = final_messages

    # 3. Determine target model ID
    model_name = openai_data.get("model", "claude-3-5-sonnet-20241022")
    target_model_id = None # Force modelId to be null
    
    # 4. Build message templates
    message_templates = []
    for msg in processed_messages:
        message_templates.append({
            "role": msg["role"],
            "content": msg.get("content", ""),
            "attachments": msg.get("attachments", [])
        })

    # 5. Apply Bypass Mode
    if CONFIG.get("bypass_enabled"):
        # Bypass mode always adds a user message with position 'a'
        message_templates.append({"role": "user", "content": " ", "participantPosition": "a", "attachments": []})

    # 6. Apply Participant Position
    # Prioritize override mode, otherwise fall back to global configuration
    mode = mode_override or CONFIG.get("id_updater_last_mode", "direct_chat")
    target_participant = battle_target_override or CONFIG.get("id_updater_battle_target", "A")
    target_participant = target_participant.lower() # Ensure lowercase

    logger.info(f"Setting participant positions according to mode '{mode}' (target: {target_participant if mode == 'battle' else 'N/A'})...")

    for msg in message_templates:
        if msg['role'] == 'system':
            if mode == 'battle':
                # Battle mode: system and user-selected assistant on same side (A→a, B→b)
                msg['participantPosition'] = target_participant
            else:
                # DirectChat mode: system fixed as 'b'
                msg['participantPosition'] = 'b'
        elif mode == 'battle':
            # In Battle mode, non-system messages use user-selected target participant
            msg['participantPosition'] = target_participant
        else: # DirectChat mode
            # In DirectChat mode, non-system messages use default 'a'
            msg['participantPosition'] = 'a'

    return {
        "message_templates": message_templates,
        "target_model_id": target_model_id,
        "session_id": session_id,
        "message_id": message_id
    }

# --- OpenAI Formatting Helper Functions (Ensure robust JSON serialization) ---
def format_openai_chunk(content: str, model: str, request_id: str) -> str:
    """Format as OpenAI streaming chunk."""
    chunk = {
        "id": request_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

def format_openai_finish_chunk(model: str, request_id: str, reason: str = 'stop') -> str:
    """Format as OpenAI finish chunk."""
    chunk = {
        "id": request_id, "object": "chat.completion.chunk",
        "created": int(time.time()), "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": reason}]
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"

def format_openai_error_chunk(error_message: str, model: str, request_id: str) -> str:
    """Format as OpenAI error chunk."""
    content = f"\n\n[LMArena Bridge Error]: {error_message}"
    return format_openai_chunk(content, model, request_id)

def format_openai_non_stream_response(content: str, model: str, request_id: str, reason: str = 'stop') -> dict:
    """Build non-streaming response body compliant with OpenAI specification."""
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": reason,
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(content) // 4,
            "total_tokens": len(content) // 4,
        },
    }

async def _process_lmarena_stream(request_id: str):
    """
    Core internal generator: process raw data stream from browser and generate structured events.
    Event types: ('content', str), ('finish', str), ('error', str)
    """
    queue = response_channels.get(request_id)
    if not queue:
        logger.error(f"PROCESSOR [ID: {request_id[:8]}]: Unable to find response channel.")
        yield 'error', 'Internal server error: response channel not found.'
        return

    buffer = ""
    timeout = CONFIG.get("stream_response_timeout_seconds",360)
    text_pattern = re.compile(r'[ab]0:"((?:\\.|[^"\\])*)"')
    finish_pattern = re.compile(r'[ab]d:(\{.*?"finishReason".*?\})')
    error_pattern = re.compile(r'(\{\s*"error".*?\})', re.DOTALL)
    cloudflare_patterns = [r'<title>Just a moment...</title>', r'Enable JavaScript and cookies to continue']

    try:
        while True:
            try:
                raw_data = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"PROCESSOR [ID: {request_id[:8]}]: Waiting for browser data timed out ({timeout} seconds).")
                yield 'error', f'Response timed out after {timeout} seconds.'
                return

            # 1. Check for direct errors or termination signals from WebSocket side
            if isinstance(raw_data, dict) and 'error' in raw_data:
                error_msg = raw_data.get('error', 'Unknown browser error')
                
                # Enhanced error handling
                if isinstance(error_msg, str):
                    # 1. Check for 413 attachment too large error
                    if '413' in error_msg or 'too large' in error_msg.lower():
                        friendly_error_msg = "Upload failed: Attachment size exceeds LMArena server limit (usually around 5MB). Please try compressing or uploading a smaller file."
                        logger.warning(f"PROCESSOR [ID: {request_id[:8]}]: Attachment too large error detected (413).")
                        yield 'error', friendly_error_msg
                        return

                    # 2. Check for Cloudflare verification page
                    if any(re.search(p, error_msg, re.IGNORECASE) for p in cloudflare_patterns):
                        friendly_error_msg = "Cloudflare human verification page detected. Please refresh the LMArena page in your browser and complete the verification manually, then retry the request."
                        if browser_ws:
                            try:
                                await browser_ws.send_text(json.dumps({"command": "refresh"}, ensure_ascii=False))
                                logger.info(f"PROCESSOR [ID: {request_id[:8]}]: Cloudflare detected in error message, refresh command sent.")
                            except Exception as e:
                                logger.error(f"PROCESSOR [ID: {request_id[:8]}]: Failed to send refresh command: {e}")
                        yield 'error', friendly_error_msg
                        return

                # 3. Other unknown errors
                yield 'error', error_msg
                return
            if raw_data == "[DONE]":
                break

            buffer += "".join(str(item) for item in raw_data) if isinstance(raw_data, list) else raw_data

            if any(re.search(p, buffer, re.IGNORECASE) for p in cloudflare_patterns):
                error_msg = "Cloudflare human verification page detected. Please refresh the LMArena page in your browser and complete the verification manually, then retry the request."
                if browser_ws:
                    try:
                        await browser_ws.send_text(json.dumps({"command": "refresh"}, ensure_ascii=False))
                        logger.info(f"PROCESSOR [ID: {request_id[:8]}]: Refresh command sent to browser.")
                    except Exception as e:
                        logger.error(f"PROCESSOR [ID: {request_id[:8]}]: Failed to send refresh command: {e}")
                yield 'error', error_msg
                return
            
            if (error_match := error_pattern.search(buffer)):
                try:
                    error_json = json.loads(error_match.group(1))
                    yield 'error', error_json.get("error", "Unknown error from LMArena")
                    return
                except json.JSONDecodeError: pass

            while (match := text_pattern.search(buffer)):
                try:
                    text_content = json.loads(f'"{match.group(1)}"')
                    if text_content: yield 'content', text_content
                except (ValueError, json.JSONDecodeError): pass
                buffer = buffer[match.end():]

            if (finish_match := finish_pattern.search(buffer)):
                try:
                    finish_data = json.loads(finish_match.group(1))
                    yield 'finish', finish_data.get("finishReason", "stop")
                except (json.JSONDecodeError, IndexError): pass
                buffer = buffer[finish_match.end():]

    except asyncio.CancelledError:
        logger.info(f"PROCESSOR [ID: {request_id[:8]}]: Task cancelled.")
    finally:
        if request_id in response_channels:
            del response_channels[request_id]
            logger.info(f"PROCESSOR [ID: {request_id[:8]}]: Response channel cleaned up.")

async def stream_generator(request_id: str, model: str):
    """Format internal event stream as OpenAI SSE response."""
    response_id = f"chatcmpl-{uuid.uuid4()}"
    logger.info(f"STREAMER [ID: {request_id[:8]}]: Stream generator started.")
    
    finish_reason_to_send = 'stop'  # Default finish reason

    async for event_type, data in _process_lmarena_stream(request_id):
        if event_type == 'content':
            yield format_openai_chunk(data, model, response_id)
        elif event_type == 'finish':
            # Record finish reason, but don't return immediately, wait for browser to send [DONE]
            finish_reason_to_send = data
            if data == 'content-filter':
                warning_msg = "\n\nResponse was terminated, possibly due to context limit exceeded or model internal censorship (most likely)"
                yield format_openai_chunk(warning_msg, model, response_id)
        elif event_type == 'error':
            logger.error(f"STREAMER [ID: {request_id[:8]}]: Error occurred in stream: {data}")
            yield format_openai_error_chunk(str(data), model, response_id)
            yield format_openai_finish_chunk(model, response_id, reason='stop')
            return # Can terminate immediately when error occurs

    # Only execute after _process_lmarena_stream naturally ends (i.e., received [DONE])
    yield format_openai_finish_chunk(model, response_id, reason=finish_reason_to_send)
    logger.info(f"STREAMER [ID: {request_id[:8]}]: Stream generator ended normally.")

async def non_stream_response(request_id: str, model: str):
    """Aggregate internal event stream and return single OpenAI JSON response."""
    response_id = f"chatcmpl-{uuid.uuid4()}"
    logger.info(f"NON-STREAM [ID: {request_id[:8]}]: Start processing non-stream response.")
    
    full_content = []
    finish_reason = "stop"
    
    async for event_type, data in _process_lmarena_stream(request_id):
        if event_type == 'content':
            full_content.append(data)
        elif event_type == 'finish':
            finish_reason = data
            if data == 'content-filter':
                full_content.append("\n\nResponse was terminated, possibly due to context limit exceeded or model internal censorship (most likely)")
            # Don't break here, continue waiting for [DONE] signal from browser to avoid race conditions
        elif event_type == 'error':
            logger.error(f"NON-STREAM [ID: {request_id[:8]}]: Error occurred during processing: {data}")
            
            # Unify error status codes for streaming and non-streaming responses
            status_code = 413 if "attachment size exceeds" in str(data).lower() else 500

            error_response = {
                "error": {
                    "message": f"[LMArena Bridge Error]: {data}",
                    "type": "bridge_error",
                    "code": "attachment_too_large" if status_code == 413 else "processing_error"
                }
            }
            return Response(content=json.dumps(error_response, ensure_ascii=False), status_code=status_code, media_type="application/json")

    final_content = "".join(full_content)
    response_data = format_openai_non_stream_response(final_content, model, response_id, reason=finish_reason)
    
    logger.info(f"NON-STREAM [ID: {request_id[:8]}]: Response aggregation complete.")
    return Response(content=json.dumps(response_data, ensure_ascii=False), media_type="application/json")

# --- WebSocket 端点 ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connection from Tampermonkey script."""
    global browser_ws
    await websocket.accept()
    if browser_ws is not None:
        logger.warning("New Tampermonkey script connection detected, old connection will be replaced.")
    logger.info("✅ Tampermonkey script successfully connected to WebSocket.")
    browser_ws = websocket
    try:
        while True:
            # Wait and receive messages from Tampermonkey script
            message_str = await websocket.receive_text()
            message = json.loads(message_str)
            
            request_id = message.get("request_id")
            data = message.get("data")

            if not request_id or data is None:
                logger.warning(f"Received invalid message from browser: {message}")
                continue

            # Put received data into corresponding response channel
            if request_id in response_channels:
                await response_channels[request_id].put(data)
            else:
                logger.warning(f"⚠️ Received response for unknown or closed request: {request_id}")

    except WebSocketDisconnect:
        logger.warning("❌ Tampermonkey script client disconnected.")
    except Exception as e:
        logger.error(f"Unknown error occurred during WebSocket handling: {e}", exc_info=True)
    finally:
        browser_ws = None
        # Clean up all waiting response channels to prevent requests from hanging
        for queue in response_channels.values():
            await queue.put({"error": "Browser disconnected during operation"})
        response_channels.clear()
        logger.info("WebSocket connection cleaned up.")

# --- Model Update Endpoint ---
@app.post("/update_models")
async def update_models_endpoint(request: Request):
    """
    Receive page HTML from Tampermonkey script, extract and update model list.
    """
    html_content = await request.body()
    if not html_content:
        logger.warning("Model update request received no HTML content.")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "No HTML content received."}
        )
    
    logger.info("Received page content from Tampermonkey script, starting to check and update models...")
    new_models_list = extract_models_from_html(html_content.decode('utf-8'))
    
    if new_models_list:
        compare_and_update_models(new_models_list, 'models.json')
        # load_model_map() is now called inside compare_and_update_models
        return JSONResponse({"status": "success", "message": "Model comparison and update complete."})
    else:
        logger.error("Failed to extract model data from HTML provided by Tampermonkey script.")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Could not extract model data from HTML."}
        )

# --- OpenAI Compatible API Endpoints ---
@app.get("/v1/models")
async def get_models():
    """Provide OpenAI-compatible model list."""
    if not MODEL_NAME_TO_ID_MAP:
        return JSONResponse(
            status_code=404,
            content={"error": "Model list is empty or 'models.json' not found."}
        )
    
    return {
        "object": "list",
        "data": [
            {
                "id": model_name, 
                "object": "model",
                "created": int(asyncio.get_event_loop().time()), 
                "owned_by": "LMArenaBridge"
            }
            for model_name in MODEL_NAME_TO_ID_MAP.keys()
        ],
    }

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    Handle chat completion request.
    Receive OpenAI format request, convert to LMArena format,
    send to Tampermonkey script via WebSocket, then return result as stream.
    """
    global last_activity_time
    last_activity_time = datetime.now() # Update activity time
    logger.info(f"API request received, activity time updated to: {last_activity_time.strftime('%Y-%m-%d %H:%M:%S')}")

    load_config()  # Load latest configuration in real-time to ensure session ID and other info are up-to-date
    # --- API Key Verification ---
    api_key = CONFIG.get("api_key")
    if api_key:
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            raise HTTPException(
                status_code=401,
                detail="API Key not provided. Please provide in Authorization header as 'Bearer YOUR_KEY'."
            )
        
        provided_key = auth_header.split(' ')[1]
        if provided_key != api_key:
            raise HTTPException(
                status_code=401,
                detail="Provided API Key is incorrect."
            )

    if not browser_ws:
        raise HTTPException(status_code=503, detail="Tampermonkey script client not connected. Please ensure LMArena page is open and script is active.")

    try:
        openai_req = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON request body")

    # --- Model to Session ID Mapping Logic ---
    model_name = openai_req.get("model")
    session_id, message_id = None, None
    mode_override, battle_target_override = None, None

    if model_name and model_name in MODEL_ENDPOINT_MAP:
        mapping_entry = MODEL_ENDPOINT_MAP[model_name]
        selected_mapping = None

        if isinstance(mapping_entry, list) and mapping_entry:
            selected_mapping = random.choice(mapping_entry)
            logger.info(f"Randomly selected a mapping from ID list for model '{model_name}'.")
        elif isinstance(mapping_entry, dict):
            selected_mapping = mapping_entry
            logger.info(f"Found a single endpoint mapping for model '{model_name}' (old format).")
        
        if selected_mapping:
            session_id = selected_mapping.get("session_id")
            message_id = selected_mapping.get("message_id")
            # Key: also get mode information
            mode_override = selected_mapping.get("mode") # May be None
            battle_target_override = selected_mapping.get("battle_target") # May be None
            log_msg = f"Will use Session ID: ...{session_id[-6:] if session_id else 'N/A'}"
            if mode_override:
                log_msg += f" (Mode: {mode_override}"
                if mode_override == 'battle':
                    log_msg += f", Target: {battle_target_override or 'A'}"
                log_msg += ")"
            logger.info(log_msg)

    # If session_id is still None after above processing, enter global fallback logic
    if not session_id:
        if CONFIG.get("use_default_ids_if_mapping_not_found", True):
            session_id = CONFIG.get("session_id")
            message_id = CONFIG.get("message_id")
            # When using global ID, don't set mode override, let it use global configuration
            mode_override, battle_target_override = None, None
            logger.info(f"Model '{model_name}' not found in mapping, using global default Session ID: ...{session_id[-6:] if session_id else 'N/A'}")
        else:
            logger.error(f"Model '{model_name}' not found in 'model_endpoint_map.json' with valid mapping, and fallback to default ID is disabled.")
            raise HTTPException(
                status_code=400,
                detail=f"Model '{model_name}' does not have a configured independent session ID. Please add a valid mapping in 'model_endpoint_map.json' or enable 'use_default_ids_if_mapping_not_found' in 'config.jsonc'."
            )

    # --- Validate final determined session information ---
    if not session_id or not message_id or "YOUR_" in session_id or "YOUR_" in message_id:
        raise HTTPException(
            status_code=400,
            detail="Final session ID or message ID is invalid. Please check configuration in 'model_endpoint_map.json' and 'config.jsonc', or run `id_updater.py` to update defaults."
        )

    if not model_name or model_name not in MODEL_NAME_TO_ID_MAP:
        logger.warning(f"Requested model '{model_name}' not in models.json, will use default model ID.")

    request_id = str(uuid.uuid4())
    response_channels[request_id] = asyncio.Queue()
    logger.info(f"API CALL [ID: {request_id[:8]}]: Response channel created.")

    try:
        # 1. Convert request, passing in possible mode override information
        lmarena_payload = convert_openai_to_lmarena_payload(
            openai_req,
            session_id,
            message_id,
            mode_override=mode_override,
            battle_target_override=battle_target_override
        )
        
        # 2. Wrap as message to send to browser
        message_to_browser = {
            "request_id": request_id,
            "payload": lmarena_payload
        }
        
        # 3. Send via WebSocket
        logger.info(f"API CALL [ID: {request_id[:8]}]: Sending payload to Tampermonkey script via WebSocket.")
        await browser_ws.send_text(json.dumps(message_to_browser))

        # 4. Decide return type based on stream parameter
        is_stream = openai_req.get("stream", True)

        if is_stream:
            # Return streaming response
            return StreamingResponse(
                stream_generator(request_id, model_name or "default_model"),
                media_type="text/event-stream"
            )
        else:
            # Return non-streaming response
            return await non_stream_response(request_id, model_name or "default_model")
    except Exception as e:
        # If error occurs during setup, clean up channel
        if request_id in response_channels:
            del response_channels[request_id]
        logger.error(f"API CALL [ID: {request_id[:8]}]: Fatal error occurred while processing request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/images/generations")
async def images_generations(request: Request):
    """
    Handle text-to-image request.
    This endpoint receives OpenAI format image generation requests and returns corresponding image URLs.
    """
    global last_activity_time
    last_activity_time = datetime.now()
    logger.info(f"Text-to-image API request received, activity time updated to: {last_activity_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Module has been initialized via `initialize_image_module`, can call directly
    response_data, status_code = await image_generation.handle_image_generation_request(request, browser_ws)
    
    return JSONResponse(content=response_data, status_code=status_code)

# --- Internal Communication Endpoints ---
@app.post("/internal/start_id_capture")
async def start_id_capture():
    """
    Receive notification from id_updater.py and activate Tampermonkey script's ID capture mode via WebSocket command.
    """
    if not browser_ws:
        logger.warning("ID CAPTURE: Activation request received, but no browser connected.")
        raise HTTPException(status_code=503, detail="Browser client not connected.")
    
    try:
        logger.info("ID CAPTURE: Activation request received, sending command via WebSocket...")
        await browser_ws.send_text(json.dumps({"command": "activate_id_capture"}))
        logger.info("ID CAPTURE: Activation command sent successfully.")
        return JSONResponse({"status": "success", "message": "Activation command sent."})
    except Exception as e:
        logger.error(f"ID CAPTURE: Error sending activation command: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send command via WebSocket.")


# --- Main Program Entry Point ---
if __name__ == "__main__":
    # Recommended to read port from config.jsonc, temporary hardcoded here
    api_port = 5102
    logger.info(f"🚀 LMArena Bridge v2.0 API server is starting...")
    logger.info(f"   - Listening address: http://127.0.0.1:{api_port}")
    logger.info(f"   - WebSocket endpoint: ws://127.0.0.1:{api_port}/ws")
    
    uvicorn.run(app, host="0.0.0.0", port=api_port)
