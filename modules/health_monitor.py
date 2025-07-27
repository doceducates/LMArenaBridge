"""
Health Monitor for LMArenaBridge Multi-Instance Architecture

This module continuously monitors the health of browser instances,
detects failures, and triggers recovery actions.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Callable

logger = logging.getLogger(__name__)


class HealthMonitor:
    """Monitors health of browser instances and handles failures."""
    
    def __init__(self, coordinator, config: dict):
        self.coordinator = coordinator
        self.config = config
        self.monitoring_config = config.get('monitoring', {})
        self.instance_config = config.get('instances', {})
        
        # Health check configuration
        self.health_check_interval = self.instance_config.get('health_check_interval', 10)
        self.instance_timeout = self.instance_config.get('instance_timeout', 30)
        self.max_retries = self.instance_config.get('max_retries', 3)
        
        # Alert thresholds
        alert_thresholds = self.monitoring_config.get('alert_thresholds', {})
        self.response_time_threshold = alert_thresholds.get('response_time', 10)
        self.error_rate_threshold = alert_thresholds.get('error_rate', 0.1)
        self.instance_failure_rate_threshold = alert_thresholds.get('instance_failure_rate', 0.2)
        
        # Monitoring state
        self.is_monitoring = False
        self.health_check_task = None
        self.instance_health_history: Dict[str, List[dict]] = {}
        self.system_health_history = []
        self.alert_callbacks: List[Callable] = []
        
        # Metrics
        self.total_health_checks = 0
        self.failed_health_checks = 0
        self.instances_recovered = 0
        self.instances_failed = 0
        
    async def start_monitoring(self):
        """Start the health monitoring process."""
        if self.is_monitoring:
            logger.warning("[HealthMonitor] Monitoring is already running")
            return
        
        self.is_monitoring = True
        self.health_check_task = asyncio.create_task(self._monitoring_loop())
        logger.info(f"[HealthMonitor] Started monitoring with {self.health_check_interval}s interval")
    
    async def stop_monitoring(self):
        """Stop the health monitoring process."""
        if not self.is_monitoring:
            return
        
        self.is_monitoring = False
        if self.health_check_task:
            self.health_check_task.cancel()
            try:
                await self.health_check_task
            except asyncio.CancelledError:
                pass
        
        logger.info("[HealthMonitor] Stopped monitoring")
    
    async def _monitoring_loop(self):
        """Main monitoring loop."""
        try:
            while self.is_monitoring:
                await self._perform_health_checks()
                await self._analyze_system_health()
                await self._cleanup_old_data()
                await asyncio.sleep(self.health_check_interval)
                
        except asyncio.CancelledError:
            logger.info("[HealthMonitor] Monitoring loop cancelled")
        except Exception as e:
            logger.error(f"[HealthMonitor] Error in monitoring loop: {e}")
            # Restart monitoring after a delay
            await asyncio.sleep(5)
            if self.is_monitoring:
                self.health_check_task = asyncio.create_task(self._monitoring_loop())
    
    async def _perform_health_checks(self):
        """Perform health checks on all instances."""
        try:
            # Get all instances from coordinator
            all_instances = self.coordinator.browser_manager.get_all_instances()
            
            if not all_instances:
                logger.debug("[HealthMonitor] No instances to check")
                return
            
            # Perform health checks concurrently
            health_check_tasks = []
            for instance_id, instance in all_instances.items():
                task = asyncio.create_task(
                    self._check_instance_health(instance_id, instance)
                )
                health_check_tasks.append(task)
            
            # Wait for all health checks to complete
            results = await asyncio.gather(*health_check_tasks, return_exceptions=True)
            
            # Process results
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    instance_id = list(all_instances.keys())[i]
                    logger.error(f"[HealthMonitor] Health check failed for {instance_id}: {result}")
                    await self._handle_instance_failure(instance_id, str(result))
            
            self.total_health_checks += len(all_instances)
            
        except Exception as e:
            logger.error(f"[HealthMonitor] Error performing health checks: {e}")
    
    async def _check_instance_health(self, instance_id: str, instance) -> dict:
        """Check health of a specific instance."""
        start_time = time.time()
        health_result = {
            'instance_id': instance_id,
            'timestamp': datetime.now(),
            'healthy': False,
            'response_time': 0.0,
            'error': None,
            'details': {}
        }
        
        try:
            # Perform the actual health check with timeout
            health_check_task = asyncio.create_task(instance.health_check())
            
            try:
                is_healthy = await asyncio.wait_for(
                    health_check_task, 
                    timeout=self.instance_timeout
                )
                
                health_result['healthy'] = is_healthy
                health_result['response_time'] = time.time() - start_time
                
                # Get additional instance details
                health_result['details'] = {
                    'status': instance.status,
                    'request_count': instance.request_count,
                    'session_id': instance.session_id,
                    'last_activity': instance.last_activity.isoformat() if instance.last_activity else None
                }
                
                if is_healthy:
                    await self._record_healthy_check(instance_id, health_result)
                else:
                    await self._handle_instance_failure(instance_id, "Health check returned false")
                
            except asyncio.TimeoutError:
                health_result['error'] = f"Health check timeout ({self.instance_timeout}s)"
                await self._handle_instance_failure(instance_id, health_result['error'])
                
        except Exception as e:
            health_result['error'] = str(e)
            health_result['response_time'] = time.time() - start_time
            await self._handle_instance_failure(instance_id, str(e))
        
        # Record health check result
        self._record_health_check(instance_id, health_result)
        
        return health_result
    
    async def _record_healthy_check(self, instance_id: str, health_result: dict):
        """Record a successful health check."""
        # Check if instance was previously unhealthy
        if instance_id in self.coordinator.unhealthy_instances:
            logger.info(f"[HealthMonitor] Instance {instance_id} recovered")
            self.instances_recovered += 1
            await self._trigger_alert('instance_recovered', {
                'instance_id': instance_id,
                'recovery_time': datetime.now()
            })
        
        # Update coordinator state
        self.coordinator.healthy_instances.add(instance_id)
        self.coordinator.unhealthy_instances.discard(instance_id)
    
    async def _handle_instance_failure(self, instance_id: str, error: str):
        """Handle instance failure."""
        logger.warning(f"[HealthMonitor] Instance {instance_id} failed health check: {error}")
        
        # Check if this is a new failure
        was_healthy = instance_id in self.coordinator.healthy_instances
        
        # Update coordinator state
        self.coordinator.unhealthy_instances.add(instance_id)
        self.coordinator.healthy_instances.discard(instance_id)
        
        if was_healthy:
            self.instances_failed += 1
            self.failed_health_checks += 1
            
            # Trigger alert for new failure
            await self._trigger_alert('instance_failed', {
                'instance_id': instance_id,
                'error': error,
                'failure_time': datetime.now()
            })
            
            # Check if we need to create replacement instances
            await self._check_replacement_needed()
    
    async def _check_replacement_needed(self):
        """Check if replacement instances are needed."""
        healthy_count = len(self.coordinator.healthy_instances)
        min_instances = self.coordinator.min_instances
        
        if healthy_count < min_instances:
            needed = min_instances - healthy_count
            logger.info(f"[HealthMonitor] Need {needed} replacement instances")
            
            # Request coordinator to create replacement instances
            for _ in range(needed):
                try:
                    instance_id = await self.coordinator.create_instance()
                    if instance_id:
                        logger.info(f"[HealthMonitor] Created replacement instance: {instance_id}")
                    else:
                        logger.error("[HealthMonitor] Failed to create replacement instance")
                except Exception as e:
                    logger.error(f"[HealthMonitor] Error creating replacement instance: {e}")
    
    def _record_health_check(self, instance_id: str, health_result: dict):
        """Record health check result in history."""
        if instance_id not in self.instance_health_history:
            self.instance_health_history[instance_id] = []
        
        self.instance_health_history[instance_id].append(health_result)
        
        # Keep only recent history (last 100 checks per instance)
        if len(self.instance_health_history[instance_id]) > 100:
            self.instance_health_history[instance_id] = self.instance_health_history[instance_id][-100:]
    
    async def _analyze_system_health(self):
        """Analyze overall system health and trigger alerts if needed."""
        try:
            current_time = datetime.now()
            
            # Calculate system metrics
            total_instances = len(self.coordinator.browser_manager.instances)
            healthy_instances = len(self.coordinator.healthy_instances)
            unhealthy_instances = len(self.coordinator.unhealthy_instances)
            
            if total_instances == 0:
                return
            
            # Calculate failure rate
            failure_rate = unhealthy_instances / total_instances
            
            # Calculate average response time
            recent_checks = []
            for instance_history in self.instance_health_history.values():
                recent_checks.extend([
                    check for check in instance_history[-10:]  # Last 10 checks
                    if check['healthy'] and check['response_time'] > 0
                ])
            
            avg_response_time = 0.0
            if recent_checks:
                avg_response_time = sum(check['response_time'] for check in recent_checks) / len(recent_checks)
            
            # Record system health
            system_health = {
                'timestamp': current_time,
                'total_instances': total_instances,
                'healthy_instances': healthy_instances,
                'unhealthy_instances': unhealthy_instances,
                'failure_rate': failure_rate,
                'average_response_time': avg_response_time,
                'active_requests': len(self.coordinator.active_requests)
            }
            
            self.system_health_history.append(system_health)
            
            # Check alert thresholds
            await self._check_alert_thresholds(system_health)
            
        except Exception as e:
            logger.error(f"[HealthMonitor] Error analyzing system health: {e}")
    
    async def _check_alert_thresholds(self, system_health: dict):
        """Check if any alert thresholds are exceeded."""
        # Check failure rate threshold
        if system_health['failure_rate'] > self.instance_failure_rate_threshold:
            await self._trigger_alert('high_failure_rate', {
                'failure_rate': system_health['failure_rate'],
                'threshold': self.instance_failure_rate_threshold,
                'unhealthy_instances': system_health['unhealthy_instances'],
                'total_instances': system_health['total_instances']
            })
        
        # Check response time threshold
        if system_health['average_response_time'] > self.response_time_threshold:
            await self._trigger_alert('high_response_time', {
                'response_time': system_health['average_response_time'],
                'threshold': self.response_time_threshold
            })
        
        # Check if no healthy instances
        if system_health['healthy_instances'] == 0:
            await self._trigger_alert('no_healthy_instances', {
                'total_instances': system_health['total_instances']
            })
    
    async def _trigger_alert(self, alert_type: str, data: dict):
        """Trigger an alert."""
        alert = {
            'type': alert_type,
            'timestamp': datetime.now(),
            'data': data
        }
        
        logger.warning(f"[HealthMonitor] ALERT: {alert_type} - {data}")
        
        # Call registered alert callbacks
        for callback in self.alert_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(alert)
                else:
                    callback(alert)
            except Exception as e:
                logger.error(f"[HealthMonitor] Error in alert callback: {e}")
    
    def add_alert_callback(self, callback: Callable):
        """Add an alert callback function."""
        self.alert_callbacks.append(callback)
    
    def remove_alert_callback(self, callback: Callable):
        """Remove an alert callback function."""
        if callback in self.alert_callbacks:
            self.alert_callbacks.remove(callback)
    
    async def _cleanup_old_data(self):
        """Clean up old monitoring data."""
        try:
            cutoff_time = datetime.now() - timedelta(hours=24)
            
            # Clean up system health history
            self.system_health_history = [
                record for record in self.system_health_history
                if record['timestamp'] > cutoff_time
            ]
            
            # Clean up instance health history
            for instance_id in list(self.instance_health_history.keys()):
                self.instance_health_history[instance_id] = [
                    check for check in self.instance_health_history[instance_id]
                    if check['timestamp'] > cutoff_time
                ]
                
                # Remove empty histories
                if not self.instance_health_history[instance_id]:
                    del self.instance_health_history[instance_id]
                    
        except Exception as e:
            logger.error(f"[HealthMonitor] Error cleaning up old data: {e}")
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get current health monitoring status."""
        return {
            'is_monitoring': self.is_monitoring,
            'health_check_interval': self.health_check_interval,
            'total_health_checks': self.total_health_checks,
            'failed_health_checks': self.failed_health_checks,
            'instances_recovered': self.instances_recovered,
            'instances_failed': self.instances_failed,
            'alert_callbacks_count': len(self.alert_callbacks)
        }
    
    def get_instance_health_history(self, instance_id: str, limit: int = 50) -> List[dict]:
        """Get health history for a specific instance."""
        history = self.instance_health_history.get(instance_id, [])
        return history[-limit:] if limit else history
    
    def get_system_health_history(self, limit: int = 100) -> List[dict]:
        """Get system health history."""
        history = self.system_health_history
        return history[-limit:] if limit else history
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get summary of health monitoring metrics."""
        total_instances = len(self.coordinator.browser_manager.instances)
        healthy_instances = len(self.coordinator.healthy_instances)
        
        return {
            'monitoring_status': {
                'is_active': self.is_monitoring,
                'check_interval': self.health_check_interval,
                'total_checks': self.total_health_checks,
                'failed_checks': self.failed_health_checks
            },
            'instance_status': {
                'total': total_instances,
                'healthy': healthy_instances,
                'unhealthy': len(self.coordinator.unhealthy_instances),
                'failure_rate': len(self.coordinator.unhealthy_instances) / max(total_instances, 1)
            },
            'recovery_stats': {
                'instances_recovered': self.instances_recovered,
                'instances_failed': self.instances_failed
            },
            'thresholds': {
                'response_time': self.response_time_threshold,
                'error_rate': self.error_rate_threshold,
                'failure_rate': self.instance_failure_rate_threshold
            }
        }