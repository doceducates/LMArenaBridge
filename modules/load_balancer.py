"""
Load Balancer for LMArenaBridge Multi-Instance Architecture

This module handles request routing, load balancing strategies,
and failover logic for multiple browser instances.
"""

import asyncio
import logging
import time
import random
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime

logger = logging.getLogger(__name__)


class LoadBalancer:
    """Handles request routing and load balancing across browser instances."""
    
    def __init__(self, coordinator, config: dict):
        self.coordinator = coordinator
        self.config = config
        self.instance_config = config.get('instances', {})
        
        # Load balancing configuration
        self.strategy = self.instance_config.get('load_balancing', 'least_busy')
        self.max_retries = self.instance_config.get('max_retries', 3)
        self.retry_delay = 1.0  # seconds
        
        # Strategy implementations
        self.strategies = {
            'round_robin': self._round_robin_strategy,
            'least_busy': self._least_busy_strategy,
            'response_time': self._response_time_strategy,
            'random': self._random_strategy,
            'weighted_round_robin': self._weighted_round_robin_strategy
        }
        
        # Round robin state
        self._round_robin_index = 0
        
        # Weighted round robin state
        self._weighted_counters = {}
        
        # Request tracking
        self.active_requests: Dict[str, dict] = {}
        self.request_history = []
        self.routing_stats = {
            'total_requests': 0,
            'successful_routes': 0,
            'failed_routes': 0,
            'retries': 0,
            'strategy_usage': {}
        }
        
        # Performance tracking
        self.instance_performance: Dict[str, dict] = {}
        
    async def route_request(self, request_id: str, payload: dict, 
                          strategy_override: str = None) -> Optional[str]:
        """Route a request to the best available instance."""
        try:
            strategy = strategy_override or self.strategy
            self.routing_stats['total_requests'] += 1
            self.routing_stats['strategy_usage'][strategy] = (
                self.routing_stats['strategy_usage'].get(strategy, 0) + 1
            )
            
            # Try to route the request with retries
            for attempt in range(self.max_retries + 1):
                instance_id = await self._select_instance(strategy)
                
                if not instance_id:
                    logger.warning(f"[LoadBalancer] No available instance for request {request_id}")
                    if attempt < self.max_retries:
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
                        continue
                    break
                
                # Validate instance is still healthy
                if not await self._validate_instance(instance_id):
                    logger.warning(f"[LoadBalancer] Instance {instance_id} failed validation")
                    if attempt < self.max_retries:
                        self.routing_stats['retries'] += 1
                        await asyncio.sleep(self.retry_delay)
                        continue
                    break
                
                # Successfully routed
                await self._track_request(request_id, instance_id, payload)
                self.routing_stats['successful_routes'] += 1
                
                logger.debug(f"[LoadBalancer] Routed request {request_id} to instance {instance_id} "
                           f"(strategy: {strategy}, attempt: {attempt + 1})")
                return instance_id
            
            # All attempts failed
            self.routing_stats['failed_routes'] += 1
            logger.error(f"[LoadBalancer] Failed to route request {request_id} after {self.max_retries + 1} attempts")
            return None
            
        except Exception as e:
            logger.error(f"[LoadBalancer] Error routing request {request_id}: {e}")
            self.routing_stats['failed_routes'] += 1
            return None
    
    async def _select_instance(self, strategy: str) -> Optional[str]:
        """Select an instance using the specified strategy."""
        try:
            if strategy not in self.strategies:
                logger.warning(f"[LoadBalancer] Unknown strategy '{strategy}', using 'least_busy'")
                strategy = 'least_busy'
            
            strategy_func = self.strategies[strategy]
            return await strategy_func()
            
        except Exception as e:
            logger.error(f"[LoadBalancer] Error in strategy '{strategy}': {e}")
            # Fallback to least busy
            return await self._least_busy_strategy()
    
    async def _round_robin_strategy(self) -> Optional[str]:
        """Round robin load balancing strategy."""
        healthy_instances = list(self.coordinator.healthy_instances)
        
        if not healthy_instances:
            return None
        
        instance_id = healthy_instances[self._round_robin_index % len(healthy_instances)]
        self._round_robin_index += 1
        
        return instance_id
    
    async def _least_busy_strategy(self) -> Optional[str]:
        """Least busy load balancing strategy."""
        healthy_instances = list(self.coordinator.healthy_instances)
        
        if not healthy_instances:
            return None
        
        # Count active requests per instance
        instance_loads = {}
        for instance_id in healthy_instances:
            active_count = sum(1 for req in self.active_requests.values() 
                             if req.get('instance_id') == instance_id)
            instance_loads[instance_id] = active_count
        
        # Return instance with minimum load
        return min(instance_loads.items(), key=lambda x: x[1])[0]
    
    async def _response_time_strategy(self) -> Optional[str]:
        """Response time based load balancing strategy."""
        healthy_instances = list(self.coordinator.healthy_instances)
        
        if not healthy_instances:
            return None
        
        # Get instance with best average response time
        best_instance = None
        best_time = float('inf')
        
        for instance_id in healthy_instances:
            perf = self.instance_performance.get(instance_id, {})
            avg_time = perf.get('avg_response_time', float('inf'))
            
            if avg_time < best_time:
                best_time = avg_time
                best_instance = instance_id
        
        return best_instance or healthy_instances[0]
    
    async def _random_strategy(self) -> Optional[str]:
        """Random load balancing strategy."""
        healthy_instances = list(self.coordinator.healthy_instances)
        
        if not healthy_instances:
            return None
        
        return random.choice(healthy_instances)
    
    async def _weighted_round_robin_strategy(self) -> Optional[str]:
        """Weighted round robin based on instance performance."""
        healthy_instances = list(self.coordinator.healthy_instances)
        
        if not healthy_instances:
            return None
        
        # Calculate weights based on performance (inverse of response time)
        weights = {}
        for instance_id in healthy_instances:
            perf = self.instance_performance.get(instance_id, {})
            avg_time = perf.get('avg_response_time', 1.0)
            # Higher weight for faster instances
            weights[instance_id] = 1.0 / max(avg_time, 0.1)
        
        # Weighted selection
        total_weight = sum(weights.values())
        if total_weight == 0:
            return random.choice(healthy_instances)
        
        # Normalize weights
        for instance_id in weights:
            weights[instance_id] /= total_weight
        
        # Select based on weights
        rand_val = random.random()
        cumulative = 0.0
        
        for instance_id, weight in weights.items():
            cumulative += weight
            if rand_val <= cumulative:
                return instance_id
        
        return healthy_instances[-1]  # Fallback
    
    async def _validate_instance(self, instance_id: str) -> bool:
        """Validate that an instance is healthy and available."""
        try:
            # Check if instance is in healthy set
            if instance_id not in self.coordinator.healthy_instances:
                return False
            
            # Get the actual instance
            instance = self.coordinator.browser_manager.get_instance(instance_id)
            if not instance:
                return False
            
            # Check instance status
            if instance.status != 'ready':
                return False
            
            # Quick health check (optional, might be too slow)
            # return await instance.health_check()
            
            return True
            
        except Exception as e:
            logger.error(f"[LoadBalancer] Error validating instance {instance_id}: {e}")
            return False
    
    async def _track_request(self, request_id: str, instance_id: str, payload: dict):
        """Track a routed request."""
        self.active_requests[request_id] = {
            'instance_id': instance_id,
            'start_time': time.time(),
            'payload_size': len(str(payload)),
            'routed_at': datetime.now()
        }
        
        # Initialize performance tracking for new instances
        if instance_id not in self.instance_performance:
            self.instance_performance[instance_id] = {
                'total_requests': 0,
                'total_response_time': 0.0,
                'avg_response_time': 0.0,
                'success_count': 0,
                'error_count': 0,
                'last_request_time': None
            }
        
        # Update instance performance
        perf = self.instance_performance[instance_id]
        perf['total_requests'] += 1
        perf['last_request_time'] = datetime.now()
    
    async def complete_request(self, request_id: str, success: bool = True, 
                             response_size: int = 0):
        """Mark a request as completed and update performance metrics."""
        try:
            if request_id not in self.active_requests:
                logger.warning(f"[LoadBalancer] Request {request_id} not found in active requests")
                return
            
            request_info = self.active_requests.pop(request_id)
            instance_id = request_info['instance_id']
            response_time = time.time() - request_info['start_time']
            
            # Update instance performance
            if instance_id in self.instance_performance:
                perf = self.instance_performance[instance_id]
                perf['total_response_time'] += response_time
                perf['avg_response_time'] = (
                    perf['total_response_time'] / perf['total_requests']
                )
                
                if success:
                    perf['success_count'] += 1
                else:
                    perf['error_count'] += 1
            
            # Add to request history
            self.request_history.append({
                'request_id': request_id,
                'instance_id': instance_id,
                'response_time': response_time,
                'success': success,
                'payload_size': request_info['payload_size'],
                'response_size': response_size,
                'completed_at': datetime.now()
            })
            
            # Keep only recent history (last 1000 requests)
            if len(self.request_history) > 1000:
                self.request_history = self.request_history[-1000:]
            
            logger.debug(f"[LoadBalancer] Completed request {request_id} "
                        f"(instance: {instance_id}, time: {response_time:.2f}s, success: {success})")
            
        except Exception as e:
            logger.error(f"[LoadBalancer] Error completing request {request_id}: {e}")
    
    async def handle_instance_failure(self, instance_id: str):
        """Handle failure of an instance by redistributing its requests."""
        try:
            # Find all active requests for this instance
            failed_requests = [
                req_id for req_id, req_info in self.active_requests.items()
                if req_info.get('instance_id') == instance_id
            ]
            
            if failed_requests:
                logger.info(f"[LoadBalancer] Redistributing {len(failed_requests)} requests "
                           f"from failed instance {instance_id}")
                
                # Mark requests as failed (they will need to be retried by the client)
                for request_id in failed_requests:
                    await self.complete_request(request_id, success=False)
            
            # Update performance tracking
            if instance_id in self.instance_performance:
                perf = self.instance_performance[instance_id]
                perf['error_count'] += len(failed_requests)
            
        except Exception as e:
            logger.error(f"[LoadBalancer] Error handling instance failure {instance_id}: {e}")
    
    def set_strategy(self, strategy: str):
        """Change the load balancing strategy."""
        if strategy in self.strategies:
            self.strategy = strategy
            logger.info(f"[LoadBalancer] Changed strategy to: {strategy}")
        else:
            logger.warning(f"[LoadBalancer] Unknown strategy: {strategy}")
    
    def get_available_strategies(self) -> List[str]:
        """Get list of available load balancing strategies."""
        return list(self.strategies.keys())
    
    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing statistics."""
        return {
            **self.routing_stats,
            'current_strategy': self.strategy,
            'active_requests_count': len(self.active_requests),
            'request_history_count': len(self.request_history)
        }
    
    def get_instance_performance(self, instance_id: str = None) -> Dict[str, Any]:
        """Get performance metrics for instances."""
        if instance_id:
            return self.instance_performance.get(instance_id, {})
        return self.instance_performance.copy()
    
    def get_load_distribution(self) -> Dict[str, Any]:
        """Get current load distribution across instances."""
        distribution = {}
        
        for instance_id in self.coordinator.healthy_instances:
            active_count = sum(1 for req in self.active_requests.values() 
                             if req.get('instance_id') == instance_id)
            
            perf = self.instance_performance.get(instance_id, {})
            
            distribution[instance_id] = {
                'active_requests': active_count,
                'total_requests': perf.get('total_requests', 0),
                'avg_response_time': perf.get('avg_response_time', 0.0),
                'success_rate': (
                    perf.get('success_count', 0) / max(perf.get('total_requests', 1), 1)
                ),
                'error_count': perf.get('error_count', 0)
            }
        
        return distribution
    
    def reset_stats(self):
        """Reset all statistics and performance data."""
        self.routing_stats = {
            'total_requests': 0,
            'successful_routes': 0,
            'failed_routes': 0,
            'retries': 0,
            'strategy_usage': {}
        }
        self.instance_performance.clear()
        self.request_history.clear()
        self._round_robin_index = 0
        self._weighted_counters.clear()
        
        logger.info("[LoadBalancer] Statistics reset")
    
    async def cleanup(self):
        """Cleanup load balancer resources."""
        try:
            # Complete any remaining active requests as failed
            for request_id in list(self.active_requests.keys()):
                await self.complete_request(request_id, success=False)
            
            logger.info("[LoadBalancer] Cleanup completed")
            
        except Exception as e:
            logger.error(f"[LoadBalancer] Error during cleanup: {e}")