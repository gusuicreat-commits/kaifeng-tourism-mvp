# 队友接手说明（Windows 优先）

交付用途：继续开发的本机原型。正式城市 V1、现场导航和游客效果尚未验收。包内 `HANDOFF_VERSION.json` 记录 Git 提交及每个文件的 SHA256；不要混用其他隔离副本。内部团队使用，采集候选及网页/媒体不因此获得转载授权。

## 1. Windows 首次运行

建议 Windows 10/11 x64、Python 3.13 x64，以及 Node.js 22.12或以上兼容版本（含 npm）。本次实际运行环境是 macOS/Python 3.13；Windows依赖解析与真机运行的区别见 [交接验收](docs/reports/team-handoff-20261023.md)。

1. 解压到一个自己的目录，打开该目录的 PowerShell。不要复制发送方的 `.venv`、`node_modules`、`.env` 或运行数据库。
2. 确认 `py -3 --version`、`node --version`、`npm --version` 能执行。若刚安装软件，重新打开终端。
3. 运行：

```powershell
py -3 scripts/start.py
```

也可双击 `start.cmd`；安装失败时窗口会保留错误。`start.ps1` 是可选入口，无须为它修改系统的全局执行策略。若没有 `py` 命令但有 Python，使用 `python scripts/start.py`。

第一次自动创建虚拟环境、安装基础和采集依赖、构建前端并初始化本机示意库。安装需要访问 PyPI/npm。终端显示应用地址，默认 `http://127.0.0.1:8017`。按 Ctrl+C 停止本次服务。端口占用请改自己的配置，不关闭其他项目进程。

开发热更新：

```powershell
py -3 scripts/start.py --dev
```

前端默认 `http://127.0.0.1:5177`，代理到8017。前端修改后生产模式需重新构建：`npm run build --prefix frontend`。macOS/Linux入口保持 `./start.sh` 或 `python3 scripts/start.py`。

## 2. 配置：先不填密钥也能启动

启动不会复制发送方密钥。首次需要配置时，在 PowerShell执行：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

| 配置 | 用途 | 没有时 |
| --- | --- | --- |
| BACKEND_PORT / FRONTEND_PORT | 本机后端/开发前端端口 | 默认8017/5177 |
| TOURISM_LLM_BASE_URL / TOURISM_LLM_MODEL / TOURISM_LLM_API_KEY | Kimi等明确配置的兼容服务 | 自然语言能力降级；结构化交互及引擎仍保留 |
| AMAP_JS_KEY / AMAP_JS_SECURITY_CODE | 高德底图与插件 | 显示缺配置或外部查询入口；安全码只留后端 |
| AMAP_WEB_SERVICE_KEY | 高德服务端地点和参考路线 | 参考查询不可用，不能补假路线 |
| AMAP_LOCATION_SEARCH_ENABLED / AMAP_TERMS_REVIEWED / AMAP_REFERENCE_ROUTES_ENABLED | 地点搜索、使用边界确认、参考路线显式开关 | 模板默认关闭，不能只填Key就假定功能已开启 |
| DASHSCOPE_API_KEY | 百炼语音转文字 | 语音显示未开通，文字输入可用 |

密钥由团队按各平台授权自行配置，不通过聊天、Git或交付包传播。域名/使用条件和配额须与实际环境匹配；这些开关不启用正式交通Provider。接口有配置也不等于余额或服务健康已验证。详细说明见 [地图](docs/AMAP_MAP_LOCATION_MVP.md)、[Kimi](docs/KIMI_API.md)、[语音](docs/SPEECH.md)。

`DATABASE_PATH` 和删除台账路径默认留空即可。新机器会新建自己的库和台账，不沿用发送方私人会话。手机定位/录音另需HTTPS；localhost网页可用不等于手机现场可用。

## 3. 队友应能看到的结果

- 首页、详情、素材篮、助手、行程和地图入口可打开；没有真实资料的区域显示待添加。
- 可以创建/保存本机行程意向；无有效正式事实时，不应生成虚构餐馆、场次或可执行路线。
- 刷新后可恢复同一浏览器的已保存意向；记忆页可导出/删除自己的资料。
- 地图、模型、语音只有在自行配置且上游正常时才可在线使用。

不要把合成fixture导入运行库只为填满页面。新初始化库内的504条模拟客流和2套示意路网是原型种子，正式城市事实默认为空。

## 4. 本地验证（不调用收费服务）

PowerShell：

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe -m pytest -q backend/tests collector/tests
npm run build --prefix frontend
Set-Location frontend
npx playwright install chromium
npx playwright test --config playwright.reference.config.ts
npx playwright test --config playwright.agent.config.ts
npx playwright test --config playwright.amap.config.ts
Set-Location ..
```

这三类专项测试使用临时库、合成资料/替身；地图在线用例默认跳过。默认使用Playwright安装的Chromium，不再写死Mac路径。需要自选浏览器时设置本机 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`，不要复制别人的路径。完整默认浏览器回归命令是 `npm run test:e2e --prefix frontend`；已有失败的范围及本轮实测成绩见[验收记录](docs/reports/team-handoff-20261023.md)。

Windows没有Python运行时和Node环境时，源码包不能直接变成免安装桌面应用。解压失败、安装失败、应用启动失败和外部API失败分别记录命令与输出，不要靠导入私人数据库“修复”。

## 5. 接手后优先做什么

| 优先级 | 工作 | 建议负责角色 | 完成判据 |
| --- | --- | --- | --- |
| 1 | 补齐团队工作簿的证据文件、入口坐标与目标日场次 | 数据负责人，团队指定具体人 | 候选来源与证据哈希可复核，完整日期/结束时间/供应依据；见[数据缺口清单](docs/TEAM_DATA_GAPS.md) |
| 2 | 修复助手知识问答与输出复核；处理已知浏览器失败 | 软件负责人 | 不把普通知识问成缺资料；参考草案数字和结论一致；明确复验记录 |
| 3 | 一条真实跨场景路线与中途修改 | 软件与数据负责人共同执行 | 相同trip、有效入口/交通/营业/场次，实走并记录偏差，不冒充全城导航验收 |

能力接口旧描述、复杂任务延迟和多游客容量仍是已知事项，见[项目现状](docs/PROJECT_STATUS.md)。本次交接不自动承诺由某位队友完成上述工作。

## 6. 文件与协作

交付包包含源码、锁定依赖、测试、必要原始需求、历史验收、现有团队工作簿及候选/采集证据。它不包含 `.env`、私人会话库/删除台账、`collection.db`、安装目录、运行日志或本机备份。`collection.db`是可再建的工程台账，候选和原始证据另有文件；不把它当事实库复制。

下载包内没有`.git`；提交号在 `HANDOFF_VERSION.json`。长期协作应从同一个团队Git远端克隆；本轮没有创建远端或上传。离线包接手可先运行，首次联调通过后再由团队选定远端。

文档顺序：[现状](docs/PROJECT_STATUS.md) → [PRD](docs/PRD.md) → [开发方向](docs/DEVELOPMENT_DIRECTION.md) → [工程架构](docs/CITY_ARCHITECTURE.md) → [专项导航](docs/README.md)。旧阶段文档在archive，仅作追溯。
