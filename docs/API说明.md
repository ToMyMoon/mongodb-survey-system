# 第二阶段 API 说明

基础地址为 http://127.0.0.1:5001 。登录、CSRF、同源检查、错误结构、ObjectId、时间和精确数字输出沿用第一阶段。写操作需登录 Cookie 和 X-CSRF-Token。

## 模板

| 方法与路径 | 输入及行为 |
| --- | --- |
| GET /api/templates | scope=all/mine/shared/history，默认 mine；可带 bankId，返回当前可访问模板；history 返回当前用户移除的收录（包括共享模板）；权限失效只返回说明占位 |
| GET /api/templates/{id} | 自有模板含来源问卷信息；他人模板仅当前共享有效时开放正文，不返回其来源问卷标识 |
| POST /api/templates | requestId、surveyId、questionId；草稿携带 revision，发布来源携带 versionId；可带 bankIds 数组、newBanks 名称数组，默认选默认题库；兼容旧 bankId。只取服务器已保存的单题内容 |
| POST /api/templates/{id}/use | surveyId、revision；服务器检查权限和草稿状态，创建新实例及新选项 ID、清空跳转、记录来源，直接追加并保存到问卷 |
| DELETE /api/templates/{id} | 自己保存或共享给自己的模板均可；只移除当前用户收录及自己的题库关联，不撤销共享，不影响他人 |
| POST /api/templates/{id}/restore | 恢复当前用户收录（仍需当前查看权限），保留原 ID；返回 skippedBanks，表示内容重复而跳过的自己的原分类，不改变共享 |
| GET /api/templates/{id}/history | 来源及可见分支，逐节点检查权限；不可读节点不返回正文，提供 accessReason；个人移除节点返回 canRestore |
| GET /api/templates/{id}/usage | 仅当前用户自己的问卷，包含发布版本、当前使用及内容修改标记，按稳定问卷 ID 计数 |
| GET /api/templates/{id}/stats | 当前用户自己的问卷，匹配直接来源或明确映射且内容一致；可带 surveyId、versionId、from、to；时间需含时区，含边界 |

模板正文不提供 PUT/PATCH 编辑接口。内容不变的派生保存返回 409 unchanged_template；同一 requestId 携带相同请求重试返回原模板，不同请求内容冲突。成功保存草稿模板会增加问卷 revision，客户端应重新载入。事务确保模板、来源关联和本次分类一起提交或回滚，之前已经保存的草稿保留。

草稿模板保存和选用接口的 revision 必须为 JSON 整数，不接受 true/false 或浮点值。同一实例连续另存以最近保存的模板为直接父来源；保留最初使用来源用于追溯及统计，不将整个来源链自动合并。

统计返回 answerCount、respondentCount、sources；选择题包含各选项 count。文本返回内容和时间，不返回填写者标识；数字增加 average、minimum、maximum、distribution（value、count、percentage）。所有结果仅针对当前用户问卷。按源题选项顺序映射到模板选项，技术 ID 不影响内容比较。

## 共享

| 方法与路径 | 输入及行为 |
| --- | --- |
| GET /api/users/lookup?username=... | 精确查找完整用户名，仅返回用户名及 ID |
| GET /api/templates/{id}/shares | 作者查看当前有效共享对象 |
| POST /api/templates/{id}/shares | 作者携带查找确认后的 username、userId，重复授权不产生重复关系 |
| DELETE /api/templates/{id}/shares/{userId} | 作者撤销共享，接收者已保存副本不受影响 |

无公开广场或任何人可用的共享链接。共享接收者不可管理原模板共享；自己的修改派生模板可以共享。共享不授予他人问卷或答卷访问权。

## 个人题库

| 方法与路径 | 输入及行为 |
| --- | --- |
| GET /api/banks | 确保默认题库及旧未分类的一次性归类，返回自己的可用题库（isDefault 标识） |
| POST /api/banks | name（1～100 字）、description，可创建多个；禁止空白、同名和系统保留名 |
| PUT /api/banks/{id} | 作者修改自定义题库 name、description；默认题库不可改名 |
| DELETE /api/banks/{id} | 删除自定义题库及分类可用状态，不删除模板；默认题库不可删除 |
| POST /api/banks/{id}/items | templateId，可收录自己及当前获共享模板，同库相同模板幂等，相同内容的另一模板返回 409 duplicate_content |
| DELETE /api/banks/{id}/items/{templateId} | 只移除分类关系，不删除模板 |

题库内容唯一约束由字段规范化摘要和活动关系唯一索引共同保证；必答参与题库判重，但不参与统计归并。模板和选项 ID、作者、来源不参与内容比较。

## 原接口兼容

原账号、问卷管理、发布、填写及按问卷版本统计接口保留。普通 PUT /api/surveys/{id} 不允许伪造或改变 sourceTemplateId、savedTemplateIds，来源由模板保存及选用接口维护。旧 answers 的 questionId 仍是问卷实例 ID，不改为模板 ID；历史发布快照和答卷不回写。

## 2026-10-27 补充协议

POST /api/templates/{id}/banks：bankIds、newBanks，多题库追加归类及命名新建在同一事务提交。取消勾选不会删除已有分类，移出分类沿用 DELETE /api/banks/{id}/items/{templateId}。

模板创建返回 skippedBanks；默认题库存在相同内容时跳过默认分类并说明，仍保留本份个人模板及其他选择。newBanks 随 requestId 重试不会重复创建。bankIds=[] 表示主动不分类；不传时使用默认题库。列表/详情补充 bankIds、readable，deleted 在响应中表示当前用户已移除；不是正文全局删除状态。已失效共享的移除记录不返回 question。

新前端写请求发送 X-Survey-User（当前显示的账号 ID），与 Cookie 实际账号不同则返回 409 account_changed，写操作不执行；所有 API 响应附同名头表示实际账号。原 CSRF 和同源检查继续生效。同步通知不携带密码、Cookie 或 CSRF。

### 保存题库弹窗交互修订

当前界面只列题库复选项与“＋ 新建题库”；点击加号后弹窗输入名称，点击“创建”单独调用 POST /api/banks。创建成功后新题库勾选、默认题库取消勾选、其他选择保留；多次创建均保留勾选。模板保存提交 bankIds，不再从界面发送 newBanks。新题库一经创建就独立保留，即使随后取消或未完成模板保存。后端 newBanks 兼容协议仍保留，其事务语义未改变。
