'use strict';
const root = document.querySelector('#app');
const account = document.querySelector('#account');
let user = null, csrf = null, currentEditor = null, fillingState = null;
let editorChecks = null, deadlineSaveQueue = Promise.resolve();
const V = window.SurveyValidation;
const types = {single:'单选题',multiple:'多选题',text:'文本填空',number:'数字填空'};
const clone = x => JSON.parse(JSON.stringify(x));
const id = () => [...crypto.getRandomValues(new Uint8Array(12))].map(x=>x.toString(16).padStart(2,'0')).join('');
function el(tag, attrs={}, ...children) {
  const node = document.createElement(tag);
  for (const [key,value] of Object.entries(attrs)) {
    if(value===undefined||value===null)continue;
    if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key==='class') node.className=value;
    else if (key==='checked'||key==='disabled'||key==='selected'||key==='hidden') node[key]=Boolean(value);
    else if (key==='value') node.value=value??'';
    else node.setAttribute(key,value);
  }
  for (const child of children.flat(Infinity)) if (child!==null&&child!==undefined) node.append(child instanceof Node?child:document.createTextNode(String(child)));
  return node;
}
const button=(text,fn,cls='')=>el('button',{type:'button',class:cls,onClick:()=>Promise.resolve().then(fn).catch(showError)},text);
const field=(label,input,cls='')=>{if(input.matches('input,textarea,select')&&!input.hasAttribute('aria-label'))input.setAttribute('aria-label',label);return el('label',{class:`field ${cls}`},label,input);};
const input=(value,onInput,attrs={})=>el('input',{value,onInput:e=>onInput(e.target.value),...attrs});
const check=(label,value,change,disabled=false)=>el('label',{class:'check'},el('input',{type:'checkbox','aria-label':label,checked:value,disabled,onChange:e=>change(e.target.checked)}),label);
function select(options,value,change,attrs={}) {
  return el('select',{onChange:e=>change(e.target.value),...attrs},options.map(([v,text])=>el('option',{value:v,selected:String(v)===String(value)},text)));
}
function notify(text) {const n=document.querySelector('#notice');n.textContent=text;n.hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>n.hidden=true,5000);}
function showError(e){if(e.status===400)validationPopup(e.message);else notify(e.message||String(e));}
function validationPopup(message){
  let dialog=document.querySelector('dialog[data-kind="validation"]');
  if(dialog){dialog.querySelector('p').textContent=message;return;}
  dialog=el('dialog',{'data-kind':'validation','aria-label':'输入有误'},el('h2',{},'请检查输入'),el('p',{},message),actions(button('知道了',()=>{dialog.close();dialog.remove();},'primary')));
  dialog.addEventListener('cancel',e=>{e.preventDefault();dialog.close();dialog.remove();});
  document.body.append(dialog);dialog.showModal();
}
let validationHintId=0;
function createChecks(inspect){
  const entries=new Map();let remote=[],lastPopup='';
  function issues(){
    const result=[...inspect(),...remote];
    for(const [key,entry] of entries)if(entry.controls.some(c=>c.validity?.badInput))result.push({keys:[key],message:'请输入完整、合法的值',incomplete:true,draftBlock:true});
    return result;
  }
  function refresh(key=null,mode='silent'){
    const found=issues();
    for(const [name,entry] of entries){
      const problem=found.find(item=>item.keys.includes(name));
      entry.node.classList.toggle('invalid',Boolean(problem));
      entry.hint.textContent=problem?problem.message:entry.rule;
      for(const control of entry.controls)if(control.closest('[data-validation-key]')===entry.node)control.setAttribute('aria-invalid',problem?'true':'false');
    }
    const active=key?found.find(item=>item.keys.includes(key)):null;
    if(!active&&key)lastPopup='';
    if(active&&mode!=='silent'&&(!active.incomplete||mode==='blur')){
      const signature=`${key}:${active.message}`;
      if(lastPopup!==signature){lastPopup=signature;validationPopup(active.message);}
    }
    return found;
  }
  function bind(key,node,rule=''){
    node.dataset.validationKey=key;node.classList.add('validation-region');
    const controls=[...node.querySelectorAll('input,textarea,select')].filter(c=>c.closest('[data-validation-key]')===node);
    const hint=el('small',{class:'field-rule',id:`validation-hint-${++validationHintId}`},rule);
    node.append(hint);entries.set(key,{node,controls,hint,rule});
    for(const control of controls){
      control.setAttribute('aria-describedby',hint.id);
      let composing=false;
      control.addEventListener('compositionstart',()=>composing=true);
      control.addEventListener('compositionend',()=>{composing=false;remote=[];refresh(key,'input');});
      for(const event of ['input','change'])control.addEventListener(event,()=>{remote=[];refresh(key,composing?'silent':'input');});
    }
    node.addEventListener('focusout',event=>{if(!node.contains(event.relatedTarget))refresh(key,'blur');});
    if(key.includes(':rule:'))node.addEventListener('click',event=>{if(event.target.closest('button'))queueMicrotask(()=>refresh(key,'input'));});
    return node;
  }
  return {bind,field:(key,label,control,rule='',cls='')=>bind(key,field(label,control,cls),rule),refresh,
    checkAll(draft=false){const found=refresh().filter(item=>!draft||item.draftBlock);if(!found.length)return true;const first=found[0];lastPopup=`${first.keys[0]}:${first.message}`;entries.get(first.keys[0])?.controls[0]?.focus();validationPopup(first.message);return false;},
    remote(message,keys){remote=[{keys,message}];refresh();validationPopup(message);}};
}
function confirmAction(message){
  return new Promise(resolve=>{
    const finish=value=>{dialog.close();dialog.remove();resolve(value);};
    const dialog=el('dialog',{},el('h2',{},'请确认'),el('p',{},message),actions(button('取消',()=>finish(false)),button('确认',()=>finish(true),'primary')));
    dialog.addEventListener('cancel',e=>{e.preventDefault();finish(false);});
    document.body.append(dialog);dialog.showModal();
  });
}
async function api(path,method='GET',data) {
  const response=await fetch(path,{method,headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});
  const result=await response.json();
  if(!response.ok){const error=new Error(result.error||'请求失败');Object.assign(error,result,{status:response.status});throw error;}
  return result;
}
const dateText=x=>x?new Date(x).toLocaleString('zh-CN'):'不限制';
const actions=(...nodes)=>el('div',{class:'actions'},nodes);
function renderAccount(){account.replaceChildren(...(user?[el('span',{},user.username),button('我的问卷',()=>location.href='/'),button('退出登录',async()=>{if(!await confirmAction('确认退出登录？'))return;await api('/api/auth/logout','POST',{});user=null;csrf=null;currentEditor=null;editorChecks=null;renderAccount();await route();})]:[]));}
function eyeIcon(hidden){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
  const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d','M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Zm10-3a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z');svg.append(path);
  if(!hidden){const line=document.createElementNS('http://www.w3.org/2000/svg','path');line.setAttribute('d','M3 3 21 21');svg.append(line);}
  return svg;
}
function auth(register=false) {
  const username=input('',()=>{},{name:'username','aria-label':'用户名',autocomplete:'username'});
  const password=input('',()=>{},{name:'password','aria-label':'密码',type:'password',autocomplete:register?'new-password':'current-password'});
  const checks=createChecks(()=>V.credentials(username.value,password.value));
  const eye=button(eyeIcon(true),()=>{const hidden=password.type==='password';password.type=hidden?'text':'password';eye.setAttribute('aria-label',hidden?'隐藏密码':'显示密码');eye.setAttribute('title',hidden?'隐藏密码':'显示密码');eye.setAttribute('aria-pressed',String(hidden));eye.replaceChildren(eyeIcon(!hidden));},'password-toggle');
  eye.setAttribute('aria-label','显示密码');eye.setAttribute('title','显示密码');eye.setAttribute('aria-pressed','false');
  const form=el('form',{novalidate:''},checks.field('username','用户名',username,'1～15 个中文、字母、数字或下划线；区分大小写，不允许空格。'),checks.field('password','密码',el('div',{class:'password-control'},password,eye),'8～20 个字符；区分大小写，不允许空白。'),el('button',{class:'primary',type:'submit'},register?'注册并登录':'登录'));
  form.addEventListener('submit',async e=>{e.preventDefault();if(!checks.checkAll())return;try{const result=await api(`/api/auth/${register?'register':'login'}`,'POST',{username:username.value,password:password.value});user=result.user;csrf=result.csrf;renderAccount();await route();}catch(error){checks.remote(error.message,['username','password']);}});
  root.replaceChildren(el('section',{class:'narrow card focus'},el('h1',{},register?'创建账号':'欢迎回来'),el('p',{class:'muted'},'登录后创建问卷，或继续填写收到的问卷。'),actions(button('登录',()=>auth(false),register?'':'primary'),button('注册',()=>auth(true),register?'primary':'')),el('hr'),form));
  checks.refresh();
}
async function route(){if(!user)return auth();const match=location.pathname.match(/^\/survey\/([0-9a-f]{24})$/);if(match)await fill(match[1]);else await dashboard();}
async function dashboard(){
  currentEditor=null;editorChecks=null;
  const data=await api('/api/surveys');
  const cards=data.surveys.map(s=>el('article',{class:'card'},el('div',{class:'section-head'},el('span',{class:`badge ${s.status==='draft'?'draft':''}`},s.status==='draft'?'草稿':'已发布'),el('span',{class:'muted small'},`已发布 ${s.versionCounter} 个版本`)),el('h2',{},s.title||'未命名问卷'),el('p',{class:'muted'},s.description||'暂无说明'),el('p',{class:'small muted'},`更新于 ${dateText(s.updatedAt)}`),actions(button('管理问卷',()=>edit(s._id),'primary'),button('统计',()=>statistics(s._id)),el('a',{class:'button',href:`/survey/${s._id}`,target:'_blank',rel:'noopener'},'填写链接'))));
  root.replaceChildren(el('div',{class:'hero'},el('div',{},el('h1',{},'我的问卷'),el('p',{class:'muted'},'从一个问题开始，收集值得理解的答案。')),button('＋ 创建问卷',async()=>{const s=await api('/api/surveys','POST',{});await edit(s._id);},'primary')),cards.length?el('div',{class:'grid'},cards):el('div',{class:'card empty'},'还没有问卷。创建第一份问卷吧。'));
}
function localDate(value){if(!value)return '';const d=new Date(value);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
function newQuestion(type='single'){return {_id:id(),type,title:'',required:true,options:type==='single'||type==='multiple'?[{_id:id(),text:''},{_id:id(),text:''}]:[],validation:type==='text'?{minLength:1}:type==='number'?{integerOnly:true}:{},jumpRules:[]};}
async function edit(sid){currentEditor=await api(`/api/surveys/${sid}`);deadlineSaveQueue=Promise.resolve();renderEditor(false);}
async function copySurveyLink(sid){
  const link=`${location.origin}/survey/${sid}`;
  try{if(!navigator.clipboard?.writeText)throw Error('clipboard unavailable');await navigator.clipboard.writeText(link);notify('问卷链接已复制');}
  catch(e){const box=input(link,()=>{},{readonly:true,'aria-label':'问卷链接'});const dialog=el('dialog',{},el('h2',{},'请手动复制问卷链接'),el('p',{},'浏览器未允许自动复制，请选择下方链接进行复制。'),box,actions(button('关闭',()=>{dialog.close();dialog.remove();})));document.body.append(dialog);dialog.showModal();box.select();}
}
function saveDeadline(s,value,status){
  status.textContent='正在自动保存…';
  deadlineSaveQueue=deadlineSaveQueue.catch(()=>{}).then(async()=>{
    const result=await api(`/api/surveys/${s._id}`,'PUT',{revision:s.revision,deadline:value});
    s.revision=result.revision;s.updatedAt=result.updatedAt;status.textContent='截止时间已自动保存';status.classList.remove('save-error');
  });
  deadlineSaveQueue.catch(e=>{status.textContent=`未保存：${e.message}`;status.classList.add('save-error');validationPopup(status.textContent);});
}
function renderEditor(announce=true){
  const s=currentEditor, published=s.status==='published';
  const checks=createChecks(()=>V.editor(s));editorChecks=checks;
  const set=(key,value)=>s[key]=value;
  const dateStatus=el('small',{class:'field-rule',role:'status'},published?'修改后自动保存；留空表示不设截止时间。':'留空表示不设截止时间。');
  const dateInput=input(localDate(s.deadline),v=>{if(!v){if(!dateInput.validity.badInput)set('deadline',null);}else if(Number.isFinite(new Date(v).getTime()))set('deadline',new Date(v).toISOString());},{type:'datetime-local','aria-label':'截止时间（本地时间，可留空）',onChange:()=>{checks.refresh('deadline','blur');if(dateInput.validity.badInput)return;if(published)saveDeadline(s,s.deadline,dateStatus);}});
  const info=el('div',{class:'card form-grid'},checks.field('title','标题（最多 30 字）',input(s.title,v=>set('title',v),{disabled:published,'aria-label':'标题（最多 30 字）'}),'必填，最多 30 个 Unicode 字符。'),checks.field('deadline','截止时间（本地时间，可留空）',el('div',{},dateInput,dateStatus)),checks.field('description','说明（最多 100 字）',el('textarea',{disabled:published,'aria-label':'说明（最多 100 字）',onInput:e=>set('description',e.target.value)},s.description),'最多 100 个 Unicode 字符，可留空。','wide'),check('允许匿名填写',s.allowAnonymous,v=>set('allowAnonymous',v),published),check('允许同一用户多次填写（首次发布后锁定）',s.allowMultipleSubmissions,v=>set('allowMultipleSubmissions',v),published||s.versionCounter>0));
  const questionArea=checks.bind('questions',el('div',{},s.questions.map((q,index)=>renderQuestion(q,index,published))),'发布前至少添加一道题目。');
  root.replaceChildren(el('div',{class:'hero'},el('div',{},el('h1',{},s.title||'编辑问卷'),el('p',{class:'muted'},published?'当前正在接收填写。关闭后可编辑并发布新版本。':'草稿允许暂不完整，发布时会检查全部题目和规则。')),actions(button('返回列表',dashboard),button('查看统计',()=>statistics(s._id)))),el('div',{class:'row'},el('span',{class:`badge ${published?'':'draft'}`},published?'已发布':'草稿'),el('a',{href:`/survey/${s._id}`,target:'_blank',rel:'noopener'},`${location.origin}/survey/${s._id}`)),info,questionArea,
    !published?el('div',{class:'card'},el('h3',{},'添加题目'),actions(Object.entries(types).map(([kind,label])=>button(`＋ ${label}`,()=>{s.questions.push(newQuestion(kind));renderEditor();})))):el('span',{hidden:true}),
    el('div',{class:'sticky actions'},...(published?[button('复制问卷链接',()=>copySurveyLink(s._id),'primary'),button('返回主页',async()=>{await deadlineSaveQueue;await dashboard();}),button('关闭并进入草稿',async()=>{if(!await confirmAction('关闭后将停止接收提交，并回到草稿编辑阶段。继续吗？'))return;await deadlineSaveQueue;await api(`/api/surveys/${s._id}/close`,'POST',{revision:s.revision});await edit(s._id);notify('已关闭，可修改并重新发布');},'danger')]:[button('保存草稿',async()=>{await saveEditor();notify('保存成功');},'primary'),button('发布',async()=>{if(!checks.checkAll())return;if(!await confirmAction('确认发布这份问卷？发布后可通过链接填写；修改题目需要先关闭。'))return;if(!checks.checkAll())return;await saveEditor();await api(`/api/surveys/${s._id}/publish`,'POST',{revision:s.revision});await edit(s._id);notify('已发布新版本');},'primary')])));
  checks.refresh();
  if(announce){const problem=checks.refresh().find(item=>!item.incomplete);if(problem)checks.refresh(problem.keys[0],'input');}
}
async function saveEditor(){const s=currentEditor;if(editorChecks&&!editorChecks.checkAll(true))throw new Error('请先修正超长或格式错误的字段，未完成的内容仍可保存为草稿');const saved=await api(`/api/surveys/${s._id}`,'PUT',s);s.revision=saved.revision;s.updatedAt=saved.updatedAt;}
function renderQuestion(q,index,disabled){
  const s=currentEditor;
  const checks=editorChecks,key=suffix=>`${q._id}:${suffix}`;
  const section=el('section',{class:'card question'});
  const tools=disabled?[]:[button('上移',()=>{if(index>0){[s.questions[index-1],s.questions[index]]=[s.questions[index],s.questions[index-1]];renderEditor();}}),button('下移',()=>{if(index<s.questions.length-1){[s.questions[index+1],s.questions[index]]=[s.questions[index],s.questions[index+1]];renderEditor();}}),button('删除',async()=>{if(await confirmAction('删除此题？引用它的跳转需要手动修复。')){s.questions.splice(index,1);renderEditor();}},'danger')];
  section.append(el('div',{class:'section-head'},el('h2',{},`${index+1}. ${types[q.type]}`),actions(tools)),checks.field(key('title'),'题目内容',el('textarea',{disabled,'aria-label':'题目内容',onInput:e=>q.title=e.target.value},q.title),'请填写题目内容。'),checks.bind(key('required'),check('必答题',q.required,v=>q.required=v,disabled)));
  section.append(field('题型',select(Object.entries(types),q.type,async kind=>{
    if(await confirmAction('更换题型会清空本题选项、校验与跳转规则，保留题目文字和标识。继续吗？')){
      const replacement=newQuestion(kind);Object.assign(q,{type:kind,options:replacement.options,validation:{},jumpRules:[]});
    }
    renderEditor();
  },{disabled})));
  if(q.type==='single'||q.type==='multiple'){
    section.append(el('h3',{},'选项'));
    const optionsArea=el('div');
    q.options.forEach((o,i)=>optionsArea.append(el('div',{class:'option-row'},el('span',{},String.fromCharCode(65+i)),checks.bind(key(`option:${o._id}`),el('div',{class:'option-input'},input(o.text,v=>o.text=v,{disabled,'aria-label':`第${index+1}题选项${i+1}`})),'选项文字不能为空或重复。'),!disabled?actions(button('↑',()=>{if(i>0){[q.options[i-1],q.options[i]]=[q.options[i],q.options[i-1]];renderEditor();}}),button('↓',()=>{if(i<q.options.length-1){[q.options[i+1],q.options[i]]=[q.options[i],q.options[i+1]];renderEditor();}}),button('删除',()=>{q.options.splice(i,1);renderEditor();editorChecks.refresh(key('options'),'blur');})):null)));
    if(!disabled&&q.options.length<10)optionsArea.append(button('＋ 添加选项',()=>{q.options.push({_id:id(),text:''});renderEditor();}));
    section.append(checks.bind(key('options'),optionsArea,'选择题需有 2～10 个选项。'));
  }
  const fields=q.type==='multiple'?[['minSelections','最少选择'],['maxSelections','最多选择']]:q.type==='text'?[['minLength','最少字数'],['maxLength','最多字数']]:q.type==='number'?[['minValue','最小值'],['maxValue','最大值']]:[];
  if(fields.length)section.append(el('hr'),el('h3',{},'答案校验'),el('div',{class:'validation'},fields.map(([name,label])=>checks.field(key(name),label,input(q.validation[name]??'',v=>{if(v==='')delete q.validation[name];else q.validation[name]=q.type==='number'?v:/^\d+$/.test(v)?Number(v):v;},{disabled,type:'text',inputmode:q.type==='number'?'decimal':'numeric','aria-label':label}),q.type==='number'?'普通十进制数字；留空表示不限制。':'非负整数；最小不超过最大，留空表示不限制。'))));
  if(q.type==='number')section.append(checks.bind(key('integerOnly'),check('必须是整数',q.validation.integerOnly||false,v=>q.validation.integerOnly=v,disabled)));
  if(q.type!=='text'){
    section.append(el('hr'),el('h3',{},'条件跳转'),el('p',{class:'small muted'},'从上到下匹配第一条规则；未命中则进入下一题。最多五层组合。'));
    q.jumpRules.forEach((rule,rindex)=>section.append(renderRule(q,index,rule,rindex,disabled)));
    if(!disabled)section.append(button('＋ 添加跳转规则',()=>{q.jumpRules.push({condition:{operator:'all',conditions:[{operator:'hasAnswer'},defaultCondition(q)]},targetQuestionId:null});renderEditor();}));
  }
  return section;
}
function defaultCondition(q){if(q.type==='number')return {operator:'eq',value:'0'};return {operator:q.type==='single'?'eq':'contains',value:q.options[0]?._id||''};}
function guarded(c){return c.operator==='all'&&c.conditions?.length===2&&c.conditions[0].operator==='hasAnswer';}
function renderRule(q,qindex,rule,rindex,disabled){
  const guard=guarded(rule.condition);
  const base=guard?rule.condition.conditions[1]:rule.condition;
  const replace=value=>{rule.condition=guard?{operator:'all',conditions:[{operator:'hasAnswer'},value]}:value;};
  const targets=[['','结束作答'],...currentEditor.questions.map((target,i)=>[target._id,`第 ${i+1} 题：${target.title||'未命名'}`]).filter(([target])=>currentEditor.questions.findIndex(x=>x._id===target)>qindex)];
  if(rule.targetQuestionId&&!targets.some(([value])=>value===rule.targetQuestionId))targets.push([rule.targetQuestionId,'⚠ 目标题目失效，请重新选择']);
  return el('div',{class:'rule'},el('div',{class:'section-head'},el('h3',{},`规则 ${rindex+1}`),!disabled?actions(button('规则上移',()=>{if(rindex>0){[q.jumpRules[rindex-1],q.jumpRules[rindex]]=[q.jumpRules[rindex],q.jumpRules[rindex-1]];renderEditor();}}),button('规则下移',()=>{if(rindex<q.jumpRules.length-1){[q.jumpRules[rindex+1],q.jumpRules[rindex]]=[q.jumpRules[rindex],q.jumpRules[rindex+1]];renderEditor();}}),button('删除规则',()=>{q.jumpRules.splice(rindex,1);renderEditor();})):null),check('先检查有作答（可取消）',guard,v=>{rule.condition=v?{operator:'all',conditions:[{operator:'hasAnswer'},base]}:base;renderEditor();},disabled),editorChecks.bind(`${q._id}:rule:${rindex}:condition`,conditionEditor(base,q,replace,disabled),'组合不能为空；not 只有一个直接子条件；最多五层。'),editorChecks.field(`${q._id}:rule:${rindex}:target`,'满足条件时跳转到',select(targets,rule.targetQuestionId||'',v=>rule.targetQuestionId=v||null,{disabled}),'只允许向后跳转或结束。'));
}
function conditionEditor(c,q,replace,disabled,depth=1){
  const labels={all:'且：全部满足',any:'或：任一满足',not:'非：条件取反',hasAnswer:'有作答',eq:'等于',contains:'选中了',containsAny:'选中任意指定项',containsAll:'选中全部指定项',gt:'大于',gte:'大于等于',lt:'小于',lte:'小于等于',between:'位于区间（含边界）'};
  const leaf=q.type==='single'?['eq']:q.type==='multiple'?['contains','containsAny','containsAll']:['eq','gt','gte','lt','lte','between'];
  const operators=['all','any','not','hasAnswer',...leaf];
  const node=el('div',{class:'condition'});
  node.append(select(operators.map(op=>[op,labels[op]]),c.operator,op=>{
    let next={operator:op};
    if(['all','any','not'].includes(op))next.conditions=[defaultCondition(q)];
    else if(op==='between')next.value={min:'0',max:'100'};
    else if(op==='containsAny'||op==='containsAll')next.value=q.options[0]?[q.options[0]._id]:[];
    else if(op!=='hasAnswer')next.value=q.type==='number'?'0':q.options[0]?._id||'';
    replace(next);renderEditor();
  },{disabled,'aria-label':'判断方式'}));
  if(['all','any','not'].includes(c.operator)){
    (c.conditions||[]).forEach((child,i)=>node.append(el('div',{},conditionEditor(child,q,next=>c.conditions[i]=next,disabled,depth+1),!disabled&&c.operator!=='not'?button('删除子条件',()=>{c.conditions.splice(i,1);renderEditor();}):null)));
    if(!disabled&&c.operator!=='not'&&depth<5)node.append(button('＋ 子条件',()=>{c.conditions.push(defaultCondition(q));renderEditor();}));
  }else if(c.operator!=='hasAnswer'){
    if(q.type==='number'){
      if(c.operator==='between')node.append(el('div',{class:'row'},field('下限',input(c.value.min,v=>c.value.min=v,{disabled})),field('上限',input(c.value.max,v=>c.value.max=v,{disabled}))));
      else node.append(field('比较数字',input(c.value,v=>c.value=v,{disabled})));
    }else if(c.operator==='containsAny'||c.operator==='containsAll'){
      q.options.forEach((o,i)=>node.append(check(`${String.fromCharCode(65+i)} ${o.text}`,c.value.includes(o._id),v=>{c.value=v?[...c.value,o._id]:c.value.filter(x=>x!==o._id);},disabled)));
    }else{
      const opts=q.options.map((o,i)=>[o._id,`${String.fromCharCode(65+i)} ${o.text||'未命名选项'}`]);
      if(!opts.some(([key])=>key===c.value))opts.push([c.value||'','⚠ 请选择有效选项']);
      node.append(select(opts,c.value,v=>c.value=v,{disabled,'aria-label':'判断选项'}));
    }
  }
  return node;
}
async function statistics(sid){
  const survey=await api(`/api/surveys/${sid}`), data=await api(`/api/surveys/${sid}/versions`);
  if(!data.versions.length){notify('尚未发布，没有可统计版本');return edit(sid);}
  const versionSelect=select(data.versions.map(v=>[v._id,`版本 ${v.versionNumber} · ${dateText(v.publishedAt)}`]),survey.publishedVersionId,()=>load());
  const qselect=select([['','整卷统计']],'',()=>load());
  const result=el('div');let lastVersion=null;
  async function load(){
    const version=versionSelect.value;
    if(lastVersion!==version){qselect.replaceChildren(el('option',{value:''},'整卷统计'));lastVersion=version;}
    const suffix=qselect.value?`/${qselect.value}`:'';
    const stats=await api(`/api/surveys/${sid}/stats${suffix}?versionId=${version}`);
    if(qselect.options.length===1)stats.questions.forEach(q=>qselect.append(el('option',{value:q.questionId},q.title)));
    const cards=stats.questions.map(q=>{
      const card=el('section',{class:'card'},el('h2',{},q.title),el('p',{class:'muted small'},`${types[q.type]} · ${q.respondentCount} 人回答`));
      if(q.options){
        const max=Math.max(1,...q.options.map(o=>o.count));
        card.append(el('table',{},el('thead',{},el('tr',{},el('th',{},'选项'),el('th',{},q.type==='single'?'选择人数':'选择次数'),el('th',{},'分布'))),el('tbody',{},q.options.map(o=>el('tr',{},el('td',{},o.text),el('td',{},o.count),el('td',{},el('meter',{min:0,max,value:o.count},o.count)))))));
      }else{
        if(q.type==='number')card.append(el('p',{},'平均值：',el('strong',{},q.average??'暂无数据')));
        card.append(q.contents.length?el('table',{},el('thead',{},el('tr',{},el('th',{},'内容'),el('th',{},'提交时间'))),el('tbody',{},q.contents.map(row=>el('tr',{},el('td',{class:'content-text'},row.value),el('td',{},dateText(row.submittedAt)))))):el('p',{class:'muted'},'暂无数据'));
      }
      return card;
    });
    result.replaceChildren(el('div',{class:'summary'},el('div',{},el('div',{class:'big-number'},stats.submissionCount),'份答卷'),el('div',{},el('div',{class:'big-number'},stats.respondentCount),'位填写者')),...cards);
  }
  root.replaceChildren(el('div',{class:'hero'},el('div',{},el('h1',{},survey.title||'问卷统计'),el('p',{class:'muted'},'按发布版本查看，历史题目与答案独立保留。')),actions(button('返回管理',()=>edit(sid)),button('查看逐份答卷',async()=>{
    const response=await api(`/api/surveys/${sid}/responses?versionId=${versionSelect.value}`);
    const definitions=await api(`/api/surveys/${sid}/stats?versionId=${versionSelect.value}`);
    result.replaceChildren(el('h2',{},'逐份答卷'),...response.responses.map(r=>el('article',{class:'card'},el('h3',{},r.isAnonymous?'匿名填写':r.username),el('p',{class:'muted'},dateText(r.submittedAt)),el('table',{},el('tbody',{},r.answers.map(answer=>{
      const q=definitions.questions.find(q=>q.questionId===answer.questionId);let text=answer.value;
      if(q?.type==='single')text=q.options.find(o=>o.optionId===answer.value)?.text||'选项不可用';
      if(q?.type==='multiple')text=q.options.filter(o=>answer.value.includes(o.optionId)).map(o=>o.text).join('、');
      return el('tr',{},el('td',{},q?.title||'历史题目'),el('td',{class:'content-text'},text));
    }))))));
  }))),el('div',{class:'card form-grid'},field('发布版本',versionSelect),field('统计范围',qselect)),el('p',{class:'note'},'单选按用户去重，多选按答卷累计；同一人不同次选择不同单选选项，可分别计入各选项人数。数字平均值按有效答案计算。'),result);
  await load();
}
async function makeCode(sid){
  const random=[...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('');
  const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([user._id,sid,Date.now(),random])));
  const hash=BigInt('0x'+[...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,'0')).join(''));
  return (100000000000000000n+hash%900000000000000000n).toString();
}
function readStorage(key){try{return JSON.parse(localStorage.getItem(key)||'null');}catch(e){throw new Error('本地草稿无法读取，请检查浏览器存储；原记录未覆盖。');}}
function writeStorage(key,value){try{localStorage.setItem(key,JSON.stringify(value));}catch(e){throw new Error('本地保存失败，可能存储空间不足。请保留此页面并重试。');}}
async function fill(sid){
  let data;
  try{data=await api(`/api/fill/${sid}`);}catch(e){root.replaceChildren(el('section',{class:'card narrow'},el('h1',{},'暂时无法填写'),el('p',{},e.message),el('a',{class:'button',href:'/'},'我的问卷')));return;}
  const key=`survey-draft:${user._id}:${sid}`;
  let stored=readStorage(key);
  if(stored&&stored.current.versionId!==data.version._id){
    root.replaceChildren(el('section',{class:'card narrow'},el('h1',{},'问卷已更新'),el('p',{},'旧草稿仍保留供查看。重新填写当前版本，不自动迁移答案。'),el('details',{},el('summary',{},'查看旧草稿'),draftAnswerTable(stored.current)),button('保留旧稿并开始新版',async()=>{writeStorage(`${key}:archive:${stored.current.versionId}`,stored);localStorage.removeItem(key);await fill(sid);},'primary')));return;
  }
  if(!stored){stored={current:{versionId:data.version._id,versionNumber:data.version.versionNumber,questionSnapshot:clone(data.version.questions),submissionCode:await makeCode(sid),answers:{},isAnonymous:false,trail:[data.version.questions[0]._id],cursor:0,review:false,pendingPayload:null},previous:null};writeStorage(key,stored);}
  fillingState={sid,key,data,stored,error:''};
  renderFill();
}
function persistFill(change,snapshot=true){const f=fillingState;const previous=clone(f.stored.current);change(f.stored.current);if(snapshot)f.stored.previous=previous;writeStorage(f.key,f.stored);}
function renderFill(){
  const f=fillingState, d=f.stored.current, version=f.data.version;
  if(d.pendingPayload)return renderPending();
  const qid=d.trail[d.cursor], index=version.questions.findIndex(q=>q._id===qid), q=version.questions[index];
  const answer=d.answers[qid];
  const checks=createChecks(()=>V.answer(q,f.stored.current.answers[qid]));
  const setAnswer=value=>{try{persistFill(x=>x.answers[qid]=value);}catch(e){showError(e);}};
  const previous=button('上一题',()=>{persistFill(x=>{x.cursor--;x.review=false;},false);f.error='';renderFill();});previous.disabled=d.cursor===0;
  const panel=el('section',{class:'card focus'},el('p',{class:'small muted'},`第 ${index+1} 题 · ${types[q.type]}${q.required?' · 必答':''}`),el('h2',{class:'content-text'},q.title));
  if(q.type==='single'||q.type==='multiple')q.options.forEach((o,i)=>{
    const selected=q.type==='single'?answer===o._id:(answer||[]).includes(o._id);
    panel.append(el('label',{class:'answer-choice'},el('input',{type:q.type==='single'?'radio':'checkbox',name:qid,checked:selected,onChange:e=>{if(q.type==='single')setAnswer(o._id);else{const values=f.stored.current.answers[qid]||[];setAnswer(e.target.checked?[...values,o._id]:values.filter(x=>x!==o._id));}}}),`${String.fromCharCode(65+i)}. ${o.text}`));
  });
  if(q.type==='single'&&!q.required)panel.append(button('清除选择',()=>{setAnswer(null);renderFill();}));
  if(q.type==='text')panel.append(el('textarea',{rows:5,'aria-label':'填写答案',onInput:e=>setAnswer(e.target.value)},answer||''));
  if(q.type==='number')panel.append(input(answer??'',setAnswer,{'aria-label':'填写数字',inputmode:'decimal',placeholder:'输入普通十进制数字'}));
  const hints=Object.entries(q.validation).filter(([key,val])=>val!==false).map(([key,val])=>`${{minSelections:'最少选择',maxSelections:'最多选择',minLength:'最少字数',maxLength:'最多字数',minValue:'最小值',maxValue:'最大值',integerOnly:'必须为整数'}[key]}${key==='integerOnly'?'':`：${val}`}`);
  if(hints.length)panel.append(el('p',{class:'small muted'},hints.join('；')));
  if(f.error)panel.append(el('p',{class:'error-box'},f.error));
  panel.append(actions(previous,button('检查并继续',async()=>{
    if(!checks.checkAll())return;
    try{
      const result=await api(`/api/fill/${f.sid}/step`,'POST',{surveyVersionId:version._id,questionId:qid,value:f.stored.current.answers[qid]??null});
      persistFill(x=>{x.trail=x.trail.slice(0,x.cursor+1);if(result.nextQuestionId){x.trail.push(result.nextQuestionId);x.cursor++;x.review=false;}else x.review=true;},false);f.error='';if(f.stored.current.review)renderReview();else renderFill();
    }catch(e){f.error=e.message;renderFill();if(e.status===400)validationPopup(e.message);}
  },'primary')));
  checks.bind('answer',panel,q.required?'必答；填写内容需满足本题规则。':'可不回答；作答后需满足本题规则。');
  root.replaceChildren(el('h1',{},version.title),el('p',{class:'muted content-text'},version.description),el('p',{class:'small muted'},`版本 ${version.versionNumber} · 截止：${dateText(f.data.deadline)} · 答案变化后自动保存在此浏览器`),panel,
    actions(button('回退到上次保存',async()=>{if(!f.stored.previous){notify('还没有可回退记录');return;}if(await confirmAction('恢复前一次保存的内容？')){const current=clone(f.stored.current);f.stored.current=clone(f.stored.previous);f.stored.previous=current;writeStorage(f.key,f.stored);f.error='';renderFill();}})),draftArchives(f.key));
  if(d.review)renderReview();
  else checks.refresh();
}
function draftArchives(key){
  const items=[];
  for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i);if(k.startsWith(`${key}:archive:`)){const old=readStorage(k);items.push(el('details',{},el('summary',{},`历史草稿${old.current.versionNumber?` · 版本 ${old.current.versionNumber}`:''}`),draftAnswerTable(old.current)));}}
  return el('div',{},items);
}
function draftAnswerTable(draft){
  return el('table',{},el('tbody',{},Object.entries(draft.answers).map(([key,value])=>{
    const q=(draft.questionSnapshot||[]).find(q=>q._id===key);let text=value??'未填写';
    if(q?.type==='single')text=q.options.find(o=>o._id===value)?.text||'未填写';
    if(q?.type==='multiple')text=q.options.filter(o=>(value||[]).includes(o._id)).map(o=>o.text).join('、')||'未填写';
    if(!q&&typeof text==='object')text='原记录未保留选项文字';
    return el('tr',{},el('td',{},q?.title||'历史题目'),el('td',{class:'content-text'},text));
  })));
}
function renderReview(){
  const f=fillingState,d=f.stored.current,version=f.data.version;
  const rows=d.trail.map(qid=>{const q=version.questions.find(q=>q._id===qid), value=d.answers[qid];let text=value??'未填写';if(q.type==='single')text=q.options.find(o=>o._id===value)?.text||'未填写';if(q.type==='multiple')text=q.options.filter(o=>(value||[]).includes(o._id)).map(o=>o.text).join('、')||'未填写';return el('tr',{},el('td',{},q.title),el('td',{class:'content-text'},text));});
  root.replaceChildren(el('h1',{},'确认填写内容'),el('p',{class:'muted'},version.title),el('section',{class:'card'},el('table',{},el('thead',{},el('tr',{},el('th',{},'题目'),el('th',{},'答案'))),el('tbody',{},rows))),version.allowAnonymous?check('匿名提交（创建者看不到你的身份）',d.isAnonymous,v=>persistFill(x=>x.isAnonymous=v)):el('p',{class:'muted'},'此问卷不允许匿名提交。'),el('p',{class:'small muted'},'提交成功后不能修改。只有正式提交的答案会进入数据库和统计。'),actions(button('返回修改',()=>{persistFill(x=>x.review=false,false);renderFill();}),button('提交答卷',async()=>{
    if(!await confirmAction('确认正式提交？提交后不可修改。'))return;
    const payload={surveyVersionId:version._id,submissionCode:d.submissionCode,isAnonymous:d.isAnonymous,answers:Object.entries(d.answers).map(([questionId,value])=>({questionId,value}))};
    persistFill(x=>x.pendingPayload=payload,false);renderPending();await sendPending();
  },'primary')));
}
function renderPending(){
  root.replaceChildren(el('section',{class:'card narrow'},el('h1',{},'等待提交确认'),el('p',{},'答案已锁定。同一次提交重试会复用原校验码和原内容，不会重复保存。'),button('重试同一次提交',sendPending,'primary'),el('p',{id:'submit-result',class:'muted'},'正在等待结果…')));
}
async function sendPending(){
  const f=fillingState;
  if(f.sending)return;
  f.sending=true;
  try{
    const result=await api(`/api/fill/${f.sid}/submit`,'POST',f.stored.current.pendingPayload);
    localStorage.removeItem(f.key);
    root.replaceChildren(el('section',{class:'card narrow focus'},el('span',{class:'badge'},'提交成功'),el('h1',{},'谢谢你的回答'),el('p',{},`提交时间：${dateText(result.submittedAt)}`),el('p',{class:'small muted'},`答卷编号：${result.responseId}`),f.data.allowMultipleSubmissions?button('再填写一次',()=>fill(f.sid),'primary'):el('p',{class:'muted'},'此问卷不允许重复填写。'),el('a',{class:'button',href:'/'},'返回我的问卷')));
  }catch(e){
    const node=document.querySelector('#submit-result');if(node)node.textContent=e.message;
    if(e.status&&e.status<500){
      f.stored.current.pendingPayload=null;writeStorage(f.key,f.stored);
      root.replaceChildren(el('section',{class:'card narrow'},el('h1',{},'暂未提交'),el('p',{class:'error-box'},e.message),button('返回并保留草稿',()=>{f.stored.current.review=false;writeStorage(f.key,f.stored);renderFill();}),button('重新检查问卷状态',()=>fill(f.sid))));
    }
  }finally{f.sending=false;}
}
(async()=>{try{const result=await api('/api/auth/me');user=result.user;csrf=result.csrf;renderAccount();await route();}catch(e){root.replaceChildren(el('div',{class:'error-box'},e.message));}})();
