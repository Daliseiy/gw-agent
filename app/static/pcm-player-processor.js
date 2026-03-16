class PcmPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.bufferSize = 24000 * 180; // 24 kHz * 180 seconds
    this.buffer = new Float32Array(this.bufferSize);
    this.readIndex = 0;
    this.writeIndex = 0;

    this.port.onmessage = (event) => {
      if (event.data && event.data.command === 'endOfAudio') {
        this.readIndex = this.writeIndex;
        return;
      }

      const int16Samples = new Int16Array(event.data);
      this.enqueue(int16Samples);
    };
  }

  enqueue(int16Samples) {
    for (let i = 0; i < int16Samples.length; i += 1) {
      this.buffer[this.writeIndex] = int16Samples[i] / 32768;
      this.writeIndex = (this.writeIndex + 1) % this.bufferSize;
      if (this.writeIndex === this.readIndex) {
        this.readIndex = (this.readIndex + 1) % this.bufferSize;
      }
    }
  }

  process(_, outputs) {
    const output = outputs[0];
    const framesPerBlock = output[0].length;

    for (let frame = 0; frame < framesPerBlock; frame += 1) {
      const sample = this.buffer[this.readIndex];
      output[0][frame] = sample;
      if (output.length > 1) {
        output[1][frame] = sample;
      }
      if (this.readIndex !== this.writeIndex) {
        this.readIndex = (this.readIndex + 1) % this.bufferSize;
      }
    }
    return true;
  }
}

registerProcessor('pcm-player-processor', PcmPlayerProcessor);
