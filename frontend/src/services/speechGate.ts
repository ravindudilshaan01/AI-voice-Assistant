export type SpeechGateState = {
  rms: number
  noiseFloor: number
  speechConfirmed: boolean
  speechActive: boolean
}

export class SpeechGate {
  private noiseFloor = 0.008
  private samples = 0
  private candidateSince = 0
  private confirmed = false
  private active = false
  private lastSpeechAt = 0

  constructor(
    private readonly confirmMs = 180,
    private readonly releaseMs = 1500,
  ) {}

  update(rms: number, now: number): SpeechGateState {
    this.samples += 1
    if (!this.confirmed && this.samples <= 24) {
      this.noiseFloor = this.noiseFloor * 0.85 + rms * 0.15
    } else if (!this.active) {
      this.noiseFloor = this.noiseFloor * 0.98 + rms * 0.02
    }
    const threshold = Math.max(0.018, this.noiseFloor * 2.8)
    if (rms >= threshold) {
      this.lastSpeechAt = now
      if (!this.candidateSince) this.candidateSince = now
      if (now - this.candidateSince >= this.confirmMs) {
        this.confirmed = true
        this.active = true
      }
    } else {
      this.candidateSince = 0
      this.active = this.confirmed && now - this.lastSpeechAt < this.releaseMs
    }
    return { rms, noiseFloor: this.noiseFloor, speechConfirmed: this.confirmed, speechActive: this.active }
  }

  get lastSpeechTime(): number { return this.lastSpeechAt }
  get hasSpeech(): boolean { return this.confirmed }
}
