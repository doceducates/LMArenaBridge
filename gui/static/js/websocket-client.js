/**
 * WebSocket Client for LMArenaBridge Multi-Instance Dashboard
 * Handles real-time communication with the server
 */

class WebSocketClient {
    constructor(url = null) {
        this.url = url || `ws://${window.location.host}/gui/ws`;
        this.socket = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;
        this.isConnected = false;
        this.messageHandlers = new Map();
        this.heartbeatInterval = null;
        this.heartbeatTimeout = 30000; // 30 seconds
        
        this.init();
    }
    
    init() {
        this.connect();
        this.setupHeartbeat();
    }
    
    connect() {
        try {
            console.log('Connecting to WebSocket:', this.url);
            this.socket = new WebSocket(this.url);
            
            this.socket.onopen = (event) => {
                console.log('WebSocket connected');
                this.isConnected = true;
                this.reconnectAttempts = 0;
                this.updateConnectionStatus(true);
                this.onConnect(event);
            };
            
            this.socket.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleMessage(data);
                } catch (error) {
                    console.error('Error parsing WebSocket message:', error);
                }
            };
            
            this.socket.onclose = (event) => {
                console.log('WebSocket disconnected:', event.code, event.reason);
                this.isConnected = false;
                this.updateConnectionStatus(false);
                this.onDisconnect(event);
                
                // Attempt to reconnect
                if (this.reconnectAttempts < this.maxReconnectAttempts) {
                    this.reconnectAttempts++;
                    console.log(`Reconnection attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts}`);
                    setTimeout(() => this.connect(), this.reconnectDelay * this.reconnectAttempts);
                } else {
                    console.error('Max reconnection attempts reached');
                    this.onMaxReconnectAttemptsReached();
                }
            };
            
            this.socket.onerror = (error) => {
                console.error('WebSocket error:', error);
                this.onError(error);
            };
            
        } catch (error) {
            console.error('Error creating WebSocket connection:', error);
            this.onError(error);
        }
    }
    
    setupHeartbeat() {
        this.heartbeatInterval = setInterval(() => {
            if (this.isConnected) {
                this.send({
                    type: 'ping',
                    timestamp: Date.now()
                });
            }
        }, this.heartbeatTimeout);
    }
    
    send(data) {
        if (this.isConnected && this.socket.readyState === WebSocket.OPEN) {
            try {
                this.socket.send(JSON.stringify(data));
                return true;
            } catch (error) {
                console.error('Error sending WebSocket message:', error);
                return false;
            }
        } else {
            console.warn('WebSocket not connected, cannot send message');
            return false;
        }
    }
    
    handleMessage(data) {
        const messageType = data.type;
        
        // Handle built-in message types
        switch (messageType) {
            case 'pong':
                // Heartbeat response
                break;
                
            case 'initial_status':
                this.onInitialStatus(data.data);
                break;
                
            case 'status_update':
                this.onStatusUpdate(data.data);
                break;
                
            case 'health_alert':
                this.onHealthAlert(data.alert);
                break;
                
            case 'instance_created':
                this.onInstanceCreated(data.instance);
                break;
                
            case 'instance_removed':
                this.onInstanceRemoved(data.instance_id);
                break;
                
            case 'metrics_update':
                this.onMetricsUpdate(data.metrics);
                break;
                
            case 'log_entry':
                this.onLogEntry(data.log);
                break;
                
            default:
                // Check for custom handlers
                if (this.messageHandlers.has(messageType)) {
                    const handler = this.messageHandlers.get(messageType);
                    handler(data);
                } else {
                    console.warn('Unknown message type:', messageType, data);
                }
                break;
        }
    }
    
    // Event handlers (can be overridden)
    onConnect(event) {
        console.log('WebSocket connection established');
        // Request initial status
        this.requestStatus();
    }
    
    onDisconnect(event) {
        console.log('WebSocket connection lost');
    }
    
    onError(error) {
        console.error('WebSocket error occurred:', error);
    }
    
    onMaxReconnectAttemptsReached() {
        console.error('Failed to reconnect to WebSocket after maximum attempts');
        this.updateConnectionStatus(false, 'Failed to reconnect');
    }
    
    onInitialStatus(data) {
        console.log('Received initial status:', data);
        // Trigger dashboard update
        if (window.dashboard) {
            window.dashboard.updateSystemStatus(data);
        }
    }
    
    onStatusUpdate(data) {
        console.log('Received status update:', data);
        // Trigger dashboard update
        if (window.dashboard) {
            window.dashboard.updateSystemStatus(data);
        }
    }
    
    onHealthAlert(alert) {
        console.warn('Health alert received:', alert);
        // Show alert in dashboard
        if (window.dashboard) {
            window.dashboard.showAlert(alert);
        }
    }
    
    onInstanceCreated(instance) {
        console.log('Instance created:', instance);
        // Update instances table
        if (window.dashboard) {
            window.dashboard.addInstance(instance);
        }
    }
    
    onInstanceRemoved(instanceId) {
        console.log('Instance removed:', instanceId);
        // Update instances table
        if (window.dashboard) {
            window.dashboard.removeInstance(instanceId);
        }
    }
    
    onMetricsUpdate(metrics) {
        console.log('Metrics update:', metrics);
        // Update charts and metrics
        if (window.dashboard) {
            window.dashboard.updateMetrics(metrics);
        }
    }
    
    onLogEntry(log) {
        // Add log entry to logs panel
        if (window.dashboard) {
            window.dashboard.addLogEntry(log);
        }
    }
    
    // Public methods
    requestStatus() {
        this.send({
            type: 'request_status',
            timestamp: Date.now()
        });
    }
    
    createInstance(config) {
        this.send({
            type: 'create_instance',
            config: config,
            timestamp: Date.now()
        });
    }
    
    removeInstance(instanceId) {
        this.send({
            type: 'remove_instance',
            instance_id: instanceId,
            timestamp: Date.now()
        });
    }
    
    updateSettings(settings) {
        this.send({
            type: 'update_settings',
            settings: settings,
            timestamp: Date.now()
        });
    }
    
    // Custom message handler registration
    addMessageHandler(messageType, handler) {
        this.messageHandlers.set(messageType, handler);
    }
    
    removeMessageHandler(messageType) {
        this.messageHandlers.delete(messageType);
    }
    
    // Connection status management
    updateConnectionStatus(connected, message = null) {
        const statusElement = document.getElementById('connection-status');
        const textElement = document.getElementById('connection-text');
        
        if (statusElement && textElement) {
            if (connected) {
                statusElement.className = 'fas fa-circle text-success me-1';
                textElement.textContent = 'Connected';
                statusElement.classList.remove('disconnected');
            } else {
                statusElement.className = 'fas fa-circle text-danger me-1';
                textElement.textContent = message || 'Disconnected';
                statusElement.classList.add('disconnected');
            }
        }
    }
    
    // Cleanup
    disconnect() {
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
        }
        
        if (this.socket) {
            this.socket.close();
            this.socket = null;
        }
        
        this.isConnected = false;
        this.updateConnectionStatus(false);
    }
    
    // Utility methods
    getConnectionState() {
        return {
            isConnected: this.isConnected,
            reconnectAttempts: this.reconnectAttempts,
            readyState: this.socket ? this.socket.readyState : null
        };
    }
    
    // Static method to get WebSocket ready state names
    static getReadyStateName(readyState) {
        const states = {
            0: 'CONNECTING',
            1: 'OPEN',
            2: 'CLOSING',
            3: 'CLOSED'
        };
        return states[readyState] || 'UNKNOWN';
    }
}

// Export for use in other scripts
window.WebSocketClient = WebSocketClient;

// Auto-initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    // Initialize WebSocket client
    window.wsClient = new WebSocketClient();
    
    // Add global error handler for WebSocket
    window.addEventListener('beforeunload', function() {
        if (window.wsClient) {
            window.wsClient.disconnect();
        }
    });
});