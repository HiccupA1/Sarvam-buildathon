// Downsamples the mic's native sample rate to 16kHz mono PCM16 and posts
// ~100ms chunks (1600 samples) to the main thread as raw Int16 buffers.
// Linear interpolation resampling -- adequate for speech, not hi-fi audio.
class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.ratio = sampleRate / this.targetSampleRate;
    this.carry = new Float32Array(0);
    this.fracPos = 0;
    this.outBuffer = [];
    this.chunkSizeSamples = 1600;
  }

  process(inputs) {
    const input = inputs[0][0];
    if (!input || input.length === 0) return true;

    const data = new Float32Array(this.carry.length + input.length);
    data.set(this.carry, 0);
    data.set(input, this.carry.length);

    let pos = this.fracPos;
    while (Math.floor(pos) + 1 < data.length) {
      const idx = Math.floor(pos);
      const frac = pos - idx;
      const sample = data[idx] * (1 - frac) + data[idx + 1] * frac;
      const clamped = Math.max(-1, Math.min(1, sample));
      this.outBuffer.push(clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff);
      pos += this.ratio;
    }

    const consumed = Math.floor(pos);
    this.carry = data.slice(consumed);
    this.fracPos = pos - consumed;

    while (this.outBuffer.length >= this.chunkSizeSamples) {
      const chunkSamples = this.outBuffer.splice(0, this.chunkSizeSamples);
      const int16 = new Int16Array(chunkSamples);
      this.port.postMessage(int16.buffer, [int16.buffer]);
    }

    return true;
  }
}

registerProcessor("pcm-worklet-processor", PCMWorkletProcessor);
