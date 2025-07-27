"""
Instance Coordinator for LMArenaBridge Multi-Instance Architecture

This module coordinates multiple browser instances, handles scaling,
and manages the lifecycle of browser instances.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from .browser_manager import BrowserManager, BrowserInstance

logger = logging.getLogger(__name__)


class InstanceCoordinator:
    """Coordinates multiple browser instances and handles scaling."""
    
    def __init__(self, config: dict):
        self.config = config
        self.browser_manager = BrowserManager(config)
        self.instance_config = config.get('instances', {})
        self.browser_config = config.get('browser', {})
        self.instance_defaults = config.get('instance_defaults', {})
        
        # Scaling configuration
        self.min_instances = self.instance_config.get('min_instances', 1)
        self.max_instances = self.instance_config.get('max_instances', 5)
        self.initial_count = self.instance_config.get('initial_count', 1)
        self.auto_scale = self.instance_config.get('auto_scale', True)
        self.scale_up_threshold = self.instance_config.get('scale_up_threshold', 0.8)
        self.scale_down_threshold = self.instance_config.get('scale_down_threshold', 0.3)
        
        # Request tracking
        self.request_queue = asyncio.Queue()
        self.active_requests: Dict[str, dict] = {}
        self.request_history = []
        
        # Instance health tracking
        self.healthy_instances: set = set()
        self.unhealthy_instances: set = set()
        self.instance_metrics: Dict[str, dict] = {}
        
        # Scaling state
        self.last_scale_action = None
        self.scale_cooldown = 60  # seconds
        
    async def initialize(self) -> bool:
        """Initialize the coordinator and create initial instances."""
        try:
            logger.info(f"[InstanceCoordinator] Initializing with {self.initial_count} instances...")
            
            # Create initial instances
            for i in range(self.initial_count):
                instance_config = self._create_instance_config(f"initial-{i}")
                instance_id = await self.browser_manager.create_instance(instance_config)
                
                if instance_id:
                    self.healthy_instances.add(instance_id)
                    self._initialize_instance_metrics(instance_id)
                    logger.info(f"[InstanceCoordinator] Created initial instance: {instance_id}")
                else:
                    logger.error(f"[InstanceCoordinator] Failed to create initial instance {i}")
            
            if len(self.healthy_instances) == 0:
                logger.error("[InstanceCoordinator] Failed to create any initial instances")
                return False
            
            logger.info(f"[InstanceCoordinator] Successfully initialized with {len(self.healthy_instances)} instances")
            return True
            
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Failed to initialize: {e}")
            return False
    
    def _create_instance_config(self, suffix: str = "") -> dict:
        """Create configuration for a new instance."""
        config = {
            **self.instance_defaults,
            **self.browser_config,
            'suffix': suffix,
            'created_at': datetime.now().isoformat()
        }
        return config
    
    def _initialize_instance_metrics(self, instance_id: str):
        """Initialize metrics tracking for an instance."""
        self.instance_metrics[instance_id] = {
            'requests_handled': 0,
            'total_response_time': 0.0,
            'average_response_time': 0.0,
            'errors': 0,
            'last_request_time': None,
            'created_at': datetime.now(),
            'status': 'healthy'
        }
    
    async def create_instance(self, config: dict = None) -> Optional[str]:
        """Create a new browser instance."""
        try:
            if len(self.browser_manager.instances) >= self.max_instances:
                logger.warning("[InstanceCoordinator] Cannot create instance: max instances reached")
                return None
            
            instance_config = config or self._create_instance_config()
            instance_id = await self.browser_manager.create_instance(instance_config)
            
            if instance_id:
                self.healthy_instances.add(instance_id)
                self._initialize_instance_metrics(instance_id)
                logger.info(f"[InstanceCoordinator] Created new instance: {instance_id}")
                return instance_id
            
            return None
            
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Error creating instance: {e}")
            return None
    
    async def remove_instance(self, instance_id: str) -> bool:
        """Remove a browser instance."""
        try:
            if len(self.healthy_instances) <= self.min_instances:
                logger.warning(f"[InstanceCoordinator] Cannot remove instance {instance_id}: min instances limit")
                return False
            
            success = await self.browser_manager.remove_instance(instance_id)
            
            if success:
                self.healthy_instances.discard(instance_id)
                self.unhealthy_instances.discard(instance_id)
                self.instance_metrics.pop(instance_id, None)
                logger.info(f"[InstanceCoordinator] Removed instance: {instance_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Error removing instance {instance_id}: {e}")
            return False
    
    async def get_best_instance(self, strategy: str = 'least_busy') -> Optional[str]:
        """Get the best available instance based on the specified strategy."""
        try:
            if not self.healthy_instances:
                logger.warning("[InstanceCoordinator] No healthy instances available")
                return None
            
            if strategy == 'round_robin':
                return self._round_robin_selection()
            elif strategy == 'least_busy':
                return self._least_busy_selection()
            elif strategy == 'response_time':
                return self._fastest_response_selection()
            else:
                # Default to least busy
                return self._least_busy_selection()
                
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Error selecting instance: {e}")
            return None
    
    def _round_robin_selection(self) -> Optional[str]:
        """Select instance using round-robin strategy."""
        if not hasattr(self, '_round_robin_index'):
            self._round_robin_index = 0
        
        healthy_list = list(self.healthy_instances)
        if not healthy_list:
            return None
        
        instance_id = healthy_list[self._round_robin_index % len(healthy_list)]
        self._round_robin_index += 1
        return instance_id
    
    def _least_busy_selection(self) -> Optional[str]:
        """Select the least busy instance."""
        if not self.healthy_instances:
            return None
        
        # Count active requests per instance
        instance_loads = {}
        for instance_id in self.healthy_instances:
            active_count = sum(1 for req in self.active_requests.values() 
                             if req.get('instance_id') == instance_id)
            instance_loads[instance_id] = active_count
        
        # Return instance with minimum load
        return min(instance_loads.items(), key=lambda x: x[1])[0]
    
    def _fastest_response_selection(self) -> Optional[str]:
        """Select instance with best average response time."""
        if not self.healthy_instances:
            return None
        
        best_instance = None
        best_time = float('inf')
        
        for instance_id in self.healthy_instances:
            metrics = self.instance_metrics.get(instance_id, {})
            avg_time = metrics.get('average_response_time', float('inf'))
            
            if avg_time < best_time:
                best_time = avg_time
                best_instance = instance_id
        
        return best_instance or list(self.healthy_instances)[0]
    
    async def handle_request(self, request_id: str, payload: dict) -> Optional[str]:
        """Handle an incoming request by assigning it to an instance."""
        try:
            # Get load balancing strategy from config
            strategy = self.instance_config.get('load_balancing', 'least_busy')
            instance_id = await self.get_best_instance(strategy)
            
            if not instance_id:
                logger.error(f"[InstanceCoordinator] No available instance for request {request_id}")
                return None
            
            # Track the request
            self.active_requests[request_id] = {
                'instance_id': instance_id,
                'start_time': time.time(),
                'payload': payload
            }
            
            # Update instance metrics
            metrics = self.instance_metrics.get(instance_id, {})
            metrics['last_request_time'] = datetime.now()
            
            logger.debug(f"[InstanceCoordinator] Assigned request {request_id} to instance {instance_id}")
            return instance_id
            
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Error handling request {request_id}: {e}")
            return None
    
    async def complete_request(self, request_id: str, success: bool = True):
        """Mark a request as completed and update metrics."""
        try:
            if request_id not in self.active_requests:
                return
            
            request_info = self.active_requests.pop(request_id)
            instance_id = request_info['instance_id']
            response_time = time.time() - request_info['start_time']
            
            # Update instance metrics
            if instance_id in self.instance_metrics:
                metrics = self.instance_metrics[instance_id]
                metrics['requests_handled'] += 1
                metrics['total_response_time'] += response_time
                metrics['average_response_time'] = (
                    metrics['total_response_time'] / metrics['requests_handled']
                )
                
                if not success:
                    metrics['errors'] += 1
            
            # Add to request history for scaling decisions
            self.request_history.append({
                'timestamp': datetime.now(),
                'response_time': response_time,
                'success': success,
                'instance_id': instance_id
            })
            
            # Keep only recent history (last hour)
            cutoff_time = datetime.now().timestamp() - 3600
            self.request_history = [
                req for req in self.request_history 
                if req['timestamp'].timestamp() > cutoff_time
            ]
            
            logger.debug(f"[InstanceCoordinator] Completed request {request_id} in {response_time:.2f}s")
            
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Error completing request {request_id}: {e}")
    
    async def health_check_all_instances(self):
        """Perform health checks on all instances."""
        try:
            all_instances = set(self.browser_manager.instances.keys())
            newly_healthy = set()
            newly_unhealthy = set()
            
            for instance_id in all_instances:
                instance = self.browser_manager.get_instance(instance_id)
                if instance:
                    is_healthy = await instance.health_check()
                    
                    if is_healthy:
                        if instance_id in self.unhealthy_instances:
                            newly_healthy.add(instance_id)
                        self.healthy_instances.add(instance_id)
                        self.unhealthy_instances.discard(instance_id)
                        
                        # Update metrics
                        if instance_id in self.instance_metrics:
                            self.instance_metrics[instance_id]['status'] = 'healthy'
                    else:
                        if instance_id in self.healthy_instances:
                            newly_unhealthy.add(instance_id)
                        self.unhealthy_instances.add(instance_id)
                        self.healthy_instances.discard(instance_id)
                        
                        # Update metrics
                        if instance_id in self.instance_metrics:
                            self.instance_metrics[instance_id]['status'] = 'unhealthy'
            
            # Log status changes
            for instance_id in newly_healthy:
                logger.info(f"[InstanceCoordinator] Instance {instance_id} recovered")
            
            for instance_id in newly_unhealthy:
                logger.warning(f"[InstanceCoordinator] Instance {instance_id} became unhealthy")
            
            # Handle unhealthy instances
            await self._handle_unhealthy_instances()
            
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Error during health check: {e}")
    
    async def _handle_unhealthy_instances(self):
        """Handle unhealthy instances by replacing them."""
        for instance_id in list(self.unhealthy_instances):
            try:
                logger.info(f"[InstanceCoordinator] Replacing unhealthy instance {instance_id}")
                
                # Remove the unhealthy instance
                await self.remove_instance(instance_id)
                
                # Create a replacement if we're below minimum
                if len(self.healthy_instances) < self.min_instances:
                    replacement_id = await self.create_instance()
                    if replacement_id:
                        logger.info(f"[InstanceCoordinator] Created replacement instance {replacement_id}")
                    else:
                        logger.error("[InstanceCoordinator] Failed to create replacement instance")
                
            except Exception as e:
                logger.error(f"[InstanceCoordinator] Error handling unhealthy instance {instance_id}: {e}")
    
    async def scale_instances(self):
        """Auto-scale instances based on load and performance."""
        try:
            if not self.auto_scale:
                return
            
            # Check cooldown period
            if (self.last_scale_action and 
                time.time() - self.last_scale_action < self.scale_cooldown):
                return
            
            current_load = self._calculate_current_load()
            should_scale_up = current_load > self.scale_up_threshold
            should_scale_down = current_load < self.scale_down_threshold
            
            current_count = len(self.healthy_instances)
            
            if should_scale_up and current_count < self.max_instances:
                await self._scale_up()
            elif should_scale_down and current_count > self.min_instances:
                await self._scale_down()
                
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Error during scaling: {e}")
    
    def _calculate_current_load(self) -> float:
        """Calculate current system load (0.0 to 1.0)."""
        if not self.healthy_instances:
            return 1.0  # Max load if no healthy instances
        
        # Calculate based on active requests vs available instances
        active_count = len(self.active_requests)
        available_count = len(self.healthy_instances)
        
        return min(active_count / available_count, 1.0)
    
    async def _scale_up(self):
        """Scale up by adding an instance."""
        try:
            logger.info("[InstanceCoordinator] Scaling up: adding instance")
            instance_id = await self.create_instance()
            
            if instance_id:
                self.last_scale_action = time.time()
                logger.info(f"[InstanceCoordinator] Successfully scaled up: added {instance_id}")
            else:
                logger.error("[InstanceCoordinator] Failed to scale up: could not create instance")
                
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Error during scale up: {e}")
    
    async def _scale_down(self):
        """Scale down by removing an instance."""
        try:
            if len(self.healthy_instances) <= self.min_instances:
                return
            
            # Find the least busy instance to remove
            instance_loads = {}
            for instance_id in self.healthy_instances:
                active_count = sum(1 for req in self.active_requests.values()
                                 if req.get('instance_id') == instance_id)
                instance_loads[instance_id] = active_count
            
            # Remove instance with minimum load
            instance_to_remove = min(instance_loads.items(), key=lambda x: x[1])[0]
            
            logger.info(f"[InstanceCoordinator] Scaling down: removing instance {instance_to_remove}")
            success = await self.remove_instance(instance_to_remove)
            
            if success:
                self.last_scale_action = time.time()
                logger.info(f"[InstanceCoordinator] Successfully scaled down: removed {instance_to_remove}")
            else:
                logger.error(f"[InstanceCoordinator] Failed to scale down: could not remove {instance_to_remove}")
                
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Error during scale down: {e}")
    
    async def cleanup(self):
        """Cleanup all instances and resources."""
        try:
            logger.info("[InstanceCoordinator] Cleaning up all instances...")
            await self.browser_manager.cleanup_all()
            self.healthy_instances.clear()
            self.unhealthy_instances.clear()
            self.instance_metrics.clear()
            self.active_requests.clear()
            logger.info("[InstanceCoordinator] Cleanup completed")
            
        except Exception as e:
            logger.error(f"[InstanceCoordinator] Error during cleanup: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get current coordinator status."""
        return {
            'total_instances': len(self.browser_manager.instances),
            'healthy_instances': len(self.healthy_instances),
            'unhealthy_instances': len(self.unhealthy_instances),
            'active_requests': len(self.active_requests),
            'current_load': self._calculate_current_load(),
            'min_instances': self.min_instances,
            'max_instances': self.max_instances,
            'auto_scale': self.auto_scale,
            'last_scale_action': self.last_scale_action,
            'instance_metrics': self.instance_metrics
        }
    
    def get_instance_list(self) -> List[Dict[str, Any]]:
        """Get detailed list of all instances."""
        instances = []
        
        for instance_id, instance in self.browser_manager.get_all_instances().items():
            status_info = instance.get_status()
            metrics = self.instance_metrics.get(instance_id, {})
            
            instances.append({
                **status_info,
                'metrics': metrics,
                'is_healthy': instance_id in self.healthy_instances
            })
        
        return instances