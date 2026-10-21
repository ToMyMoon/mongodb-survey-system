# 基于 MongoDB 的问卷系统

数据库课程大作业第一阶段。使用 Python、Flask、PyMongo 和简单 HTML/JavaScript，实现账号、问卷创建与发布、单选/多选/文本/数字题、可配置校验与跳转、正式提交和统计。

代码包含前后端实现及自动测试，不依赖现成问卷系统框架，不使用关系数据库。业务集合为 users、surveys、survey_versions、responses，另有 sessions 支持登录。

## 环境要求

本仓库的启动脚本面向 Windows PowerShell。

| 环境 | 要求 |
| --- | --- |
| Python | 3.11 或以上，并安装 Windows Python Launcher，使 `py -3` 可用；已在 Python 3.12 验证 |
| MongoDB Server | 必须有 mongod.exe，只有 MongoDB Compass 不够；已在 MongoDB 8.3.11 验证 |
| Node.js 与 npm | 仅前端自动测试需要；可使用 Node.js 24.15 或以上的 24.x 版本 |
| 网络 | 首次安装 Python 和前端测试依赖时需要连接软件包源 |

系统本身运行不需要 Node.js。Python 和 MongoDB 需先自行安装，启动脚本不会代为安装这两项软件。

## 获取项目和启动

仓库地址：<https://github.com/ToMyMoon/mongodb-survey-system>。

从 GitHub 下载仓库 ZIP 并解压，或使用 Git 克隆仓库：

```powershell
git clone https://github.com/ToMyMoon/mongodb-survey-system.git
cd mongodb-survey-system
```

在能看到 app.py、start.ps1 的仓库根目录打开 PowerShell。

如果 mongod.exe 已在 PATH 中，可直接启动。否则先设置实际安装路径，例如：

```powershell
$env:MONGOD_PATH = 'C:\Program Files\MongoDB\Server\8.3\bin\mongod.exe'
```

上述路径只是示例，请替换为本机实际位置。随后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

首次启动会创建 .venv、安装 requirements.txt、启动项目独立 MongoDB 副本集、初始化集合校验和索引，随后启动网页服务。保持此窗口运行，在浏览器打开：

<http://127.0.0.1:5000/>

首次使用自行注册账号。仓库不包含作者本机数据库，没有随仓库提供的预置账号或问卷。关闭前台网页服务可按 Ctrl+C，MongoDB 的持久数据会保留。

如果已有 .venv 但依赖没有安装完整，可执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 数据库与配置

项目默认使用 `mongodb://127.0.0.1:27018/?replicaSet=survey-rs`，数据库为 survey_coursework，数据保存在仓库下 .mongo 目录。bootstrap.py 自动初始化名为 survey-rs 的单节点副本集，以支持事务。不要将其改成不支持事务的独立节点。

27018 端口被其他 MongoDB 实例或另一份项目副本占用时，启动脚本会停止并提示，不会接管或修改该实例。应先处理端口占用，再启动本仓库。

网页默认端口为 5000。如需调整，在启动前设置：

```powershell
$env:SURVEY_PORT = '5001'
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

随后访问 `http://127.0.0.1:5001/`。网页和 MongoDB 均仅绑定本机地址，本项目没有远程部署配置。

## 页面验收步骤

建议用普通窗口注册创建者账号，用无痕窗口注册填写者账号，保持两个独立会话。

1. 创建者创建问卷，填写标题和说明，添加单选、多选、文本和数字题，保存草稿。新题默认必答，文本最少字数默认 1，数字默认要求整数，均可在草稿修改。
2. 配置多选最少 2、最多 3 项，文本最少 2、最多 4 字，数字为整数且在 0～120 内。非法配置应显示提示。
3. 为第一道单选题设置“选 A 跳至第三题”。点击发布后先取消，确认仍为草稿，再次点击并确认发布。
4. 复制问卷链接，在填写者窗口打开。验证选 A 时跳过第二题，未命中规则时按顺序作答。
5. 在对应路径输入少选/多选、过短/过长文本、数字 -1/121/2.5，检查提示；改为合法答案后继续。
6. 完成后点击提交，先取消并继续修改，再次确认提交。创建者查看答卷、整卷和单题统计，核对输入内容、人数、次数和数字平均值。
7. 分别在允许重复和禁止重复的问卷中再次填写，核对限制；允许匿名的问卷中选择匿名，检查创建者看不到填写者身份。
8. 关闭问卷，确认停止接受新的正式提交；修改题目后重新发布，检查旧发布版本及旧答卷仍可查看。

输入阶段还可检查即时标红、密码小眼睛、发布/退出确认、返回主页及截止时间自动保存。详细用例见 [测试报告](docs/测试报告.md)。

## 后端自动测试

完成首次环境安装后，在仓库根目录另开 PowerShell。先确保项目 MongoDB 已启动，再运行测试：

```powershell
.\.venv\Scripts\python.exe bootstrap.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --junitxml=test-results.xml
```

当前测试集预期为 **88 项通过**。使用 `-p no:cacheprovider` 关闭非必需缓存，避免部分 Windows 环境下 .pytest_cache 写权限警告，测试用例不受影响。

后端测试中的集成用例通过 Flask 测试客户端调用真实后端，连接真实 MongoDB 副本集，验证事务、唯一索引、入库数据和统计；规则单元用例直接调用校验和条件函数。测试使用随机命名的 survey_test_ 测试库并在结束后清理，不清空页面创建的 survey_coursework 数据库。无需为了这些测试先启动网页服务，但数据库必须可连接。

只运行补充的三组测试时，可执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_requirements.py
```

该组包含 57 项：多选/数字跳转完整流程 25 项、填写边界 30 项、不同用户并发提交 2 项。

## 前端自动测试

在仓库根目录运行：

```powershell
npm.cmd ci
npm.cmd test
```

当前测试集预期为 **32 项通过**。测试在 jsdom 中执行实际页面脚本，模拟输入、点击、保存恢复、确认取消和错误响应。后端返回由夹具提供，因此不要求 MongoDB 或网页服务运行，也不能替代后端集成测试和实际浏览器验收。

## 已保存的测试记录

仓库中的 [后端结果](测试记录/test-results.xml) 和 [前端结果](测试记录/frontend-test-results.txt) 是此前实际执行的记录，合计 120 项通过。教师重新运行时，应以本机输出为准。

后端记录包含 2026-10-20 在原工作目录重新运行得到的 88 项通过结果，终端显示耗时 67.83 秒；测试报告同时保留此前完整回归的历史时间。前端记录为此前 32 项通过的结果。本次整理提交目录未重新执行完整业务测试。

运行上面的后端命令会在仓库根目录生成新的 test-results.xml，不覆盖“测试记录”目录里的历史记录。

## 文档与文件导航

| 材料 | 内容 |
| --- | --- |
| [提交目录说明](提交目录说明.md) | 上传文件树、各文件作用、应排除的本地文件 |
| [设计说明](docs/设计说明.md) | 数据库集合/字段、嵌入与引用、索引、校验、跳转、提交和统计规则 |
| [API 说明](docs/API说明.md) | 接口方法、输入输出、权限和错误 |
| [测试报告](docs/测试报告.md) | 测试步骤、输入、预期输出及实际结果 |
| [AI 使用日志](docs/AI使用日志.md) | 实际提示词、产出概括和修改过程 |
| 验收截图 | 实际页面的统计和交互验收证据 |

文档中提到的 test-results.xml、frontend-test-results.txt 历史文件统一存放于“测试记录”目录。本 README 是此 GitHub 提交目录的启动与测试入口。

## 已确认的范围

正式业务数据及服务端会话存 MongoDB；未提交填写草稿按后续确认的需求仅缓存在填写者浏览器，不能跨设备恢复。问卷关闭后可回草稿并生成新发布版本，旧答卷保留，统计按版本查看。

本阶段不提供提交后改答、服务器填写草稿或跨版本合并统计。并发用例验证本机功能一致性，不代表大规模性能压测结果。第二阶段应在现有代码和核心结构上扩展。
