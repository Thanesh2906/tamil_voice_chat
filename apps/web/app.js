const api = location.hostname === 'localhost' ? 'http://localhost:8000' : location.origin;
let accessToken, socket, stream, processor, context;
const $ = id => document.getElementById(id);
$('loginBtn').onclick = async () => {
  const response = await fetch(`${api}/auth/login`, {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({email:$('email').value,password:$('password').value})});
  if (!response.ok) return $('state').textContent = 'Login failed';
  accessToken = (await response.json()).access_token; $('login').hidden=true; $('voice').hidden=false; await loadProjects();
};
async function loadProjects(){const r=await fetch(`${api}/projects`,{headers:{authorization:`Bearer ${accessToken}`}});if(r.ok)for(const p of await r.json())$('project').add(new Option(p.name,p.id));}
function connect(){return new Promise((resolve,reject)=>{const url=api.replace(/^http/,'ws')+'/voice/session';socket=new WebSocket(url,['jarvis-bearer',accessToken]);socket.onopen=resolve;socket.onerror=reject;socket.onmessage=e=>handle(JSON.parse(e.data));socket.onclose=()=>setTimeout(()=>accessToken&&connect().catch(()=>{}),1500);});}
function handle(e){if(e.type==='transcript')$('transcript').textContent=e.text;if(e.type==='token')$('answer').textContent+=e.text;if(e.type==='audio'){const audio=new Audio(`data:${e.media_type};base64,${e.data}`);audio.play();}if(e.type==='final')$('state').textContent='Ready';if(e.type==='error')$('state').textContent=e.message||e.code;}
$('talk').onpointerdown=async()=>{if(!socket||socket.readyState!==1)await connect();$('answer').textContent='';stream=await navigator.mediaDevices.getUserMedia({audio:true});context=new AudioContext({sampleRate:16000});const source=context.createMediaStreamSource(stream);processor=context.createScriptProcessor(4096,1,1);processor.onaudioprocess=e=>{const f=e.inputBuffer.getChannelData(0),b=new ArrayBuffer(f.length*2),v=new DataView(b);for(let i=0;i<f.length;i++)v.setInt16(i*2,Math.max(-1,Math.min(1,f[i]))*0x7fff,true);if(socket.readyState===1)socket.send(b)};source.connect(processor);processor.connect(context.destination);socket.send(JSON.stringify({type:'start',sample_rate:context.sampleRate,locale:'ta-IN',project_id:$('project').value||null}));$('talk').classList.add('recording');$('state').textContent='Listening';};
$('talk').onpointerup=async()=>{processor?.disconnect();stream?.getTracks().forEach(t=>t.stop());await context?.close();socket.send(JSON.stringify({type:'stop'}));$('talk').classList.remove('recording');$('state').textContent='Thinking';};
$('cancel').onclick=()=>socket?.send(JSON.stringify({type:'barge_in'}));

