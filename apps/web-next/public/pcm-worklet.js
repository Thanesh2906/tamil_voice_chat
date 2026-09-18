class Pcm16Processor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    const pcm = new Int16Array(input.length);
    for (let index = 0; index < input.length; index += 1) {
      pcm[index] = Math.max(-32768, Math.min(32767, input[index] * 32768));
    }
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    return true;
  }
}

registerProcessor("pcm16-processor", Pcm16Processor);
