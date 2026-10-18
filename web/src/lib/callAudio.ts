/**
 * Web Audio mic capture (16 kHz PCM) and playback (24 kHz PCM) over WebSocket.
 * Ported from backend/app/static/call.html according to spec §7.
 */

const CAPTURE_WORKLET = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0][0];
    if (channel) {
      const pcm = new Int16Array(channel.length);
      for (let i = 0; i < channel.length; i++) {
        const sample = Math.max(-1, Math.min(1, channel[i]));
        pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor("capture", Capture);
`

const LEVEL_REPORT_MS = 100

export type CallAudioState = 'idle' | 'starting' | 'connected' | 'ended' | 'error'

export interface CallAudioCallbacks {
  onStateChange?: (state: CallAudioState, detail?: string) => void
  onLevel?: (level: number) => void
  onEnded?: (status: string) => void
  onError?: (error: Error) => void
}

export class CallAudioController {
  private socket: WebSocket | null = null
  private micStream: MediaStream | null = null
  private micContext: AudioContext | null = null
  private playContext: AudioContext | null = null
  private playAt: number = 0
  private playing: AudioBufferSourceNode[] = []
  private callbacks: CallAudioCallbacks
  private levelPeak: number = 0
  private levelReportedAt: number = 0

  constructor(callbacks: CallAudioCallbacks = {}) {
    this.callbacks = callbacks
  }

  async start(ticket: string, wsUrl?: string): Promise<void> {
    this.callbacks.onStateChange?.('starting', 'requesting_microphone')

    try {
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      })

      this.callbacks.onStateChange?.('starting', 'initializing_audio')
      this.micContext = new AudioContext({ sampleRate: 16000 })
      if (!this.playContext) {
        this.playContext = new AudioContext({ sampleRate: 24000 })
      }
      await this.playContext.resume()

      const blob = new Blob([CAPTURE_WORKLET], { type: 'application/javascript' })
      const moduleUrl = URL.createObjectURL(blob)
      await this.micContext.audioWorklet.addModule(moduleUrl)
      URL.revokeObjectURL(moduleUrl)

      const captureNode = new AudioWorkletNode(this.micContext, 'capture')
      const source = this.micContext.createMediaStreamSource(this.micStream)
      source.connect(captureNode)

      // WebSocket connection
      this.callbacks.onStateChange?.('starting', 'connecting_socket')
      const defaultHost = window.location.host
      const defaultScheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const targetUrl = wsUrl || `${defaultScheme}://${defaultHost}/browser/stream?ticket=${encodeURIComponent(ticket)}`

      this.socket = new WebSocket(targetUrl)
      this.socket.binaryType = 'arraybuffer'

      captureNode.port.onmessage = (event: MessageEvent) => {
        // the loudest sample since the last report, reported at most every 100 ms so the page is not redrawn per block
        const samples = new Int16Array(event.data as ArrayBuffer)
        for (let i = 0; i < samples.length; i++) this.levelPeak = Math.max(this.levelPeak, Math.abs(samples[i]))
        const at = performance.now()
        if (at - this.levelReportedAt >= LEVEL_REPORT_MS) {
          this.callbacks.onLevel?.(this.levelPeak / 0x8000)
          this.levelPeak = 0
          this.levelReportedAt = at
        }
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
          this.socket.send(event.data)
        }
      }

      this.socket.onopen = () => {
        this.callbacks.onStateChange?.('connected', 'Agent speaks first')
      }

      this.socket.onmessage = (event: MessageEvent) => {
        if (typeof event.data !== 'string') {
          this.play(event.data as ArrayBuffer)
          return
        }
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'clear') {
            this.clearPlayback()
          } else if (msg.type === 'ended') {
            this.callbacks.onEnded?.(msg.status || 'completed')
            this.stop()
          }
        } catch {
          // not json
        }
      }

      this.socket.onclose = () => {
        this.callbacks.onStateChange?.('ended')
        this.stop()
      }

      this.socket.onerror = () => {
        const err = new Error('WebSocket stream error')
        this.callbacks.onError?.(err)
        this.stop()
      }
    } catch (err: unknown) {
      const error = err instanceof Error ? err : new Error(String(err))
      this.callbacks.onError?.(error)
      this.callbacks.onStateChange?.('error', error.message)
      this.stop()
      throw error
    }
  }

  /** Tells the call that the person cannot speak and needs help. The call becomes an emergency. */
  requestHelp(): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false
    this.socket.send(JSON.stringify({ type: 'help' }))
    return true
  }

  private play(buffer: ArrayBuffer): void {
    if (!this.playContext) return
    const pcm = new Int16Array(buffer.slice(0, buffer.byteLength - (buffer.byteLength % 2)))
    if (!pcm.length) return

    const audio = this.playContext.createBuffer(1, pcm.length, 24000)
    const data = audio.getChannelData(0)
    for (let i = 0; i < pcm.length; i++) {
      data[i] = pcm[i] / 0x8000
    }

    const sourceNode = this.playContext.createBufferSource()
    sourceNode.buffer = audio
    sourceNode.connect(this.playContext.destination)

    this.playAt = Math.max(this.playAt, this.playContext.currentTime + 0.05)
    sourceNode.start(this.playAt)
    this.playAt += audio.duration

    this.playing.push(sourceNode)
    sourceNode.onended = () => {
      this.playing = this.playing.filter((s) => s !== sourceNode)
    }
  }

  clearPlayback(): void {
    this.playing.forEach((source) => {
      try {
        source.stop()
      } catch {
        // ignore
      }
    })
    this.playing = []
    this.playAt = 0
  }

  stop(): void {
    if (this.socket) {
      try {
        this.socket.close()
      } catch {
        // ignore
      }
      this.socket = null
    }

    if (this.micStream) {
      this.micStream.getTracks().forEach((track) => track.stop())
      this.micStream = null
    }

    if (this.micContext) {
      this.micContext.close().catch(() => {})
      this.micContext = null
    }

    this.clearPlayback()
  }
}
