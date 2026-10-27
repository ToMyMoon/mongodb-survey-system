# 基于 MongoDB 的问卷系统 · 第二阶段

本分支在第一阶段实现上增量扩展。第一阶段保留在 [main 分支](https://github.com/ToMyMoon/mongodb-survey-system/tree/main)，第二阶段使用 [stage2 分支](https://github.com/ToMyMoon/mongodb-survey-system/tree/stage2)，共享原有提交历史。

保留注册登录、四类题型、配置校验、组合跳转、发布快照、匿名及重复限制、幂等提交和原有问卷统计；新增固定单题模板、个人题库、指定用户共享、个人移除与恢复、来源历史、使用情况和跨问卷单题统计。模板使用后生成独立题目副本，修改不回写原模板。

## 环境与获取

启动脚本面向 Windows PowerShell。需要 Python 3.11 或以上及 `py -3` 启动器、MongoDB Server（包含 mongod.exe）；首次安装依赖需要网络。Node.js 与 npm 仅前端自动测试需要，日常运行不需要 Node.js。

```powershell
git clone --branch stage2 https://github.com/ToMyMoon/mongodb-survey-system.git
cd mongodb-survey-system
```

已有同一仓库的其他本地副本，可先保存自己的改动，再执行 `git fetch origin` 和 `git switch stage2`。请在独立目录运行第二阶段，避免与正在运行的另一份第二阶段实例共用端口。

## 启动

在能看到 app.py、start.ps1 的仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

如果 mongod.exe 没有加入 PATH，先设置实际安装位置，例如：

```powershell
$env:MONGOD_PATH = 'C:\Program Files\MongoDB\Server\8.3\bin\mongod.exe'
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

路径只是示例，需按本机安装情况替换。脚本创建项目自身的 .venv、安装依赖、启动独立 MongoDB 副本集并启动网页。访问 [问卷首页](http://127.0.0.1:5001/) 或 [模板与题库](http://127.0.0.1:5001/library)。按 Ctrl+C 可停止前台网页服务，数据库文件仍保留。

| 项目 | 第二阶段默认配置 |
| --- | --- |
| 网页 | 127.0.0.1:5001 |
| MongoDB | 127.0.0.1:27019 |
| 副本集 | survey-stage2-rs |
| 数据库 | survey_coursework_stage2 |
| 数据目录 | 当前目录 .mongo |

第一阶段使用 5000 / 27018 / survey-rs / survey_coursework，第二阶段默认配置与其隔离。bootstrap.py 核对已有实例的副本集和数据目录，不接管其他实例。同一端口已有另一份第二阶段实例时，请先正常停止它，再运行本目录。

可设置 SURVEY_PORT、SURVEY_MONGO_URI、SURVEY_MONGO_DB 覆盖网页连接配置；bootstrap 仍管理固定的本机 27019 实例。请勿把第二阶段连接设置指向第一阶段或其他重要数据库。项目只绑定本机地址，没有远程部署配置。

**仓库不含作者的本机数据库、账号、问卷或答卷。全新克隆后请自行注册。** 本地实施日志中导入的旧数据和验收账号并不会随 GitHub 仓库提供。

## 第二阶段体验顺序

1. 创建自己的问卷和完整题目，点击“加入模板”；草稿会先保存，发布问卷取发布快照。
2. 保存到题库时列出默认题库及所有已建题库，默认仅勾选默认题库。列表末尾是“＋ 新建题库”，点击后才弹出名称及“创建 / 取消”；创建成功勾选新题库、取消默认题库、保留其他选择。新题库创建独立完成，取消模板保存也保留。
3. 在新问卷点击“从模板或题库选题”。修改加入后的副本，再查看原模板，确认互不影响。同一模板可重复选用。
4. 修改题目后另存新模板，查看来源与历史；正文不支持原地编辑。全部题库按模板 ID 汇总，同一模板归入多个题库只显示一次；同一题库仍按内容去重。
5. 自己保存及别人共享的模板都可“从我的模板移除”，只影响自己的列表和题库，不删除正文、不影响他人、不自动撤销共享；当前仍有权限时可恢复。
6. 作者按完整用户名查找并确认共享，撤销共享是独立操作。接收者不能直接转分享原模板；修改后另存的个人模板可分享。来源无权限时显示原因，既有题目副本保留。
7. 查看使用情况及跨问卷统计：只涉及当前用户自己的问卷，按来源与内容匹配，支持问卷、版本和提交时间筛选。回答次数逐次累计，人数跨问卷去重。

双账号体验请使用两个独立浏览器，或普通窗口和无痕窗口。同一浏览器环境的多个标签共享 Cookie；项目会同步账号变化并阻止旧身份写入，不为共享 Cookie 的标签提供独立登录空间。切换账号前请保存编辑。

## 自动测试

先确保本目录环境已建立且第二阶段 MongoDB 正常：

```powershell
.\.venv\Scripts\python.exe bootstrap.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --junitxml=test-results.xml
npm.cmd ci --ignore-scripts
npm.cmd test
```

最近一次完整执行为 **后端 140 项、前端 61 项，共 201 项全部通过**。见 [后端原始结果](测试记录/test-results.xml)、[前端原始结果](测试记录/frontend-test-results.txt) 和 [测试报告](docs/测试报告.md)。这是此前实际执行的记录，教师重跑应以本机输出为准，本次整理 GitHub 分支未重复执行完整业务测试。

后端集成测试使用 27019 上随机命名的 survey_test_ 数据库，结束清理本次随机库，不清空页面业务库；不要求网页服务运行。前端在 jsdom 中执行实际脚本并模拟网络，不能代替真实数据库或完整浏览器联动验收。运行测试会在根目录生成新结果，由 .gitignore 排除，不覆盖仓库“测试记录”中的已提交证据。

## 文档与源码

| 入口 | 内容 |
| --- | --- |
| [需求与设计确认](docs/需求与设计确认.md) | 已确认规则及后续体验调整 |
| [设计说明](docs/设计说明.md) | MongoDB 集合、个人收录、题库、来源、权限和统计 |
| [API 说明](docs/API说明.md) | 接口、输入输出和错误 |
| [测试报告](docs/测试报告.md) | 当前 201 项结果及历史记录 |
| [AI 使用日志](docs/AI使用日志.md) | 提示词、实际产出及调整过程 |
| [提交目录说明](提交目录说明.md) | 仓库内容与本地归档的区别 |
| app.py / engine.py | 沿用的问卷主流程、条件与答案引擎及必要接入 |
| template_library.py / static/library.js | 第二阶段模板、题库、共享、来源、个人移除和统计 |
| db.py / static/app.js | 集合与索引、原有界面及账号变化保护 |
| tests/ | 原有及第二阶段自动测试 |

验收截图中 2026-10-20 文件为第一阶段历史画面，题库交互修订截图为第二阶段实际页面。日志和测试报告提到的本地隔离摘要、旧数据迁移核对及数据库备份属于实施归档，不全部随公开仓库提供。

`tools/probe_server.py` 为启动脚本依赖；`check_runtime.py`、`backup_catalogue.py` 用于本目录运行检查及第二阶段目录备份。`import_stage1.py`、`verify_isolation.py`、`package_source.py` 保留本地开发目录的迁移、隔离核对与打包过程，依赖日志描述的两阶段目录结构和本地记录，不是全新克隆启动的必需步骤。没有第一阶段本机数据时不要运行迁移工具。

源码不包含本地项目完成报告、原始作业 Word、数据库、安装环境或运行日志；可运行环境由依赖文件重建。
