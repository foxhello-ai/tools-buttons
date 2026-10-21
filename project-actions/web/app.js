'use strict';
const token = document.querySelector('meta[name="project-actions-token"]').content;
const desktop = new URLSearchParams(location.search).get('desktop') === '1';
if (desktop) document.body.classList.add('desktop');
const $ = id => document.getElementById(id);
const terminal = new Set(['succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted']);
const names = {queued:'等待中', pending:'待执行', running:'进行中', succeeded:'已完成', failed:'失败', skipped:'已跳过', cancelled:'已停止', timed_out:'已超时', interrupted:'已中断'};
const paths = {folder:'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z M3 10h18',play:'m9 5 11 7-11 7Z',upload:'M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5',download:'M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4',server:'M4 3h16v7H4Zm0 11h16v7H4ZM7 7h.01M7 18h.01',history:'M3 11a9 9 0 1 1 2.5 7M3 4v7h7m2-4v6l4 2',file:'M6 2h8l4 4v16H6Zm8 0v5h4M9 12h6m-6 4h6'};
const state = {project:null, actions:[], runs:[], run:null, events:[], filter:null, cursor:0, abort:null, loading:false, buttonsSignature:null};

function element(tag, text, className) {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
function notice(message='') {$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:{'Authorization':`Bearer ${token}`,...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});
  if(!response.ok){let message;try{message=(await response.json()).error;}catch{message=`请求失败（${response.status}）`;}const error=new Error(message);error.status=response.status;throw error;}
  return response;
}
function svgElement(tag, attributes={}, text) {const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value]of Object.entries(attributes))node.setAttribute(key,value);if(text!==undefined)node.textContent=text;return node;}
function iconButton(icon, title, callback) {
  const wrap=element('div',undefined,'action-wrap');
  const button=element('button',undefined,'icon-button');button.type='button';button.setAttribute('aria-label',title);
  if(paths[icon]){const svg=svgElement('svg',{viewBox:'0 0 24 24','aria-hidden':'true'});svg.append(svgElement('path',{d:paths[icon]}));button.append(svg);}else{button.textContent=icon;}
  const hint=element('span',title,'tooltip');hint.setAttribute('role','tooltip');wrap.append(button,hint);button.addEventListener('click',callback);return wrap;
}
function renderButtons() {
  const signature=JSON.stringify([state.actions,state.runs.filter(run=>!terminal.has(run.status)).map(run=>run.actionId),!!state.runs.length]);
  if(signature===state.buttonsSignature)return;state.buttonsSignature=signature;
  $('launcher').replaceChildren();
  for(const action of state.actions){
    const wrap=iconButton(action.icon||'play',action.tooltip,()=>start(action));
    const active=state.runs.some(run=>run.actionId===action.id&&!terminal.has(run.status));
    wrap.querySelector('button').disabled=active;
    $('launcher').append(wrap);
  }
  if(state.runs.length){const history=iconButton('history','查看任务记录',showHistory);history.querySelector('button').classList.add('quiet');$('launcher').append(history);}
}
async function load() {
  const data=await(await api('/api/project')).json();state.project=data.project;state.actions=data.actions;state.runs=data.runs;
  document.title=`${data.project.name} · 项目按钮`;$('project-name').textContent=data.project.name;renderButtons();
}
async function start(action) {
  if(state.loading)return;state.loading=true;notice();
  try{const run=await(await api('/api/runs',{method:'POST',body:JSON.stringify({actionId:action.id})})).json();state.runs=[run,...state.runs];renderButtons();await openRun(run.id);}
  catch(error){notice(error.message);}finally{state.loading=false;}
}
function showWorkspace(){document.body.classList.add('has-task');$('workspace').hidden=false;}
function stopWatching(){state.abort?.abort();state.abort=null;}
async function openRun(id) {
  stopWatching();notice();const controller=new AbortController();state.abort=controller;
  try{
    const run=await(await api(`/api/runs/${id}`,{signal:controller.signal})).json();
    if(controller.signal.aborted)return;
    history.replaceState(null,'',`#run=${encodeURIComponent(id)}`);
    state.run=run;state.events=[];state.filter=null;state.cursor=0;showWorkspace();$('history').hidden=true;$('task-content').hidden=false;renderRun();renderLogs();
    watch(id,controller).catch(error=>{if(error.name!=='AbortError')notice(error.message);});
  }catch(error){if(error.name!=='AbortError')notice(error.message);}
}
function applyEvent(event) {
  if(!state.run||event.runId!==state.run.id||event.sequence<=state.cursor)return;
  state.cursor=event.sequence;
  if(event.type==='log'){state.events.push(event);if(state.events.length>2000)state.events.shift();appendLog(event);}
  else if(event.type==='node'){
    if(event.nodes)state.run.nodes=event.nodes;else{const index=state.run.nodes.findIndex(node=>node.id===event.node.id);if(index<0)state.run.nodes.push(event.node);else state.run.nodes[index]=event.node;}
    renderGraph();
  }else if(event.type==='run'){
    Object.assign(state.run,{status:event.status,exitCode:event.exitCode??null,reason:event.reason??null,finishedAt:event.finishedAt??null});renderRun();
    const index=state.runs.findIndex(run=>run.id===event.runId);if(index>=0)state.runs[index]={...state.run};renderButtons();
  }else if(event.type==='artifact'){
    if(!state.run.artifacts.some(item=>item.id===event.artifact.id))state.run.artifacts.push(event.artifact);renderArtifacts();
  }
}
async function watch(id, controller) {
  while(!controller.signal.aborted){
    try{
      const response=await api(`/api/runs/${id}/events?after=${state.cursor}`,{signal:controller.signal});
      notice();
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
      while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let boundary;
        while((boundary=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);const data=block.split('\n').find(line=>line.startsWith('data: '));if(!data)continue;const payload=JSON.parse(data.slice(6));
          if(block.startsWith('event: snapshot')){state.run=payload;renderRun();}else{applyEvent(payload);}
        }if(done)break;
      }
      // Fetch the authoritative snapshot, including runs marked interrupted after a restart.
      state.run=await(await api(`/api/runs/${id}`,{signal:controller.signal})).json();renderRun();
      if(terminal.has(state.run.status)){await load();return;}
    }catch(error){if(error.name==='AbortError'||error.status===403||error.status===404)throw error;notice('连接中断，正在重新连接…');}
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
}
function renderRun() {
  const run=state.run;if(!run)return;$('run-title').textContent=run.title;$('run-status').textContent=names[run.status]||run.status;$('run-status').className=`status ${run.status}`;
  $('cancel').hidden=terminal.has(run.status);$('run-reason').textContent=run.reason||'';$('run-reason').hidden=!run.reason;renderGraph();renderArtifacts();
}
function renderGraph() {
  const nodes=state.run?.nodes||[];$('flow-section').hidden=!nodes.length;$('graph').replaceChildren();if(!nodes.length)return;
  const byId=new Map(nodes.map(node=>[node.id,node]));const ranks=new Map();
  function rank(id, visiting=new Set()){if(ranks.has(id))return ranks.get(id);if(visiting.has(id))return 0;visiting.add(id);const deps=byId.get(id)?.dependsOn||[];const value=deps.length?1+Math.max(...deps.map(dep=>rank(dep,new Set(visiting)))):0;ranks.set(id,value);return value;}
  const groups=[];for(const node of nodes){const column=rank(node.id);(groups[column]??=[]).push(node);}
  const cardWidth=164,cardHeight=64,gapX=56,gapY=28,maxRows=Math.max(...groups.map(group=>group?.length||0));
  const width=groups.length*(cardWidth+gapX)-gapX+12,height=maxRows*(cardHeight+gapY)-gapY+16;
  const positions=new Map();groups.forEach((group,column)=>group?.forEach((node,row)=>positions.set(node.id,{x:6+column*(cardWidth+gapX),y:8+row*(cardHeight+gapY)+(maxRows-group.length)*(cardHeight+gapY)/2})));
  const svg=svgElement('svg',{width,height,viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':'任务执行流程'});
  const defs=svgElement('defs');for(const [key,color]of Object.entries({pending:'#d7e2d9',running:'#5d95cf',succeeded:'#68ae87',failed:'#d16e69'})){const marker=svgElement('marker',{id:`arrow-${key}`,viewBox:'0 0 8 8',refX:'7',refY:'4',markerWidth:'6',markerHeight:'6',orient:'auto'});marker.append(svgElement('path',{d:'M0 0 8 4 0 8Z',fill:color}));defs.append(marker);}svg.append(defs);
  for(const node of nodes){for(const dependency of node.dependsOn||[]){const from=positions.get(dependency),to=positions.get(node.id);if(!from||!to)continue;const x1=from.x+cardWidth,y1=from.y+cardHeight/2,x2=to.x,y2=to.y+cardHeight/2,mid=(x1+x2)/2;const edgeState=node.state==='running'?'running':node.state==='failed'?'failed':byId.get(dependency)?.state==='succeeded'?'succeeded':'pending';svg.append(svgElement('path',{d:`M${x1} ${y1}C${mid} ${y1} ${mid} ${y2} ${x2-3} ${y2}`,class:`flow-edge ${edgeState}`,'marker-end':`url(#arrow-${edgeState})`}));}}
  for(const node of nodes){const {x,y}=positions.get(node.id),group=svgElement('g',{class:`flow-node ${node.state} ${state.filter===node.id?'selected':''}`,transform:`translate(${x},${y})`,tabindex:'0',role:'button','aria-label':`${node.label}：${names[node.state]||node.state}`});
    group.append(svgElement('title',{},node.message||`${node.label} · ${names[node.state]||node.state}`),svgElement('rect',{class:'card',width:cardWidth,height:cardHeight,rx:10}),svgElement('circle',{class:'node-dot',cx:17,cy:22,r:4}),svgElement('text',{class:'node-label',x:29,y:26},node.label.length>10?node.label.slice(0,10)+'…':node.label),svgElement('text',{class:'node-status',x:16,y:47},`${names[node.state]||node.state}${node.progress!==null&&node.progress!==undefined?` · ${node.progress}%`:''}`));
    if(node.state==='running'&&node.progress)group.append(svgElement('rect',{class:'node-progress',x:10,y:59,width:(cardWidth-20)*node.progress/100,height:3,rx:1.5}));
    const filter=()=>{state.filter=state.filter===node.id?null:node.id;renderGraph();renderLogs();};group.addEventListener('click',filter);group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();filter();}});svg.append(group);
  }$('graph').append(svg);
}
function logLine(event) {const line=element('div',undefined,`log-line ${event.level}`);line.append(element('span',new Date(event.timestamp).toLocaleTimeString('zh-CN',{hour12:false}),'log-time'),element('span',event.message,'log-message'));return line;}
function appendLog(event) {
  if(state.filter&&event.nodeId!==state.filter)return;
  const container=$('logs'),shouldScroll=container.scrollHeight-container.scrollTop-container.clientHeight<60;
  container.querySelector('.log-empty')?.remove();container.append(logLine(event));while(container.children.length>2000)container.firstElementChild.remove();
  if(shouldScroll)container.scrollTop=container.scrollHeight;
  $('log-note').textContent=state.events.length>=2000?'显示最近 2000 条，完整记录保存在项目中':'';
}
function renderLogs() {
  const logs=state.events.filter(event=>!state.filter||event.nodeId===state.filter);$('logs').replaceChildren(...logs.map(logLine));
  if(!logs.length)$('logs').append(element('div',state.filter?'该节点暂无日志':'等待任务输出…','log-empty'));
  const node=state.run?.nodes.find(item=>item.id===state.filter);$('log-title').textContent=node?`${node.label} · 日志`:'运行日志';$('clear-filter').hidden=!state.filter;$('logs').scrollTop=$('logs').scrollHeight;
}
function renderArtifacts() {
  const items=state.run?.artifacts||[];$('artifacts').hidden=!items.length;$('artifacts').replaceChildren();
  for(const artifact of items){const button=element('button',`↓ ${artifact.label}`,'artifact');button.addEventListener('click',async()=>{try{const response=await api(`/api/runs/${state.run.id}/artifacts/${artifact.id}`),blob=await response.blob(),url=URL.createObjectURL(blob),link=element('a');link.href=url;link.download=artifact.path.split('/').pop();link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){notice(error.message);}});$('artifacts').append(button);}
}
async function showHistory() {
  history.replaceState(null,'','#history');
  stopWatching();try{await load();}catch(error){notice(error.message);return;}showWorkspace();$('task-content').hidden=true;$('history').hidden=false;$('run-title').textContent='任务记录';$('run-status').textContent='';$('cancel').hidden=true;$('history').replaceChildren();
  if(!state.runs.length)$('history').append(element('p','暂无任务记录','history-empty'));
  for(const run of state.runs){const button=element('button',undefined,'history-row');button.append(element('span',run.title,'history-title'),element('span',new Date(run.createdAt).toLocaleString('zh-CN'),'history-date'),element('span',names[run.status]||run.status,`status ${run.status}`));button.addEventListener('click',()=>openRun(run.id));$('history').append(button);}
}
$('close').addEventListener('click',()=>{stopWatching();history.replaceState(null,'',location.pathname+location.search);$('workspace').hidden=true;document.body.classList.remove('has-task');});
$('clear-filter').addEventListener('click',()=>{state.filter=null;renderGraph();renderLogs();});
$('cancel').addEventListener('click',async()=>{if(!state.run)return;try{await api(`/api/runs/${state.run.id}/cancel`,{method:'POST'});}catch(error){notice(error.message);}});
async function openLocation() {
  const match = /^#run=([a-f0-9]{32})$/.exec(location.hash);
  if (match) await openRun(match[1]);
  else if (location.hash === '#history' || desktop) await showHistory();
}
load().then(openLocation).catch(error=>notice(error.message));
window.addEventListener('hashchange',()=>openLocation().catch(error=>notice(error.message)));
// Refresh definitions and action availability when returning to the project window.
window.addEventListener('focus',()=>load().catch(error=>notice(error.message)));
setInterval(()=>{if(!document.hidden)load().catch(()=>{});},3000);
