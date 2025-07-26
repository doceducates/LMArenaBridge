// ==UserScript==
// @name         LMArena API Bridge
// @namespace    http://tampermonkey.net/
// @version      2.5
// @description  Bridges LMArena to a local API server via WebSocket for streamlined automation.
// @author       Lianues
// @match        https://lmarena.ai/*
// @match        https://*.lmarena.ai/*
// @icon         https://www.google.com/s2/favicons?sz=64&domain=lmarena.ai
// @grant        none
// @run-at       document-end
// ==/UserScript==

(function () {
    'use strict';

    // --- Configuration ---
    const SERVER_URL = "ws://localhost:5102/ws"; // Matches the port in api_server.py
    let socket;
    let isCaptureModeActive = false; // Switch for ID capture mode

    // --- Core logic ---
    function connect() {
    console.log(`[API Bridge] Connecting to local server: ${SERVER_URL}...`);
        socket = new WebSocket(SERVER_URL);

        socket.onopen = () => {
            console.log("[API Bridge] ✅ WebSocket connection to local server established.");
            document.title = "✅ " + document.title;
        };

        socket.onmessage = async (event) => {
            try {
                const message = JSON.parse(event.data);

                // Check if it's a command, not a standard chat request
                if (message.command) {
                    console.log(`[API Bridge] ⬇️ Received command: ${message.command}`);
                    if (message.command === 'refresh' || message.command === 'reconnect') {
                        console.log(`[API Bridge] Received '${message.command}' command, refreshing page...`);
                        location.reload();
                    } else if (message.command === 'activate_id_capture') {
                        console.log("[API Bridge] ✅ ID capture mode activated. Please trigger a 'Retry' action on the page.");
                        isCaptureModeActive = true;
                        // Optionally give user a visual cue
                        document.title = "🎯 " + document.title;
                    }
                    return;
                }

                const { request_id, payload } = message;

                if (!request_id || !payload) {
                    console.error("[API Bridge] Received invalid message from server:", message);
                    return;
                }
                
                console.log(`[API Bridge] ⬇️ Received chat request ${request_id.substring(0, 8)}. Preparing to execute fetch operation.`);
                await executeFetchAndStreamBack(request_id, payload);

            } catch (error) {
                console.error("[API Bridge] Error processing server message:", error);
            }
        };

        socket.onclose = () => {
            console.warn("[API Bridge] 🔌 Connection to local server closed. Will attempt to reconnect in 5 seconds...");
            if (document.title.startsWith("✅ ")) {
                document.title = document.title.substring(2);
            }
            setTimeout(connect, 5000);
        };

        socket.onerror = (error) => {
            console.error("[API Bridge] ❌ WebSocket error:", error);
            socket.close(); // Will trigger reconnect logic in onclose
        };
    }

    async function executeFetchAndStreamBack(requestId, payload) {
        console.log(`[API Bridge] Current domain: ${window.location.hostname}`);
        const { is_image_request, message_templates, target_model_id, session_id, message_id } = payload;

        // --- Use session info passed from backend configuration ---
        if (!session_id || !message_id) {
            const errorMsg = "Session info (session_id or message_id) received from backend is empty. Please run the `id_updater.py` script to set it first.";
            console.error(`[API Bridge] ${errorMsg}`);
            sendToServer(requestId, { error: errorMsg });
            sendToServer(requestId, "[DONE]");
            return;
        }

        // URL is the same for chat and image generation
        const apiUrl = `/api/stream/retry-evaluation-session-message/${session_id}/messages/${message_id}`;
        const httpMethod = 'PUT';
        
        console.log(`[API Bridge] Using API endpoint: ${apiUrl}`);
        
        const newMessages = [];
        let lastMsgIdInChain = null;

        if (!message_templates || message_templates.length === 0) {
            const errorMsg = "Message list received from backend is empty.";
            console.error(`[API Bridge] ${errorMsg}`);
            sendToServer(requestId, { error: errorMsg });
            sendToServer(requestId, "[DONE]");
            return;
        }

        // This loop logic is common for chat and image generation, since backend prepares correct message_templates
        for (let i = 0; i < message_templates.length; i++) {
            const template = message_templates[i];
            const currentMsgId = crypto.randomUUID();
            const parentIds = lastMsgIdInChain ? [lastMsgIdInChain] : [];
            
            // For image requests, status is always 'success'
            // Otherwise, only the last message is 'pending'
            const status = is_image_request ? 'success' : ((i === message_templates.length - 1) ? 'pending' : 'success');

            newMessages.push({
                role: template.role,
                content: template.content,
                id: currentMsgId,
                evaluationId: null,
                evaluationSessionId: session_id,
                parentMessageIds: parentIds,
                experimental_attachments: template.attachments || [],
                failureReason: null,
                metadata: null,
                participantPosition: template.participantPosition || "a",
                createdAt: new Date().toISOString(),
                updatedAt: new Date().toISOString(),
                status: status,
            });
            lastMsgIdInChain = currentMsgId;
        }

        const body = {
            messages: newMessages,
            modelId: target_model_id,
        };

        console.log("[API Bridge] Final payload to send to LMArena API:", JSON.stringify(body, null, 2));

        // Set a flag so our fetch interceptor knows this request is initiated by the script itself
        window.isApiBridgeRequest = true;
        try {
            const response = await fetch(apiUrl, {
                method: httpMethod,
                headers: {
                    'Content-Type': 'text/plain;charset=UTF-8', // LMArena uses text/plain
                    'Accept': '*/*',
                },
                body: JSON.stringify(body),
                credentials: 'include' // Must include cookie
            });

            if (!response.ok || !response.body) {
                const errorBody = await response.text();
                throw new Error(`Network response not OK. Status: ${response.status}. Content: ${errorBody}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();

            while (true) {
                const { value, done } = await reader.read();
                if (done) {
                    console.log(`[API Bridge] ✅ Stream for request ${requestId.substring(0, 8)} ended.`);
                    sendToServer(requestId, "[DONE]");
                    break;
                }
                const chunk = decoder.decode(value);
                // Directly forward raw data chunk back to backend
                sendToServer(requestId, chunk);
            }

        } catch (error) {
            console.error(`[API Bridge] ❌ Error executing fetch for request ${requestId.substring(0, 8)}:`, error);
            sendToServer(requestId, { error: error.message });
            sendToServer(requestId, "[DONE]");
        } finally {
            // After request ends, reset flag regardless of success or failure
            window.isApiBridgeRequest = false;
        }
    }

    function sendToServer(requestId, data) {
        if (socket && socket.readyState === WebSocket.OPEN) {
            const message = {
                request_id: requestId,
                data: data
            };
            socket.send(JSON.stringify(message));
        } else {
            console.error("[API Bridge] Unable to send data, WebSocket connection not open.");
        }
    }

    // --- Network request interception ---
    const originalFetch = window.fetch;
    window.fetch = function(...args) {
        const urlArg = args[0];
        let urlString = '';

        // 确保我们总是处理字符串形式的 URL
        if (urlArg instanceof Request) {
            urlString = urlArg.url;
        } else if (urlArg instanceof URL) {
            urlString = urlArg.href;
        } else if (typeof urlArg === 'string') {
            urlString = urlArg;
        }

    // Only match if URL is a valid string
        if (urlString) {
            const match = urlString.match(/\/api\/stream\/retry-evaluation-session-message\/([a-f0-9-]+)\/messages\/([a-f0-9-]+)/);

            // Only update ID if request is not initiated by API Bridge itself and capture mode is active
            if (match && !window.isApiBridgeRequest && isCaptureModeActive) {
                const sessionId = match[1];
                const messageId = match[2];
                console.log(`[API Bridge Interceptor] 🎯 ID captured in active mode! Sending...`);

                // Turn off capture mode, ensure only sent once
                isCaptureModeActive = false;
                if (document.title.startsWith("🎯 ")) {
                    document.title = document.title.substring(2);
                }

                // Asynchronously send captured ID to local id_updater.py script
                fetch('http://127.0.0.1:5103/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ sessionId, messageId })
                })
                .then(response => {
                    if (!response.ok) throw new Error(`Server responded with status: ${response.status}`);
                    console.log(`[API Bridge] ✅ ID update sent successfully. Capture mode automatically turned off.`);
                })
                .catch(err => {
                    console.error('[API Bridge] Error sending ID update:', err.message);
                    // Even if sending fails, capture mode is off, will not retry.
                });
            }
        }

    // Call original fetch function to ensure page functionality is not affected
        return originalFetch.apply(this, args);
    };


    // --- Send page source after load ---
    function sendPageSourceAfterLoad() {
        const sendSource = async () => {
            console.log("[API Bridge] Page load complete. Sending page source for model list update...");
            try {
                const htmlContent = document.documentElement.outerHTML;
                await fetch('http://localhost:5102/update_models', { // URL matches endpoint in api_server.py
                    method: 'POST',
                    headers: {
                        'Content-Type': 'text/html; charset=utf-8'
                    },
                    body: htmlContent
                });
                 console.log("[API Bridge] Page source sent successfully.");
            } catch (e) {
                console.error("[API Bridge] Failed to send page source:", e);
            }
        };

        if (document.readyState === 'complete') {
            sendSource();
        } else {
            window.addEventListener('load', sendSource);
        }
    }


    // --- Start connection ---
    console.log("========================================");
    console.log("  LMArena API Bridge v2.1 is running.");
    console.log("  - Chat feature connected to ws://localhost:5102");
    console.log("  - ID capturer will send to http://localhost:5103");
    console.log("========================================");
    
    sendPageSourceAfterLoad(); // Send page source
    connect(); // Establish WebSocket connection

})();
