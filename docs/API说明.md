# API 说明

基础地址 `http://127.0.0.1:5000`。JSON 请求和响应，ObjectId 在接口中为 24 位十六进制字符串；时间为带时区的 ISO 8601，数据库存 UTC。数字答案数据库存 Decimal128，接口以十进制字符串输出以保留精度。submissionCode 数据库存 Long，接口必须使用 18 位数字字符串。

## 登录与请求校验

| 方法及路径 | 输入 | 成功响应 |
| --- | --- | --- |
| POST /api/auth/register | username、password | user、csrf，并设置 HttpOnly Cookie |
| POST /api/auth/login | username、password | 同上 |
| GET /api/auth/me | 无 | user、csrf，未登录时为 null |
| POST /api/auth/logout | 空对象 | 清除服务器会话及 Cookie |

后续请求携带登录 Cookie；写接口（除注册登录）携带 `X-CSRF-Token`，值来自登录或 me 接口。浏览器 Origin 必须与服务同源。所有写接口用 `Content-Type: application/json`。

## 创建者管理

| 方法及路径 | 输入 | 响应与行为 |
| --- | --- | --- |
| GET /api/surveys | 无 | surveys：自己的问卷摘要，不含 questions |
| POST /api/surveys | 空对象 | 201，新草稿及 revision=0 |
| GET /api/surveys/{id} | 无 | 完整管理文档 |
| PUT /api/surveys/{id} | revision 及待修改字段 | 更新后文档，revision 增加 |
| POST /api/surveys/{id}/publish | revision | 201，新的不可变发布版本 |
| POST /api/surveys/{id}/close | revision | status=draft |
| GET /api/surveys/{id}/versions | 无 | versions：历史版本摘要 |

以上接口只允许创建者。PUT 可修改 title、description、allowAnonymous、allowMultipleSubmissions、deadline、questions；发布中只允许改变 deadline；allowMultipleSubmissions 在首次发布后始终锁定。revision 用于避免多个编辑页面相互覆盖。

题型使用 single、multiple、text、number。嵌入题目和选项的 _id 是稳定 24 位十六进制字符串。题目统一携带 options、validation、jumpRules，非选择题 options 为空数组。

页面新建题目默认 required=true；文本题 validation.minLength=1，数字题 validation.integerOnly=true，均可修改。此为页面创建默认值，不覆盖已有数据。输入阶段增加前端校验，后端仍执行原有校验。发布和退出确认均由页面完成，取消不发送相应请求。发布中修改截止时间自动调用 PUT，仅发送 revision、deadline，连续修改依次使用最新 revision。复制问卷链接不需要新增接口。

跳转规则示例：

```json
{
  "condition": {
    "operator": "all",
    "conditions": [
      {"operator": "hasAnswer"},
      {"operator": "between", "value": {"min": "18", "max": "30"}}
    ]
  },
  "targetQuestionId": null
}
```

## 填写者

| 方法及路径 | 输入 | 响应 |
| --- | --- | --- |
| GET /api/fill/{id} | 无 | surveyId、当前 version、deadline、重复许可 |
| POST /api/fill/{id}/step | surveyVersionId、questionId、value | 校验当前答案并返回 nextQuestionId，null 表示结束 |
| POST /api/fill/{id}/submit | 见下例 | 201 新提交；200 相同请求重试，返回原答卷 ID |

提交格式：

```json
{
  "surveyVersionId": "24位发布版本ID",
  "submissionCode": "123456789012345678",
  "isAnonymous": false,
  "answers": [
    {"questionId": "24位题目ID", "value": "文本或单选ID"}
  ]
}
```

多选 value 为选项 ID 数组；数字可用普通十进制字符串传入，最终保存为 Decimal128。提交者身份和 repeatRestricted 只由服务器确定，不采用客户端同名字段。后端从首题重新计算路径，剔除路径外的已知题目答案，拒绝未知或重复题目标识，并验证路径上必答和限制。

响应包含 responseId、submittedAt、replayed。相同校验码只有用户、问卷、版本、匿名选择和规范化答案均一致时才返回原成功结果。不得修改已有答卷。确认成功后的网络重试即使问卷已关闭，仍可取得原结果。

## 统计与答卷

| 方法及路径 | 说明 |
| --- | --- |
| GET /api/surveys/{id}/stats | 整卷统计，默认最近发布版本 |
| GET /api/surveys/{id}/stats/{questionId} | 单题统计 |
| GET /api/surveys/{id}/responses | 逐份答卷 |

可带 `?versionId=...` 查看历史版本。只允许创建者。统计响应包含版本、提交份数、去重填写人数、题目统计；选择题包含零次选项，填空包含所有有效内容，数字增加 average（无答案为 null）。不跨版本合并。

答卷响应仅返回答卷 ID、版本、答案、时间、匿名标记；非匿名增加 username。匿名不返回 respondentId、username、submissionCode、payloadHash。

## 错误

统一包含 error（中文说明）、code。校验错误可能附 questionId。

| 状态或 code | 含义 |
| --- | --- |
| 400 invalid | 格式、配置或答案不合法 |
| 401 login_required | 未登录或会话过期 |
| 403 | CSRF 或来源校验失败 |
| 404 | 无权访问或资源不存在 |
| 409 edit_conflict | 编辑版本过旧，需重新载入 |
| 409 closed / expired | 未开放或已截止 |
| 409 version_changed | 旧填写版本已失效，保留旧稿并开始新版 |
| 409 already_submitted | 此用户已提交禁止重复填写的问卷 |
| 409 submission_conflict | 同一码被用于不同内容或用户 |
| 413 document_too_large | 超出数据库单文档容量，未保存 |
| 503 database_error | 数据库操作未确认成功，保留原请求重试 |
