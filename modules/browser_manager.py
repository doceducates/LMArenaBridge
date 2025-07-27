"""
Browser Instance Manager for LMArenaBridge Multi-Instance Architecture

This module handles Playwright browser instances, session management,
and communication with LMArena.ai for parallel processing.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

logger = logging.getLogger(__name__)


class BrowserInstance:
    """Manages a single Playwright browser instance for LMArena communication."""
    
    def __init__(self, instance_id: str, config: dict):
        self.instance_id = instance_id
        self.config = config
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.session_id: Optional[str] = None
        self.message_id: Optional[str] = None
        self.mode = config.get('mode', 'direct_chat')
        self.battle_target = config.get('battle_target', 'A')
        self.status = 'initializing'
        self.last_activity = datetime.now()
        self.proxy_config = config.get('proxy')
        self.request_count = 0
        self.max_requests_per_session = config.get('max_requests_per_session', 100)
        self.session_lifetime = config.get('session_lifetime', 3600)  # seconds
        self.session_created_at = None
        self.intercepted_requests = []
        
    async def initialize(self) -> bool:
        """Initialize the browser instance and navigate to LMArena."""
        try:
            logger.info(f"[Instance {self.instance_id}] Initializing browser instance...")
            
            # Launch Playwright
            self.playwright = await async_playwright().start()
            
            # Configure browser launch options
            browser_config = self.config.get('browser', {})
            launch_options = {
                'headless': browser_config.get('headless', False),
                'args': ['--no-sandbox', '--disable-dev-shm-usage']
            }
            
            # Add proxy configuration if provided
            if self.proxy_config and self.proxy_config.get('enabled'):
                proxy_settings = await self._setup_proxy()
                if proxy_settings:
                    launch_options['proxy'] = proxy_settings
            
            # Launch browser
            browser_type = browser_config.get('type', 'chromium')
            if browser_type == 'chromium':
                self.browser = await self.playwright.chromium.launch(**launch_options)
            elif browser_type == 'firefox':
                self.browser = await self.playwright.firefox.launch(**launch_options)
            elif browser_type == 'webkit':
                self.browser = await self.playwright.webkit.launch(**launch_options)
            else:
                raise ValueError(f"Unsupported browser type: {browser_type}")
            
            # Create incognito context (MANDATORY for rate limiting prevention)
            context_options = {
                'viewport': browser_config.get('viewport', {'width': 1280, 'height': 720}),
                'user_agent': browser_config.get('user_agent', 
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            }
            
            self.context = await self.browser.new_context(**context_options)
            self.page = await self.context.new_page()
            
            # Set up request interception for session ID extraction
            await self._setup_request_interception()
            
            # Navigate to LMArena and set up session
            success = await self._navigate_and_setup()
            
            if success:
                self.status = 'ready'
                self.session_created_at = datetime.now()
                logger.info(f"[Instance {self.instance_id}] Successfully initialized and ready")
                return True
            else:
                self.status = 'failed'
                await self.cleanup()
                return False
                
        except Exception as e:
            logger.error(f"[Instance {self.instance_id}] Failed to initialize: {e}")
            self.status = 'failed'
            await self.cleanup()
            return False
    
    async def _setup_proxy(self) -> Optional[Dict[str, Any]]:
        """Set up proxy configuration for this instance."""
        if not self.proxy_config or not self.proxy_config.get('enabled'):
            return None
            
        providers = self.proxy_config.get('providers', [])
        if not providers:
            logger.warning(f"[Instance {self.instance_id}] No proxy providers configured")
            return None
        
        # Select a proxy provider (could implement rotation logic here)
        provider = providers[0]  # Simple selection for now
        
        proxy_settings = {
            'server': f"{provider['type']}://{provider['host']}:{provider['port']}"
        }
        
        if provider.get('username') and provider.get('password'):
            proxy_settings['username'] = provider['username']
            proxy_settings['password'] = provider['password']
        
        logger.info(f"[Instance {self.instance_id}] Using proxy: {provider['host']}:{provider['port']}")
        return proxy_settings
    
    async def _setup_request_interception(self):
        """Set up request interception to capture session and message IDs."""
        async def handle_request(route):
            try:
                request = route.request
                url = request.url
                # Capture LMArena API requests
                if 'lmarena.ai' in url and ('/conversation' in url or '/chat' in url):
                    self.intercepted_requests.append({
                        'url': url,
                        'method': request.method,
                        'timestamp': datetime.now()
                    })
                    
                    # Extract session_id and message_id from URL
                    await self._extract_ids_from_url(url)
                
                # Continue with the request
                await route.continue_()
            except Exception as e:
                logger.error(f"[Instance {self.instance_id}] Error in route handler: {e}")
                try:
                    await route.continue_()
                except:
                    pass  # Route may already be handled
        
        await self.page.route('**/*', handle_request)
    
    async def _extract_ids_from_url(self, url: str):
        """Extract session_id and message_id from intercepted request URLs."""
        try:
            # Pattern matching for LMArena API URLs
            import re
            
            # Look for session_id pattern
            session_match = re.search(r'session[_-]?id[=:]([a-f0-9-]+)', url, re.IGNORECASE)
            if session_match:
                self.session_id = session_match.group(1)
                logger.debug(f"[Instance {self.instance_id}] Extracted session_id: {self.session_id}")
            
            # Look for message_id pattern
            message_match = re.search(r'message[_-]?id[=:]([a-f0-9-]+)', url, re.IGNORECASE)
            if message_match:
                self.message_id = message_match.group(1)
                logger.debug(f"[Instance {self.instance_id}] Extracted message_id: {self.message_id}")
                
        except Exception as e:
            logger.error(f"[Instance {self.instance_id}] Error extracting IDs from URL: {e}")
    
    async def _navigate_and_setup(self) -> bool:
        """Navigate to LMArena and set up the session."""
        try:
            # Navigate to LMArena with increased timeout
            logger.info(f"[Instance {self.instance_id}] Navigating to lmarena.ai...")
            
            # Try different wait strategies
            try:
                await self.page.goto('https://lmarena.ai', wait_until='networkidle', timeout=60000)
            except Exception as e:
                logger.warning(f"[Instance {self.instance_id}] Networkidle failed, trying domcontentloaded: {e}")
                try:
                    await self.page.goto('https://lmarena.ai', wait_until='domcontentloaded', timeout=60000)
                except Exception as e2:
                    logger.warning(f"[Instance {self.instance_id}] Domcontentloaded also failed: {e2}")
                    # Try basic load
                    await self.page.goto('https://lmarena.ai', wait_until='load', timeout=60000)
            
            # Wait for page to load
            await asyncio.sleep(3)
            
            # Check if page loaded successfully
            try:
                title = await self.page.title()
                logger.info(f"[Instance {self.instance_id}] Page loaded successfully: {title}")
                
                # Check for Cloudflare protection
                if "cloudflare" in title.lower() or "attention required" in title.lower():
                    logger.warning(f"[Instance {self.instance_id}] Cloudflare protection detected. Using fallback mode.")
                    return await self._setup_fallback_mode()
                    
            except Exception as e:
                logger.warning(f"[Instance {self.instance_id}] Could not get page title: {e}")
            
            # Select mode (direct_chat or battle)
            if self.mode == 'battle':
                await self._setup_battle_mode()
            else:
                await self._setup_direct_chat_mode()
            
            # Generate session IDs by sending a test message
            success = await self._generate_session_ids()
            
            return success
            
        except Exception as e:
            logger.error(f"[Instance {self.instance_id}] Error during navigation and setup: {e}")
            # Try fallback mode
            return await self._setup_fallback_mode()
    
    async def _setup_fallback_mode(self) -> bool:
        """Set up fallback mode when LMArena is not accessible."""
        try:
            logger.info(f"[Instance {self.instance_id}] Setting up fallback mode with generated IDs")
            
            # Generate fallback session IDs
            self.session_id = str(uuid.uuid4())
            self.message_id = str(uuid.uuid4())
            
            logger.info(f"[Instance {self.instance_id}] Fallback session_id: {self.session_id}")
            logger.info(f"[Instance {self.instance_id}] Fallback message_id: {self.message_id}")
            
            # Mark as ready but in fallback mode
            self.status = 'ready_fallback'
            
            return True
            
        except Exception as e:
            logger.error(f"[Instance {self.instance_id}] Error in fallback mode setup: {e}")
            return False
    
    async def _setup_direct_chat_mode(self):
        """Set up direct chat mode."""
        try:
            # Look for direct chat button/link and click it
            # This is a placeholder - actual implementation would depend on LMArena's UI
            logger.info(f"[Instance {self.instance_id}] Setting up direct chat mode...")
            
            # Wait for chat interface to be ready
            await self.page.wait_for_selector('textarea, input[type="text"]', timeout=10000)
            
        except Exception as e:
            logger.warning(f"[Instance {self.instance_id}] Could not set up direct chat mode: {e}")
    
    async def _setup_battle_mode(self):
        """Set up battle mode."""
        try:
            logger.info(f"[Instance {self.instance_id}] Setting up battle mode (target: {self.battle_target})...")
            
            # Look for battle mode button/link and click it
            # This is a placeholder - actual implementation would depend on LMArena's UI
            
            # Wait for battle interface to be ready
            await self.page.wait_for_selector('textarea, input[type="text"]', timeout=10000)
            
        except Exception as e:
            logger.warning(f"[Instance {self.instance_id}] Could not set up battle mode: {e}")
    
    async def _generate_session_ids(self) -> bool:
        """Generate session and message IDs by sending a test message."""
        try:
            logger.info(f"[Instance {self.instance_id}] Generating session IDs...")
            
            # Find the message input field
            input_selector = 'textarea, input[type="text"]'
            await self.page.wait_for_selector(input_selector, timeout=10000)
            
            # Send a test message to trigger ID generation
            test_message = "Hello"
            await self.page.fill(input_selector, test_message)
            
            # Submit the message
            await self.page.keyboard.press('Enter')
            
            # Wait for response and ID extraction
            await asyncio.sleep(3)
            
            # Check if we successfully extracted IDs
            if self.session_id and self.message_id:
                logger.info(f"[Instance {self.instance_id}] Successfully generated session IDs")
                return True
            else:
                # Fallback: generate UUIDs if extraction failed
                self.session_id = str(uuid.uuid4())
                self.message_id = str(uuid.uuid4())
                logger.warning(f"[Instance {self.instance_id}] Using fallback UUID generation for session IDs")
                return True
                
        except Exception as e:
            logger.error(f"[Instance {self.instance_id}] Failed to generate session IDs: {e}")
            return False
    
    async def health_check(self) -> bool:
        """Perform health check on this instance."""
        try:
            if not self.page or not self.browser:
                return False
            
            # For fallback mode, just check if browser is responsive
            if self.status == 'ready_fallback':
                try:
                    await self.page.evaluate('() => document.title')
                    self.last_activity = datetime.now()
                    return True
                except Exception as e:
                    logger.warning(f"[Instance {self.instance_id}] Fallback mode health check failed: {e}")
                    return False
            
            # Check if browser is still responsive
            await self.page.evaluate('() => document.title')
            
            # Check session validity
            if self._is_session_expired():
                logger.info(f"[Instance {self.instance_id}] Session expired, needs regeneration")
                return await self._regenerate_session()
            
            # Update last activity
            self.last_activity = datetime.now()
            return True
            
        except Exception as e:
            logger.error(f"[Instance {self.instance_id}] Health check failed: {e}")
            return False
    
    def _is_session_expired(self) -> bool:
        """Check if the current session has expired."""
        if not self.session_created_at:
            return True
        
        # Check session lifetime
        session_age = (datetime.now() - self.session_created_at).total_seconds()
        if session_age > self.session_lifetime:
            return True
        
        # Check request count
        if self.request_count >= self.max_requests_per_session:
            return True
        
        return False
    
    async def _regenerate_session(self) -> bool:
        """Regenerate session IDs for this instance."""
        try:
            logger.info(f"[Instance {self.instance_id}] Regenerating session...")
            
            # Reset counters
            self.request_count = 0
            self.session_created_at = datetime.now()
            
            # Generate new session IDs
            success = await self._generate_session_ids()
            
            if success:
                logger.info(f"[Instance {self.instance_id}] Session regenerated successfully")
            
            return success
            
        except Exception as e:
            logger.error(f"[Instance {self.instance_id}] Failed to regenerate session: {e}")
            return False
    
    async def send_message(self, message: str, attachments: list = None) -> bool:
        """Send a message through this browser instance."""
        try:
            if self.status != 'ready':
                return False
            
            # Find message input
            input_selector = 'textarea, input[type="text"]'
            await self.page.wait_for_selector(input_selector, timeout=5000)
            
            # Clear and fill message
            await self.page.fill(input_selector, message)
            
            # Handle attachments if provided
            if attachments:
                await self._handle_attachments(attachments)
            
            # Submit message
            await self.page.keyboard.press('Enter')
            
            # Update counters
            self.request_count += 1
            self.last_activity = datetime.now()
            
            return True
            
        except Exception as e:
            logger.error(f"[Instance {self.instance_id}] Failed to send message: {e}")
            return False
    
    async def _handle_attachments(self, attachments: list):
        """Handle file attachments for the message."""
        # Placeholder for attachment handling
        # Implementation would depend on LMArena's file upload interface
        pass
    
    async def get_response_stream(self):
        """Get the response stream from the browser."""
        # This would implement response streaming from the browser
        # Placeholder for now
        pass
    
    async def cleanup(self):
        """Clean up browser resources."""
        try:
            # Unroute all routes first to prevent handler errors
            if self.page:
                try:
                    await self.page.unroute('**/*')
                except Exception as e:
                    logger.debug(f"[Instance {self.instance_id}] Error unrouting: {e}")
                
                try:
                    await self.page.close()
                except Exception as e:
                    logger.debug(f"[Instance {self.instance_id}] Error closing page: {e}")
            
            if self.context:
                try:
                    await self.context.close()
                except Exception as e:
                    logger.debug(f"[Instance {self.instance_id}] Error closing context: {e}")
            
            if self.browser:
                try:
                    await self.browser.close()
                except Exception as e:
                    logger.debug(f"[Instance {self.instance_id}] Error closing browser: {e}")
            
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception as e:
                    logger.debug(f"[Instance {self.instance_id}] Error stopping playwright: {e}")
                
            logger.info(f"[Instance {self.instance_id}] Cleaned up successfully")
            
        except Exception as e:
            logger.error(f"[Instance {self.instance_id}] Error during cleanup: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status information for this instance."""
        return {
            'instance_id': self.instance_id,
            'status': self.status,
            'mode': self.mode,
            'battle_target': self.battle_target if self.mode == 'battle' else None,
            'session_id': self.session_id,
            'message_id': self.message_id,
            'request_count': self.request_count,
            'last_activity': self.last_activity.isoformat() if self.last_activity else None,
            'session_created_at': self.session_created_at.isoformat() if self.session_created_at else None,
            'proxy_enabled': bool(self.proxy_config and self.proxy_config.get('enabled'))
        }


class BrowserManager:
    """Manages multiple browser instances."""
    
    def __init__(self, config: dict):
        self.config = config
        self.instances: Dict[str, BrowserInstance] = {}
        self.instance_configs = {}
        
    async def create_instance(self, instance_config: dict) -> str:
        """Create a new browser instance."""
        instance_id = str(uuid.uuid4())
        
        try:
            instance = BrowserInstance(instance_id, instance_config)
            success = await instance.initialize()
            
            if success:
                self.instances[instance_id] = instance
                self.instance_configs[instance_id] = instance_config
                logger.info(f"[BrowserManager] Created instance {instance_id}")
                return instance_id
            else:
                logger.error(f"[BrowserManager] Failed to create instance {instance_id}")
                return None
                
        except Exception as e:
            logger.error(f"[BrowserManager] Error creating instance: {e}")
            return None
    
    async def remove_instance(self, instance_id: str) -> bool:
        """Remove and cleanup a browser instance."""
        try:
            if instance_id in self.instances:
                instance = self.instances[instance_id]
                await instance.cleanup()
                del self.instances[instance_id]
                del self.instance_configs[instance_id]
                logger.info(f"[BrowserManager] Removed instance {instance_id}")
                return True
            return False
            
        except Exception as e:
            logger.error(f"[BrowserManager] Error removing instance {instance_id}: {e}")
            return False
    
    async def get_healthy_instances(self) -> list:
        """Get list of healthy instance IDs."""
        healthy = []
        
        for instance_id, instance in self.instances.items():
            if await instance.health_check():
                healthy.append(instance_id)
        
        return healthy
    
    async def cleanup_all(self):
        """Cleanup all browser instances."""
        for instance_id in list(self.instances.keys()):
            await self.remove_instance(instance_id)
    
    def get_instance(self, instance_id: str) -> Optional[BrowserInstance]:
        """Get a specific browser instance."""
        return self.instances.get(instance_id)
    
    def get_all_instances(self) -> Dict[str, BrowserInstance]:
        """Get all browser instances."""
        return self.instances.copy()
    
    def get_instance_count(self) -> int:
        """Get total number of instances."""
        return len(self.instances)