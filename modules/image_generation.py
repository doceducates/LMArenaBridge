# modules/image_generation.py

import asyncio
import json
import re
import time
import uuid
from typing import AsyncGenerator

# Global variables, will be passed from main service later
logger = None
response_channels = None
CONFIG = None
MODEL_NAME_TO_ID_MAP = None
DEFAULT_MODEL_ID = None


def initialize_image_module(app_logger, channels, app_config, model_map, default_model_id):
    """Initialize global variables required by the module."""
    global logger, response_channels, CONFIG, MODEL_NAME_TO_ID_MAP, DEFAULT_MODEL_ID
    logger = app_logger
    response_channels = channels
    CONFIG = app_config
    MODEL_NAME_TO_ID_MAP = model_map
    DEFAULT_MODEL_ID = default_model_id
    logger.info("Text-to-image module successfully initialized.")

def convert_to_lmarena_image_payload(prompt: str, model_id: str, session_id: str, message_id: str) -> dict:
    """Convert text prompt to LMArena image generation payload."""
    return {
        "is_image_request": True,
        "message_templates": [{
            "role": "user",
            "content": prompt,
            "attachments": [],
            "participantPosition": "a"
        }],
        "target_model_id": model_id,
        "session_id": session_id,
        "message_id": message_id
    }

async def _process_image_stream(request_id: str) -> AsyncGenerator[tuple[str, str], None]:
    """Process image generation data stream from browser and generate structured events."""
    queue = response_channels.get(request_id)
    if not queue:
        logger.error(f"IMAGE PROCESSOR [ID: {request_id[:8]}]: Unable to find response channel.")
        yield 'error', 'Internal server error: response channel not found.'
        return

    buffer = ""
    timeout = CONFIG.get("stream_response_timeout_seconds", 360)
    # Generalized regex to match a or b prefix
    image_pattern = re.compile(r'[ab]2:(\[.*?\])')
    finish_pattern = re.compile(r'[ab]d:(\{.*?"finishReason".*?\})')
    # Generic error matching, can capture JSON objects containing "error" or "context_file"
    error_pattern = re.compile(r'(\{\s*".*?"\s*:\s*".*?"(error|context_file).*?"\s*\})', re.DOTALL | re.IGNORECASE)
    
    found_image_url = None # Used to store the found URL

    try:
        while True:
            try:
                raw_data = await asyncio.wait_for(queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"IMAGE PROCESSOR [ID: {request_id[:8]}]: Waiting for browser data timed out ({timeout} seconds).")
                # If timeout but image already received, consider it successful
                if found_image_url:
                    yield 'image_url', found_image_url
                else:
                    yield 'error', f'Response timed out after {timeout} seconds.'
                return

            if isinstance(raw_data, dict) and 'error' in raw_data:
                yield 'error', raw_data.get('error', 'Unknown browser error')
                return
            
            # [DONE] is the end signal of the stream
            if raw_data == "[DONE]":
                break

            buffer += "".join(str(item) for item in raw_data) if isinstance(raw_data, list) else raw_data
            
            if (error_match := error_pattern.search(buffer)):
                try:
                    error_json = json.loads(error_match.group(1))
                    yield 'error', error_json.get("error", "Unknown error from LMArena")
                    return
                except json.JSONDecodeError: pass

            # Only match when URL has not been found yet
            if not found_image_url:
                while (match := image_pattern.search(buffer)):
                    try:
                        image_data_list = json.loads(match.group(1))
                        if isinstance(image_data_list, list) and image_data_list:
                            image_info = image_data_list[0]
                            if image_info.get("type") == "image" and "image" in image_info:
                                found_image_url = image_info["image"]
                                # After finding, no longer continue searching for images in this buffer
                                buffer = buffer[match.end():]
                                break
                    except (json.JSONDecodeError, IndexError) as e:
                        logger.error(f"Error parsing image URL: {e}, buffer: {buffer}")
                    buffer = buffer[match.end():]

            if (finish_match := finish_pattern.search(buffer)):
                try:
                    finish_data = json.loads(finish_match.group(1))
                    yield 'finish', finish_data.get("finishReason", "stop")
                except (json.JSONDecodeError, IndexError): pass
                buffer = buffer[finish_match.end():]
        
        # After loop ends, yield final result based on whether URL was found
        if found_image_url:
            yield 'image_url', found_image_url
        # If no image URL but has finish reason, don't send error, let caller decide
        elif not any(e[0] == 'finish' for e in locals().get('_debug_events', [])):
             # If stream ended but still no image found, report error
            yield 'error', 'Stream ended without providing an image URL.'

    except asyncio.CancelledError:
        logger.info(f"IMAGE PROCESSOR [ID: {request_id[:8]}]: Task was cancelled.")
    finally:
        if request_id in response_channels:
            del response_channels[request_id]
            logger.info(f"IMAGE PROCESSOR [ID: {request_id[:8]}]: Response channel cleaned up.")


async def generate_single_image(prompt: str, model_name: str, browser_ws) -> str | dict:
    """
    Execute single text-to-image request and return image URL or error dictionary.
    """
    if not browser_ws:
        return {"error": "Browser client not connected."}

    target_model_id = None # Force modelId to be null
    session_id = CONFIG.get("session_id")
    message_id = CONFIG.get("message_id")

    if not session_id or not message_id or "YOUR_" in session_id or "YOUR_" in message_id:
        return {"error": "Session ID or Message ID is not configured."}

    request_id = str(uuid.uuid4())
    response_channels[request_id] = asyncio.Queue()

    try:
        lmarena_payload = convert_to_lmarena_image_payload(prompt, target_model_id, session_id, message_id)
        message_to_browser = {"request_id": request_id, "payload": lmarena_payload}
        
        logger.info(f"IMAGE GEN (SINGLE) [ID: {request_id[:8]}]: Sending request...")
        await browser_ws.send_text(json.dumps(message_to_browser))

        # _process_image_stream now only yields 'image_url' or 'error' or 'finish'
        async for event_type, data in _process_image_stream(request_id):
            if event_type == 'image_url':
                logger.info(f"IMAGE GEN (SINGLE) [ID: {request_id[:8]}]: Successfully obtained image URL.")
                return data # Success, return URL string
            elif event_type == 'error':
                 logger.error(f"IMAGE GEN (SINGLE) [ID: {request_id[:8]}]: Stream processing error: {data}")
                 return {"error": data}
            elif event_type == 'finish':
                logger.warning(f"IMAGE GEN (SINGLE) [ID: {request_id[:8]}]: Received end signal: {data}")
                if data == 'content-filter':
                    return {"error": "Response was terminated, possibly due to context limit exceeded or internal model review (most likely)"}
        
        # If the loop ends normally but without any return (e.g., only received finish:stop), report an error.
        return {"error": "Image generation stream ended without a result."}

    except Exception as e:
        logger.error(f"IMAGE GEN (SINGLE) [ID: {request_id[:8]}]: Fatal error occurred during processing: {e}", exc_info=True)
        if request_id in response_channels:
            del response_channels[request_id]
        return {"error": "An internal server error occurred."}


async def handle_image_generation_request(request, browser_ws):
    """Handle text-to-image API endpoint requests, supports parallel generation."""
    try:
        req_body = await request.json()
    except json.JSONDecodeError:
        return {"error": "Invalid JSON request body"}, 400

    prompt = req_body.get("prompt")
    if not prompt:
        return {"error": "Prompt is required"}, 400
    
    n = req_body.get("n", 1)
    if not isinstance(n, int) or not 1 <= n <= 10: # OpenAI limits n to 1-10
        return {"error": "Parameter 'n' must be an integer between 1 and 10."}, 400

    model_name = req_body.get("model", "dall-e-3")

    logger.info(f"Received text-to-image request: n={n}, prompt='{prompt[:30]}...'")

    # Create n parallel tasks
    tasks = [generate_single_image(prompt, model_name, browser_ws) for _ in range(n)]
    results = await asyncio.gather(*tasks)

    successful_urls = [res for res in results if isinstance(res, str)]
    errors = [res['error'] for res in results if isinstance(res, dict)]

    if errors:
        logger.error(f"Text-to-image request had {len(errors)} failed tasks: {errors}")
    
    if not successful_urls:
         # If all tasks failed, return an error
        error_message = f"All {n} image generation tasks failed. Last error: {errors[-1] if errors else 'Unknown error'}"
        return {"error": error_message}, 500

    # Format as OpenAI response
    response_data = {
        "created": int(time.time()),
        "data": [
            {
                "url": url,
            } for url in successful_urls
        ]
    }
    return response_data, 200