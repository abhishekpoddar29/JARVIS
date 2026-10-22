class GeminiInputProcessor extends AudioWorkletProcessor {
    constructor() {
      super();
      this.targetRate = 16000;
      this.phase = 0;
      this.sum = 0;
      this.count = 0;
      this.chunk = new Int16Array(640); // 40 ms at 16 kHz.
      this.offset = 0;
    }
  
    process(inputs) {
      const input = inputs[0]?.[0];
      if (!input) return true;
  
      const step = this.targetRate / sampleRate;
      for (let index = 0; index < input.length; index += 1) {
        this.sum += input[index];
        this.count += 1;
        this.phase += step;
  
        if (this.phase < 1) continue;
  
        const sample = Math.max(-1, Math.min(1, this.sum / this.count));
        this.chunk[this.offset] = sample < 0
          ? Math.round(sample * 32768)
          : Math.round(sample * 32767);
        this.offset += 1;
        this.phase -= 1;
        this.sum = 0;
        this.count = 0;
  
        if (this.offset === this.chunk.length) {
          const buffer = this.chunk.buffer;
          this.port.postMessage(buffer, [buffer]);
          this.chunk = new Int16Array(640);
          this.offset = 0;
        }
      }
  
      return true;
    }
  }
  
  registerProcessor("gemini-input-processor", GeminiInputProcessor);
  