/* AudioWorklet: microphone audio -> 16 kHz mono 16-bit PCM in 100 ms blocks.
 * Loaded by the Live Translation page with audioWorklet.addModule(). */
class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.ratio = sampleRate / this.targetRate;   // `sampleRate` is the context rate
    this.position = 0;                            // fractional read position
    this.carry = [];                              // input not yet resampled
    this.block = new Int16Array(1600);            // 100 ms at 16 kHz
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;

    // Average-then-pick resampling: a simple low-pass that avoids aliasing.
    const samples = this.carry.length ? Float32Array.from([...this.carry, ...channel]) : channel;
    let pos = this.position;
    while (pos + this.ratio <= samples.length) {
      const start = Math.floor(pos);
      const end = Math.min(samples.length, Math.floor(pos + this.ratio));
      let sum = 0;
      for (let i = start; i < end; i++) sum += samples[i];
      const value = end > start ? sum / (end - start) : samples[start];
      const clipped = Math.max(-1, Math.min(1, value));
      this.block[this.filled++] = clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff;
      if (this.filled === this.block.length) {
        this.port.postMessage(this.block.buffer.slice(0));
        this.filled = 0;
      }
      pos += this.ratio;
    }
    const consumed = Math.floor(pos);
    this.carry = Array.from(samples.subarray(consumed));
    this.position = pos - consumed;
    return true;
  }
}

registerProcessor('pcm-recorder', PcmRecorderProcessor);
