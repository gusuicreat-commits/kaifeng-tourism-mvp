// Collect mono samples; output stays silent so microphone audio never plays back.
class VoiceRecorder extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buffer = new Float32Array(2048)
    this.used = 0
    this.active = true
    this.port.onmessage = ({data}) => {
      if (data === 'stop') {
        this.active = false
        this.flush()
        this.port.postMessage({done: true})
      }
    }
  }
  flush() {
    if (!this.used) return
    const samples = this.buffer.slice(0, this.used)
    this.port.postMessage({samples}, [samples.buffer])
    this.used = 0
  }
  process(inputs) {
    const input = inputs[0]?.[0]
    if (this.active && input) {
      for (const value of input) {
        this.buffer[this.used++] = value
        if (this.used === this.buffer.length) this.flush()
      }
    }
    return true
  }
}
registerProcessor('voice-recorder', VoiceRecorder)
