"""
Session Extractor for LMArenaBridge Multi-Instance Architecture

This module handles automatic extraction of session and message IDs
from LMArena.ai, replacing the Tampermonkey functionality.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, List
from playwright.async_api import Page, Request, Response

logger = logging.getLogger(__name__)


class SessionExtractor:
    """Extracts session and message IDs from LMArena interactions."""
    
    def __init__(self, page: Page, config: dict = None):
        self.page = page
        self.config = config or {}
        self.intercepted_requests: List[dict] = []
        self.intercepted_responses: List[dict] = []
        self.session_id: Optional[str] = None
        self.message_id: Optional[str] = None
        self.extraction_patterns = {
            'session_id': [
                r'session[_-]?id["\']?\s*[:=]\s*["\']?([a-f0-9-]{36})["\']?',
                r'sessionId["\']?\s*[:=]\s*["\']?([a-f0-9-]{36})["\']?',
                r'"session":\s*"([a-f0-9-]{36})"',
                r'/session/([a-f0-9-]{36})',
                r'session=([a-f0-9-]{36})'
            ],
            'message_id': [
                r'message[_-]?id["\']?\s*[:=]\s*["\']?([a-f0-9-]{36})["\']?',
                r'messageId["\']?\s*[:=]\s*["\']?([a-f0-9-]{36})["\']?',
                r'"message":\s*"([a-f0-9-]{36})"',
                r'/message/([a-f0-9-]{36})',
                r'message=([a-f0-9-]{36})'
            ]
        }
        self.is_intercepting = False
        
    async def setup_interception(self):
        """Set up request and response interception."""
        if self.is_intercepting:
            return
        
        try:
            # Intercept requests
            await self.page.route('**/*', self._handle_request)
            
            # Listen for responses
            self.page.on('response', self._handle_response)
            
            self.is_intercepting = True
            logger.info("[SessionExtractor] Request/response interception set up")
            
        except Exception as e:
            logger.error(f"[SessionExtractor] Failed to set up interception: {e}")
            raise
    
    async def _handle_request(self, route):
        """Handle intercepted requests."""
        try:
            request = route.request
            
            # Log relevant requests
            if self._is_relevant_request(request):
                request_data = {
                    'url': request.url,
                    'method': request.method,
                    'headers': dict(request.headers),
                    'post_data': request.post_data,
                    'timestamp': datetime.now()
                }
                
                self.intercepted_requests.append(request_data)
                
                # Try to extract IDs from request
                await self._extract_ids_from_request(request_data)
                
                logger.debug(f"[SessionExtractor] Intercepted request: {request.method} {request.url}")
            
            # Continue with the request
            await route.continue_()
            
        except Exception as e:
            logger.error(f"[SessionExtractor] Error handling request: {e}")
            await route.continue_()
    
    async def _handle_response(self, response: Response):
        """Handle intercepted responses."""
        try:
            if self._is_relevant_response(response):
                # Get response body if it's JSON
                try:
                    if 'application/json' in response.headers.get('content-type', ''):
                        body = await response.text()
                    else:
                        body = None
                except:
                    body = None
                
                response_data = {
                    'url': response.url,
                    'status': response.status,
                    'headers': dict(response.headers),
                    'body': body,
                    'timestamp': datetime.now()
                }
                
                self.intercepted_responses.append(response_data)
                
                # Try to extract IDs from response
                await self._extract_ids_from_response(response_data)
                
                logger.debug(f"[SessionExtractor] Intercepted response: {response.status} {response.url}")
        
        except Exception as e:
            logger.error(f"[SessionExtractor] Error handling response: {e}")
    
    def _is_relevant_request(self, request: Request) -> bool:
        """Check if a request is relevant for ID extraction."""
        url = request.url.lower()
        
        # LMArena API endpoints
        relevant_patterns = [
            'lmarena.ai',
            '/api/',
            '/chat',
            '/conversation',
            '/message',
            '/session'
        ]
        
        return any(pattern in url for pattern in relevant_patterns)
    
    def _is_relevant_response(self, response: Response) -> bool:
        """Check if a response is relevant for ID extraction."""
        url = response.url.lower()
        
        # Same patterns as requests
        relevant_patterns = [
            'lmarena.ai',
            '/api/',
            '/chat',
            '/conversation',
            '/message',
            '/session'
        ]
        
        return any(pattern in url for pattern in relevant_patterns)
    
    async def _extract_ids_from_request(self, request_data: dict):
        """Extract session/message IDs from request data."""
        try:
            # Extract from URL
            await self._extract_from_text(request_data['url'])
            
            # Extract from POST data
            if request_data.get('post_data'):
                await self._extract_from_text(request_data['post_data'])
            
            # Extract from headers
            for header_value in request_data.get('headers', {}).values():
                if isinstance(header_value, str):
                    await self._extract_from_text(header_value)
                    
        except Exception as e:
            logger.error(f"[SessionExtractor] Error extracting IDs from request: {e}")
    
    async def _extract_ids_from_response(self, response_data: dict):
        """Extract session/message IDs from response data."""
        try:
            # Extract from response body
            if response_data.get('body'):
                await self._extract_from_text(response_data['body'])
            
            # Extract from headers
            for header_value in response_data.get('headers', {}).values():
                if isinstance(header_value, str):
                    await self._extract_from_text(header_value)
                    
        except Exception as e:
            logger.error(f"[SessionExtractor] Error extracting IDs from response: {e}")
    
    async def _extract_from_text(self, text: str):
        """Extract IDs from text using regex patterns."""
        if not text:
            return
        
        try:
            # Extract session ID
            if not self.session_id:
                for pattern in self.extraction_patterns['session_id']:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        self.session_id = match.group(1)
                        logger.info(f"[SessionExtractor] Extracted session_id: {self.session_id}")
                        break
            
            # Extract message ID
            if not self.message_id:
                for pattern in self.extraction_patterns['message_id']:
                    match = re.search(pattern, text, re.IGNORECASE)
                    if match:
                        self.message_id = match.group(1)
                        logger.info(f"[SessionExtractor] Extracted message_id: {self.message_id}")
                        break
                        
        except Exception as e:
            logger.error(f"[SessionExtractor] Error in pattern matching: {e}")
    
    async def extract_session_ids(self, mode: str = 'direct_chat', 
                                battle_target: str = 'A') -> Tuple[Optional[str], Optional[str]]:
        """Extract session and message IDs by interacting with LMArena."""
        try:
            logger.info(f"[SessionExtractor] Starting ID extraction for mode: {mode}")
            
            # Set up interception
            await self.setup_interception()
            
            # Navigate to appropriate mode
            await self._navigate_to_mode(mode, battle_target)
            
            # Send test message to trigger ID generation
            success = await self._send_test_message()
            
            if not success:
                logger.warning("[SessionExtractor] Failed to send test message")
            
            # Wait for IDs to be extracted
            await self._wait_for_ids()
            
            # Validate extracted IDs
            if self.session_id and self.message_id:
                if self._validate_ids(self.session_id, self.message_id):
                    logger.info(f"[SessionExtractor] Successfully extracted valid IDs")
                    return self.session_id, self.message_id
                else:
                    logger.warning("[SessionExtractor] Extracted IDs failed validation")
            
            # Fallback to UUID generation
            logger.warning("[SessionExtractor] Using fallback UUID generation")
            return self._generate_fallback_ids()
            
        except Exception as e:
            logger.error(f"[SessionExtractor] Error during ID extraction: {e}")
            return self._generate_fallback_ids()
    
    async def _navigate_to_mode(self, mode: str, battle_target: str = 'A'):
        """Navigate to the specified mode in LMArena."""
        try:
            current_url = self.page.url
            
            if mode == 'battle':
                # Navigate to battle mode
                if 'battle' not in current_url:
                    logger.info("[SessionExtractor] Navigating to battle mode")
                    # This would need to be updated based on actual LMArena UI
                    # await self.page.click('text=Battle')
                    await self.page.goto('https://lmarena.ai/?arena')
                    await self.page.wait_for_load_state('networkidle')
            else:
                # Navigate to direct chat mode
                if 'direct' not in current_url and 'chat' not in current_url:
                    logger.info("[SessionExtractor] Navigating to direct chat mode")
                    await self.page.goto('https://lmarena.ai/')
                    await self.page.wait_for_load_state('networkidle')
            
            # Wait for interface to be ready
            await asyncio.sleep(2)
            
        except Exception as e:
            logger.error(f"[SessionExtractor] Error navigating to mode {mode}: {e}")
    
    async def _send_test_message(self) -> bool:
        """Send a test message to trigger ID generation."""
        try:
            # Find message input field
            input_selectors = [
                'textarea[placeholder*="message"]',
                'textarea[placeholder*="Message"]',
                'input[type="text"][placeholder*="message"]',
                'textarea',
                'input[type="text"]'
            ]
            
            input_element = None
            for selector in input_selectors:
                try:
                    input_element = await self.page.wait_for_selector(selector, timeout=5000)
                    if input_element:
                        break
                except:
                    continue
            
            if not input_element:
                logger.error("[SessionExtractor] Could not find message input field")
                return False
            
            # Send test message
            test_message = "Hello"
            await input_element.fill(test_message)
            
            # Submit message (try different methods)
            try:
                # Try Enter key
                await self.page.keyboard.press('Enter')
            except:
                try:
                    # Try clicking send button
                    send_button = await self.page.wait_for_selector(
                        'button[type="submit"], button:has-text("Send"), button:has-text("send")',
                        timeout=2000
                    )
                    if send_button:
                        await send_button.click()
                except:
                    logger.warning("[SessionExtractor] Could not submit message")
                    return False
            
            logger.info("[SessionExtractor] Test message sent")
            return True
            
        except Exception as e:
            logger.error(f"[SessionExtractor] Error sending test message: {e}")
            return False
    
    async def _wait_for_ids(self, timeout: int = 10):
        """Wait for session and message IDs to be extracted."""
        start_time = asyncio.get_event_loop().time()
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            if self.session_id and self.message_id:
                return
            await asyncio.sleep(0.5)
        
        logger.warning(f"[SessionExtractor] Timeout waiting for ID extraction after {timeout}s")
    
    def _validate_ids(self, session_id: str, message_id: str) -> bool:
        """Validate extracted IDs."""
        try:
            # Check if they look like UUIDs
            uuid_pattern = r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'
            
            session_valid = bool(re.match(uuid_pattern, session_id, re.IGNORECASE))
            message_valid = bool(re.match(uuid_pattern, message_id, re.IGNORECASE))
            
            # They should be different
            different = session_id != message_id
            
            return session_valid and message_valid and different
            
        except Exception as e:
            logger.error(f"[SessionExtractor] Error validating IDs: {e}")
            return False
    
    def _generate_fallback_ids(self) -> Tuple[str, str]:
        """Generate fallback UUIDs when extraction fails."""
        session_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        
        logger.info(f"[SessionExtractor] Generated fallback IDs: session={session_id}, message={message_id}")
        return session_id, message_id
    
    async def regenerate_ids(self) -> Tuple[Optional[str], Optional[str]]:
        """Regenerate session and message IDs."""
        # Clear existing IDs
        self.session_id = None
        self.message_id = None
        self.intercepted_requests.clear()
        self.intercepted_responses.clear()
        
        # Extract new IDs
        return await self.extract_session_ids()
    
    def get_extraction_history(self) -> Dict[str, Any]:
        """Get history of intercepted requests and responses."""
        return {
            'requests': self.intercepted_requests[-50:],  # Last 50 requests
            'responses': self.intercepted_responses[-50:],  # Last 50 responses
            'current_session_id': self.session_id,
            'current_message_id': self.message_id,
            'extraction_time': datetime.now().isoformat()
        }
    
    async def cleanup(self):
        """Clean up interception resources."""
        try:
            if self.is_intercepting:
                # Remove event listeners
                self.page.remove_listener('response', self._handle_response)
                
                # Unroute all routes
                await self.page.unroute('**/*')
                
                self.is_intercepting = False
                logger.info("[SessionExtractor] Cleanup completed")
                
        except Exception as e:
            logger.error(f"[SessionExtractor] Error during cleanup: {e}")