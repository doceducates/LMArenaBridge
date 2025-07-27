/**
 * Dashboard Controller for LMArenaBridge Multi-Instance Management
 * Handles UI interactions, data visualization, and real-time updates
 */

class Dashboard {
    constructor() {
        this.charts = {};
        this.instances = new Map();
        this.alerts = [];
        this.logs = [];
        this.maxLogs = 1000;
        this.refreshInterval = 5000; // 5 seconds
        this.refreshTimer = null;
        
        this.init();
    }
    
    init() {
        this.setupEventListeners();
        this.initializeCharts();
        this.startAutoRefresh();
        console.log('Dashboard initialized');
    }
    
    setupEventListeners() {
        // Refresh button
        document.getElementById('refresh-btn')?.addEventListener('click', () => {
            this.refreshData();
        });
        
        // Add instance button
        document.getElementById('add-instance-btn')?.addEventListener('click', () => {
            this.showAddInstanceModal();
        });
        
        // Create instance button (in modal)
        document.getElementById('create-instance-btn')?.addEventListener('click', () => {
            this.createInstance();
        });
        
        // Instance mode change
        document.getElementById('instance-mode')?.addEventListener('change', (e) => {
            this.toggleBattleTargetGroup(e.target.value === 'battle');
        });
        
        // Load balancer settings
        document.getElementById('update-lb-settings')?.addEventListener('click', () => {
            this.updateLoadBalancerSettings();
        });
        
        // Save settings
        document.getElementById('save-settings-btn')?.addEventListener('click', () => {
            this.saveSettings();
        });
        
        // Clear logs
        document.getElementById('clear-logs-btn')?.addEventListener('click', () => {
            this.clearLogs();
        });
        
        // Log level filter
        document.getElementById('log-level-filter')?.addEventListener('change', (e) => {
            this.filterLogs(e.target.value);
        });
        
        // Auto-scale button
        document.getElementById('scale-instances-btn')?.addEventListener('click', () => {
            this.triggerAutoScale();
        });
    }
    
    initializeCharts() {
        // Health metrics chart
        const healthCtx = document.getElementById('health-chart');
        if (healthCtx) {
            this.charts.health = new Chart(healthCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Healthy Instances',
                        data: [],
                        borderColor: '#198754',
                        backgroundColor: 'rgba(25, 135, 84, 0.1)',
                        tension: 0.4
                    }, {
                        label: 'Total Instances',
                        data: [],
                        borderColor: '#0d6efd',
                        backgroundColor: 'rgba(13, 110, 253, 0.1)',
                        tension: 0.4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            labels: { color: '#fff' }
                        }
                    },
                    scales: {
                        x: { 
                            ticks: { color: '#fff' },
                            grid: { color: 'rgba(255, 255, 255, 0.1)' }
                        },
                        y: { 
                            ticks: { color: '#fff' },
                            grid: { color: 'rgba(255, 255, 255, 0.1)' }
                        }
                    }
                }
            });
        }
        
        // Response time chart
        const responseCtx = document.getElementById('response-time-chart');
        if (responseCtx) {
            this.charts.responseTime = new Chart(responseCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Average Response Time (ms)',
                        data: [],
                        borderColor: '#ffc107',
                        backgroundColor: 'rgba(255, 193, 7, 0.1)',
                        tension: 0.4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            labels: { color: '#fff' }
                        }
                    },
                    scales: {
                        x: { 
                            ticks: { color: '#fff' },
                            grid: { color: 'rgba(255, 255, 255, 0.1)' }
                        },
                        y: { 
                            ticks: { color: '#fff' },
                            grid: { color: 'rgba(255, 255, 255, 0.1)' }
                        }
                    }
                }
            });
        }
        
        // Load distribution chart
        const loadCtx = document.getElementById('load-distribution-chart');
        if (loadCtx) {
            this.charts.loadDistribution = new Chart(loadCtx, {
                type: 'doughnut',
                data: {
                    labels: [],
                    datasets: [{
                        data: [],
                        backgroundColor: [
                            '#198754', '#0d6efd', '#ffc107', '#dc3545', 
                            '#6f42c1', '#20c997', '#fd7e14', '#e83e8c'
                        ]
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            labels: { color: '#fff' }
                        }
                    }
                }
            });
        }
    }
    
    async refreshData() {
        try {
            // Show loading state
            this.setLoadingState(true);
            
            // Fetch system status
            const statusResponse = await fetch('/gui/api/status');
            if (statusResponse.ok) {
                const statusData = await statusResponse.json();
                this.updateSystemStatus(statusData);
            }
            
            // Fetch instances
            const instancesResponse = await fetch('/gui/api/instances');
            if (instancesResponse.ok) {
                const instancesData = await instancesResponse.json();
                this.updateInstancesTable(instancesData.instances);
            }
            
            // Fetch metrics
            const metricsResponse = await fetch('/gui/api/metrics');
            if (metricsResponse.ok) {
                const metricsData = await metricsResponse.json();
                this.updateMetrics(metricsData);
            }
            
        } catch (error) {
            console.error('Error refreshing data:', error);
            this.showAlert({
                type: 'error',
                message: 'Failed to refresh data: ' + error.message,
                timestamp: new Date()
            });
        } finally {
            this.setLoadingState(false);
        }
    }
    
    updateSystemStatus(data) {
        // Update overview cards
        const coordinator = data.coordinator || {};
        const healthMonitor = data.health_monitor || {};
        const loadBalancer = data.load_balancer || {};
        
        this.updateElement('total-instances', coordinator.total_instances || 0);
        this.updateElement('healthy-instances', coordinator.healthy_instances || 0);
        this.updateElement('active-requests', coordinator.active_requests || 0);
        
        // Calculate and display system load
        const load = coordinator.current_load || 0;
        this.updateElement('system-load', `${(load * 100).toFixed(1)}%`);
        
        // Update load balancer stats
        this.updateElement('total-requests', loadBalancer.total_requests || 0);
        this.updateElement('successful-requests', loadBalancer.successful_routes || 0);
        this.updateElement('failed-requests', loadBalancer.failed_routes || 0);
        this.updateElement('retry-count', loadBalancer.retries || 0);
        
        // Update charts with new data
        this.updateHealthChart(coordinator);
        this.updateLoadDistributionChart(data);
    }
    
    updateInstancesTable(instances) {
        const tbody = document.getElementById('instances-table-body');
        if (!tbody) return;
        
        tbody.innerHTML = '';
        
        instances.forEach(instance => {
            this.instances.set(instance.instance_id, instance);
            
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>
                    <code>${instance.instance_id.substring(0, 8)}...</code>
                </td>
                <td>
                    <span class="status-indicator ${this.getStatusClass(instance.status)}"></span>
                    ${instance.status}
                    ${instance.is_healthy ? '<i class="fas fa-heart text-success ms-1"></i>' : '<i class="fas fa-heart-broken text-danger ms-1"></i>'}
                </td>
                <td>
                    <span class="badge bg-primary">${instance.mode}</span>
                    ${instance.battle_target ? `<span class="badge bg-secondary ms-1">Target ${instance.battle_target}</span>` : ''}
                </td>
                <td>
                    <span class="badge bg-info">${instance.request_count || 0}</span>
                    ${instance.metrics ? `<small class="text-muted d-block">Max: ${instance.max_requests_per_session || 'N/A'}</small>` : ''}
                </td>
                <td>
                    ${instance.metrics && instance.metrics.average_response_time ? 
                        `${(instance.metrics.average_response_time * 1000).toFixed(0)}ms` : 'N/A'}
                </td>
                <td>
                    <small>${instance.last_activity ? this.formatTimestamp(instance.last_activity) : 'Never'}</small>
                </td>
                <td class="instance-actions">
                    <button class="btn btn-sm btn-outline-info" onclick="dashboard.healthCheckInstance('${instance.instance_id}')" title="Health Check">
                        <i class="fas fa-heartbeat"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-warning" onclick="dashboard.regenerateSession('${instance.instance_id}')" title="Regenerate Session">
                        <i class="fas fa-sync-alt"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="dashboard.removeInstanceConfirm('${instance.instance_id}')" title="Remove Instance">
                        <i class="fas fa-trash"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        });
    }
    
    updateHealthChart(coordinator) {
        if (!this.charts.health) return;
        
        const now = new Date().toLocaleTimeString();
        const chart = this.charts.health;
        
        // Add new data point
        chart.data.labels.push(now);
        chart.data.datasets[0].data.push(coordinator.healthy_instances || 0);
        chart.data.datasets[1].data.push(coordinator.total_instances || 0);
        
        // Keep only last 20 data points
        if (chart.data.labels.length > 20) {
            chart.data.labels.shift();
            chart.data.datasets[0].data.shift();
            chart.data.datasets[1].data.shift();
        }
        
        chart.update('none');
    }
    
    updateLoadDistributionChart(data) {
        if (!this.charts.loadDistribution || !data.load_balancer) return;
        
        const loadBalancer = data.load_balancer;
        const distribution = loadBalancer.load_distribution || {};
        
        const labels = Object.keys(distribution).map(id => id.substring(0, 8) + '...');
        const values = Object.values(distribution).map(d => d.active_requests || 0);
        
        this.charts.loadDistribution.data.labels = labels;
        this.charts.loadDistribution.data.datasets[0].data = values;
        this.charts.loadDistribution.update('none');
    }
    
    updateMetrics(metrics) {
        // Update response time chart
        if (this.charts.responseTime && metrics.health_metrics) {
            const now = new Date().toLocaleTimeString();
            const chart = this.charts.responseTime;
            
            // Calculate average response time across all instances
            const instancePerf = metrics.instance_performance || {};
            const responseTimes = Object.values(instancePerf)
                .map(p => p.avg_response_time || 0)
                .filter(t => t > 0);
            
            const avgResponseTime = responseTimes.length > 0 
                ? responseTimes.reduce((a, b) => a + b, 0) / responseTimes.length * 1000
                : 0;
            
            chart.data.labels.push(now);
            chart.data.datasets[0].data.push(avgResponseTime);
            
            // Keep only last 20 data points
            if (chart.data.labels.length > 20) {
                chart.data.labels.shift();
                chart.data.datasets[0].data.shift();
            }
            
            chart.update('none');
        }
    }
    
    showAddInstanceModal() {
        const modal = new bootstrap.Modal(document.getElementById('addInstanceModal'));
        modal.show();
    }
    
    toggleBattleTargetGroup(show) {
        const group = document.getElementById('battle-target-group');
        if (group) {
            group.style.display = show ? 'block' : 'none';
        }
    }
    
    async createInstance() {
        try {
            const mode = document.getElementById('instance-mode')?.value || 'direct_chat';
            const battleTarget = document.getElementById('battle-target')?.value || 'A';
            const browserType = document.getElementById('instance-browser')?.value || 'chromium';
            
            const config = {
                mode: mode,
                browser_type: browserType
            };
            
            if (mode === 'battle') {
                config.battle_target = battleTarget;
            }
            
            const response = await fetch('/gui/api/instances', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ config })
            });
            
            if (response.ok) {
                const result = await response.json();
                this.showAlert({
                    type: 'success',
                    message: `Instance ${result.instance_id.substring(0, 8)}... created successfully`,
                    timestamp: new Date()
                });
                
                // Close modal
                const modal = bootstrap.Modal.getInstance(document.getElementById('addInstanceModal'));
                modal.hide();
                
                // Refresh data
                setTimeout(() => this.refreshData(), 1000);
            } else {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to create instance');
            }
            
        } catch (error) {
            console.error('Error creating instance:', error);
            this.showAlert({
                type: 'error',
                message: 'Failed to create instance: ' + error.message,
                timestamp: new Date()
            });
        }
    }
    
    async removeInstanceConfirm(instanceId) {
        if (confirm(`Are you sure you want to remove instance ${instanceId.substring(0, 8)}...?`)) {
            await this.removeInstance(instanceId);
        }
    }
    
    async removeInstance(instanceId) {
        try {
            const response = await fetch(`/gui/api/instances/${instanceId}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                this.showAlert({
                    type: 'success',
                    message: `Instance ${instanceId.substring(0, 8)}... removed successfully`,
                    timestamp: new Date()
                });
                
                // Remove from local instances map
                this.instances.delete(instanceId);
                
                // Refresh data
                setTimeout(() => this.refreshData(), 1000);
            } else {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to remove instance');
            }
            
        } catch (error) {
            console.error('Error removing instance:', error);
            this.showAlert({
                type: 'error',
                message: 'Failed to remove instance: ' + error.message,
                timestamp: new Date()
            });
        }
    }
    
    async healthCheckInstance(instanceId) {
        try {
            // This would trigger a health check via WebSocket or API
            if (window.wsClient) {
                window.wsClient.send({
                    type: 'health_check_instance',
                    instance_id: instanceId,
                    timestamp: Date.now()
                });
            }
            
            this.showAlert({
                type: 'info',
                message: `Health check triggered for instance ${instanceId.substring(0, 8)}...`,
                timestamp: new Date()
            });
            
        } catch (error) {
            console.error('Error triggering health check:', error);
        }
    }
    
    async regenerateSession(instanceId) {
        try {
            // This would trigger session regeneration via WebSocket or API
            if (window.wsClient) {
                window.wsClient.send({
                    type: 'regenerate_session',
                    instance_id: instanceId,
                    timestamp: Date.now()
                });
            }
            
            this.showAlert({
                type: 'info',
                message: `Session regeneration triggered for instance ${instanceId.substring(0, 8)}...`,
                timestamp: new Date()
            });
            
        } catch (error) {
            console.error('Error triggering session regeneration:', error);
        }
    }
    
    async updateLoadBalancerSettings() {
        try {
            const strategy = document.getElementById('lb-strategy')?.value;
            const maxRetries = document.getElementById('max-retries')?.value;
            
            const settings = {
                load_balancing_strategy: strategy,
                max_retries: parseInt(maxRetries)
            };
            
            if (window.wsClient) {
                window.wsClient.updateSettings(settings);
            }
            
            this.showAlert({
                type: 'success',
                message: 'Load balancer settings updated',
                timestamp: new Date()
            });
            
        } catch (error) {
            console.error('Error updating load balancer settings:', error);
            this.showAlert({
                type: 'error',
                message: 'Failed to update load balancer settings: ' + error.message,
                timestamp: new Date()
            });
        }
    }
    
    async saveSettings() {
        try {
            const settings = {
                min_instances: parseInt(document.getElementById('min-instances')?.value || 1),
                max_instances: parseInt(document.getElementById('max-instances')?.value || 5),
                auto_scale: document.getElementById('auto-scale')?.checked || false,
                health_check_interval: parseInt(document.getElementById('health-check-interval')?.value || 10),
                browser_type: document.getElementById('browser-type')?.value || 'chromium',
                headless_mode: document.getElementById('headless-mode')?.checked || false,
                proxy_enabled: document.getElementById('proxy-enabled')?.checked || false
            };
            
            if (window.wsClient) {
                window.wsClient.updateSettings(settings);
            }
            
            this.showAlert({
                type: 'success',
                message: 'Settings saved successfully',
                timestamp: new Date()
            });
            
        } catch (error) {
            console.error('Error saving settings:', error);
            this.showAlert({
                type: 'error',
                message: 'Failed to save settings: ' + error.message,
                timestamp: new Date()
            });
        }
    }
    
    async triggerAutoScale() {
        try {
            if (window.wsClient) {
                window.wsClient.send({
                    type: 'trigger_auto_scale',
                    timestamp: Date.now()
                });
            }
            
            this.showAlert({
                type: 'info',
                message: 'Auto-scaling triggered',
                timestamp: new Date()
            });
            
        } catch (error) {
            console.error('Error triggering auto-scale:', error);
        }
    }
    
    showAlert(alert) {
        this.alerts.unshift(alert);
        
        // Keep only last 50 alerts
        if (this.alerts.length > 50) {
            this.alerts = this.alerts.slice(0, 50);
        }
        
        this.updateAlertsDisplay();
    }
    
    updateAlertsDisplay() {
        const container = document.getElementById('alerts-container');
        if (!container) return;
        
        container.innerHTML = '';
        
        this.alerts.slice(0, 10).forEach(alert => {
            const alertElement = document.createElement('div');
            alertElement.className = `alert-item ${alert.type}`;
            alertElement.innerHTML = `
                <div class="d-flex justify-content-between align-items-start">
                    <div>
                        <strong>${this.getAlertIcon(alert.type)} ${this.capitalizeFirst(alert.type)}</strong>
                        <p class="mb-1">${alert.message}</p>
                        <small class="alert-timestamp">${this.formatTimestamp(alert.timestamp)}</small>
                    </div>
                    <button type="button" class="btn-close btn-close-white btn-sm" onclick="dashboard.dismissAlert(${this.alerts.indexOf(alert)})"></button>
                </div>
            `;
            container.appendChild(alertElement);
        });
    }
    
    dismissAlert(index) {
        this.alerts.splice(index, 1);
        this.updateAlertsDisplay();
    }
    
    addLogEntry(log) {
        this.logs.unshift(log);
        
        // Keep only max logs
        if (this.logs.length > this.maxLogs) {
            this.logs = this.logs.slice(0, this.maxLogs);
        }
        
        this.updateLogsDisplay();
    }
    
    updateLogsDisplay() {
        const container = document.getElementById('logs-container');
        if (!container) return;
        
        const filter = document.getElementById('log-level-filter')?.value || '';
        const filteredLogs = filter ? this.logs.filter(log => log.level === filter) : this.logs;
        
        container.innerHTML = '';
        
        filteredLogs.slice(0, 100).forEach(log => {
            const logElement = document.createElement('div');
            logElement.className = `log-entry ${log.level.toLowerCase()}`;
            logElement.innerHTML = `
                <span class="log-timestamp">${this.formatTimestamp(log.timestamp)}</span>
                <span class="badge bg-${this.getLogLevelColor(log.level)} me-2">${log.level}</span>
                ${log.message}
            `;
            container.appendChild(logElement);
        });
        
        // Auto-scroll to bottom
        container.scrollTop = container.scrollHeight;
    }
    
    filterLogs(level) {
        this.updateLogsDisplay();
    }
    
    clearLogs() {
        this.logs = [];
        this.updateLogsDisplay();
    }
    
    // Utility methods
    updateElement(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value;
        }
    }
    
    getStatusClass(status) {
        const statusMap = {
            'ready': 'status-healthy',
            'healthy': 'status-healthy',
            'unhealthy': 'status-unhealthy',
            'failed': 'status-unhealthy',
            'initializing': 'status-initializing',
            'starting': 'status-initializing'
        };
        return statusMap[status] || 'status-unknown';
    }
    
    getAlertIcon(type) {
        const iconMap = {
            'success': '<i class="fas fa-check-circle"></i>',
            'error': '<i class="fas fa-exclamation-circle"></i>',
            'warning': '<i class="fas fa-exclamation-triangle"></i>',
            'info': '<i class="fas fa-info-circle"></i>'
        };
        return iconMap[type] || '<i class="fas fa-bell"></i>';
    }
    
    getLogLevelColor(level) {
        const colorMap = {
            'ERROR': 'danger',
            'WARNING': 'warning',
            'INFO': 'info',
            'DEBUG': 'secondary'
        };
        return colorMap[level] || 'secondary';
    }
    
    capitalizeFirst(str) {
        return str.charAt(0).toUpperCase() + str.slice(1);
    }
    
    formatTimestamp(timestamp) {
        const date = new Date(timestamp);
        return date.toLocaleString();
    }
    
    setLoadingState(loading) {
        const refreshBtn = document.getElementById('refresh-btn');
        if (refreshBtn) {
            if (loading) {
                refreshBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
                refreshBtn.disabled = true;
            } else {
                refreshBtn.innerHTML = '<i class="fas fa-sync-alt"></i> Refresh';
                refreshBtn.disabled = false;
            }
        }
    }
    
    startAutoRefresh() {
        this.refreshTimer = setInterval(() => {
            if (window.wsClient && window.wsClient.isConnected) {
                window.wsClient.requestStatus();
            } else {
                this.refreshData();
            }
        }, this.refreshInterval);
    }
    
    stopAutoRefresh() {
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
    }
    
    // WebSocket event handlers (called by WebSocket client)
    addInstance(instance) {
        this.instances.set(instance.instance_id, instance);
        this.refreshData(); // Refresh to update the table
    }
    
    removeInstance(instanceId) {
        this.instances.delete(instanceId);
        this.refreshData(); // Refresh to update the table
    }
    
    // Cleanup
    destroy() {
        this.stopAutoRefresh();
        
        // Destroy charts
        Object.values(this.charts).forEach(chart => {
            if (chart && typeof chart.destroy === 'function') {
                chart.destroy();
            }
        });
        
        this.charts = {};
        this.instances.clear();
        this.alerts = [];
        this.logs = [];
    }
}

// Initialize dashboard when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    window.dashboard = new Dashboard();
    
    // Cleanup on page unload
    window.addEventListener('beforeunload', function() {
        if (window.dashboard) {
            window.dashboard.destroy();
        }
    });
});