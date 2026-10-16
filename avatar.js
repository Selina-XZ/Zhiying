(() => {
'use strict';

const visuals = {
  none:'<p class="visual-empty">本事项没有配置审核图片<br>请以大字文字为准</p>',
  activity:`<svg viewBox="0 0 420 280" role="img" aria-label="社区手工花灯活动通知图"><rect width="420" height="280" rx="24" fill="#F3E0BA"/><rect x="55" y="42" width="310" height="196" rx="20" fill="#FFFDF8" stroke="#8D4C3D" stroke-width="6"/><path d="M55 92h310" stroke="#8D4C3D" stroke-width="6"/><path d="M125 28v42M295 28v42" stroke="#6A382F" stroke-width="12" stroke-linecap="round"/><circle cx="135" cy="152" r="31" fill="#E8C3A4"/><path d="M119 153l12 12 23-29" fill="none" stroke="#3F3833" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/><path d="M194 137h114M194 169h86" stroke="#766B63" stroke-width="12" stroke-linecap="round"/><text x="210" y="220" text-anchor="middle" fill="#3F3833" font-size="19" font-weight="700">手工花灯活动</text></svg>`,
  location:`<svg viewBox="0 0 420 280" role="img" aria-label="社区一楼活动室位置图"><rect width="420" height="280" rx="24" fill="#F4E8D3"/><path d="M88 230V97l122-66 122 66v133" fill="#FFFDF8" stroke="#8D4C3D" stroke-width="7" stroke-linejoin="round"/><path d="M58 230h304" stroke="#3F3833" stroke-width="8" stroke-linecap="round"/><rect x="167" y="143" width="86" height="87" rx="7" fill="#E8C3A4" stroke="#8D4C3D" stroke-width="6"/><text x="210" y="82" text-anchor="middle" fill="#3F3833" font-size="22" font-weight="800">一楼活动室</text></svg>`,
  fraud:`<svg viewBox="0 0 420 280" role="img" aria-label="陌生转账先核实的反诈提醒图"><rect width="420" height="280" rx="24" fill="#E8B6A8"/><path d="M210 34l116 42v71c0 67-48 101-116 115-68-14-116-48-116-115V76z" fill="#FFFDF8" stroke="#A64A3E" stroke-width="8"/><path d="M210 83v83" stroke="#A64A3E" stroke-width="14" stroke-linecap="round"/><circle cx="210" cy="202" r="9" fill="#A64A3E"/><text x="210" y="244" text-anchor="middle" fill="#3F3833" font-size="18" font-weight="800">陌生转账先核实</text></svg>`,
  notice:`<svg viewBox="0 0 420 280" role="img" aria-label="社区温馨通知图"><rect width="420" height="280" rx="24" fill="#EBC7B5"/><rect x="56" y="48" width="308" height="184" rx="22" fill="#FFFDF8" stroke="#8D4C3D" stroke-width="7"/><path d="M108 104h204M108 145h162M108 186h185" stroke="#DCA28E" stroke-width="13" stroke-linecap="round"/><circle cx="329" cy="198" r="26" fill="#F3E0BA"/><path d="M317 199l9 9 18-22" fill="none" stroke="#3F3833" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/></svg>`
};
const imageCaptions = {
  activity:'工作人员选择的手工花灯活动通知图，请同时核对大字时间和地点。',
  location:'工作人员选择的社区活动室位置图，请同时核对大字地点。',
  fraud:'工作人员选择的反诈提醒图，请以大字内容为准。',
  notice:'工作人员选择的社区通知图，请以大字内容为准。'
};
const SDK_URL='https://media.xingyun3d.com/xingyun3d/general/litesdk/xmovAvatar_e2e@latest.js';
const GATEWAY_SERVER='https://nebula-agent.xingyun3d.com/user/v1/ttsa_v2/session';
const LOCAL_HOSTS=new Set(['127.0.0.1','localhost','::1','[::1]']);
const TRANSIENT_SOCKET_CODES=new Set(['10007','3107']);
const VOICE_ANSWER_FALLBACK_MS=3500;
const VOICE_ANSWER_START_TIMEOUT_MS=7000;
const OFFSET_LABELS={1440:'提前一天',60:'提前一小时',10:'提前十分钟'};
const RENDER_LABELS={
  uninitialized:'尚未初始化',init:'正在准备 SDK 渲染',rendering:'SDK 已报告渲染',
  pausing:'SDK 渲染正在暂停',paused:'SDK 渲染已暂停',resumed:'SDK 已报告恢复渲染',
  stopped:'SDK 渲染已停止',unavailable:'未收到 SDK 渲染报告'
};
const CONNECTION_LABELS={disconnected:'未连接',connecting:'正在连接',connected:'会话已连接',error:'连接异常'};
const SPEECH_LABELS={idle:'未播报',requesting:'等待播报',speaking:'正在播报',interrupted:'播报已停止',error:'播报异常'};
const ASR_LABELS={idle:'未开启', 'requesting-permission':'等待麦克风权限',starting:'正在开启',listening:'正在聆听',stopping:'正在停止',failed:'语音交流异常'};
const PAGE_LABELS={elder_active:'老人端在前台',staff_view:'当前切到工作人员端',hidden:'老人页面在后台',closing:'老人页面已关闭'};

let currentItem=null;
let currentReminder=null;
let pendingResponse=null;
let isDemo=true;
let currentRole='elder';
let sdkLoadPromise=null;
let claimBusy=false;
let reportTimer=null;
let renderWaitTimer=null;
let knownItems=[];
let requestedItemLoaded=false;
let presentationStage='idle';
let presentationToken=0;
let pendingAutoPlay=false;
let presentationFallbackTimer=null;
let reminderWakeTimer=null;
let voiceAnswerFallbackTimer=null;
const claimedReminderIds=new Set();
const sessionId=(globalThis.crypto?.randomUUID?.()||`session-${Date.now()}-${Math.random().toString(16).slice(2)}`).slice(0,64);
const runtime={
  pageState:'elder_active',connectionState:'disconnected',renderState:'uninitialized',
  speechState:'idle',asrState:'idle',lastAction:'老人端数字人页面已经打开'
};
const $=id=>document.getElementById(id);
const disconnectedMarkup=$('avatar-container').innerHTML;

function esc(value){return String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));}
function isLocalHostname(hostname=location.hostname){return LOCAL_HOSTS.has(hostname);}
function sdkErrorCode(error){const code=String(error?.code??'未知').replace(/[^a-zA-Z0-9_-]/g,'').slice(0,40);return code||'未知';}
function escapeXml(text){return text.replace(/[<>&"']/g,character=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&apos;'}[character]));}

async function api(path,data){
  const response=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result=await response.json();
  if(!response.ok)throw Error(result.error||'请求失败');
  return result;
}

function formatTime(value){
  if(!value)return '未提供时间';
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return value;
  return new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'long',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(date);
}

function localInputValue(date){
  const shifted=new Date(date.getTime()-date.getTimezoneOffset()*60000);
  return shifted.toISOString().slice(0,16);
}

function canvasLayoutSummary(container){
  const parent=container.getBoundingClientRect();
  return [...container.querySelectorAll('canvas')].slice(0,6).map((canvas,index)=>{
    const rect=canvas.getBoundingClientRect();
    const style=getComputedStyle(canvas);
    const left=Math.round(rect.left-parent.left);const top=Math.round(rect.top-parent.top);
    return `Canvas${index+1} ${Math.round(rect.width)}×${Math.round(rect.height)} @ ${left},${top} 缓冲区${canvas.width}×${canvas.height} ${style.position} ${style.display}/${style.visibility}/o${style.opacity} z${style.zIndex}`;
  }).join('；').slice(0,800);
}

function safeNodeToken(value){
  const token=String(value||'').replace(/[^a-zA-Z0-9_-]/g,'').slice(0,40);
  return token==='avatar-bg-container'||token==='avatar-sdk-widget-container'?token:'';
}

function renderNodes(container){
  const nodes=[...container.children];
  for(const node of container.querySelectorAll('canvas,video'))if(!nodes.includes(node))nodes.push(node);
  return nodes.slice(0,40);
}

function renderLayoutSummary(container){
  const parent=container.getBoundingClientRect();
  return renderNodes(container).slice(0,12).map((node,index)=>{
    const rect=node.getBoundingClientRect();
    const style=getComputedStyle(node);
    const left=Math.round(rect.left-parent.left);const top=Math.round(rect.top-parent.top);
    const id=safeNodeToken(node.id);const classes=[...node.classList].map(safeNodeToken).filter(Boolean).slice(0,2);
    const identity=`${node.tagName.toLowerCase()}${id?`#${id}`:''}${classes.length?`.${classes.join('.')}`:''}`;
    return `节点${index+1} ${identity} ${Math.round(rect.width)}×${Math.round(rect.height)} @ ${left},${top} ${style.position} ${style.display}/${style.visibility}/o${style.opacity} z${style.zIndex}`;
  }).join('；').slice(0,1600);
}

function runtimePayload(pageState=runtime.pageState){
  const container=$('avatar-container');
  const rect=container.getBoundingClientRect();
  return {
    session_id:sessionId,
    page_state:pageState,
    connection_state:runtime.connectionState,
    render_state:runtime.renderState,
    speech_state:runtime.speechState,
    asr_state:runtime.asrState,
    current_item_id:Number.isInteger(currentItem?.id)?currentItem.id:null,
    last_action:runtime.lastAction.slice(0,300),
    container_width:Math.max(0,Math.min(10000,Math.round(rect.width))),
    container_height:Math.max(0,Math.min(10000,Math.round(rect.height))),
    canvas_count:Math.min(20,container.querySelectorAll('canvas').length),
    canvas_layout:canvasLayoutSummary(container),
    render_node_count:renderNodes(container).length,
    render_layout:renderLayoutSummary(container)
  };
}

function reportRuntime(immediate=false){
  clearTimeout(reportTimer);
  const send=()=>api('/api/avatar-runtime',runtimePayload()).catch(()=>{});
  if(immediate)void send();
  else reportTimer=setTimeout(send,120);
}

function reportClosing(){
  runtime.pageState='closing';
  const body=JSON.stringify(runtimePayload('closing'));
  try{navigator.sendBeacon('/api/avatar-runtime',new Blob([body],{type:'application/json'}));}catch(_){}
}

function showStatus(message,warning=false){
  $('avatarFeedback').textContent=message;
  $('avatarFeedback').className=warning?'avatar-feedback warning':'avatar-feedback';
  runtime.lastAction=String(message||'').slice(0,300);
  reportRuntime();
}

function showTechnicalStatus(message){
  const text=String(message||'').slice(0,300);
  $('connectionSummary').textContent=text;
  runtime.lastAction=text;
  reportRuntime();
}

function createButton(label,onClick,className=''){
  const button=document.createElement('button');
  button.type='button';button.textContent=label;
  if(className)button.className=className;
  button.onclick=onClick;
  return button;
}

function setInteraction(title,hint,buttons=[]){
  $('avatarInteractionTitle').textContent=title;
  $('avatarInteractionHint').textContent=hint;
  $('avatarInteractionButtons').replaceChildren(...buttons);
  $('avatarInteractionExtra').replaceChildren();
}

function setSubtitle(text,label='知应正在说明'){
  $('avatarSubtitleLabel').textContent=label;
  $('avatarSubtitle').textContent=text;
}

function splitItemLines(content){
  return String(content||'').replace(/([。！？；])/g,'$1\n').split(/\n+/).map(line=>line.trim()).filter(Boolean);
}

function automaticInfoGraphic(item){
  const palette=item.category==='反诈宣传'?{back:'#E8B6A8',accent:'#A64A3E',label:'温馨提醒'}:item.category==='自由内容'?{back:'#EBC7B5',accent:'#8D4C3D',label:'社区通知'}:{back:'#F3E0BA',accent:'#8D4C3D',label:'事项信息'};
  const title=splitItemLines(item.content)[0]||'请查看事项内容';
  const shortTitle=title.slice(0,22)+(title.length>22?'…':'');
  const details=[item.event_time?`时间：${formatTime(item.event_time)}`:'',item.location?`地点：${item.location}`:''].filter(Boolean);
  const detail=details.join('　').slice(0,35)||'请以左侧大字事项为准';
  return `<svg viewBox="0 0 420 280" role="img" aria-label="知应根据事项文字生成的信息图"><rect width="420" height="280" rx="24" fill="${palette.back}"/><rect x="38" y="35" width="344" height="210" rx="20" fill="#FFFDF8" stroke="${palette.accent}" stroke-width="6"/><circle cx="85" cy="83" r="24" fill="${palette.accent}"/><path d="M75 83h20M85 73v20" stroke="#FFFDF8" stroke-width="6" stroke-linecap="round"/><text x="123" y="89" fill="${palette.accent}" font-size="20" font-weight="800">${escapeXml(palette.label)}</text><path d="M68 123h284" stroke="#D7B9A6" stroke-width="3"/><text x="68" y="158" fill="#3F3833" font-size="22" font-weight="800">${escapeXml(shortTitle)}</text><text x="68" y="197" fill="#766B63" font-size="16" font-weight="700">${escapeXml(detail)}</text><text x="68" y="225" fill="#766B63" font-size="13">根据事项文字生成的信息图</text></svg>`;
}

function renderVisual(item){
  const visual=$('avatarVisual');
  if(item.image_url&&/^\/api\/items\/[1-9]\d*\/image$/.test(item.image_url)){
    const image=document.createElement('img');
    image.src=item.image_url;
    image.alt=item.image_source==='doubao'?'豆包生成、工作人员确认的辅助图片，请同时核对事项大字内容。':'工作人员审核图片，请同时核对事项大字内容。';
    image.decoding='async';
    visual.replaceChildren(image);
    $('avatarImageCaption').textContent=item.image_source==='doubao'?'豆包生成图片，已经工作人员确认；请以大字事实为准。':`工作人员上传的审核图片${item.image_filename?`：${item.image_filename}`:''}，请以大字事实为准。`;
    return;
  }
  const visualKey=item.image_key||'auto';
  visual.innerHTML=visuals[visualKey]||automaticInfoGraphic(item);
  $('avatarImageCaption').textContent=visuals[visualKey]?(imageCaptions[visualKey]||'工作人员配置的图片，请以大字事实为准。'):'知应根据事项文字生成的信息图，请以大字事实为准。';
}

function hidePresentationDetails(){
  $('avatarImageCard').hidden=true;
  $('elderGuidance').hidden=true;
  $('avatarInteractionDock').hidden=true;
  $('avatarInteractionButtons').replaceChildren();
  $('avatarInteractionExtra').replaceChildren();
}

function showPresentationFallback(message){
  if(presentationStage!=='presenting')return;
  $('avatarInteractionDock').hidden=false;
  setInteraction('可以继续查看',message,[createButton('先查看图片和选择',()=>revealPresentation('已跳过语音讲解。'),'positive')]);
  showStatus(message,true);
}

function revealPresentation(message='事项讲解已结束。'){
  if(presentationStage!=='presenting')return;
  clearTimeout(presentationFallbackTimer);
  presentationFallbackTimer=null;
  pendingAutoPlay=false;
  presentationStage='ready';
  $('avatarImageCard').hidden=false;
  $('elderGuidance').hidden=false;
  $('avatarInteractionDock').hidden=false;
  renderPrimaryActions();
  if(message)showStatus(message);
}

function tryAutoPlayPresentation(){
  if(presentationStage!=='presenting'||!pendingAutoPlay||!currentItem)return;
  if(!avatarBridge.connected||!avatarBridge.visualReady){
    showPresentationFallback('数字人尚未连接或 SDK 尚未报告渲染。您可以先查看图片和选择；不会自动播放。');
    return;
  }
  const token=presentationToken;
  const started=avatarBridge.speakText(spokenText(currentItem),'当前事项','presentation');
  if(!started){
    showPresentationFallback('自动讲解没有开始。您可以先查看图片和选择。');
    return;
  }
  pendingAutoPlay=false;
  $('avatarInteractionDock').hidden=true;
  presentationFallbackTimer=setTimeout(()=>{
    if(presentationStage==='presenting'&&presentationToken===token&&runtime.speechState==='requesting'){
      showPresentationFallback('自动讲解暂未开始。您可以先查看图片和选择。');
    }
  },7000);
}

function beginPresentation(){
  presentationToken+=1;
  clearTimeout(presentationFallbackTimer);
  presentationFallbackTimer=null;
  presentationStage='presenting';
  pendingAutoPlay=true;
  hidePresentationDetails();
  setSubtitle(spokenText(currentItem),'知应正在说明');
  tryAutoPlayPresentation();
}

function finishPresentationFromSpeech(message){
  if(presentationStage==='presenting')revealPresentation(message);
}

function loadAvatarSdk(){
  if(window.XingyunAvatarAgent)return Promise.resolve();
  if(sdkLoadPromise)return sdkLoadPromise;
  sdkLoadPromise=new Promise((resolve,reject)=>{
    const script=document.createElement('script');
    script.src=SDK_URL;script.referrerPolicy='no-referrer';
    script.onload=()=>window.XingyunAvatarAgent?resolve():reject(Error('SDK 构造器不可用'));
    script.onerror=()=>reject(Error('SDK 文件加载失败'));
    document.head.append(script);
  }).catch(error=>{sdkLoadPromise=null;throw error;});
  return sdkLoadPromise;
}

function restoreAvatarStage(){
  const container=$('avatar-container');
  container.classList.remove('connected','rendering');
  container.removeAttribute('style');
  container.dataset.avatarState='disconnected';
  container.innerHTML=disconnectedMarkup;
}

function updateVoiceConversationUi(message=''){
  const state=runtime.asrState;
  const active=['requesting-permission','starting','listening','stopping'].includes(state);
  const canStart=avatarBridge.connected&&avatarBridge.agentReady&&currentRole==='elder'&&!document.hidden;
  $('startVoiceConversation').hidden=active;
  $('startVoiceConversation').disabled=!canStart;
  $('stopVoiceConversation').hidden=!active;
  $('stopVoiceConversation').disabled=state==='stopping';
  $('replayVoiceAnswer').disabled=!avatarBridge.connected||!avatarBridge.lastVoiceAnswer||avatarBridge.speaking;
  $('avatarVolume').disabled=!avatarBridge.connected;
  if(message)$('voiceConversationStatus').textContent=message;
  else if(!avatarBridge.connected)$('voiceConversationStatus').textContent='连接数字人后，您可以自己决定何时打开麦克风。';
  else if(!avatarBridge.agentReady)$('voiceConversationStatus').textContent='数字人会话正在准备，暂时不能打开麦克风。';
  else $('voiceConversationStatus').textContent={
    idle:'麦克风未开启。点击后才会开始聆听。',
    'requesting-permission':'正在等待浏览器允许使用麦克风。',
    starting:'正在开启语音交流。',
    listening:'正在聆听。您可以连续对话，也可以随时开口打断知应。',
    stopping:'正在关闭麦克风。',
    failed:'语音交流启动失败；您可以检查麦克风权限后重试。'
  }[state]||'语音交流状态暂时无法确认。';
}

function clearVoiceConversationTranscript(){
  avatarBridge.cancelVoiceAnswerFallback();
  avatarBridge.llmText='';avatarBridge.lastVoiceAnswer='';
  $('voiceHeardText').textContent='';
  $('voiceAnswerText').textContent='';
  $('voiceTranscript').hidden=true;
  updateVoiceConversationUi();
}

function updateConnectionUi(summary){
  const connected=runtime.connectionState==='connected';
  const busy=runtime.connectionState==='connecting';
  const visualReady=avatarBridge.visualReady;
  $('connectionPanel').classList.toggle('connected',connected);
  $('connectionBadge').className=`connection ${runtime.connectionState==='disconnected'?'':runtime.connectionState}`.trim();
  $('connectionBadge').textContent=visualReady?'SDK 已报告渲染，人物待确认':connected?'会话已连接，等待 SDK 渲染':busy?'正在连接':runtime.connectionState==='error'?'连接异常':'数字人未连接';
  $('connectAvatar').hidden=connected;
  $('disconnectAvatar').hidden=!connected;
  $('connectAvatar').disabled=busy;
  $('disconnectAvatar').disabled=busy;
  for(const field of[$('appIdInput'),$('appSecretInput'),$('credentialConsent')])field.disabled=connected||busy;
  $('connectionSummary').textContent=summary;
  updateVoiceConversationUi();
}

const avatarBridge={
  agent:null,recognition:null,voiceActive:false,speakingReply:'',pendingVoiceReply:null,connected:false,agentReady:false,visualReady:false,speaking:false,clientSpeakId:null,lastSpeakState:'',speechPurpose:null,pendingReminderSpeech:null,asrState:'idle',conversationState:'idle',llmText:'',lastVoiceAnswer:'',pendingVoiceAnswer:'',voiceAnswerAttempts:0,voiceResponseHadSpeech:false,
  assertHostSize(stage){
    const rect=$('avatar-container').getBoundingClientRect();
    const width=Math.round(rect.width);const height=Math.round(rect.height);
    if(width<10||height<10){
      const error=Error(`${stage}的 SDK 宿主尺寸异常：${width}×${height}`);
      error.code='container_size';
      throw error;
    }
    return `${width}×${height}`;
  },
  async connect(appId,appSecret){
    if(!isLocalHostname())throw Error('只允许在本机地址连接数字人。');
    if(this.agent)throw Error('数字人已经连接或正在连接。');
    runtime.connectionState='connecting';runtime.renderState='init';runtime.speechState='idle';runtime.asrState='idle';
    this.agentReady=false;this.asrState='idle';this.conversationState='idle';
    updateConnectionUi('正在加载魔珐 SDK；凭据不会写入本地存储。');reportRuntime(true);
    await loadAvatarSdk();
    const container=$('avatar-container');
    container.replaceChildren();container.classList.add('connected');container.dataset.avatarState='connecting';
    let agent;
    try{
      this.assertHostSize('构造 SDK 前');
      const handleRender=state=>this.handleRenderState(state);
      agent=new window.XingyunAvatarAgent({
        container,appId,appSecret,gatewayServer:GATEWAY_SERVER,enableClientInterrupt:true,
        audio:{inputEnabled:false},
        features:{anti_interference:{semantic_judge_enabled:true},vad_merge_mode:true,volume_and_repetition_text_detection:true},
        reconnect:{enabled:true,maxAttempts:6,initialDelayMs:500,maxDelayMs:8000},
        onMessage:error=>this.handleSdkError('渲染或会话',error),
        onStatusChange:status=>this.handleStatus(status),
        onRenderChange:handleRender,
        onVoiceStateChange:(state,duration,clientSpeakId)=>this.handleVoiceState(state,duration,clientSpeakId),
        onSpeakStateChange:(state,clientSpeakId)=>this.handleSpeakState(state,clientSpeakId),
        agentCallbacks:{
          onAgentStateChange:state=>this.handleAgentState(state),
          onRenderChange:handleRender,
          onSpeakStateChange:event=>this.handleSpeakState(event?.state,event?.clientSpeakId),
          onConversationChange:event=>this.handleConversation(event),
          onError:error=>this.handleAgentError(error)
        }
      });
      this.agent=agent;
      this.assertHostSize('构造 SDK 后');
      await agent.init({onDownloadProgress:progress=>{
        const value=Number.isFinite(Number(progress))?Math.max(0,Math.min(100,Math.round(Number(progress)))):0;
        $('connectionSummary').textContent=`数字人资源加载：${value}%`;
      }});
      const hostSize=this.assertHostSize('SDK 初始化后');
      this.connected=true;this.agentReady=true;runtime.connectionState='connected';
      container.dataset.avatarState='connected';
      updateConnectionUi(`数字人会话已连接，SDK 宿主保持 ${hostSize}，正在等待渲染报告。`);
      this.setVolume(Number($('avatarVolume').value),false);
      showStatus(`声音会话已经连接，SDK 宿主为 ${hostSize}；收到 rendering 后仍需核对人物是否实际可见。`);
      clearTimeout(renderWaitTimer);
      renderWaitTimer=setTimeout(()=>{
        if(this.connected&&!this.visualReady){
          runtime.renderState='unavailable';
          updateConnectionUi('会话已连接，但尚未收到 SDK rendering 状态。请查看工作人员端运行状况。');
          showStatus('可以听到声音，但 SDK 尚未报告渲染状态。',true);
          if(presentationStage!=='presenting')renderPrimaryActions();reportRuntime(true);
        }
      },8000);
      if(presentationStage!=='presenting')renderPrimaryActions();reportRuntime(true);
      if(this.pendingReminderSpeech)queueMicrotask(()=>this.flushPendingReminderSpeech());
    }catch(error){
      this.agent=null;this.connected=false;this.agentReady=false;this.visualReady=false;this.speaking=false;this.asrState='idle';
      runtime.connectionState='error';runtime.renderState='stopped';runtime.speechState='error';runtime.asrState='idle';
      try{if(agent)await agent.destroy('init_failed');}catch(_){}
      restoreAvatarStage();
      const detail=error?.message?String(error.message).slice(0,200):'';
      const summary=error?.code==='container_size'?'SDK 宿主尺寸异常，已停止连接以免人物被裁切。':`连接失败（错误码：${sdkErrorCode(error)}）${detail?`：${detail}`:''}。请检查凭据、网络和应用配置。`;
      updateConnectionUi(summary);
      reportRuntime(true);throw error;
    }
  },
  handleRenderState(state){
    const value=String(state??'').toLowerCase();
    if(!['init','rendering','pausing','paused','resumed','stopped'].includes(value))return;
    if(runtime.renderState===value)return;
    runtime.renderState=value;
    const visible=value==='rendering'||value==='resumed';
    this.visualReady=visible;
    $('avatar-container').classList.toggle('rendering',visible);
    if(visible){
      clearTimeout(renderWaitTimer);
      updateConnectionUi('已收到 SDK rendering 状态；请核对人物是否与背景同时可见。');
      showStatus(value==='resumed'?'SDK 已报告恢复渲染；请继续核对人物是否可见。':'SDK 已报告渲染启动；请核对人物是否与背景同时可见。');
      requestAnimationFrame(()=>tryAutoPlayPresentation());
    }else{
      updateConnectionUi(`当前画面状态：${RENDER_LABELS[value]||value}。`);
      if(value==='paused'||value==='stopped')showStatus(`数字人${RENDER_LABELS[value]}。`,true);
    }
    if(presentationStage!=='presenting')renderPrimaryActions();reportRuntime(true);
  },
  invokeEmbodiedState(method){
    if(!this.agent||typeof this.agent[method]!=='function')return false;
    try{this.agent[method]();return true;}catch(error){this.handleSdkError(`数字人${method}状态`,error);return false;}
  },
  setVolume(rawValue,announce=true){
    const value=Math.max(0,Math.min(1,Number(rawValue)));
    if(!this.agent||typeof this.agent.setVolume!=='function'||!Number.isFinite(value))return false;
    try{this.agent.setVolume(value);if(announce)updateVoiceConversationUi(`数字人声音已调整为${value>=.9?'较大':value>=.65?'适中':'较轻'}。`);return true;}
    catch(error){this.handleSdkError('音量调整',error);return false;}
  },
  cancelVoiceAnswerFallback(clearPending=true){
    clearTimeout(voiceAnswerFallbackTimer);voiceAnswerFallbackTimer=null;
    if(clearPending)this.pendingVoiceAnswer='';
  },
  armVoiceAnswerFallback(text){
    const value=String(text||'').trim().slice(0,3000);
    this.cancelVoiceAnswerFallback();
    this.lastVoiceAnswer=value;this.pendingVoiceAnswer=this.voiceResponseHadSpeech?'':value;this.voiceAnswerAttempts=0;
    updateVoiceConversationUi();
    if(!this.pendingVoiceAnswer)return;
    voiceAnswerFallbackTimer=setTimeout(()=>{voiceAnswerFallbackTimer=null;this.attemptVoiceAnswerFallback();},VOICE_ANSWER_FALLBACK_MS);
  },
  attemptVoiceAnswerFallback(){
    if(!this.pendingVoiceAnswer)return;
    if(!this.connected||!this.agent||document.hidden||currentRole!=='elder'||currentReminder){this.cancelVoiceAnswerFallback();return;}
    if(this.speaking){voiceAnswerFallbackTimer=setTimeout(()=>{voiceAnswerFallbackTimer=null;this.attemptVoiceAnswerFallback();},900);return;}
    this.voiceAnswerAttempts+=1;
    const started=this.speakText(this.pendingVoiceAnswer,'知应回答补播','voice_fallback');
    if(!started){
      if(this.voiceAnswerAttempts<2)voiceAnswerFallbackTimer=setTimeout(()=>{voiceAnswerFallbackTimer=null;this.attemptVoiceAnswerFallback();},1000);
      else{this.cancelVoiceAnswerFallback();updateVoiceConversationUi('回答文字已显示，但未能启动数字人声音；请点击“重听刚才回答”。');}
      return;
    }
    updateVoiceConversationUi('回答文字已显示但未检测到开口，正在用魔珐数字人补播。');
    voiceAnswerFallbackTimer=setTimeout(()=>{
      voiceAnswerFallbackTimer=null;
      if(!this.pendingVoiceAnswer||this.voiceResponseHadSpeech)return;
      if(this.voiceAnswerAttempts<2){
        try{this.agent?.interrupt?.('voice_answer_no_start');}catch(_){}
        this.speaking=false;this.speechPurpose=null;runtime.speechState='idle';this.updateSpeechUi();
        voiceAnswerFallbackTimer=setTimeout(()=>{voiceAnswerFallbackTimer=null;this.attemptVoiceAnswerFallback();},800);
      }else{
        this.cancelVoiceAnswerFallback();runtime.speechState='error';this.updateSpeechUi();reportRuntime(true);
        updateVoiceConversationUi('回答文字已显示，但魔珐数字人仍未开口；可点击“重听刚才回答”重试。');
      }
    },VOICE_ANSWER_START_TIMEOUT_MS);
  },
  markVoiceAnswerSpeechStarted(){
    const purpose=this.speechPurpose;
    const isVoiceAnswer=['voice_fallback','voice_replay','voice_chat'].includes(purpose)||(!purpose&&this.asrState==='listening'&&['asking','speaking','completed'].includes(this.conversationState));
    if(!isVoiceAnswer)return;
    this.voiceResponseHadSpeech=true;this.cancelVoiceAnswerFallback();
    updateVoiceConversationUi('魔珐数字人正在回答，您也可以自然打断。');
  },
  replayLastVoiceAnswer(){
    if(!this.lastVoiceAnswer)return false;
    this.cancelVoiceAnswerFallback();
    return this.speakText(this.lastVoiceAnswer,'刚才的回答','voice_replay');
  },
  setASRState(state,message=''){
    const value=String(state??'').toLowerCase();
    if(!['idle','requesting-permission','starting','listening','stopping','failed'].includes(value))return;
    this.asrState=value;runtime.asrState=value;
    updateVoiceConversationUi(message);reportRuntime(true);
  },
  startRecognition(){
    const Recognition=globalThis.SpeechRecognition||globalThis.webkitSpeechRecognition;
    if(!Recognition)return false;
    const recognition=new Recognition();
    recognition.lang='zh-CN';recognition.continuous=false;recognition.interimResults=true;recognition.maxAlternatives=1;
    recognition.onstart=()=>{this.invokeEmbodiedState('listen');this.setASRState('listening','正在聆听。您可以连续对话，也可以随时开口打断知应。');};
    recognition.onresult=event=>this.handleBrowserRecognitionResult(event);
    recognition.onerror=event=>this.handleBrowserRecognitionError(event);
    recognition.onend=()=>this.handleBrowserRecognitionEnd();
    this.recognition=recognition;
    try{recognition.start();return true;}
    catch(_){this.recognition=null;return false;}
  },
  abortRecognitionSilently(){
    const recognition=this.recognition;this.recognition=null;
    if(recognition){try{recognition.abort();}catch(_){}}
  },
  isEchoOfSpeaking(text){
    if(!this.speaking||!this.speakingReply)return false;
    const norm=s=>String(s||'').replace(/[\s，。！？、,.!?;；:：\-—]/g,'');
    const reply=norm(this.speakingReply);const heard=norm(text);
    if(!reply||!heard)return false;
    if(reply===heard)return true;
    if(heard.length>=4&&reply.includes(heard))return true;
    if(reply.length>=4&&heard.includes(reply))return true;
    return false;
  },
  startVoiceConversation(){
    if(!this.connected||!this.agent||!this.agentReady){
      updateVoiceConversationUi('数字人会话尚未准备好，不能打开麦克风。');
      return false;
    }
    if(currentRole!=='elder'||document.hidden){
      updateVoiceConversationUi('只有老人页面在前台时才能打开麦克风。');
      return false;
    }
    if(this.voiceActive)return true;
    const Recognition=globalThis.SpeechRecognition||globalThis.webkitSpeechRecognition;
    if(!Recognition){
      this.setASRState('failed','当前浏览器不支持语音识别，请使用最新版 Edge 或 Chrome 后重试。');
      showStatus('当前浏览器不支持语音识别，不能打开麦克风。',true);
      return false;
    }
    clearVoiceConversationTranscript();this.voiceResponseHadSpeech=false;
    this.voiceActive=true;this.speakingReply='';this.pendingVoiceReply=null;
    this.setASRState('requesting-permission','正在等待浏览器允许使用麦克风。');
    if(this.startRecognition()){
      showStatus('语音交流已经开启；您可以连续和知应对话，也可以直接开口打断它，点“结束语音交流”才会关闭麦克风。');
      return true;
    }
    this.voiceActive=false;
    this.setASRState('failed','语音交流启动失败。请检查麦克风权限和设备。');
    showStatus('语音交流启动失败；请检查麦克风权限后重试。',true);
    return false;
  },
  stopVoiceConversation(reason='user_stop',announce=true){
    this.voiceActive=false;this.speakingReply='';this.pendingVoiceReply=null;this.cancelVoiceAnswerFallback();
    this.abortRecognitionSilently();
    this.invokeEmbodiedState('idle');
    this.setASRState('idle',announce?'语音交流已结束，麦克风已经停止。':'麦克风已经停止；需要时请再次点击开始。');
    if(announce)showStatus('语音交流已结束；本轮临时对话没有保存。');
    return true;
  },
  handleBrowserRecognitionResult(event){
    let interim='';let finalText='';
    for(let index=event.resultIndex;index<event.results.length;index++){
      const result=event.results[index];
      const transcript=String(result[0]?.transcript??'').trim();
      if(result.isFinal)finalText=transcript;else interim+=transcript;
    }
    if(interim){
      $('voiceTranscript').hidden=false;
      $('voiceHeardText').textContent=interim.slice(0,2000);
      updateVoiceConversationUi('正在聆听，已经听到一部分内容。');
    }
    if(finalText){
      finalText=finalText.slice(0,2000);
      if(this.isEchoOfSpeaking(finalText)){
        updateVoiceConversationUi('知应正在回答，您可以随时开口打断。');
        return;
      }
      $('voiceTranscript').hidden=false;
      $('voiceHeardText').textContent=finalText;
      if(!currentReminder)setSubtitle(finalText,'我刚才说');
      updateVoiceConversationUi('已经听清，知应正在准备回答。');
      if(this.speaking)this.interrupt('voice_barge_in');
      void this.submitVoiceChat(finalText);
    }
  },
  handleBrowserRecognitionError(event){
    const error=String(event?.error??'');
    if(error==='aborted'||error==='no-speech')return;
    this.voiceActive=false;this.recognition=null;
    const message={
      'not-allowed':'浏览器没有允许使用麦克风，请检查权限后重试。',
      'audio-capture':'没有找到可用的麦克风设备。',
      'network':'语音识别网络异常，请稍后再试。',
      'service-not-allowed':'浏览器语音识别服务暂时不可用。'
    }[error]||'语音识别出现异常，请重试。';
    this.setASRState('failed',message);
    showStatus(`语音识别出现问题：${message}`,true);
  },
  handleBrowserRecognitionEnd(){
    this.recognition=null;
    if(this.voiceActive){
      this.startRecognition();
    }else if(this.asrState==='listening'){
      this.setASRState('idle','');
    }
  },
  async submitVoiceChat(text){
    const value=String(text||'').trim().slice(0,2000);
    if(!value)return;
    this.cancelVoiceAnswerFallback();this.voiceResponseHadSpeech=false;
    try{
      const result=await api('/api/voice/chat',{text:value});
      const reply=String(result?.reply??'').trim().slice(0,3000);
      if(!reply){
        updateVoiceConversationUi('知应没有生成回答，请再说一次。');
        return;
      }
      this.llmText=reply;this.lastVoiceAnswer=reply;
      $('voiceTranscript').hidden=false;
      $('voiceAnswerText').textContent=reply;
      if(!currentReminder)setSubtitle(reply,'知应回答');
      this.armVoiceAnswerFallback(reply);
      this.deliverVoiceReply(reply);
    }catch(error){
      this.cancelVoiceAnswerFallback();
      updateVoiceConversationUi(`对话回答失败：${error?.message||'服务暂时不可用'}。请再说一次。`);
      showStatus(`对话回答失败：${error?.message||'服务暂时不可用'}。`,true);
    }
  },
  deliverVoiceReply(reply){
    if(!this.connected||!this.agent){
      updateVoiceConversationUi('数字人未连接，回答只显示在文字区，没有执行语音播报。');
      return;
    }
    if(this.speaking){this.pendingVoiceReply=reply;return;}
    this.pendingVoiceReply=null;
    this.speakText(reply,'知应回答','voice_chat');
  },
  flushPendingVoiceReply(){
    if(!this.pendingVoiceReply||!this.connected||this.speaking)return;
    const reply=this.pendingVoiceReply;this.pendingVoiceReply=null;
    if(!this.speakText(reply,'知应回答','voice_chat'))this.pendingVoiceReply=reply;
  },
  handleSdkError(channel,error){
    const code=sdkErrorCode(error);const domain=String(error?.domain??'').toLowerCase();
    if(TRANSIENT_SOCKET_CODES.has(code)){
      showTechnicalStatus('魔珐语音连接短暂中断，SDK 正在自动恢复。');
      if(this.asrState==='listening')updateVoiceConversationUi('魔珐语音连接短暂中断，正在自动恢复；不会自动提交任何业务操作。');
      return;
    }
    if(domain==='asr'||domain==='permission'){
      this.cancelVoiceAnswerFallback();this.setASRState('failed',`麦克风或语音识别出现异常（错误码：${code}）。请检查权限后重试。`);
      showTechnicalStatus(`魔珐语音识别异常（错误码：${code}）。`);return;
    }
    if(domain==='brain'){
      this.cancelVoiceAnswerFallback();updateVoiceConversationUi(`本轮魔珐 Brain 回答未完成（错误码：${code}），请再说一次。`);
      showTechnicalStatus(`魔珐 Brain 暂时不可用（错误码：${code}）。`);return;
    }
    if(domain==='ttsa'||domain==='network'){
      updateVoiceConversationUi(`魔珐声音连接出现异常（错误码：${code}），SDK 正在尝试恢复。`);
      showTechnicalStatus(`魔珐${domain==='ttsa'?'声音':'网络'}连接异常（错误码：${code}）。`);return;
    }
    this.reportError(channel,error);
  },
  handleAgentError(error){this.handleSdkError('智能体',error);},
  speakText(text,reason='当前字幕',purpose='manual'){
    const voicePurpose=String(purpose).startsWith('voice_');
    if(!this.connected||!this.agent){
      if(voicePurpose)updateVoiceConversationUi(`数字人未连接，${reason}没有执行语音播报。`);
      else showStatus(`数字人未连接，${reason}只显示了大字，没有执行语音播报。`,true);
      return false;
    }
    if(this.speaking){
      if(voicePurpose)updateVoiceConversationUi('数字人正在说话，请稍后重听。');
      else showStatus('数字人正在播报。请先听完或停止，避免声音重叠。',true);
      return false;
    }
    const value=String(text||'').trim();
    if(!value){
      if(voicePurpose)updateVoiceConversationUi('当前没有可以重听的回答。');
      else showStatus('当前没有可播报的大字字幕。',true);
      return false;
    }
    try{
      this.speaking=true;this.speechPurpose=purpose;this.speakingReply=value;runtime.speechState='requesting';this.updateSpeechUi();reportRuntime(true);
      this.clientSpeakId=this.agent.speak(`<speak>${escapeXml(value)}</speak>`);
      if(voicePurpose)updateVoiceConversationUi(`${reason}请求已发送，正在等待魔珐数字人开口。`);
      else showStatus(`${reason}的播报请求已发送，等待数字人开始说话。`);
      return true;
    }catch(error){
      this.speaking=false;this.speechPurpose=null;runtime.speechState='error';this.updateSpeechUi();this.handleSdkError('播报',error);return false;
    }
  },
  queueReminderSpeech(text){
    this.cancelVoiceAnswerFallback();
    this.pendingReminderSpeech=String(text||'').trim();
    if(!this.pendingReminderSpeech)return;
    if(!this.connected)return;
    if(this.speaking||['asking','speaking'].includes(this.conversationState)){
      this.interrupt('reminder_due');
      showStatus('提醒时间到了，知应正在停止当前讲解，随后会主动播报提醒。');
      return;
    }
    this.flushPendingReminderSpeech();
  },
  flushPendingReminderSpeech(){
    if(!this.pendingReminderSpeech||!this.connected||this.speaking||['asking','speaking'].includes(this.conversationState))return;
    const text=this.pendingReminderSpeech;
    this.pendingReminderSpeech=null;
    if(!this.speakText(text,'到期提醒','reminder'))this.pendingReminderSpeech=text;
  },
  speakCurrent(){return this.speakText($('avatarSubtitle').textContent,'当前事项');},
  interrupt(reason='user_click'){
    if(!this.connected||!this.agent)return null;
    try{return this.agent.interrupt(reason);}catch(error){this.reportError('打断',error);return null;}
  },
  async disconnect(reason='user_disconnect'){
    clearTimeout(renderWaitTimer);
    if(this.agent&&this.asrState!=='idle')await this.stopVoiceConversation(reason,false);
    const agent=this.agent;this.agent=null;this.connected=false;this.agentReady=false;this.visualReady=false;this.speaking=false;this.speechPurpose=null;this.speakingReply='';this.pendingVoiceReply=null;this.clientSpeakId=null;this.asrState='idle';this.conversationState='idle';
    runtime.connectionState='disconnected';runtime.renderState='stopped';runtime.speechState='idle';runtime.asrState='idle';
    clearVoiceConversationTranscript();
    updateConnectionUi('正在释放数字人连接和渲染资源。');
    try{if(agent)await agent.destroy(reason);restoreAvatarStage();updateConnectionUi('数字人已断开；凭据输入框保持为空。');showStatus('数字人连接和画面已经销毁；事项与提醒记录仍保留。');}
    catch(error){restoreAvatarStage();runtime.connectionState='error';updateConnectionUi(`断开时出现错误（错误码：${sdkErrorCode(error)}）。`);}
    this.updateSpeechUi();if(presentationStage==='presenting')showPresentationFallback('数字人已断开。您可以先查看图片和选择。');else if(!currentReminder)renderPrimaryActions();reportRuntime(true);
  },
  destroyForUnload(){
    clearTimeout(renderWaitTimer);this.cancelVoiceAnswerFallback();
    const agent=this.agent;this.agent=null;this.connected=false;this.agentReady=false;this.visualReady=false;this.voiceActive=false;this.speakingReply='';this.pendingVoiceReply=null;this.asrState='idle';runtime.asrState='idle';
    if(this.recognition){try{this.recognition.abort();}catch(_){}this.recognition=null;}
    if(agent){try{void agent.destroy('page_unload');}catch(_){}}
  },
  handleAgentState(state){
    const value=String(state??'unknown');
    if(value==='running'){
      this.agentReady=true;
      $('connectionSummary').textContent=this.visualReady?'数字人会话可用，SDK 已报告渲染；请核对人物是否可见。':'数字人会话可用，仍在等待 SDK 渲染状态。';
      if(this.asrState==='listening')this.invokeEmbodiedState('listen');
    }else if(value==='reconnecting'){
      this.agentReady=false;this.cancelVoiceAnswerFallback();
      this.setASRState('idle','数字人连接正在恢复；麦克风不会自动重新开启。');
      showTechnicalStatus('魔珐语音连接正在自动恢复，请暂时不要操作。');
    }else if(['failed','destroyed'].includes(value)){
      this.agentReady=false;this.cancelVoiceAnswerFallback();
      this.setASRState('idle');
      showTechnicalStatus(`数字人运行状态：${value}。`);
    }
    updateVoiceConversationUi();reportRuntime();
  },
  handleStatus(status){
    const names={0:'在线',1:'离线',4:'连接关闭',5:'可见',6:'不可见'};
    $('connectionSummary').textContent=`数字人会话状态：${names[status]||String(status)}；画面状态：${RENDER_LABELS[runtime.renderState]||runtime.renderState}。`;
    reportRuntime();
  },
  handleVoiceState(state,duration,clientSpeakId){
    if(clientSpeakId!==undefined)this.clientSpeakId=clientSpeakId;
    const value=String(state??'').toLowerCase();
    if(value.includes('start')){this.setSpeaking(true);this.markVoiceAnswerSpeechStarted();}
    if(value.includes('end')||value.includes('stop')||value.includes('error')){
      const wasPresentation=this.speechPurpose==='presentation';
      this.setSpeaking(false,value.includes('error')?'error':'idle');
      if(wasPresentation){
        if(value.includes('error'))showPresentationFallback('自动讲解失败。您可以先查看图片和选择。');
        else finishPresentationFromSpeech('事项讲解已结束。');
      }
    }
  },
  handleSpeakState(state,clientSpeakId){
    const value=String(state??'');const key=`${value}:${clientSpeakId??''}`;
    if(!value||key===this.lastSpeakState)return;this.lastSpeakState=key;
    if(clientSpeakId!==undefined)this.clientSpeakId=clientSpeakId;
    if(value==='speak_start'){this.setSpeaking(true);this.markVoiceAnswerSpeechStarted();showStatus('数字人已经开始播报。');}
    else if(value==='speak_end'){
      const wasPresentation=this.speechPurpose==='presentation';
      this.setSpeaking(false);showStatus('数字人播报已经结束。');
      if(wasPresentation)finishPresentationFromSpeech('事项讲解已结束。');
    }
    else if(value==='speak_error'){
      const wasPresentation=this.speechPurpose==='presentation';
      this.setSpeaking(false,'error');showStatus('数字人播报失败，请查看大字内容。',true);
      if(wasPresentation)showPresentationFallback('自动讲解失败。您可以先查看图片和选择。');
    }
  },
  handleConversation(event){
    const state=String(event?.state??'');
    this.conversationState=state||'idle';
    if(state==='asking')updateVoiceConversationUi('知应正在理解您的话。');
    else if(state==='speaking')updateVoiceConversationUi('魔珐数字人正在回答，您也可以自然打断。');
    else if(state==='interrupted'){
      const wasPresentation=this.speechPurpose==='presentation';
      this.setSpeaking(false,'interrupted');showStatus('数字人已确认当前播报被停止。');
      if(wasPresentation)finishPresentationFromSpeech('已停止讲解，现在可以查看图片和选择。');
      if(this.asrState==='listening')updateVoiceConversationUi('本轮回答已停止，麦克风仍在聆听。');
    }else if(state==='completed'){
      const wasPresentation=this.speechPurpose==='presentation';
      this.setSpeaking(false);
      if(wasPresentation)finishPresentationFromSpeech('事项讲解已结束。');
      if(this.asrState==='listening'){this.invokeEmbodiedState('listen');updateVoiceConversationUi('本轮交流已完成，麦克风仍在聆听。');}
    }else if(state==='failed'){
      const wasPresentation=this.speechPurpose==='presentation';
      this.setSpeaking(false,'error');showStatus('本轮数字人播报失败，请查看大字内容。',true);
      if(wasPresentation)showPresentationFallback('自动讲解失败。您可以先查看图片和选择。');
      if(this.asrState==='listening')updateVoiceConversationUi('本轮回答失败，麦克风仍在聆听。');
    }
  },
  setSpeaking(value,endState='idle'){
    this.speaking=value;
    if(!value){this.speechPurpose=null;this.speakingReply='';}
    runtime.speechState=value?'speaking':endState;this.updateSpeechUi();reportRuntime(true);
    updateVoiceConversationUi();
    if(!value&&this.pendingReminderSpeech)queueMicrotask(()=>this.flushPendingReminderSpeech());
    if(!value&&this.pendingVoiceReply)queueMicrotask(()=>this.flushPendingVoiceReply());
  },
  updateSpeechUi(){$('interruptSpeech').hidden=!this.speaking;},
  reportError(channel,error){
    const wasPresentation=this.speechPurpose==='presentation';
    this.speaking=false;this.speechPurpose=null;runtime.speechState='error';this.updateSpeechUi();
    showStatus(`${channel}错误（错误码：${sdkErrorCode(error)}）。未记录完整错误对象。`,true);
    if(wasPresentation)showPresentationFallback('自动讲解发生错误。您可以先查看图片和选择。');
    reportRuntime(true);
  }
};

function spokenText(item){
  const parts=[item.content];
  if(item.event_time)parts.push(`时间是${formatTime(item.event_time)}`);
  if(item.location)parts.push(`地点是${item.location}`);
  return parts.map(part=>String(part).replace(/[。！？；\s]+$/,'')).join('。')+'。';
}

function renderItem(item,resetActions=true){
  currentItem=item;
  $('avatarItemCategory').textContent=`${item.category}${item.id?` · 事项 #${item.id}`:' · 示例内容'}`;
  $('avatarItemTitle').textContent=item.category==='反诈宣传'?'宣传':item.category==='自由内容'?'通知':'事项';
  const lines=splitItemLines(item.content);
  $('avatarItemContent').replaceChildren(...lines.map(line=>{const paragraph=document.createElement('p');paragraph.textContent=line;return paragraph;}));
  const facts=[];
  if(item.event_time)facts.push(`时间：${formatTime(item.event_time)}`);
  if(item.location)facts.push(`地点：${item.location}`);
  $('avatarFacts').replaceChildren(...facts.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
  $('avatarItemStatus').textContent=`当前状态：${item.status}`;
  renderVisual(item);
  setSubtitle(spokenText(item),'知应正在说明');
  if(resetActions){
    presentationStage='ready';pendingAutoPlay=false;
    $('avatarImageCard').hidden=false;
    $('elderGuidance').hidden=false;
    $('avatarInteractionDock').hidden=false;
    renderPrimaryActions();
  }
  reportRuntime();
}

function demoItem(){
  return {id:null,direction:'下达',category:'指令与代办事项',content:'10月18日上午9点，在社区一楼活动室举行手工花灯体验活动，工作人员会准备纸灯笼和安全剪刀。',event_time:'2026-10-18T09:00',location:'社区一楼活动室',image_key:'activity',status:'演示内容，尚未保存',reminder_enabled:true,response_mode:'options',options:[{id:1,label:'上午9点场',kind:'accept'},{id:2,label:'下午2点场',kind:'accept'},{id:3,label:'不参加',kind:'reject'}],reminders:[],events:[]};
}

function renderPrimaryActions(){
  if(!currentItem)return;
  const replayLabel=avatarBridge.visualReady?'知应讲给我听':avatarBridge.connected?'播报大字内容（等待 SDK 渲染）':'连接数字人后播报';
  const replay=createButton(replayLabel,()=>avatarBridge.speakCurrent(),'positive');
  const respond=createButton('我要回应',renderResponseChoices);
  const support=createButton('没听懂或需要帮助',renderSupport,'secondary');
  setInteraction('您想怎么做？','先听明白，再慢慢选择。',[replay,respond,support]);
}

function renderSupport(){
  const preset=()=>{
    const facts=[currentItem.content];
    if(currentItem.event_time)facts.push(`要记住的时间是${formatTime(currentItem.event_time)}`);
    if(currentItem.location)facts.push(`地点是${currentItem.location}`);
    return `换句话说：${facts.join('。')}。`;
  };
  const rephrase=createButton('换一种说法',()=>{
    const text=preset();
    setSubtitle(text,'预设的换一种说法');renderSupport();
    showStatus('已按保存的事项事实换一种说法；本次改述没有调用模型或自动保存。');
    if(avatarBridge.connected)avatarBridge.speakText(text,'换一种说法');
  },'secondary');
  const help=createButton('请工作人员帮助',()=>prepareDirectResponse('需要帮助','请工作人员帮助'));
  const back=createButton('返回',renderPrimaryActions,'secondary');
  setInteraction('需要哪种帮助？','请求帮助只会生成工作人员跟进记录，不表示已经有人到场。',[rephrase,help,back]);
}

function legacyPrimaryChoices(item){
  if(item.response_mode==='attendance')return[{label:'我参加',action:'参加',kind:'accept'},{label:'确认收到',action:'确认收到',kind:'acknowledge'}];
  return[{label:'确认收到',action:'确认收到',kind:'acknowledge'}];
}

function renderResponseChoices(){
  const choices=currentItem.options?.length?currentItem.options.map(option=>({label:option.label,optionId:option.id,kind:option.kind})):legacyPrimaryChoices(currentItem);
  const buttons=choices.map(choice=>createButton(choice.label,()=>prepareChoiceResponse(choice),choice.kind==='reject'?'danger':choice.kind==='accept'?'positive':'secondary'));
  buttons.push(createButton('其他选择',()=>renderOtherChoices(choices),'secondary'));
  setInteraction('请作出明确选择','这些按钮只记录您的选择，不表示事项已经完成。',buttons);
}

function renderOtherChoices(primaryChoices){
  const kinds=new Set(primaryChoices.map(choice=>choice.kind));const extras=[];
  if(!kinds.has('later'))extras.push({label:'我稍后再决定',action:'稍后决定'});
  if(!kinds.has('reject'))extras.push({label:'我不接受',action:'拒绝'});
  if(!kinds.has('help'))extras.push({label:'请工作人员帮助',action:'需要帮助'});
  const buttons=extras.map(choice=>createButton(choice.label,()=>prepareDirectResponse(choice.action,choice.label),choice.action==='拒绝'?'danger':'secondary'));
  buttons.push(createButton('返回主要选择',renderResponseChoices,'secondary'));
  setInteraction('其他选择','不用着急。没有回应不会被当作同意。',buttons);
}

function prepareChoiceResponse(choice){pendingResponse={label:choice.label,optionId:choice.optionId||null,action:choice.action||'选择选项'};renderResponseConfirmation();}
function prepareDirectResponse(action,label){pendingResponse={label,optionId:null,action};renderResponseConfirmation();}
function renderResponseConfirmation(){
  setInteraction('请再确认一次','确认后才会保存；仍可返回修改。',[createButton(`确认“${pendingResponse.label}”`,savePendingResponse),createButton('返回修改',renderResponseChoices,'secondary')]);
  const copy=document.createElement('p');copy.className='confirm-copy';copy.textContent=`准备记录：${pendingResponse.label}`;$('avatarInteractionExtra').append(copy);
}

async function savePendingResponse(){
  if(!currentItem||!pendingResponse)return;
  if(isDemo){showStatus(`这是演示内容，“${pendingResponse.label}”没有保存。`,true);renderSaved('演示选择未保存；请从“我的事项与反馈”打开正式事项。');return;}
  const buttons=[...$('avatarInteractionButtons').querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);
  try{
    const payload={id:currentItem.id,action:pendingResponse.action};if(pendingResponse.optionId)payload.option_id=pendingResponse.optionId;
    const result=await api('/api/respond',payload);currentItem=await api(`/api/items/${currentItem.id}`);renderItem(currentItem,false);
    showStatus(`“${pendingResponse.label}”已保存。${result.status}；这不表示事项已经完成。`);
    if(result.reminder_available)renderReminderSetup();else renderSaved(`您的选择“${pendingResponse.label}”已经保存。`);
    window.dispatchEvent(new CustomEvent('zhiying:item-updated'));
  }catch(error){showStatus(error.message||'回应保存失败。',true);renderResponseConfirmation();}
}

function validReminderOffsets(){
  const eventAt=new Date(currentItem.event_time).getTime();const now=Date.now();
  if(!Number.isFinite(eventAt))return[];
  return[1440,60,10].filter(minutes=>eventAt-minutes*60000>now).slice(0,2);
}

function renderReminderSetup(){
  const offsets=validReminderOffsets();
  const buttons=offsets.map(minutes=>createButton(OFFSET_LABELS[minutes],()=>createReminder({offset_minutes:minutes}),'positive'));
  buttons.push(createButton('不设置提醒',()=>{showStatus('本次没有创建提醒；刚才的事项选择仍已保存。');renderSaved('事项选择已保存，本次未设置提醒。');},'secondary'));
  setInteraction('要让知应提醒您吗？','提醒会真实保存；页面关闭或设备休眠时不能即时发声。',buttons);
  const details=document.createElement('details');details.className='custom-time';
  details.innerHTML='<summary>我想自己选择提醒时间</summary><div class="custom-time-grid"><input id="customReminderTime" type="datetime-local" aria-label="自选提醒时间"><button id="saveCustomReminder" type="button">确认这个时间</button></div>';
  $('avatarInteractionExtra').append(details);
  const input=details.querySelector('#customReminderTime');input.min=localInputValue(new Date(Date.now()+60000));if(currentItem.event_time)input.max=localInputValue(new Date(currentItem.event_time));
  details.querySelector('#saveCustomReminder').onclick=()=>{if(!input.value){showStatus('请先选择提醒时间。',true);return;}createReminder({remind_at:input.value});};
}

async function createReminder(values){
  const controls=[...$('avatarInteractionButtons').querySelectorAll('button'),...$('avatarInteractionExtra').querySelectorAll('button')];controls.forEach(button=>button.disabled=true);
  try{
    const reminder=await api('/api/reminders',{item_id:currentItem.id,...values});currentItem=await api(`/api/items/${currentItem.id}`);renderItem(currentItem,false);
    const when=formatTime(reminder.scheduled_for);showStatus(`提醒已由服务保存，计划时间是${when}。`);renderSaved(`提醒已设置：${when}。只有老人页面打开时才能显示或调用数字人播报。`);
    window.dispatchEvent(new CustomEvent('zhiying:item-updated'));
  }catch(error){showStatus(error.message||'提醒保存失败。',true);renderReminderSetup();}
}

function renderSaved(message){
  setInteraction('已经记录',message,[createButton('回到这件事',renderPrimaryActions,'secondary')]);
  const copy=document.createElement('p');copy.className='saved-copy';copy.textContent=message;$('avatarInteractionExtra').append(copy);
}

async function loadItem(rawId,{scroll=true}={}){
  const id=String(rawId);
  if(!/^[1-9]\d*$/.test(id))throw Error('事项编号不正确。');
  try{
    const item=await api(`/api/items/${id}`);
    if(item.direction!=='下达')throw Error('这个事项不是工作人员下达内容。');
    isDemo=false;renderItem(item,false);showStatus('正式事项已经加载，知应会先讲解；尚未作出的选择不会被当作同意。');beginPresentation();
    if(scroll)$('elderExperience').scrollIntoView({behavior:'smooth',block:'start'});
    return item;
  }catch(error){showStatus(error.message||'事项读取失败。',true);throw error;}
}

function syncItems(items){
  knownItems=Array.isArray(items)?items:[];
  if(currentItem?.id){
    const updated=knownItems.find(item=>item.id===currentItem.id);
    if(updated)renderItem(updated,false);
    else if(!currentReminder){
      isDemo=true;
      renderItem(demoItem());
      showStatus('刚才展示的事项已经被删除，舞台回到演示内容。');
    }
  }
  const requested=new URLSearchParams(location.search).get('item');
  if(requested&&!requestedItemLoaded){requestedItemLoaded=true;void loadItem(requested,{scroll:false}).catch(()=>{});}
  scheduleNearestReminder();
}

function scheduleNearestReminder(){
  clearTimeout(reminderWakeTimer);reminderWakeTimer=null;
  if(currentRole!=='elder'||document.hidden)return;
  const times=[];
  for(const item of knownItems){
    for(const reminder of item.reminders||[]){
      if(reminder.status!=='scheduled')continue;
      const time=new Date(reminder.scheduled_for).getTime();
      if(Number.isFinite(time))times.push(time);
    }
  }
  if(!times.length)return;
  const delay=Math.max(0,Math.min(2147483000,Math.min(...times)-Date.now()));
  reminderWakeTimer=setTimeout(()=>{reminderWakeTimer=null;void pollReminders();},delay);
}

async function pollReminders(){
  if(claimBusy||currentRole!=='elder'||document.hidden)return;
  claimBusy=true;
  try{
    const result=await api('/api/reminders/claim',{exclude_ids:[...claimedReminderIds]});
    if(result.reminder){claimedReminderIds.add(result.reminder.id);presentReminder(result);}
  }catch(error){showStatus(`提醒检查失败：${error.message||'请求失败'}。`,true);}finally{claimBusy=false;scheduleNearestReminder();}
}

function presentReminder(context){
  currentReminder=context.reminder;isDemo=false;renderItem(context.item,false);
  presentationStage='ready';pendingAutoPlay=false;$('avatarImageCard').hidden=false;$('elderGuidance').hidden=false;$('avatarInteractionDock').hidden=false;
  $('avatarAlarmBanner').hidden=false;
  const overdue=['overdue','overdue_presented'].includes(currentReminder.status);
  const text=`${overdue?'这是一条逾期提醒。':'提醒时间到了。'}${spokenText(context.item)}`;
  setSubtitle(text,overdue?'知应补显逾期提醒':'知应主动提醒');
  setInteraction(overdue?'这条提醒已经逾期':'提醒时间到了','请选择一种回应；不回应不会被当作同意。',[createButton('我看到了',()=>respondToReminder('acknowledge'),'positive'),createButton('十分钟后再提醒',()=>respondToReminder('snooze')),createButton('请工作人员帮助',()=>respondToReminder('help'),'secondary')]);
  if(avatarBridge.connected)avatarBridge.queueReminderSpeech(text);else{avatarBridge.pendingReminderSpeech=text;showStatus('到期提醒已显示为大字；数字人未连接，所以尚未执行语音播报。',true);}
}

async function respondToReminder(action){
  if(!currentReminder)return;
  avatarBridge.pendingReminderSpeech=null;
  const buttons=[...$('avatarInteractionButtons').querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);
  try{
    await api('/api/reminders/respond',{id:currentReminder.id,action});$('avatarAlarmBanner').hidden=true;currentReminder=null;currentItem=await api(`/api/items/${currentItem.id}`);renderItem(currentItem,false);
    const messages={acknowledge:'已保存“我看到了”；这不表示事项已经完成。',snooze:'已保存“十分钟后再提醒”。',help:'已生成工作人员跟进记录；这不表示工作人员已经到场。'};
    showStatus(messages[action]);renderSaved(messages[action]);window.dispatchEvent(new CustomEvent('zhiying:item-updated'));
  }catch(error){showStatus(error.message||'提醒回应保存失败。',true);buttons.forEach(button=>button.disabled=false);}
}

function setRuntimeCard(id,text,state=''){
  const value=$(id);if(!value)return;
  value.textContent=text;
  const card=value.closest('.runtime-card');card.className=`runtime-card${state?` ${state}`:''}`;
}

async function refreshRuntimeMonitor(){
  if(!$('staffMonitor'))return;
  try{
    const value=await api('/api/avatar-runtime');
    const pageText=!value.fresh?'老人端当前未在线':PAGE_LABELS[value.page_state]||value.page_state;
    setRuntimeCard('runtimePage',pageText,value.elder_active?'good':'warning');
    setRuntimeCard('runtimeConnection',CONNECTION_LABELS[value.connection_state]||value.connection_state,value.connection_state==='connected'?'good':value.connection_state==='error'?'warning':'');
    const renderText=value.fresh?(RENDER_LABELS[value.render_state]||value.render_state):'无法确认当前画面';
    setRuntimeCard('runtimeRender',renderText,['rendering','resumed'].includes(value.render_state)&&value.fresh?'good':value.connection_state==='connected'?'warning':'');
    setRuntimeCard('runtimeSpeech',SPEECH_LABELS[value.speech_state]||value.speech_state,value.speech_state==='speaking'?'good':value.speech_state==='error'?'warning':'');
    setRuntimeCard('runtimeAsr',ASR_LABELS[value.asr_state]||value.asr_state,value.asr_state==='listening'?'good':value.asr_state==='failed'?'warning':'');
    $('runtimeItem').textContent=value.current_item_id?`事项 #${value.current_item_id}`:'当前没有正式事项';
    $('runtimeUpdated').textContent=value.updated_at?`${formatTime(value.updated_at)}${value.age_seconds!==null?`（${value.age_seconds} 秒前）`:''}`:'尚无上报';
    $('runtimeAction').textContent=value.last_action||'尚无最近动作';
    const canvasSummary=value.canvas_layout?`；${value.canvas_layout}`:'';
    const renderSummary=value.render_layout?`；${value.render_layout}`:'';
    $('runtimeCanvas').textContent=`SDK 宿主 ${value.container_width||0}×${value.container_height||0}，Canvas ${value.canvas_count||0} 个${canvasSummary}`;
    $('runtimeNodes').textContent=`渲染节点 ${value.render_node_count||0} 个${renderSummary}`;
  }catch(error){setRuntimeCard('runtimePage','运行状态读取失败','warning');$('runtimeAction').textContent=error.message||'请求失败';}
}

function setRole(role){
  currentRole=role;
  runtime.pageState=document.hidden?'hidden':role==='elder'?'elder_active':'staff_view';
  if(role==='elder')requestAnimationFrame(()=>pollReminders());
  else{
    if(avatarBridge.asrState!=='idle')void avatarBridge.stopVoiceConversation('role_switch',false);
    void refreshRuntimeMonitor();
  }
  scheduleNearestReminder();updateVoiceConversationUi();reportRuntime(true);
}

$('startVoiceConversation').onclick=()=>{void avatarBridge.startVoiceConversation();};
$('stopVoiceConversation').onclick=()=>{void avatarBridge.stopVoiceConversation('user_stop',true);};
$('replayVoiceAnswer').onclick=()=>{if(!avatarBridge.replayLastVoiceAnswer())updateVoiceConversationUi('当前没有可以重听的回答。');};
$('avatarVolume').onchange=event=>{avatarBridge.setVolume(event.target.value,true);};

$('interruptSpeech').onclick=()=>{
  const elapsed=avatarBridge.interrupt('user_click');
  if(elapsed===null)showStatus('数字人未连接，没有发送打断命令。',true);
  else showStatus(`已发送数字人打断命令（调用耗时 ${elapsed} 毫秒），等待 SDK 确认 interrupted。`);
};

$('connectionForm').onsubmit=async event=>{
  event.preventDefault();
  if(!isLocalHostname()){showStatus('当前不是本机地址，已拒绝提供数字人凭据。',true);return;}
  if(!$('credentialConsent').checked){showStatus('请先确认您理解浏览器侧凭据风险。',true);return;}
  const appId=$('appIdInput').value.trim();const appSecret=$('appSecretInput').value;
  if(!appId||!appSecret){showStatus('请输入 App ID 和 App Secret。',true);return;}
  $('appIdInput').value='';$('appSecretInput').value='';$('credentialConsent').checked=false;
  try{await avatarBridge.connect(appId,appSecret);}catch(error){showStatus(`数字人连接失败（错误码：${sdkErrorCode(error)}）。凭据输入框已清空。`,true);}
};
$('disconnectAvatar').onclick=async()=>{$('disconnectAvatar').disabled=true;await avatarBridge.disconnect('user_disconnect');};
const refreshRuntimeButton=$('refreshRuntime');if(refreshRuntimeButton)refreshRuntimeButton.onclick=refreshRuntimeMonitor;

document.addEventListener('visibilitychange',()=>{
  runtime.pageState=document.hidden?'hidden':currentRole==='elder'?'elder_active':'staff_view';
  if(document.hidden){
    clearTimeout(reminderWakeTimer);reminderWakeTimer=null;
    if(avatarBridge.asrState!=='idle')void avatarBridge.stopVoiceConversation('page_hidden',false);
  }else if(currentRole==='elder')requestAnimationFrame(()=>pollReminders());
  scheduleNearestReminder();updateVoiceConversationUi();reportRuntime(true);
});
window.addEventListener('beforeunload',()=>{reportClosing();avatarBridge.destroyForUnload();clearTimeout(reminderWakeTimer);clearInterval(reminderTimer);clearInterval(runtimeHeartbeat);clearInterval(monitorTimer);});

if(isLocalHostname())updateConnectionUi('尚未向魔珐发送连接请求；输入内容不会保存。');
else{runtime.connectionState='error';updateConnectionUi('当前页面不是本机地址，数字人连接已禁用。');for(const field of[$('appIdInput'),$('appSecretInput'),$('credentialConsent'),$('connectAvatar')])field.disabled=true;}

renderItem(demoItem());showStatus('当前显示具体演示事项；从下方事项列表选择后会加载正式内容。');
const reminderTimer=setInterval(pollReminders,10000);
const runtimeHeartbeat=setInterval(()=>reportRuntime(true),10000);
const monitorTimer=setInterval(()=>{if(currentRole==='staff')void refreshRuntimeMonitor();},5000);
reportRuntime(true);pollReminders();refreshRuntimeMonitor();

window.zhiyingAvatar={loadItem,syncItems,setRole,refreshRuntimeMonitor,getCurrentItem:()=>currentItem};
})();
