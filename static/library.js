'use strict';

function libraryDialog(title, ...nodes) {
  const d=el('dialog',{class:'library-dialog'},el('h2',{},title),...nodes);
  d.append(actions(button('关闭',()=>{d.close();d.remove();})));
  d.addEventListener('close',()=>d.remove());
  document.body.append(d);d.showModal();return d;
}
function busyButton(label, work, cls='') {
  const b=button(label,async()=>{if(b.disabled)return;b.disabled=true;try{await work();}finally{b.disabled=false;}},cls);
  return b;
}
function questionPreview(q) {
  return el('section',{class:'template-preview'},el('h3',{},q.title),el('p',{class:'muted'},`${types[q.type]} · ${q.required?'必答':'非必答'}`),
    q.options.length?el('ol',{},q.options.map(o=>el('li',{},o.text))):null,
    el('dl',{},Object.entries(q.validation).map(([key,value])=>el('div',{},el('dt',{},({minValue:'最小值',maxValue:'最大值',integerOnly:'仅整数',minLength:'最少字数',maxLength:'最多字数',minSelections:'最少选择',maxSelections:'最多选择'})[key]||key),el('dd',{},typeof value==='boolean'?(value?'是':'否'):value)))));
}
function bankChoices(banks, selected=banks.filter(b=>b.isDefault).map(b=>b._id)) {
  const ids=new Set(selected);
  const group=el('fieldset',{'aria-label':'保存到题库'},el('legend',{},'保存到题库（可多选）'));
  const choices=el('div');
  const create=button('新建题库',()=>{
    const name=input('',()=>{},{'aria-label':'新题库名称',maxlength:'100',placeholder:'输入新题库名称'});
    const error=el('p',{class:'error-box',role:'alert',hidden:true});
    const dialog=el('dialog',{class:'bank-name-dialog','aria-label':'新建题库'},el('h2',{},'新建题库'));
    let sending=false;
    const close=()=>{if(sending)return;dialog.close();dialog.remove();};
    const submit=busyButton('创建',async()=>{
      const value=name.value.trim();
      if(!value){error.textContent='请输入新题库名称';error.hidden=false;name.focus();return;}
      if(value==='默认题库'||value==='全部题库'||banks.some(b=>b.name===value)){
        error.textContent='已有同名题库或名称为系统保留，请换一个名称';error.hidden=false;name.focus();return;
      }
      sending=true;name.disabled=true;cancel.disabled=true;error.hidden=true;
      try{
        const bank=await api('/api/banks','POST',{name:value});
        if(!group.isConnected){dialog.close();dialog.remove();return;}
        banks.push(bank);ids.add(bank._id);
        banks.filter(b=>b.isDefault).forEach(b=>ids.delete(b._id));
        renderChoices();dialog.close();dialog.remove();
      }catch(e){
        if(dialog.isConnected){error.textContent=e.message||'题库创建未确认，请稍后重试';error.hidden=false;}
      }finally{sending=false;name.disabled=false;cancel.disabled=false;}
    },'primary');
    const cancel=button('取消',close);
    const form=el('form',{novalidate:''},field('新题库名称',name),error,actions(cancel,submit));
    form.addEventListener('submit',event=>{event.preventDefault();submit.click();});
    name.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();submit.click();}});
    dialog.append(form);
    dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
    dialog.addEventListener('close',()=>dialog.remove());
    document.body.append(dialog);dialog.showModal();name.focus();
  },'bank-create');
  create.setAttribute('aria-label','新建题库');
  create.prepend(el('span',{class:'bank-create-icon','aria-hidden':'true'},'＋'));
  function renderChoices(){
    choices.replaceChildren(...[...banks].sort((a,b)=>Number(Boolean(b.isDefault))-Number(Boolean(a.isDefault)))
      .map(b=>check(b.name,ids.has(b._id),checked=>{if(checked)ids.add(b._id);else ids.delete(b._id);})),create);
  }
  renderChoices();
  group.append(choices);
  return {node:group,values:()=>({bankIds:[...ids]}),freeze:locked=>{group.disabled=locked;}};
}
async function saveQuestionTemplate(q) {
  const s=currentEditor;
  const banks=(await api('/api/banks')).banks;
  const category=bankChoices(banks);
  let payload=null;
  const d=libraryDialog('将这道题加入模板',el('p',{},'保存题干、题型、选项、必答和校验。不携带跳转或已有答案。草稿会先保存。'),category.node);
  const save=busyButton('保存题目模板',async()=>{
    if(!payload){
      if(s.status==='draft')await saveEditor();else await deadlineSaveQueue;
      payload={requestId:id(),surveyId:s._id,questionId:q._id,revision:s.revision,...category.values()};
      if(s.status==='published')payload.versionId=s.publishedVersionId;
    }
    category.freeze(true);
    try {
      const result=await api('/api/templates','POST',payload);
      d.close();await edit(s._id);notify(result.skippedBanks?.length?'模板已保存；默认题库已有相同内容，本份保留在全部题库及其他所选题库。':'题目模板已保存');
    }catch(error){
      // On an unconfirmed network failure retain the exact request for idempotent retry.
      if(error.status && error.status<500){payload=null;category.freeze(false);}
      throw error;
    }
  },'primary');
  d.insertBefore(actions(save),d.lastChild);
}
async function pickTemplate() {
  await saveEditor();
  const banks=(await api('/api/banks')).banks;
  const list=el('div');
  const category=select([['','全部题库'],...banks.map(b=>[b._id,b.name])],'',()=>load());
  const scope=select([['all','全部可用模板'],['mine','自己的模板'],['shared','共享给我的模板']],'all',()=>load());
  const d=libraryDialog('从模板或题库选题',el('div',{class:'form-grid'},field('题库',category),field('来源',scope)),list);
  let loadSequence=0;
  async function load(){
    const sequence=++loadSequence;
    try {
    const data=await api(`/api/templates?scope=${scope.value}${category.value?'&bankId='+category.value:''}`);
    if(sequence!==loadSequence||!d.isConnected)return;
    list.replaceChildren(...data.templates.map(t=>el('article',{class:'card'},questionPreview(t.question),busyButton('添加到当前问卷',async()=>{
      await api(`/api/templates/${t._id}/use`,'POST',{surveyId:currentEditor._id,revision:currentEditor.revision});
      const sid=currentEditor._id;d.close();await edit(sid);notify('题目已添加，可独立编辑和配置跳转');
    },'primary'))));
    if(!data.templates.length)list.append(el('p',{class:'muted'},'没有符合条件的可用模板。'));
    }catch(error){
      if(sequence!==loadSequence||!d.isConnected)return;
      list.replaceChildren(el('p',{},'模板加载失败，请重试。'),button('重试加载',load));showError(error);
    }
  }
  await load();
}
async function bankForm(existing, done) {
  const name=input(existing?.name||'',()=>{}), description=el('textarea',{},existing?.description||'');
  const d=libraryDialog(existing?'编辑个人题库':'新建个人题库',field('题库名称',name),field('说明',description));
  d.insertBefore(actions(busyButton('保存题库',async()=>{
    await api(existing?`/api/banks/${existing._id}`:'/api/banks',existing?'PUT':'POST',{name:name.value,description:description.value});
    d.close();await done();notify('题库已保存');
  },'primary')),d.lastChild);
}
async function classifyTemplate(t, done) {
  const banks=(await api('/api/banks')).banks;
  const category=bankChoices(banks,t.bankIds||[]);
  const d=libraryDialog('将模板加入个人题库',category.node,el('p',{},'勾选要加入的题库。已有分类保留；移除分类请使用“移出当前题库”。同一题库不接受重复内容。'));
  d.insertBefore(actions(busyButton('加入所选题库',async()=>{
    const r=await api(`/api/templates/${t._id}/banks`,'POST',category.values());d.close();await done();notify(r.skippedBanks?.length?'已加入其他所选题库，默认题库已有相同内容。':'已加入所选题库');
  },'primary')),d.lastChild);
}
async function sharingDialog(t) {
  const username=input('',()=>{}), result=el('div'), active=el('div');
  let found=null;
  const d=libraryDialog('共享给指定用户',field('完整用户名（区分大小写）',username),actions(busyButton('查找用户',async()=>{
    found=null;result.replaceChildren();
    const data=await api(`/api/users/lookup?username=${encodeURIComponent(username.value)}`);found=data.user;
    result.replaceChildren(el('p',{},`找到用户：${found.username}`),busyButton('确认共享给此用户',async()=>{
      await api(`/api/templates/${t._id}/shares`,'POST',{username:found.username,userId:found._id});
      result.replaceChildren();username.value='';await load();notify('共享已建立');
    },'primary'));
  })),result,el('h3',{},'当前共享对象'),active);
  async function load(){
    const data=await api(`/api/templates/${t._id}/shares`);
    active.replaceChildren(...data.users.map(u=>el('p',{},u.username,' ',busyButton('撤销共享',async()=>{
      if(!await confirmAction(`撤销对 ${u.username} 的共享？已有问卷副本保留。`))return;
      await api(`/api/templates/${t._id}/shares/${u._id}`,'DELETE');await load();
    },'danger'))));
    if(!data.users.length)active.append(el('p',{class:'muted'},'暂无共享对象。'));
  }
  await load();return d;
}
async function templateHistory(t, done) {
  const data=await api(`/api/templates/${t._id}/history`);
  const dialog=libraryDialog('来源与历史',el('p',{class:'note'},'每个节点是一份独立保存的模板，可从旧内容派生新模板。'),...data.nodes.map(n=>el('article',{class:'card'},
    el('p',{class:'small muted'},`来源：${n.sourceTemplateId?(data.nodes.find(p=>p._id===n.sourceTemplateId)?.question?.title||'未获共享权限的来源模板'):'首次保存（无上游模板）'}${n.createdAt?' · '+dateText(n.createdAt):''}`),
    n.readable?questionPreview(n.question):el('p',{},n.accessReason||'此来源模板属于其他用户，目前未共享给你或共享已撤销。来源关系保留，不影响你的题目副本和派生模板。'),
    n.readable&&n.deleted?el('p',{},'已从我的模板移除，历史内容保留；其他用户不受影响。'):null,
    n.readable&&n.deleted&&(n.canRestore||n.mine)?busyButton('恢复这份历史模板',async()=>{const r=await api(`/api/templates/${n._id}/restore`,'POST',{});dialog.close();dialog.remove();await done();notify(r.skippedBanks.length?`已恢复；以下题库存在重复内容，未加入：${r.skippedBanks.join('、')}`:'已恢复到模板列表');}):null)));
}
async function templateUsage(t) {
  const data=await api(`/api/templates/${t._id}/usage`);
  libraryDialog('自己的问卷使用情况',el('p',{},`${data.surveyCount} 份问卷使用过此模板；不展示其他用户的问卷。`),...data.surveys.map(s=>el('article',{class:'card'},el('h3',{},s.title||'未命名问卷'),el('p',{},s.status==='published'?'已发布':'草稿'),
    el('ul',{},s.instances.map(i=>el('li',{},`${i.versionNumber?'发布版本 '+i.versionNumber:'编辑内容'} · ${i.current?'当前使用':'历史或非当前内容'} · ${i.modified?'题目内容已修改':'题目内容一致'}`))),
    button('管理这份问卷',async()=>{document.querySelectorAll('dialog').forEach(d=>d.close());await edit(s.surveyId);}))),!data.surveys.length?el('p',{},'自己的问卷暂无使用记录。'):null);
}
function pagedTable(headers, rows, size=30) {
  const wrap=el('div'), table=el('table'), navigation=el('div',{class:'actions'});let page=0;
  function render(){
    table.replaceChildren(el('thead',{},el('tr',{},headers.map(h=>el('th',{},h)))),el('tbody',{},rows.slice(page*size,(page+1)*size).map(row=>el('tr',{},row.map(v=>el('td',{class:'content-text'},v))))));
    navigation.replaceChildren(button('上一页',()=>{if(page>0){page--;render();}}),el('span',{},`第 ${page+1} / ${Math.max(1,Math.ceil(rows.length/size))} 页 · 共 ${rows.length} 条`),button('下一页',()=>{if((page+1)*size<rows.length){page++;render();}}));
  }
  wrap.append(table,navigation);render();return wrap;
}
async function templateStatistics(t) {
  const usage=(await api(`/api/templates/${t._id}/usage`)).surveys;
  const survey=select([['','全部自己的问卷'],...usage.map(s=>[s.surveyId,s.title||'未命名问卷'])],'',()=>updateVersions());
  const version=select([['','全部符合条件的发布版本']],'',()=>{});
  const from=input('',()=>{},{type:'datetime-local'}), to=input('',()=>{},{type:'datetime-local'});
  const result=el('div');
  function updateVersions(){
    const opts=new Map();usage.filter(s=>!survey.value||s.surveyId===survey.value).forEach(s=>s.instances.filter(i=>i.versionId).forEach(i=>opts.set(i.versionId,`${s.title} · 版本 ${i.versionNumber}`)));
    version.replaceChildren(el('option',{value:''},'全部符合条件的发布版本'),...[...opts].map(([v,label])=>el('option',{value:v},label)));
  }
  const d=libraryDialog('跨问卷单题统计',el('p',{class:'note'},'只汇总自己问卷中来源明确且内容一致的题目。回答次数逐次累计，人数按账号去重；必答或跳转不同会影响回答样本。'),
    el('div',{class:'form-grid'},field('问卷',survey),field('发布版本',version),field('提交开始时间',from),field('提交结束时间',to)),actions(busyButton('查询统计',()=>load(),'primary')),result);
  async function load(){
    const args=new URLSearchParams();if(survey.value)args.set('surveyId',survey.value);if(version.value)args.set('versionId',version.value);
    if(from.value)args.set('from',new Date(from.value).toISOString());if(to.value)args.set('to',new Date(to.value).toISOString());
    const s=await api(`/api/templates/${t._id}/stats?${args}`);
    result.replaceChildren(el('h3',{},s.title),el('p',{},`${s.answerCount} 次有效回答 · ${s.respondentCount} 人`));
    if(s.options)result.append(pagedTable(['选项',s.type==='single'?'选择人数':'选择次数'],s.options.map(o=>[o.text,o.count])));
    else if(s.type==='number'){
      result.append(el('p',{},`平均值：${s.average??'暂无数据'}；最小值：${s.minimum??'暂无数据'}；最大值：${s.maximum??'暂无数据'}`),
        pagedTable(['数值','回答次数','占比'],s.distribution.map(r=>[r.value,r.count,r.percentage+'%'])));
    }else result.append(pagedTable(['内容','提交时间'],s.contents.map(r=>[r.value,dateText(r.submittedAt)])));
    result.append(el('details',{},el('summary',{},`查看 ${s.sources.length} 项题目来源`),el('ul',{},s.sources.map(r=>el('li',{},`${r.title} · 版本 ${r.versionNumber} · 第 ${r.questionNumber} 题`)))));
    if(!s.answerCount)result.append(el('p',{class:'muted'},'暂无数据'));
  }
  updateVersions();await load();return d;
}
async function libraryPage() {
  currentEditor=null;editorChecks=null;
  let scopeValue='all', bankValue='';
  const banks=el('div'), templates=el('div',{class:'grid'});
  const scope=select([['all','全部可用模板'],['mine','自己保存的模板'],['shared','共享给我的模板'],['history','已从我的模板移除']],'all',()=>{scopeValue=scope.value;loadTemplates();});
  const category=select([['','全部题库']],'',()=>{bankValue=category.value;loadTemplates();});
  async function loadBanks(){
    const data=await api('/api/banks');
    if(bankValue&&!data.banks.some(b=>b._id===bankValue))bankValue='';
    category.replaceChildren(el('option',{value:'',selected:!bankValue},'全部题库'),...data.banks.map(b=>el('option',{value:b._id,selected:b._id===bankValue},b.name)));
    banks.replaceChildren(...data.banks.map(b=>el('article',{class:'card'},el('h3',{},b.name),el('p',{},b.description),b.isDefault?el('p',{class:'muted'},'系统默认题库，可将模板移出分类。'):actions(button('编辑题库',()=>bankForm(b,refresh)),button('删除题库',async()=>{
      if(!await confirmAction('删除题库只移除分类，不删除模板。继续吗？'))return;
      await api(`/api/banks/${b._id}`,'DELETE');await refresh();
    },'danger')))));
  }
  async function loadTemplates(){
    const sequence=++loadSequence;
    try {
    const data=await api(`/api/templates?scope=${scopeValue}${bankValue&&scopeValue!=='history'?'&bankId='+bankValue:''}`);
    if(sequence!==loadSequence||!templates.isConnected)return;
    templates.replaceChildren(...data.templates.map(t=>{
      const mine=t.ownerId===user._id;
      if(t.readable===false)return el('article',{class:'card'},el('span',{class:'badge'},'已从我的模板移除'),el('p',{},t.accessReason));
      const tools=[button('来源与历史',()=>templateHistory(t,refresh)),button('使用情况',()=>templateUsage(t)),button('跨问卷统计',()=>templateStatistics(t))];
      if(!t.deleted){
        tools.push(button('加入题库',()=>classifyTemplate(t,refresh)));
        if(bankValue)tools.push(button('移出当前题库',async()=>{await api(`/api/banks/${bankValue}/items/${t._id}`,'DELETE');await loadTemplates();}));
        if(mine)tools.push(button('共享管理',()=>sharingDialog(t)));
        tools.push(button('从我的模板移除',async()=>{
          if(!await confirmAction('仅从你的模板列表和个人题库移除，历史可恢复。其他用户、已有问卷及共享授权均不受影响。'))return;
          await api(`/api/templates/${t._id}`,'DELETE');await refresh();
        },'danger'));
      }else{
        if(mine)tools.push(button('共享管理',()=>sharingDialog(t)));
        tools.push(busyButton('恢复模板',async()=>{const r=await api(`/api/templates/${t._id}/restore`,'POST',{});await refresh();notify(r.skippedBanks.length?'已恢复；重复题库分类已跳过：'+r.skippedBanks.join('、'):'已恢复');},'primary'));
      }
      return el('article',{class:'card'},el('span',{class:'badge'},t.deleted?'已从我的模板移除':mine?'自己的模板':'他人共享'),questionPreview(t.question),actions(tools));
    }));
    if(!data.templates.length)templates.append(el('div',{class:'card empty'},'暂无模板。可在自己的问卷管理页将完整题目“加入模板”。'));
    }catch(error){
      if(sequence!==loadSequence||!templates.isConnected)return;
      templates.replaceChildren(el('p',{},'模板加载失败，请重试。'),button('重试加载',loadTemplates));showError(error);
    }
  }
  let loadSequence=0;
  async function refresh(){await loadBanks();await loadTemplates();}
  root.replaceChildren(el('div',{class:'hero'},el('div',{},el('h1',{},'题目模板与个人题库'),el('p',{class:'muted'},'保存一道题，多份问卷独立使用。')),actions(button('我的问卷',()=>location.href='/'),button('新建题库',()=>bankForm(null,refresh),'primary'))),
    el('div',{class:'card form-grid'},field('模板范围',scope),field('题库分类',category)),templates,el('details',{},el('summary',{},'管理个人题库'),banks));
  await refresh();
}
