// Normalize all browsers to PCM16 / mono / 16kHz WAV, independent of MediaRecorder codecs.
export class WavRecorder {
 private context?:AudioContext
 private stream?:MediaStream
 private source?:MediaStreamAudioSourceNode
 private node?:AudioWorkletNode
 private chunks:Float32Array[]=[]
 private count=0
 private cancelled=false
 private finishing=false
 private flush?:()=>void

 constructor(private interrupted:()=>void){}

 async start(){
  this.context=new AudioContext()
  try{
   await this.context.resume()
   if(this.cancelled)return false
   const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false})
   if(this.cancelled){stream.getTracks().forEach(t=>t.stop());return false}
   this.stream=stream
   stream.getAudioTracks().forEach(t=>t.addEventListener('ended',()=>{if(!this.finishing&&!this.cancelled)this.interrupted()}))
   await this.context.audioWorklet.addModule('/audio-recorder.js')
   if(this.cancelled)return false
   this.node=new AudioWorkletNode(this.context,'voice-recorder',{numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1],channelCount:1,channelCountMode:'explicit'})
   this.node.port.onmessage=({data})=>{
    if(this.cancelled)return
    if(data.samples instanceof Float32Array){
     const remaining=Math.floor(this.context!.sampleRate*60)-this.count
     const samples=data.samples.subarray(0,Math.max(0,remaining))
     if(samples.length){this.chunks.push(samples);this.count+=samples.length}
    }
    if(data.done)this.flush?.()
   }
   this.node.onprocessorerror=()=>{if(!this.finishing&&!this.cancelled)this.interrupted()}
   this.source=this.context.createMediaStreamSource(stream)
   this.source.connect(this.node)
   this.node.connect(this.context.destination)
   return true
  }catch(error){this.cancel();throw error}
 }

 async stop():Promise<Blob>{
  this.finishing=true
  if(!this.context||!this.node||this.cancelled)throw new Error('录音已取消')
  // Flush the last partial block before closing the microphone.
  await new Promise<void>(resolve=>{
   const timeout=setTimeout(resolve,500)
   this.flush=()=>{clearTimeout(timeout);resolve()}
   this.node!.port.postMessage('stop')
  })
  if(this.cancelled)throw new Error('录音已取消')
  const rate=this.context.sampleRate
  this.release()
  const samples=new Float32Array(this.count)
  let offset=0
  for(const chunk of this.chunks){samples.set(chunk,offset);offset+=chunk.length}
  this.chunks=[]
  const length=Math.floor(samples.length*16000/rate)
  if(length<3200)throw new Error('录音太短，请至少说一句话后再结束。')
  const buffer=new ArrayBuffer(44+length*2),view=new DataView(buffer)
  const write=(start:number,value:string)=>{for(let i=0;i<value.length;i++)view.setUint8(start+i,value.charCodeAt(i))}
  write(0,'RIFF');view.setUint32(4,36+length*2,true);write(8,'WAVE');write(12,'fmt ')
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true)
  view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true)
  write(36,'data');view.setUint32(40,length*2,true)
  // Average source samples when downsampling (typically 48kHz -> 16kHz).
  for(let i=0;i<length;i++){
   const from=Math.floor(i*rate/16000),to=Math.min(samples.length,Math.max(from+1,Math.floor((i+1)*rate/16000)))
   let value=0
   for(let j=from;j<to;j++)value+=samples[j]!
   value=Math.max(-1,Math.min(1,value/(to-from)))
   view.setInt16(44+i*2,value<0?value*32768:value*32767,true)
  }
  return new Blob([buffer],{type:'audio/wav'})
 }

 private release(){
  this.stream?.getTracks().forEach(t=>t.stop())
  this.source?.disconnect();this.node?.disconnect()
  if(this.node)this.node.port.onmessage=null
  if(this.context&&this.context.state!=='closed')void this.context.close().catch(()=>{})
 }

 cancel(){this.cancelled=true;this.flush?.();this.release();this.chunks=[]}
}
