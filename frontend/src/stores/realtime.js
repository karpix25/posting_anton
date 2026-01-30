import { defineStore } from 'pinia'

export const useRealtimeStore = defineStore('realtime', {
    state: () => ({
        connected: false,
        reconnectTimer: null,
        lastEvent: null
    }),

    actions: {
        connect() {
            if (this.reconnectTimer) {
                clearTimeout(this.reconnectTimer)
                this.reconnectTimer = null
            }

            console.log('[SSE] Connecting...')
            const es = new EventSource('/api/events/stream')

            es.onopen = () => {
                console.log('[SSE] Connection established')
                this.connected = true
            }

            es.onmessage = (e) => {
                try {
                    const data = JSON.parse(e.data)
                    this.lastEvent = data

                    if (data.type === 'connected') {
                        this.connected = true
                    }
                    // Other event handling can be done in components via watch(lastEvent) or specific actions
                } catch (err) {
                    console.warn('[SSE] Failed to parse message:', err)
                }
            }

            es.onerror = () => {
                console.log('[SSE] Connection lost, reconnecting in 5s...')
                this.connected = false
                es.close()

                if (!this.reconnectTimer) {
                    this.reconnectTimer = setTimeout(() => {
                        this.reconnectTimer = null
                        this.connect()
                    }, 5000)
                }
            }
        }
    }
})
