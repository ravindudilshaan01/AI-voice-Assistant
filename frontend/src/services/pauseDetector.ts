export type PauseState = {
  speechConfirmed: boolean
  shouldStop: boolean
  threshold: number
}

/** Detects a completed utterance without controlling recording restarts. */
export class PauseDetector {
  private noiseFloor = 0.004
  private candidateSince: number | null = null
  private confirmed = false
  private lastSpeechAt = 0

  constructor(
    private readonly confirmMs = 180,
    private readonly silenceMs = 1_900,
  ) {}

  update(rms: number, now: number): PauseState {
    const threshold = Math.max(0.012, this.noiseFloor * 2.8)
    if (!this.confirmed && rms < threshold) {
      this.noiseFloor = this.noiseFloor * 0.96 + rms * 0.04
    }
    if (rms >= threshold) {
      this.lastSpeechAt = now
      if (this.candidateSince === null) this.candidateSince = now
      if (now - this.candidateSince >= this.confirmMs) this.confirmed = true
    } else {
      this.candidateSince = null
    }
    return {
      speechConfirmed: this.confirmed,
      shouldStop: this.confirmed && now - this.lastSpeechAt >= this.silenceMs,
      threshold,
    }
  }

  get hasSpeech(): boolean { return this.confirmed }
}
