"""
Enhanced Multi-Instance API Server for LMArenaBridge

This is the new multi-instance version of the API server that replaces
the single-instance api_server.py with full multi-instance support.
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
import re
import threading
import random
import mimetypes
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Any

import uvicorn
import requests
from packaging.version import parse as parse_version
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Import multi-instance modules
from modules.browser_manager import BrowserManager
from modules.instance_coordinator import InstanceCoordinator
from modules.health_monitor import HealthMonitor
from modules.load_balancer import LoadBalancer
from modules.session_extractor import SessionExtractor
from modules import image_generation

# Basic configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global state and configuration
CONFIG = {}
MODEL_NAME_TO_ID_MAP = {}
MODEL_ENDPOINT_MAP = {}
DEFAULT_MODEL_ID = None

# Multi-instance components
instance_coordinator: Optional[InstanceCoordinator] = None
health_monitor: Optional[HealthMonitor] = None
load_balancer: Optional[LoadBalancer] = None

# Legacy compatibility
response_channels: dict[str, asyncio.Queue] = {}
last_activity_time = None
idle_monitor_thread = None
main_event_loop = None

# GUI WebSocket connections
gui_websocket_connections: set = set()

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
        logger.info(f"  - Multi-Instance Mode: ✅ Enabled")
        logger.info(f"  - Initial Instances: {CONFIG.get('instances', {}).get('initial_count', 1)}")
        logger.info(f"  - Max Instances: {CONFIG.get('instances', {}).get('max_instances', 5)}")
        logger.info(f"  - Load Balancing: {CONFIG.get('instances', {}).get('load_balancing', 'least_busy')}")
        logger.info(f"  - GUI Enabled: {'✅ Yes' if CONFIG.get('gui', {}).get('enabled') else '❌ No'}")
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

def load_model_endpoint_map():
    """Load model to endpoint mapping from model_endpoint_map.json."""
    global MODEL_ENDPOINT_MAP
    try:
        with open('model_endpoint_map.json', 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                MODEL_ENDPOINT_MAP = {}
            else:
                MODEL_ENDPOINT_MAP = json.loads(content)
        logger.info(f"Successfully loaded {len(MODEL_ENDPOINT_MAP)} model endpoint mappings.")
    except FileNotFoundError:
        MODEL_ENDPOINT_MAP = {}
        logger.warning("'model_endpoint_map.json' file not found. Using empty mapping.")
    except json.JSONDecodeError as err:
        MODEL_ENDPOINT_MAP = {}
        logger.error(f"Failed to parse 'model_endpoint_map.json': {err}. Using empty mapping.")

async def initialize_multi_instance_system():
    """Initialize the multi-instance system components."""
    global instance_coordinator, health_monitor, load_balancer
    
    try:
        logger.info("🚀 Initializing multi-instance system...")
        
        # Initialize instance coordinator
        instance_coordinator = InstanceCoordinator(CONFIG)
        success = await instance_coordinator.initialize()
        
        if not success:
            logger.error("❌ Failed to initialize instance coordinator")
            return False
        
        # Initialize health monitor
        health_monitor = HealthMonitor(instance_coordinator, CONFIG)
        await health_monitor.start_monitoring()
        
        # Initialize load balancer
        load_balancer = LoadBalancer(instance_coordinator, CONFIG)
        
        # Set up alert callbacks
        health_monitor.add_alert_callback(handle_health_alert)
        
        logger.info("✅ Multi-instance system initialized successfully")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize multi-instance system: {e}")
        return False

async def handle_health_alert(alert: dict):
    """Handle health monitoring alerts."""
    try:
        alert_type = alert.get('type')
        data = alert.get('data', {})
        
        logger.warning(f"🚨 Health Alert: {alert_type}")
        
        # Broadcast alert to GUI clients
        if gui_websocket_connections:
            alert_message = {
                "type": "health_alert",
                "alert": alert
            }
            await broadcast_to_gui_clients(alert_message)
        
        # Handle specific alert types
        if alert_type == 'instance_failed':
            instance_id = data.get('instance_id')
            if instance_id and load_balancer:
                await load_balancer.handle_instance_failure(instance_id)
        
    except Exception as e:
        logger.error(f"Error handling health alert: {e}")

async def broadcast_to_gui_clients(message: dict):
    """Broadcast message to all connected GUI WebSocket clients."""
    if not gui_websocket_connections:
        return
    
    message_str = json.dumps(message, ensure_ascii=False)
    disconnected = set()
    
    for websocket in gui_websocket_connections:
        try:
            await websocket.send_text(message_str)
        except Exception:
            disconnected.add(websocket)
    
    # Remove disconnected clients
    gui_websocket_connections -= disconnected

# FastAPI Lifecycle Events
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle function run at server startup."""
    global last_activity_time, main_event_loop
    
    main_event_loop = asyncio.get_running_loop()
    load_config()
    load_model_map()
    load_model_endpoint_map()
    
    # Initialize multi-instance system
    success = await initialize_multi_instance_system()
    if not success:
        logger.error("Failed to initialize multi-instance system. Exiting.")
        sys.exit(1)
    
    # Initialize image generation module
    image_generation.initialize_image_module(
        app_logger=logger,
        channels=response_channels,
        app_config=CONFIG,
        model_map=MODEL_NAME_TO_ID_MAP,
        default_model_id=DEFAULT_MODEL_ID
    )
    
    last_activity_time = datetime.now()
    
    logger.info("🎉 Multi-Instance LMArenaBridge Server Started!")
    logger.info("="*60)
    
    yield
    
    # Cleanup on shutdown
    logger.info("🛑 Shutting down multi-instance system...")
    if health_monitor:
        await health_monitor.stop_monitoring()
    if load_balancer:
        await load_balancer.cleanup()
    if instance_coordinator:
        await instance_coordinator.cleanup()
    logger.info("✅ Shutdown complete")

app = FastAPI(lifespan=lifespan)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files and templates for GUI
if CONFIG.get('gui', {}).get('enabled', True):
    try:
        app.mount("/static", StaticFiles(directory="gui/static"), name="static")
        templates = Jinja2Templates(directory="gui/templates")
    except Exception as e:
        logger.warning(f"Could not mount GUI static files: {e}")

# Helper Functions
def _process_openai_message(message: dict) -> dict:
    """Process OpenAI messages, separate text and attachments."""
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
                original_filename = image_url_data.get("detail")

                if url and url.startswith("data:"):
                    try:
                        content_type = url.split(';')[0].split(':')[1]
                        
                        if original_filename and isinstance(original_filename, str):
                            file_name = original_filename
                        else:
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

                        attachments.append({
                            "name": file_name,
                            "contentType": content_type,
                            "url": url
                        })
                    except (IndexError, ValueError) as e:
                        logger.warning(f"Unable to parse base64 data URI: {url[:60]}... Error: {e}")

        text_content = "\n".join(text_parts)
    else:
        text_content = content or ""

    # Ensure empty content for user role is replaced with a space
    if role == "user" and not text_content.strip():
        text_content = " "

    return {
        "role": role,
        "content": text_content,
        "attachments": attachments
    }

def convert_openai_to_lmarena_payload(openai_data: dict, session_id: str, message_id: str, 
                                    mode_override: str = None, battle_target_override: str = None) -> dict:
    """Convert OpenAI format to LMArena payload format."""
    messages = openai_data.get("messages", [])
    model = openai_data.get("model", "")
    
    # Process messages
    processed_messages = []
    for msg in messages:
        processed_msg = _process_openai_message(msg)
        processed_messages.append(processed_msg)
    
    # Get mode from config
    mode = mode_override or CONFIG.get("id_updater_last_mode", "direct_chat")
    battle_target = battle_target_override or CONFIG.get("id_updater_battle_target", "A")
    
    # Build LMArena payload
    payload = {
        "message_templates": processed_messages,
        "session_id": session_id,
        "message_id": message_id,
        "mode": mode,
        "model": model
    }
    
    if mode == "battle":
        payload["battle_target"] = battle_target
    
    return payload

# API Endpoints

@app.get("/v1/models")
async def get_models():
    """Get available models."""
    global last_activity_time
    last_activity_time = datetime.now()
    
    models = []
    for name, model_id in MODEL_NAME_TO_ID_MAP.items():
        models.append({
            "id": name,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "lmarena"
        })
    
    return {"object": "list", "data": models}

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Handle chat completion requests with multi-instance support."""
    global last_activity_time
    last_activity_time = datetime.now()
    
    try:
        # Parse request
        openai_data = await request.json()
        model = openai_data.get("model", "")
        stream = openai_data.get("stream", False)
        request_id = str(uuid.uuid4())
        
        logger.info(f"[API] New chat request: {request_id} (model: {model}, stream: {stream})")
        
        # Route request to an instance
        if not load_balancer:
            raise HTTPException(status_code=503, detail="Multi-instance system not initialized")
        
        instance_id = await load_balancer.route_request(request_id, openai_data)
        if not instance_id:
            raise HTTPException(status_code=503, detail="No available instances")
        
        # Get the instance
        instance = instance_coordinator.browser_manager.get_instance(instance_id)
        if not instance:
            await load_balancer.complete_request(request_id, success=False)
            raise HTTPException(status_code=503, detail="Instance not available")
        
        # Get session IDs for this instance
        session_id = instance.session_id
        message_id = instance.message_id
        
        if not session_id or not message_id:
            await load_balancer.complete_request(request_id, success=False)
            raise HTTPException(status_code=503, detail="Instance session not ready")
        
        # Convert to LMArena format
        lmarena_payload = convert_openai_to_lmarena_payload(openai_data, session_id, message_id)
        
        # Create response channel
        response_channels[request_id] = asyncio.Queue()
        
        # Send message through the instance
        last_message = lmarena_payload.get("message_templates", [])[-1] if lmarena_payload.get("message_templates") else {}
        success = await instance.send_message(
            last_message.get("content", ""),
            last_message.get("attachments", [])
        )
        
        if not success:
            await load_balancer.complete_request(request_id, success=False)
            raise HTTPException(status_code=503, detail="Failed to send message to instance")
        
        # Handle streaming vs non-streaming response
        if stream:
            return StreamingResponse(
                stream_generator(request_id, model, instance_id),
                media_type="text/plain"
            )
        else:
            return await non_stream_response(request_id, model, instance_id)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error in chat completions: {e}")
        if request_id in response_channels:
            del response_channels[request_id]
        if load_balancer:
            await load_balancer.complete_request(request_id, success=False)
        raise HTTPException(status_code=500, detail=str(e))

async def stream_generator(request_id: str, model: str, instance_id: str):
    """Generate streaming response for chat completions."""
    try:
        queue = response_channels.get(request_id)
        if not queue:
            yield format_openai_error_chunk("Response channel not found", model, request_id)
            return
        
        timeout = CONFIG.get("stream_response_timeout_seconds", 360)
        
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=timeout)
                
                if data.get("type") == "chunk":
                    content = data.get("content", "")
                    yield format_openai_chunk(content, model, request_id)
                elif data.get("type") == "done":
                    yield format_openai_finish_chunk(model, request_id)
                    break
                elif data.get("type") == "error":
                    error_msg = data.get("content", "Unknown error")
                    yield format_openai_error_chunk(error_msg, model, request_id)
                    break
                    
            except asyncio.TimeoutError:
                yield format_openai_error_chunk("Response timeout", model, request_id)
                break
                
    except Exception as e:
        logger.error(f"[API] Error in stream generator: {e}")
        yield format_openai_error_chunk(str(e), model, request_id)
    finally:
        # Cleanup
        if request_id in response_channels:
            del response_channels[request_id]
        if load_balancer:
            await load_balancer.complete_request(request_id, success=True)

async def non_stream_response(request_id: str, model: str, instance_id: str):
    """Generate non-streaming response for chat completions."""
    try:
        queue = response_channels.get(request_id)
        if not queue:
            raise HTTPException(status_code=500, detail="Response channel not found")
        
        timeout = CONFIG.get("stream_response_timeout_seconds", 360)
        content_parts = []
        
        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=timeout)
                
                if data.get("type") == "chunk":
                    content_parts.append(data.get("content", ""))
                elif data.get("type") == "done":
                    break
                elif data.get("type") == "error":
                    error_msg = data.get("content", "Unknown error")
                    raise HTTPException(status_code=500, detail=error_msg)
                    
            except asyncio.TimeoutError:
                raise HTTPException(status_code=500, detail="Response timeout")
        
        full_content = "".join(content_parts)
        return format_openai_non_stream_response(full_content, model, request_id)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Error in non-stream response: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup
        if request_id in response_channels:
            del response_channels[request_id]
        if load_balancer:
            await load_balancer.complete_request(request_id, success=True)

def format_openai_chunk(content: str, model: str, request_id: str) -> str:
    """Format content as OpenAI streaming chunk."""
    chunk = {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {"content": content},
            "finish_reason": None
        }]
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

def format_openai_finish_chunk(model: str, request_id: str, reason: str = 'stop') -> str:
    """Format finish chunk for OpenAI streaming."""
    chunk = {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": reason
        }]
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n"

def format_openai_error_chunk(error_message: str, model: str, request_id: str) -> str:
    """Format error as OpenAI chunk."""
    chunk = {
        "id": f"chatcmpl-{request_id}",
        "object": "error",
        "created": int(time.time()),
        "model": model,
        "error": {"message": error_message, "type": "server_error"}
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

def format_openai_non_stream_response(content: str, model: str, request_id: str, reason: str = 'stop') -> dict:
    """Format non-streaming OpenAI response."""
    return {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content
            },
            "finish_reason": reason
        }],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }
    }

# GUI API Endpoints

@app.get("/gui/dashboard")
async def serve_dashboard(request: Request):
    """Serve the GUI dashboard."""
    if not CONFIG.get('gui', {}).get('enabled', True):
        raise HTTPException(status_code=404, detail="GUI disabled")
    
    try:
        return templates.TemplateResponse("dashboard.html", {"request": request})
    except Exception as e:
        logger.error(f"Error serving dashboard: {e}")
        raise HTTPException(status_code=500, detail="Dashboard not available")

@app.get("/gui/api/status")
async def get_system_status():
    """Get current system status."""
    if not instance_coordinator or not health_monitor or not load_balancer:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    return {
        "coordinator": instance_coordinator.get_status(),
        "health_monitor": health_monitor.get_health_status(),
        "load_balancer": load_balancer.get_routing_stats(),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/gui/api/instances")
async def get_instances():
    """Get list of all instances."""
    if not instance_coordinator:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    return {
        "instances": instance_coordinator.get_instance_list(),
        "summary": instance_coordinator.get_status()
    }

@app.post("/gui/api/instances")
async def create_instance(request: Request):
    """Create a new instance."""
    if not instance_coordinator:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    try:
        data = await request.json()
        instance_config = data.get("config", {})
        
        instance_id = await instance_coordinator.create_instance(instance_config)
        if instance_id:
            return {"success": True, "instance_id": instance_id}
        else:
            raise HTTPException(status_code=500, detail="Failed to create instance")
            
    except Exception as e:
        logger.error(f"Error creating instance: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/gui/api/instances/{instance_id}")
async def remove_instance(instance_id: str):
    """Remove an instance."""
    if not instance_coordinator:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    try:
        success = await instance_coordinator.remove_instance(instance_id)
        if success:
            return {"success": True}
        else:
            raise HTTPException(status_code=404, detail="Instance not found or cannot be removed")
            
    except Exception as e:
        logger.error(f"Error removing instance {instance_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/gui/api/metrics")
async def get_metrics():
    """Get performance metrics."""
    if not health_monitor or not load_balancer:
        raise HTTPException(status_code=503, detail="System not initialized")
    
    return {
        "health_metrics": health_monitor.get_metrics_summary(),
        "load_balancer_metrics": load_balancer.get_routing_stats(),
        "instance_performance": load_balancer.get_instance_performance(),
        "load_distribution": load_balancer.get_load_distribution()
    }

@app.websocket("/gui/ws")
async def gui_websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time GUI updates."""
    await websocket.accept()
    gui_websocket_connections.add(websocket)
    
    try:
        # Send initial status
        if instance_coordinator and health_monitor and load_balancer:
            initial_status = {
                "type": "initial_status",
                "data": {
                    "coordinator": instance_coordinator.get_status(),
                    "health_monitor": health_monitor.get_health_status(),
                    "load_balancer": load_balancer.get_routing_stats()
                }
            }
            await websocket.send_text(json.dumps(initial_status, ensure_ascii=False))
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)
                
                # Handle different message types
                if message.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}, ensure_ascii=False))
                elif message.get("type") == "request_status":
                    # Send current status
                    status_update = {
                        "type": "status_update",
                        "data": {
                            "coordinator": instance_coordinator.get_status() if instance_coordinator else {},
                            "health_monitor": health_monitor.get_health_status() if health_monitor else {},
                            "load_balancer": load_balancer.get_routing_stats() if load_balancer else {}
                        }
                    }
                    await websocket.send_text(json.dumps(status_update, ensure_ascii=False))
                    
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"Error in GUI WebSocket: {e}")
                break
                
    except Exception as e:
        logger.error(f"GUI WebSocket error: {e}")
    finally:
        gui_websocket_connections.discard(websocket)

# Legacy compatibility endpoints (for backward compatibility)

@app.websocket("/ws")
async def legacy_websocket_endpoint(websocket: WebSocket):
    """Legacy WebSocket endpoint for backward compatibility."""
    await websocket.accept()
    logger.warning("Legacy WebSocket connection detected. Consider upgrading to multi-instance client.")
    
    try:
        while True:
            data = await websocket.receive_text()
            # Handle legacy Tampermonkey messages
            # This is a placeholder - actual implementation would depend on legacy protocol
            await websocket.send_text(json.dumps({"status": "legacy_mode_active"}, ensure_ascii=False))
            
    except WebSocketDisconnect:
        logger.info("Legacy WebSocket disconnected")
    except Exception as e:
        logger.error(f"Legacy WebSocket error: {e}")

if __name__ == "__main__":
    # Run the server
    gui_config = CONFIG.get('gui', {})
    host = gui_config.get('host', 'localhost')
    port = gui_config.get('port', 5104)
    
    logger.info(f"🚀 Starting Multi-Instance LMArenaBridge Server on {host}:{port}")
    
    uvicorn.run(
        "api_server_multi:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )