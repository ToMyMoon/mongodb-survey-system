/* Shared input-stage checks. Server validation remains authoritative. */
(function () {
  'use strict';
  const length = value => Array.from(String(value ?? '')).length;
  const present = value => value !== null && value !== undefined && (Array.isArray(value) ? value.length > 0 : typeof value === 'string' ? value.trim().length > 0 : true);
  const issue = (keys, message, incomplete = false, draftBlock = false) => ({keys: Array.isArray(keys) ? keys : [keys], message, incomplete, draftBlock});
  function decimal(value) {
    if (typeof value === 'boolean' || value === null || value === undefined) throw Error('请输入普通十进制数字，不支持科学计数法');
    const text = String(value);
    if (!/^-?(?:\d+(?:\.\d*)?|\.\d+)$/.test(text)) throw Error('请输入普通十进制数字，不支持科学计数法');
    const negative = text.startsWith('-');
    const [whole, fraction = ''] = text.replace(/^-/, '').split('.');
    const digits = ((whole || '0') + fraction).replace(/^0+/, '') || '0';
    const significant = digits.replace(/0+$/, '') || '0';
    const trailing = digits === '0' ? 0 : digits.length - significant.length;
    if (significant.length > 34 || (digits !== '0' && (digits.length - fraction.length - 1 > 6144 || -fraction.length + trailing < -6176))) throw Error('数字超出可精确保存范围，最多 34 位有效数字');
    return {n: BigInt(digits) * (negative ? -1n : 1n), scale: fraction.length};
  }
  function compare(a, b) {
    const scale = Math.max(a.scale, b.scale);
    const left = a.n * 10n ** BigInt(scale - a.scale), right = b.n * 10n ** BigInt(scale - b.scale);
    return left < right ? -1 : left > right ? 1 : 0;
  }
  const integer = a => a.n % (10n ** BigInt(a.scale)) === 0n;
  const floor = a => a.n / (10n ** BigInt(a.scale)) - (a.n < 0n && !integer(a) ? 1n : 0n);
  const ceil = a => a.n / (10n ** BigInt(a.scale)) + (a.n > 0n && !integer(a) ? 1n : 0n);
  function credentials(username, password) {
    const out = [];
    if (!username) out.push(issue('username', '请填写用户名：1～15 个中文、英文字母、数字或下划线，区分大小写', true));
    else if (length(username) > 15 || !/^[A-Za-z0-9_\u3400-\u9fff]+$/.test(username)) out.push(issue('username', '用户名限 1～15 个中文、英文字母、数字或下划线，不允许空格，区分大小写'));
    if (/\s/.test(password)) out.push(issue('password', '密码不允许空格、换行等空白，长度为 8～20 个字符'));
    else if (length(password) > 20) out.push(issue('password', '密码最多 20 个字符，区分大小写'));
    else if (length(password) < 8) out.push(issue('password', '密码需要 8～20 个字符，区分大小写，不允许空白', true));
    return out;
  }
  function conditionMessage(c, q, depth = 1) {
    if (depth > 5) return '条件树最多五层，默认“有作答”也计入层数';
    if (!c || typeof c !== 'object') return '请配置合法条件';
    const op = c.operator;
    if (['all', 'any', 'not'].includes(op)) {
      if (!Array.isArray(c.conditions) || c.conditions.length === 0) return '组合条件不能为空，请添加子条件';
      if (op === 'not' && c.conditions.length !== 1) return 'not 只能有一个直接子条件，该子条件可以是组合';
      for (const child of c.conditions) {const message = conditionMessage(child, q, depth + 1); if (message) return message;}
      return '';
    }
    const allowed = {single: ['eq'], multiple: ['contains', 'containsAny', 'containsAll'], number: ['eq', 'gt', 'gte', 'lt', 'lte', 'between']};
    if (op === 'hasAnswer') return '';
    if (!(allowed[q.type] || []).includes(op)) return '判断方式与当前题型不符';
    if (q.type === 'number') {
      try {
        if (op === 'between') {
          if (!c.value || typeof c.value !== 'object') return '区间需要下限和上限';
          if (compare(decimal(c.value.min), decimal(c.value.max)) > 0) return '区间下限不能大于上限';
        } else decimal(c.value);
      } catch (e) {return e.message;}
    } else {
      const options = q.options.map(o => o._id);
      if (op === 'eq' || op === 'contains') {if (!options.includes(c.value)) return '引用的选项已失效，请重新选择';}
      else if (!Array.isArray(c.value) || !c.value.length) return '请至少指定一个判断选项';
      else if (new Set(c.value).size !== c.value.length || !c.value.every(v => options.includes(v))) return '条件选项重复或已失效';
    }
    return '';
  }
  function editor(s) {
    const out = [];
    if (!s.title.trim()) out.push(issue('title', '发布前请填写问卷标题，最多 30 个字符', true));
    else if (length(s.title) > 30) out.push(issue('title', '问卷标题最多 30 个字符', false, true));
    if (length(s.description) > 100) out.push(issue('description', '问卷说明最多 100 个字符', false, true));
    if (s.deadline && !Number.isFinite(Date.parse(s.deadline))) out.push(issue('deadline', '请填写完整、合法的截止时间', false, true));
    if (!s.questions.length) out.push(issue('questions', '发布前至少添加一道题目', true));
    s.questions.forEach((q, index) => {
      const key = suffix => `${q._id}:${suffix}`, v = q.validation;
      if (!q.title.trim()) out.push(issue(key('title'), '请填写题目内容', true));
      if (q.type === 'single' || q.type === 'multiple') {
        if (q.options.length < 2) out.push(issue(key('options'), '选择题需要 2～10 个选项', true));
        if (q.options.length > 10) out.push(issue(key('options'), '每题最多 10 个选项', false, true));
        const seen = new Map();
        for (const option of q.options) {
          const optionKey = key(`option:${option._id}`);
          if (!option.text.trim()) out.push(issue(optionKey, '请填写选项文字，同一题不能重复', true));
          else if (seen.has(option.text)) out.push(issue([optionKey, seen.get(option.text)], '同一题的选项文字不能重复'));
          else seen.set(option.text, optionKey);
        }
      }
      if (q.type === 'multiple' || q.type === 'text') {
        const [lo, hi] = q.type === 'multiple' ? ['minSelections', 'maxSelections'] : ['minLength', 'maxLength'];
        for (const name of [lo, hi]) if (name in v && (!Number.isSafeInteger(v[name]) || v[name] < 0)) out.push(issue(key(name), '请输入非负整数，留空表示不限制'));
        if (Number.isSafeInteger(v[lo]) && Number.isSafeInteger(v[hi]) && v[lo] > v[hi]) out.push(issue([key(lo), key(hi)], '最小限制不能大于最大限制'));
        if (q.type === 'multiple') {
          for (const name of [lo, hi]) if (v[name] > q.options.length) out.push(issue([key(name), key('options')], '选择数量限制不能超过当前选项总数'));
          if (q.required && v[hi] === 0) out.push(issue([key(hi), key('required')], '必答多选题的最多选择数量不能为 0'));
        }
      }
      if (q.type === 'number') {
        const values = {};
        for (const name of ['minValue', 'maxValue']) if (name in v) {
          try {values[name] = decimal(v[name]);} catch (e) {out.push(issue(key(name), e.message));}
        }
        if (values.minValue && values.maxValue) {
          if (compare(values.minValue, values.maxValue) > 0) out.push(issue([key('minValue'), key('maxValue')], '最小值不能大于最大值'));
          else if (v.integerOnly && ceil(values.minValue) > floor(values.maxValue)) out.push(issue([key('minValue'), key('maxValue'), key('integerOnly')], '当前区间内不存在整数，请修改范围或取消整数限制'));
        }
      }
      q.jumpRules.forEach((rule, rindex) => {
        const prefix = `rule:${rindex}:`;
        const message = q.type === 'text' ? '文本题不配置跳转规则' : conditionMessage(rule.condition, q);
        if (message) out.push(issue(key(`${prefix}condition`), message));
        if (rule.targetQuestionId !== null && !s.questions.slice(index + 1).some(target => target._id === rule.targetQuestionId)) out.push(issue(key(`${prefix}target`), '目标必须是本问卷中位于当前题之后的题目，或结束作答'));
      });
    });
    return out;
  }
  function answer(q, value) {
    if (!present(value)) return q.required ? [issue('answer', '此题为必答题，请先完成填写', true)] : [];
    const v = q.validation, out = [], fail = (message, incomplete = false) => out.push(issue('answer', message, incomplete));
    if (q.type === 'single') {if (!q.options.some(o => o._id === value)) fail('请选择当前题目的一个合法选项');}
    else if (q.type === 'multiple') {
      if (!Array.isArray(value) || new Set(value).size !== value.length || !value.every(id => q.options.some(o => o._id === id))) fail('选项重复或不属于当前题目');
      else if ('maxSelections' in v && value.length > v.maxSelections) fail(`最多选择 ${v.maxSelections} 个选项`);
      else if (value.length < (v.minSelections || 0)) fail(`至少选择 ${v.minSelections} 个选项`, true);
    } else if (q.type === 'text') {
      if ('maxLength' in v && length(value) > v.maxLength) fail(`最多输入 ${v.maxLength} 个字符，空格和换行也计入`);
      else if (length(value) < (v.minLength || 0)) fail(`至少输入 ${v.minLength} 个字符`, true);
    } else {
      try {
        const n = decimal(value);
        if (v.integerOnly && !integer(n)) fail('此题必须填写整数，可包含负号');
        else if ('minValue' in v && compare(n, decimal(v.minValue)) < 0) fail(`数字不能小于 ${v.minValue}`);
        else if ('maxValue' in v && compare(n, decimal(v.maxValue)) > 0) fail(`数字不能大于 ${v.maxValue}`);
      } catch (e) {fail(e.message, /^-?$|^-?\.$/.test(String(value)));}
    }
    return out;
  }
  window.SurveyValidation = {credentials, editor, answer, conditionMessage, length};
})();
