// DOM behavior tests run without controlling a browser. Database integration is tested separately.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {webcrypto} = require('node:crypto');
const {JSDOM, VirtualConsole} = require('jsdom');
const base = path.join(__dirname,'..');
const html = fs.readFileSync(path.join(base,'templates/index.html'),'utf8');
const script = fs.readFileSync(path.join(base,'static/app.js'),'utf8');
const validationScript = fs.readFileSync(path.join(base,'static/validation.js'),'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const sid='111111111111111111111111', vid='222222222222222222222222';
const q1='333333333333333333333333', q2='444444444444444444444444';
const a='555555555555555555555555', b='666666666666666666666666';
const uid='777777777777777777777777';
const draftKey=`survey-draft:${uid}:${sid}`;
function fixture(){
  const questions=[{_id:q1,type:'single',title:'第一题',required:true,options:[{_id:a,text:'选项甲'},{_id:b,text:'选项乙'}],validation:{},jumpRules:[]},
    {_id:q2,type:'text',title:'第二题',required:false,options:[],validation:{},jumpRules:[]}];
  return {survey:{_id:sid,title:'行为测试',description:'说明',status:'draft',questions,allowAnonymous:true,allowMultipleSubmissions:false,deadline:null,revision:0,versionCounter:0,publishedVersionId:null},
    version:{_id:vid,surveyId:sid,title:'行为测试',description:'说明',allowAnonymous:true,questions:clone(questions),versionNumber:1}, submissions:[],saves:[],steps:[],credentials:[],logouts:0,published:null,failOnce:false,authenticated:true};
}
async function wait(check,message='等待页面状态超时'){
  for(let i=0;i<150;i++){if(check())return;await new Promise(resolve=>setTimeout(resolve,10));}
  throw new Error(message);
}
function boot(t,store={},url=`http://localhost:5000/survey/${sid}`,f=fixture()){
  const console = new VirtualConsole();
  const errors=[];console.on('jsdomError',e=>errors.push(e));
  const dom=new JSDOM(html,{url,runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:console});
  t.after(()=>dom.window.close());
  const w=dom.window;
  Object.assign(w,{TextEncoder,TextDecoder});
  Object.defineProperty(w.crypto,'subtle',{value:webcrypto.subtle});
  Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async value=>{if(f.clipboardFailure)throw Error('clipboard denied');f.copied=value;}}});
  if(!w.HTMLDialogElement.prototype.showModal)w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};
  if(!w.HTMLDialogElement.prototype.close)w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
  for(const [key,value] of Object.entries(store))w.localStorage.setItem(key,value);
  w.fetch=async(url,options={})=>{
    const method=options.method||'GET', data=options.body?JSON.parse(options.body):{};
    let result={};
    if(url==='/api/auth/me')result={user:f.authenticated?{_id:uid,username:'测试用户'}:null,csrf:f.authenticated?'test-csrf':null};
    else if(url==='/api/auth/login'||url==='/api/auth/register'){f.credentials.push(clone(data));f.authenticated=true;result={user:{_id:uid,username:data.username},csrf:'test-csrf'};}
    else if(url==='/api/auth/logout'){f.logouts++;f.authenticated=false;}
    else if(url==='/api/surveys'&&method==='GET')result={surveys:[f.survey]};
    else if(url===`/api/surveys/${sid}`&&method==='GET')result=f.survey;
    else if(url===`/api/surveys/${sid}/versions`)result={versions:[{_id:vid,versionNumber:1,publishedAt:'2026-10-19T00:00:00Z'}]};
    else if(url.startsWith(`/api/surveys/${sid}/stats`))result={versionId:vid,versionNumber:1,submissionCount:1,respondentCount:1,questions:[{questionId:q1,title:'第一题统计',type:'single',respondentCount:1,options:[{optionId:a,text:'选项甲',count:1},{optionId:b,text:'选项乙',count:0}]},{questionId:q2,title:'第二题统计',type:'text',respondentCount:1,contents:[{value:'统计内容',submittedAt:'2026-10-19T00:00:00Z'}]}]};
    else if(url===`/api/surveys/${sid}`&&method==='PUT'){f.saves.push(clone(data));if(f.failDeadline)return {ok:false,status:409,json:async()=>({error:'编辑冲突，请重新载入',code:'edit_conflict'})};f.survey={...f.survey,...clone(data),revision:data.revision+1};result=f.survey;}
    else if(url===`/api/surveys/${sid}/publish`){f.published=clone(f.survey);f.survey.status='published';f.survey.versionCounter=1;f.survey.publishedVersionId=vid;result=f.version;}
    else if(url===`/api/fill/${sid}`)result={surveyId:sid,version:f.version,deadline:null,allowMultipleSubmissions:false};
    else if(url===`/api/fill/${sid}/step`){f.steps.push(clone(data));result={nextQuestionId:data.questionId===q1?q2:null};}
    else if(url===`/api/fill/${sid}/submit`){f.submissions.push(clone(data));if(f.failOnce){f.failOnce=false;throw new Error('模拟网络中断');}result={responseId:'888888888888888888888888',submittedAt:'2026-10-19T00:00:00Z'};}
    else throw new Error(`未设置的测试端点：${method} ${url}`);
    return {ok:true,status:200,json:async()=>clone(result)};
  };
  w.eval(validationScript);w.eval(script);
  const text=()=>w.document.body.textContent;
  const findButton=label=>[...w.document.querySelectorAll('button')].find(e=>e.textContent===label||e.getAttribute('aria-label')===label);
  const click=async label=>{await wait(()=>findButton(label),`按钮不存在：${label}`);findButton(label).click();await new Promise(resolve=>setTimeout(resolve,0));};
  const change=(selector,value)=>{const input=w.document.querySelector(selector);assert.ok(input,selector);input.value=value;input.dispatchEvent(new w.Event('input',{bubbles:true}));};
  return {w,f,text,click,change,errors};
}

test('保存后继续修改题目，发布使用最新内容，不丢失编辑',async t=>{
  const r=boot(t,{},'http://localhost:5000/');
  await r.click('管理问卷');await wait(()=>r.w.document.querySelector('textarea[aria-label="题目内容"]'));
  r.change('textarea[aria-label="题目内容"]','第一次编辑');
  await r.click('保存草稿');await wait(()=>r.f.saves.length===1);
  r.change('textarea[aria-label="题目内容"]','保存后继续编辑');
  await r.click('发布');await r.click('确认');await wait(()=>r.f.published);
  assert.equal(r.f.published.questions[0].title,'保存后继续编辑');
  assert.equal(r.f.saves[1].revision,1);
  assert.ok(!r.w.document.querySelector('#app').textContent.includes('null'));
});

test('组合编辑器支持默认有作答以及 not(all(...))',async t=>{
  const r=boot(t,{},'http://localhost:5000/');
  await r.click('管理问卷');await r.click('＋ 添加跳转规则');
  let selects=r.w.document.querySelectorAll('.rule select[aria-label="判断方式"]');
  selects[0].value='not';selects[0].dispatchEvent(new r.w.Event('change',{bubbles:true}));
  selects=r.w.document.querySelectorAll('.rule select[aria-label="判断方式"]');
  selects[1].value='all';selects[1].dispatchEvent(new r.w.Event('change',{bubbles:true}));
  await r.click('保存草稿');await wait(()=>r.f.saves.length===1);
  const c=r.f.saves[0].questions[0].jumpRules[0].condition;
  assert.equal(c.operator,'all');assert.equal(c.conditions[0].operator,'hasAnswer');
  assert.equal(c.conditions[1].operator,'not');assert.equal(c.conditions[1].conditions.length,1);
  assert.equal(c.conditions[1].conditions[0].operator,'all');
});

test('答案自动保存、刷新恢复，回退先确认并恢复前次快照',async t=>{
  const r=boot(t);await wait(()=>r.w.document.querySelector('input[type="radio"]'));
  r.w.document.querySelector('input[type="radio"]').click();await r.click('检查并继续');
  await wait(()=>r.w.document.querySelector('textarea'));
  r.change('textarea','原来的回答');r.change('textarea','更新后的回答');
  const saved=r.w.localStorage.getItem(draftKey);assert.ok(saved);
  const reloaded=boot(t,{[draftKey]:saved});await wait(()=>reloaded.w.document.querySelector('textarea'));
  assert.equal(reloaded.w.document.querySelector('textarea').value,'更新后的回答');
  await reloaded.click('回退到上次保存');assert.ok(reloaded.w.document.querySelector('dialog[open]'));
  await reloaded.click('取消');assert.equal(reloaded.w.document.querySelector('textarea').value,'更新后的回答');
  await reloaded.click('回退到上次保存');await reloaded.click('确认');
  assert.equal(reloaded.w.document.querySelector('textarea').value,'原来的回答');
  assert.equal(JSON.parse(saved).current.submissionCode,JSON.parse(reloaded.w.localStorage.getItem(draftKey)).current.submissionCode);
});

test('提交确认取消不发送，确认后锁定并提交，成功清除本地草稿',async t=>{
  const r=boot(t);await wait(()=>r.w.document.querySelector('input[type="radio"]'));
  r.w.document.querySelector('input[type="radio"]').click();await r.click('检查并继续');
  await wait(()=>r.w.document.querySelector('textarea'));r.change('textarea','正式回答');await r.click('检查并继续');
  await r.click('提交答卷');assert.ok(r.w.document.querySelector('dialog[open]'));
  await r.click('取消');assert.equal(r.f.submissions.length,0);
  await r.click('返回修改');assert.ok(r.w.document.querySelector('textarea:not([disabled])'));
  await r.click('检查并继续');await r.click('提交答卷');await r.click('确认');
  await wait(()=>r.text().includes('谢谢你的回答'));
  assert.equal(r.f.submissions.length,1);assert.equal(r.w.localStorage.getItem(draftKey),null);
});

test('网络失败时锁定原请求，重试复用校验码及完全相同的内容',async t=>{
  const f=fixture();f.failOnce=true;
  const r=boot(t,{},`http://localhost:5000/survey/${sid}`,f);await wait(()=>r.w.document.querySelector('input[type="radio"]'));
  r.w.document.querySelector('input[type="radio"]').click();await r.click('检查并继续');await r.click('检查并继续');
  await r.click('提交答卷');await r.click('确认');await wait(()=>r.text().includes('模拟网络中断'));
  assert.ok(JSON.parse(r.w.localStorage.getItem(draftKey)).current.pendingPayload);
  assert.equal(r.w.document.querySelector('textarea'),null);
  await r.click('重试同一次提交');await wait(()=>r.text().includes('谢谢你的回答'));
  assert.equal(f.submissions.length,2);assert.deepEqual(f.submissions[0],f.submissions[1]);
});

test('新版本不自动迁移旧草稿，主动开始新版后归档旧稿',async t=>{
  const old={current:{versionId:'999999999999999999999999',submissionCode:'123456789012345678',answers:{[q1]:a}},previous:null};
  const r=boot(t,{[draftKey]:JSON.stringify(old)});await wait(()=>r.text().includes('问卷已更新'));
  assert.equal(JSON.parse(r.w.localStorage.getItem(draftKey)).current.versionId,old.current.versionId);
  await r.click('保留旧稿并开始新版');await wait(()=>r.w.document.querySelector('input[type="radio"]'));
  assert.ok(r.w.localStorage.getItem(`${draftKey}:archive:${old.current.versionId}`));
  const fresh=JSON.parse(r.w.localStorage.getItem(draftKey));assert.equal(fresh.current.versionId,vid);
  assert.deepEqual(fresh.current.answers,{});assert.notEqual(fresh.current.submissionCode,old.current.submissionCode);
});

test('统计卡片渲染为实际题目和零次选项，不显示 DOM 对象字符串',async t=>{
  const r=boot(t,{},'http://localhost:5000/');await r.click('统计');
  await wait(()=>r.text().includes('第一题统计'));
  assert.ok(r.text().includes('第二题统计'));assert.ok(r.text().includes('选项乙'));
  assert.ok(r.text().includes('统计内容'));assert.ok(!r.text().includes('[object HTMLElement]'));
  assert.equal(r.w.document.querySelectorAll('meter').length,2);
});

test('用户名超长或非法字符在输入阶段弹窗并标红，修正后清除',async t=>{
  const f=fixture();f.authenticated=false;const r=boot(t,{},'http://localhost:5000/',f);
  await wait(()=>r.w.document.querySelector('[name="username"]'));
  const node=r.w.document.querySelector('[name="username"]');assert.equal(node.getAttribute('aria-invalid'),'true');
  r.change('[name="username"]','用'.repeat(16));
  assert.ok(r.w.document.querySelector('dialog[data-kind="validation"]'));assert.equal(node.getAttribute('aria-invalid'),'true');
  assert.ok(node.closest('.validation-region').textContent.includes('15'));await r.click('知道了');
  r.change('[name="username"]','用'.repeat(15));assert.equal(node.getAttribute('aria-invalid'),'false');
  r.change('[name="username"]','abc def');assert.ok(r.w.document.querySelector('dialog[data-kind="validation"]'));
  assert.equal(f.credentials.length,0);
});

test('密码未完成先标红，离开字段提示，空格或超长立即提示',async t=>{
  const f=fixture();f.authenticated=false;const r=boot(t,{},'http://localhost:5000/',f);
  await wait(()=>r.w.document.querySelector('[name="password"]'));
  r.change('[name="password"]','short');const node=r.w.document.querySelector('[name="password"]');
  assert.equal(node.getAttribute('aria-invalid'),'true');assert.equal(r.w.document.querySelector('dialog'),null);
  node.dispatchEvent(new r.w.FocusEvent('focusout',{bubbles:true,relatedTarget:null}));assert.ok(r.w.document.querySelector('dialog'));
  await r.click('知道了');r.change('[name="password"]','abcdefgh');assert.equal(node.getAttribute('aria-invalid'),'false');
  for(const value of ['abcdefgh ', 'x'.repeat(21)]){r.change('[name="password"]',value);assert.ok(r.w.document.querySelector('dialog'));await r.click('知道了');r.change('[name="password"]','abcdefgh');}
  assert.equal(f.credentials.length,0);
});

test('登录和注册都能切换密码可见性，不改变已输入密码',async t=>{
  const f=fixture();f.authenticated=false;const r=boot(t,{},'http://localhost:5000/',f);
  for(const registration of [false,true]){
    if(registration)await r.click('注册');
    await wait(()=>r.w.document.querySelector('[name="password"]'));
    r.change('[name="password"]','SafePassword8');const node=r.w.document.querySelector('[name="password"]');
    assert.equal(node.type,'password');await r.click('显示密码');assert.equal(node.type,'text');assert.equal(node.value,'SafePassword8');
    await r.click('隐藏密码');assert.equal(node.type,'password');assert.equal(node.value,'SafePassword8');
  }
});

test('不合法账号禁止发送，合法 Unicode 密码按码点计数后原样发送',async t=>{
  const f=fixture();f.authenticated=false;const r=boot(t,{},'http://localhost:5000/',f);
  await wait(()=>r.w.document.querySelector('form'));
  r.w.document.querySelector('form').dispatchEvent(new r.w.Event('submit',{bubbles:true,cancelable:true}));
  assert.equal(f.credentials.length,0);await r.click('知道了');
  r.change('[name="username"]','测试Ab_1');r.change('[name="password"]','😀'.repeat(20));
  assert.equal(r.w.document.querySelector('[name="password"]').getAttribute('aria-invalid'),'false');
  r.w.document.querySelector('form').dispatchEvent(new r.w.Event('submit',{bubbles:true,cancelable:true}));
  await wait(()=>f.credentials.length===1);assert.equal(f.credentials[0].password,'😀'.repeat(20));assert.equal(f.credentials[0].username,'测试Ab_1');
});

test('退出登录必须确认，取消保留登录，确认才退出',async t=>{
  const r=boot(t,{},'http://localhost:5000/');await r.click('退出登录');assert.ok(r.w.document.querySelector('dialog'));
  assert.equal(r.f.logouts,0);await r.click('取消');assert.ok(r.w.document.querySelector('#account').textContent.includes('测试用户'));
  await r.click('退出登录');await r.click('确认');await wait(()=>r.f.logouts===1);
  assert.equal(r.w.document.querySelector('#account').textContent,'');assert.ok(r.w.document.querySelector('[name="password"]'));
});

test('新增四种题型均默认必答，文本最少1字、数字默认整数且允许修改',async t=>{
  const r=boot(t,{},'http://localhost:5000/');await r.click('管理问卷');
  for(const label of ['＋ 单选题','＋ 多选题','＋ 文本填空','＋ 数字填空'])await r.click(label);
  const sections=[...r.w.document.querySelectorAll('.question')].slice(2);
  assert.equal(sections.length,4);
  for(const section of sections)assert.ok([...section.querySelectorAll('label.check')].find(n=>n.textContent==='必答题').querySelector('input').checked);
  const min=sections[2].querySelector('[aria-label="最少字数"]');assert.equal(min.value,'1');min.value='3';min.dispatchEvent(new r.w.Event('input',{bubbles:true}));
  const integer=[...sections[3].querySelectorAll('label.check')].find(n=>n.textContent==='必须是整数').querySelector('input');assert.ok(integer.checked);integer.click();
  const required=[...sections[0].querySelectorAll('label.check')].find(n=>n.textContent==='必答题').querySelector('input');required.click();
  await r.click('保存草稿');await wait(()=>r.f.saves.length===1);
  const created=r.f.saves[0].questions.slice(2);assert.equal(created[0].required,false);assert.equal(created[2].validation.minLength,3);assert.equal(created[3].validation.integerOnly,false);
});

test('发布前确认可取消且不会保存发布，确认才发送并切换状态',async t=>{
  const r=boot(t,{},'http://localhost:5000/');await r.click('管理问卷');await r.click('发布');
  assert.ok(r.w.document.querySelector('dialog'));assert.equal(r.f.published,null);assert.equal(r.f.saves.length,0);
  await r.click('取消');assert.equal(r.f.published,null);assert.equal(r.f.saves.length,0);assert.ok(r.w.document.querySelector('button').isConnected);
  assert.equal(r.w.document.querySelector('[aria-label="标题（最多 30 字）"]').disabled,false);
  await r.click('发布');await r.click('确认');await wait(()=>r.f.published);
  assert.equal(r.f.saves.length,1);assert.ok(r.text().includes('复制问卷链接'));assert.ok(!r.text().includes('检查并发布'));
});

test('问卷标题说明即时提示，Unicode长度正确，空标题仍能保存草稿',async t=>{
  const r=boot(t,{},'http://localhost:5000/');await r.click('管理问卷');
  const title='[aria-label="标题（最多 30 字）"]',desc='[aria-label="说明（最多 100 字）"]';
  r.change(title,'😀'.repeat(30));assert.equal(r.w.document.querySelector(title).getAttribute('aria-invalid'),'false');assert.equal(r.w.document.querySelector('dialog'),null);
  r.change(title,'😀'.repeat(31));assert.ok(r.w.document.querySelector('dialog'));assert.equal(r.w.document.querySelector(title).getAttribute('aria-invalid'),'true');
  await r.click('知道了');await r.click('保存草稿');assert.equal(r.f.saves.length,0);await r.click('知道了');
  r.change(title,'');assert.equal(r.w.document.querySelector(title).getAttribute('aria-invalid'),'true');
  await r.click('保存草稿');await wait(()=>r.f.saves.length===1);assert.equal(r.f.saves[0].title,'');
  r.change(desc,'说'.repeat(101));assert.ok(r.w.document.querySelector('dialog'));assert.equal(r.w.document.querySelector(desc).getAttribute('aria-invalid'),'true');await r.click('知道了');
  r.change(desc,'说'.repeat(100));assert.equal(r.w.document.querySelector(desc).getAttribute('aria-invalid'),'false');
});

test('重复选项在输入阶段将相关两个选项标红',async t=>{
  const r=boot(t,{},'http://localhost:5000/');await r.click('管理问卷');r.change('[aria-label="第1题选项1"]','选项乙');
  assert.ok(r.w.document.querySelector('dialog'));for(const label of ['第1题选项1','第1题选项2'])assert.equal(r.w.document.querySelector(`[aria-label="${label}"]`).getAttribute('aria-invalid'),'true');
  await r.click('知道了');r.change('[aria-label="第1题选项1"]','修改后的选项甲');
  for(const label of ['第1题选项1','第1题选项2'])assert.equal(r.w.document.querySelector(`[aria-label="${label}"]`).getAttribute('aria-invalid'),'false');
});

test('选择数量上下限、非负整数及选项总数限制在编辑时检查',async t=>{
  const f=fixture();f.survey.questions[0].type='multiple';const r=boot(t,{},'http://localhost:5000/',f);await r.click('管理问卷');
  r.change('[aria-label="最少选择"]','2');r.change('[aria-label="最多选择"]','1');assert.ok(r.w.document.querySelector('dialog'));
  for(const name of ['最少选择','最多选择'])assert.equal(r.w.document.querySelector(`[aria-label="${name}"]`).getAttribute('aria-invalid'),'true');
  await r.click('知道了');r.change('[aria-label="最多选择"]','2');assert.equal(r.w.document.querySelector('[aria-label="最少选择"]').getAttribute('aria-invalid'),'false');
  for(const value of ['3','-1','1.5']){r.change('[aria-label="最少选择"]',value);assert.ok(r.w.document.querySelector('dialog'));await r.click('知道了');r.change('[aria-label="最少选择"]','1');}
});

test('数字范围冲突和无可用整数立即提示，取消整数限制可解除冲突',async t=>{
  const f=fixture();Object.assign(f.survey.questions[0],{type:'number',options:[],validation:{integerOnly:true}});const r=boot(t,{},'http://localhost:5000/',f);await r.click('管理问卷');
  r.change('[aria-label="最小值"]','0.2');r.change('[aria-label="最大值"]','0.8');assert.ok(r.w.document.querySelector('dialog'));assert.ok(r.text().includes('不存在整数'));
  await r.click('知道了');r.w.document.querySelector('[aria-label="必须是整数"]').click();
  assert.equal(r.w.document.querySelector('[aria-label="最小值"]').getAttribute('aria-invalid'),'false');
  r.change('[aria-label="最小值"]','1');assert.ok(r.w.document.querySelector('dialog'));assert.ok(r.text().includes('最小值不能大于最大值'));
});

test('精确十进制范围比较不因普通浮点数丢失精度而漏报',async t=>{
  const f=fixture();Object.assign(f.survey.questions[0],{type:'number',options:[],validation:{}});const r=boot(t,{},'http://localhost:5000/',f);await r.click('管理问卷');
  r.change('[aria-label="最小值"]','12345678901234567890.1');r.change('[aria-label="最大值"]','12345678901234567890.0');
  assert.ok(r.w.document.querySelector('dialog'));assert.ok(r.text().includes('最小值不能大于最大值'));
});

test('删除被引用题目后目标标红并即时提示修正规则',async t=>{
  const f=fixture();f.survey.questions[0].jumpRules=[{condition:{operator:'hasAnswer'},targetQuestionId:q2}];
  const r=boot(t,{},'http://localhost:5000/',f);await r.click('管理问卷');const second=r.w.document.querySelectorAll('.question')[1];
  [...second.querySelector('.section-head').querySelectorAll('button')].find(n=>n.textContent==='删除').click();
  await wait(()=>r.w.document.querySelector('dialog'));await r.click('确认');
  assert.ok(r.w.document.querySelector('dialog[data-kind="validation"]'));assert.ok(r.w.document.querySelector('[data-validation-key$=":target"].invalid'));
});

test('发布后复制正确链接并可返回主页，不再出现保存截止时间按钮',async t=>{
  const f=fixture();Object.assign(f.survey,{status:'published',versionCounter:1,publishedVersionId:vid});const r=boot(t,{},'http://localhost:5000/',f);
  await r.click('管理问卷');assert.ok(r.text().includes('返回主页'));assert.ok(r.text().includes('关闭并进入草稿'));assert.ok(!r.text().includes('保存截止时间'));
  await r.click('复制问卷链接');assert.equal(f.copied,`http://localhost:5000/survey/${sid}`);assert.equal(f.saves.length,0);
  await r.click('返回主页');await wait(()=>r.text().includes('＋ 创建问卷'));
});

test('复制权限受限时给出可手动选择的真实链接，不误报复制成功',async t=>{
  const f=fixture();Object.assign(f.survey,{status:'published',versionCounter:1,publishedVersionId:vid});f.clipboardFailure=true;
  const r=boot(t,{},'http://localhost:5000/',f);await r.click('管理问卷');await r.click('复制问卷链接');
  assert.ok(r.w.document.querySelector('dialog'));assert.equal(r.w.document.querySelector('[aria-label="问卷链接"]').value,`http://localhost:5000/survey/${sid}`);
  assert.ok(r.text().includes('请手动复制'));assert.ok(!r.w.document.querySelector('#notice').textContent.includes('已复制'));
});

test('发布中截止时间自动保存，连续修改依次使用最新revision',async t=>{
  const f=fixture();Object.assign(f.survey,{status:'published',versionCounter:1,publishedVersionId:vid});const r=boot(t,{},'http://localhost:5000/',f);await r.click('管理问卷');
  const selector='input[type="datetime-local"]';
  for(const value of ['2026-11-01T08:30','2026-11-01T09:30','2026-11-01T10:30']){r.change(selector,value);r.w.document.querySelector(selector).dispatchEvent(new r.w.Event('change',{bubbles:true}));}
  await wait(()=>f.saves.length===3);assert.deepEqual(f.saves.map(s=>s.revision),[0,1,2]);assert.equal(f.survey.revision,3);
  assert.equal(f.survey.deadline,new Date('2026-11-01T10:30').toISOString());assert.ok(r.text().includes('已自动保存'));
  r.change(selector,'');r.w.document.querySelector(selector).dispatchEvent(new r.w.Event('change',{bubbles:true}));await wait(()=>f.saves.length===4);assert.equal(f.survey.deadline,null);
});

test('截止时间自动保存失败显示红色未保存提示，保留修改供重试',async t=>{
  const f=fixture();Object.assign(f.survey,{status:'published',versionCounter:1,publishedVersionId:vid});f.failDeadline=true;
  const r=boot(t,{},'http://localhost:5000/',f);await r.click('管理问卷');r.change('input[type="datetime-local"]','2026-11-01T08:30');
  r.w.document.querySelector('input[type="datetime-local"]').dispatchEvent(new r.w.Event('change',{bubbles:true}));
  await wait(()=>r.text().includes('未保存'));assert.ok(r.w.document.querySelector('.save-error'));assert.ok(r.w.document.querySelector('dialog'));
  await r.click('知道了');f.failDeadline=false;r.change('input[type="datetime-local"]','2026-11-01T09:30');r.w.document.querySelector('input[type="datetime-local"]').dispatchEvent(new r.w.Event('change',{bubbles:true}));
  await wait(()=>r.text().includes('已自动保存'));assert.equal(r.w.document.querySelector('.save-error'),null);
});

for(const [name,kind,validation,value,valid,incomplete] of [
  ['文本过短','text',{minLength:2,maxLength:4},'中','中文',true],
  ['文本过长','text',{minLength:2,maxLength:4},'中文数字啊','😀中',false],
  ['数字小于范围','number',{minValue:'0',maxValue:'120'},'-1','0',false],
  ['数字大于范围','number',{minValue:'0',maxValue:'120'},'121','120',false],
  ['数字必须整数','number',{integerOnly:true},'2.5','2',false],
  ['数字科学计数法','number',{},'1e3','1000',false],
  ['数字精度超限','number',{},'1'.repeat(35),'1'.repeat(34),false],
])test(`填写阶段${name}即时标红，修正前不发送逐题请求`,async t=>{
  const f=fixture();Object.assign(f.version.questions[0],{type:kind,options:[],validation});const r=boot(t,{},`http://localhost:5000/survey/${sid}`,f);
  const selector=kind==='text'?'textarea':'[aria-label="填写数字"]';await wait(()=>r.w.document.querySelector(selector));r.change(selector,value);
  assert.equal(r.w.document.querySelector(selector).getAttribute('aria-invalid'),'true');
  if(incomplete){assert.equal(r.w.document.querySelector('dialog'),null);r.w.document.querySelector(selector).dispatchEvent(new r.w.FocusEvent('focusout',{bubbles:true,relatedTarget:null}));}
  assert.ok(r.w.document.querySelector('dialog'));assert.equal(f.steps.length,0);assert.equal(f.submissions.length,0);
  await r.click('知道了');r.change(selector,valid);assert.equal(r.w.document.querySelector(selector).getAttribute('aria-invalid'),'false');await r.click('检查并继续');await wait(()=>f.steps.length===1);
});

test('填写多选题未满足最少数量标红，超过最多数量立即弹窗',async t=>{
  const f=fixture();Object.assign(f.version.questions[0],{type:'multiple',validation:{minSelections:2,maxSelections:2}});f.version.questions[0].options.push({_id:'999999999999999999999999',text:'选项丙'});
  const r=boot(t,{},`http://localhost:5000/survey/${sid}`,f);await wait(()=>r.w.document.querySelectorAll('.answer-choice input').length===3);
  const inputs=r.w.document.querySelectorAll('.answer-choice input');inputs[0].click();assert.equal(inputs[0].getAttribute('aria-invalid'),'true');assert.equal(r.w.document.querySelector('dialog'),null);
  inputs[1].click();assert.equal(inputs[0].getAttribute('aria-invalid'),'false');inputs[2].click();assert.ok(r.w.document.querySelector('dialog'));assert.equal(f.steps.length,0);
  await r.click('知道了');inputs[2].click();assert.equal(inputs[0].getAttribute('aria-invalid'),'false');await r.click('检查并继续');await wait(()=>f.steps.length===1);
});
