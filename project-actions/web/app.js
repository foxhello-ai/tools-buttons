'use strict';
const token = document.querySelector('meta[name="project-actions-token"]').content;
const desktop = new URLSearchParams(location.search).get('desktop') === '1';
if (desktop) document.body.classList.add('desktop');
const $ = id => document.getElementById(id);
const terminal = new Set(['succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted']);
const names = {queued:'等待中', pending:'待执行', running:'进行中', succeeded:'已完成', failed:'失败', skipped:'已跳过', cancelled:'已停止', timed_out:'已超时', interrupted:'已中断'};
const paths = {folder:'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z M3 10h18',play:'m9 5 11 7-11 7Z',upload:'M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5',download:'M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4',server:'M4 3h16v7H4Zm0 11h16v7H4ZM7 7h.01M7 18h.01',history:'M3 11a9 9 0 1 1 2.5 7M3 4v7h7m2-4v6l4 2',file:'M6 2h8l4 4v16H6Zm8 0v5h4M9 12h6m-6 4h6'};
const state = {project:null, actions:[], runs:[], run:null, events:[], filter:null, cursor:0, abort:null, loading:false, buttonsSignature:null, form:null, formSerial:0};

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
  if(action.parameters?.length||action.previewCommand){await openForm(action);return;}
  if(state.loading)return;state.loading=true;notice();
  try{const run=await(await api('/api/runs',{method:'POST',body:JSON.stringify({actionId:action.id})})).json();state.runs=[run,...state.runs];renderButtons();await openRun(run.id);}
  catch(error){notice(error.message);}finally{state.loading=false;}
}
function showWorkspace(){document.body.classList.add('has-task');$('workspace').hidden=false;}
function stopWatching(){state.abort?.abort();state.abort=null;}
async function openRun(id) {
  hideForm();stopWatching();notice();const controller=new AbortController();state.abort=controller;
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
  }else if(event.type==='checkpoint'){state.run.checkpoints=(state.run.checkpoints||[]).filter(item=>item.path!==event.checkpoint.path).concat(event.checkpoint);renderNodeActions();
  }else if(event.type==='result'){state.run.result=event.data;renderContext();
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
  $('cancel').hidden=terminal.has(run.status);$('run-reason').textContent=run.reason||'';$('run-reason').hidden=!run.reason;renderGraph();renderArtifacts();renderContext();
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
  for(const node of nodes){const {x,y}=positions.get(node.id),group=svgElement('g',{class:`flow-node ${node.state} ${node.execution||''} ${state.filter===node.id?'selected':''}`,transform:`translate(${x},${y})`,tabindex:'0',role:'button','aria-label':`${node.label}：${names[node.state]||node.state}`});
    group.append(svgElement('title',{},node.message||`${node.label} · ${names[node.state]||node.state}`),svgElement('rect',{class:'card',width:cardWidth,height:cardHeight,rx:10}),svgElement('circle',{class:'node-dot',cx:17,cy:22,r:4}),svgElement('text',{class:'node-label',x:29,y:26},node.label.length>10?node.label.slice(0,10)+'…':node.label),svgElement('text',{class:'node-status',x:16,y:47},`${node.execution==='reused'?'复用结果':names[node.state]||node.state}${node.progress!==null&&node.progress!==undefined?` · ${node.progress}%`:''}`));
    if(node.state==='running'&&node.progress)group.append(svgElement('rect',{class:'node-progress',x:10,y:59,width:(cardWidth-20)*node.progress/100,height:3,rx:1.5}));
    const filter=()=>{state.filter=state.filter===node.id?null:node.id;renderGraph();renderLogs();};group.addEventListener('click',filter);group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();filter();}});svg.append(group);
  }$('graph').append(svg);renderNodeActions();
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
  hideForm();
  history.replaceState(null,'','#history');
  stopWatching();try{await load();}catch(error){notice(error.message);return;}showWorkspace();$('task-content').hidden=true;$('history').hidden=false;$('run-title').textContent='任务记录';$('run-status').textContent='';$('cancel').hidden=true;$('history').replaceChildren();
  if(!state.runs.length)$('history').append(element('p','暂无任务记录','history-empty'));
  for(const run of state.runs){const button=element('button',undefined,'history-row');button.append(element('span',run.title,'history-title'),element('span',new Date(run.createdAt).toLocaleString('zh-CN'),'history-date'),element('span',names[run.status]||run.status,`status ${run.status}`));if(run.parentRunId)button.append(element('span',`续跑 · 从 ${run.resumeFromNode} 开始`,'history-source'));button.addEventListener('click',()=>openRun(run.id));$('history').append(button);}
}
$('close').addEventListener('click',()=>{stopWatching();history.replaceState(null,'',location.pathname+location.search);$('workspace').hidden=true;document.body.classList.remove('has-task');});
$('clear-filter').addEventListener('click',()=>{state.filter=null;renderGraph();renderLogs();});
$('cancel').addEventListener('click',async()=>{if(!state.run)return;try{await api(`/api/runs/${state.run.id}/cancel`,{method:'POST'});}catch(error){notice(error.message);}});

function hideForm(){state.formSerial++;state.form=null;$('action-dialog').hidden=true;}
function dialogError(error){$('dialog-error').hidden=!error;$('dialog-error').textContent=error?.message||'';}
function facts(container,items){const list=element('dl',undefined,'input-facts');for(const item of items)list.append(element('dt',item.label),element('dd',item.value));container.append(list);}
function jsonKey(value){if(Array.isArray(value))return`[${value.map(jsonKey).join(',')}]`;if(value&&typeof value==='object')return`{${Object.keys(value).sort().map(key=>`${JSON.stringify(key)}:${jsonKey(value[key])}`).join(',')}}`;return JSON.stringify(value);}
function invalidatePreview(){const form=state.form;if(!form)return;form.request++;form.preview=null;$('dialog-confirm').disabled=true;$('dialog-preview').replaceChildren();dialogError();}
function readParameters(){
  const values={};for(const field of state.form.fields){const control=$(`parameter-${field.name}`);if(field.type==='select'){if(control.value!=='')values[field.name]=field.options[Number(control.value)].value;}
    else if(field.type==='boolean')values[field.name]=control.checked;
    else if(field.type==='number'){if(control.value!==''){const value=Number(control.value);if(!Number.isFinite(value))throw new Error('请输入有效数字：'+field.label);values[field.name]=value;}}
    else if(control.value!==''||field.required!==false)values[field.name]=control.value;
  }return values;
}
function renderFields(fields){
  $('parameter-fields').replaceChildren();
  for(const field of fields){const row=element('div',undefined,'parameter-field'),label=element('label',field.label);label.htmlFor=`parameter-${field.name}`;row.append(label);
    const control=element(field.type==='select'?'select':'input');control.id=`parameter-${field.name}`;control.name=field.name;
    if(field.type==='select'){
      const empty=element('option','请选择');empty.value='';control.append(empty);
      field.options.forEach((option,index)=>{const item=element('option',option.label+(option.disabledReason?`（${option.disabledReason}）`:''));item.value=String(index);item.disabled=!!option.disabledReason;control.append(item);});
      const initial=field.options.findIndex(item=>!item.disabledReason&&jsonKey(item.value)===jsonKey(field.default));control.value=initial<0?'':String(initial);
      const details=element('div',undefined,'option-details');
      const show=()=>{details.replaceChildren();const option=field.options[Number(control.value)];if(control.value!==''&&option){facts(details,option.details||[]);if(option.disabledReason)details.append(element('p',option.disabledReason));}};
      control.addEventListener('change',show);row.append(control,details);show();
    }else{control.type=field.type==='boolean'?'checkbox':field.type==='number'?'number':'text';if(field.type==='number')control.step='any';if(field.type==='boolean')control.checked=field.default===true;else control.value=field.default??'';row.append(control);}
    control.addEventListener('input',invalidatePreview);control.addEventListener('change',invalidatePreview);$('parameter-fields').append(row);
  }
}
async function openForm(action,resume=null){
  hideForm();stopWatching();notice();const serial=state.formSerial;state.form={action,resume,fields:[],optionsId:null,preview:null,request:0};
  document.body.classList.add('has-task');$('workspace').hidden=true;$('action-dialog').hidden=false;$('dialog-project').textContent=state.project.name;
  $('dialog-title').textContent=resume?`从“${resume.label}”继续`:action.tooltip;
  $('dialog-refresh').hidden=!!resume;$('dialog-inspect').hidden=!!resume;$('dialog-confirm').textContent=resume?'确认继续':'确认执行';$('dialog-confirm').disabled=true;
  $('parameter-fields').replaceChildren();$('dialog-preview').replaceChildren(element('p','正在读取…','muted'));dialogError();
  if(!resume)history.replaceState(null,'',`#action=${encodeURIComponent(action.id)}`);
  try{
    if(resume){await inspectForm();return;}
    const data=await(await api(`/api/actions/${encodeURIComponent(action.id)}/options`)).json();if(serial!==state.formSerial)return;
    state.form.optionsId=data.optionsId;state.form.fields=data.parameters;renderFields(data.parameters);$('dialog-preview').replaceChildren();
    if(!data.parameters.length)await inspectForm();
  }catch(error){if(serial===state.formSerial)dialogError(error);}
}
async function inspectForm(){
  const form=state.form;if(!form)return;invalidatePreview();const serial=state.formSerial,request=form.request;
  $('dialog-inspect').disabled=true;$('dialog-preview').replaceChildren(element('p','正在校验…','muted'));
  try{
    const path=form.resume?`/api/runs/${form.resume.runId}/resume-preview`:`/api/actions/${encodeURIComponent(form.action.id)}/preview`;
    const body=form.resume?{nodeId:form.resume.nodeId}:{parameters:readParameters(),optionsId:form.optionsId};
    const preview=await(await api(path,{method:'POST',body:JSON.stringify(body)})).json();if(serial!==state.formSerial||request!==form.request)return;
    const container=$('dialog-preview');container.replaceChildren();facts(container,preview.summary||[]);
    if(preview.plan?.length){const list=element('ol',undefined,'execution-plan');for(const step of preview.plan){const label=(form.resume?state.run?.nodes||[]:form.action.workflow?.nodes||[]).find(x=>x.id===step.nodeId)?.label||step.nodeId;list.append(element('li',`${label} · ${{run:'将执行',reuse:'复用已校验结果',skip:'跳过'}[step.operation]}`));}container.append(list);}
    if(!preview.allowed){dialogError(new Error(preview.reason));const retry=element('button','重新校验','text-button');retry.addEventListener('click',inspectForm);container.append(retry);return;}
    form.preview=preview;$('dialog-confirm').disabled=false;
  }catch(error){if(serial===state.formSerial&&request===form.request){$('dialog-preview').replaceChildren();dialogError(error);const retry=element('button','重新校验','text-button');retry.addEventListener('click',inspectForm);$('dialog-preview').append(retry);}}
  finally{if(serial===state.formSerial)$('dialog-inspect').disabled=false;}
}
$('dialog-inspect').addEventListener('click',inspectForm);
$('dialog-refresh').addEventListener('click',()=>{if(state.form)openForm(state.form.action);});
$('dialog-cancel').addEventListener('click',()=>{const resume=state.form?.resume;hideForm();if(resume)openRun(resume.runId);else if(desktop)showHistory();else{document.body.classList.remove('has-task');history.replaceState(null,'',location.pathname+location.search);}});
$('dialog-confirm').addEventListener('click',async()=>{
  const form=state.form;if(!form?.preview||state.loading)return;state.loading=true;$('dialog-confirm').disabled=true;dialogError();
  try{const run=await(await api('/api/runs',{method:'POST',body:JSON.stringify({actionId:form.action.id,previewId:form.preview.previewId})})).json();state.runs=[run,...state.runs];renderButtons();await openRun(run.id);}
  catch(error){if(form===state.form){form.preview=null;dialogError(error);const retry=element('button','重新校验','text-button');retry.addEventListener('click',inspectForm);$('dialog-preview').append(retry);}else notice(error.message);}
  finally{state.loading=false;}
});
function renderNodeActions(){
  const container=$('node-actions');container.replaceChildren();const run=state.run,node=run?.nodes.find(item=>item.id===state.filter);container.hidden=!node;if(!node)return;
  container.append(element('span',`${node.label} · ${node.execution==='reused'?'复用任务 '+node.reusedFrom.runId.slice(0,8)+' 的结果':names[node.state]||node.state}`));
  const action=state.actions.find(item=>item.id===run.actionId);
  if(action?.resume?.nodes.includes(node.id)&&run.status!=='succeeded'){
    const button=element('button','从此节点继续','text-button');button.disabled=!terminal.has(run.status);button.addEventListener('click',()=>openForm(action,{runId:run.id,nodeId:node.id,label:node.label}));container.append(button);
    if(!terminal.has(run.status))container.append(element('span','任务停止后可继续','muted'));
  }
}
function renderContext(){
  const container=$('run-context'),run=state.run;container.replaceChildren();const children=state.runs.filter(item=>item.parentRunId===run?.id);container.hidden=!run||(!Object.keys(run.parameters||{}).length&&!run.parentRunId&&!run.result&&!run.preview?.summary?.length&&!children.length);if(container.hidden)return;
  if(run.parentRunId){const link=element('button',`从节点 ${run.resumeFromNode} 继续 · 查看原任务 ${run.parentRunId.slice(0,8)}`,'text-button');link.addEventListener('click',()=>openRun(run.parentRunId));container.append(link);}
  if(run.preview?.summary?.length)facts(container,run.preview.summary);
  else if(Object.keys(run.parameters||{}).length)facts(container,Object.entries(run.parameters).map(([label,value])=>({label,value:typeof value==='string'?value:JSON.stringify(value)})));
  for(const child of children){const link=element('button',`查看后续续跑 ${child.id.slice(0,8)}`,'text-button');link.addEventListener('click',()=>openRun(child.id));container.append(link);}
  if(run.result){container.append(element('h2','脚本上报结果'),element('pre',JSON.stringify(run.result,null,2),'result-json'));}
}

async function openLocation() {
  const match = /^#run=([a-f0-9]{32})$/.exec(location.hash);
  const actionMatch=/^#action=([A-Za-z0-9_.-]+)$/.exec(location.hash);
  if(actionMatch){const action=state.actions.find(item=>item.id===actionMatch[1]);if(!action)throw new Error('该动作已不存在');await openForm(action);}
  else if (match) await openRun(match[1]);
  else if (location.hash === '#history' || desktop) await showHistory();
}
load().then(openLocation).catch(error=>notice(error.message));
window.addEventListener('hashchange',()=>openLocation().catch(error=>notice(error.message)));
// Refresh definitions and action availability when returning to the project window.
window.addEventListener('focus',()=>load().catch(error=>notice(error.message)));
setInterval(()=>{if(!document.hidden)load().catch(()=>{});},3000);
