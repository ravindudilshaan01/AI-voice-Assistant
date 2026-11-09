class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this.targetRate = 16000
    this.frameSize = 320
    this.pending = []
    this.position = 0
  }

  process(inputs) {
    const channels = inputs[0]
    if (!channels || channels.length === 0 || channels[0].length === 0) return true
    const input = channels[0]
    const ratio = sampleRate / this.targetRate
    for (; this.position < input.length; this.position += ratio) {
      const index = Math.min(input.length - 1, Math.floor(this.position))
      this.pending.push(Math.max(-1, Math.min(1, input[index])))
      if (this.pending.length === this.frameSize) {
        const pcm = new Int16Array(this.frameSize)
        let sum = 0
        for (let i = 0; i < pcm.length; i++) {
          const sample = this.pending[i]
          sum += sample * sample
          pcm[i] = Math.round(sample < 0 ? sample * 32768 : sample * 32767)
        }
        this.port.postMessage({ pcm, rms: Math.sqrt(sum / pcm.length) }, [pcm.buffer])
        this.pending = []
      }
    }
    this.position -= input.length
    return true
  }
}

registerProcessor('pcm-capture', PcmCaptureProcessor)
