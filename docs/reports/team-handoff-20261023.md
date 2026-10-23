# 团队开发交接验收

2026-10-23。范围为继续开发的源码交付，队友使用Windows x64；不是正式城市V1、现场导航或游客效果验收。机器摘要见 [team-handoff-20261023.json](team-handoff-20261023.json)。

## 本轮处理

- 首次Git源码基线、按提交导出的ZIP及逐文件SHA256清单；提交号在包内HANDOFF_VERSION.json，离线Git历史另用bundle交付。
- `scripts/setup.py` / `start.py` 提供跨平台安装启动，新增Windows CMD/PowerShell入口；原shell入口继续兼容。
- 补采集及Excel/PDF处理依赖和许可记录；Windows显式安装tzdata时区数据；开发启动选择npm.cmd。
- 四份Playwright配置不再写死Mac Chrome或venv路径。Agent与地图测试服务改为默认读取正常frontend/dist，仍允许显式隔离构建路径覆盖。
- 清理交付边界：不含本机密钥、运行库/台账、备份、依赖目录、诊断现场或日志；保留已有工作簿、候选、原始采集资料与需求证据。
- [接手步骤](../../HANDOFF.md)说明配置、缺密钥降级、验证与前三项任务；[数据缺口](../TEAM_DATA_GAPS.md)明确原始evidence包待资料提供者补交。

## 实际验证

在 `/private/tmp/kaifeng-handoff-clean-20261023` 建立全新源码目录：没有原.venv、node_modules、.env、运行库或采集台账，联网按锁定清单安装。

| 项目 | 结果 | 边界 |
| --- | --- | --- |
| 从零安装、pip check、TypeScript及Vite构建 | 通过 | 实际macOS arm64 / Python3.13.15 / Node26.3.0 |
| backend/tests + collector/tests | **747 passed，1 warning** | 合成/替身和临时库，不代表真实业务 |
| 正常启动器、健康接口、6个主要页面 | 通过 | 独立8061端口、无密钥；390px截图已检查，无页面错误和横向溢出 |
| 默认浏览器回归 | **84 passed / 4 failed / 1 skipped** | 显式合成事实/绑定、独立临时库；没有把已知失败抹掉 |
| 参考问路专项 | **4 passed** | HTTP协议替身，无真实地图费用 |
| Agent专项最终复验 | **7 passed** | 默认Playwright浏览器；合成事实/运营包和模型替身 |
| 地图专项 | **12 passed / 1 skipped** | 在线SDK用例默认跳过；不证明手机实际定位 |
| 工作簿转换及city-facts dry-run | **469 received，ready=0** | 新依赖实际可用，全部未核验候选；没有apply |
| Windows x64 / Python3.13依赖解析 | 47项可解析 | 核对平台wheel/元数据，并显式解析tzdata；不是Windows真机安装或完整条件依赖执行 |
| Windows CMD/PowerShell及手机现场 | **未执行** | 已提供平台入口与路径处理，需队友Windows实机接手验证 |

套件间有重叠，不能将浏览器通过数或Python成绩相加作为独立业务任务数。新实例截图见 [team-handoff-clean-start.png](../screenshots/team-handoff-clean-start.png)。

## 复现命令与首次失败

新目录先运行 `python3 scripts/setup.py`，随后：

```bash
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q backend/tests collector/tests
cd frontend
npx playwright install chromium
E2E_CITY_FACTS=../backend/tests/fixtures/city_facts.json E2E_OBJECT_BINDINGS=../backend/tests/fixtures/object_bindings.json npx playwright test
npx playwright test --config playwright.reference.config.ts
npx playwright test --config playwright.agent.config.ts
npx playwright test --config playwright.amap.config.ts
```

Windows对应命令与编码设置见HANDOFF。默认套件、参考及地图首次使用显式指定的Playwright已下载Chromium，Agent最终7项复验及正常启动页面检查使用默认安装浏览器。

隔离安装首次受工具网络/DNS边界限制，开放依赖下载后原锁定版本成功安装。Agent首次7项失败源于测试服务写死 `.test-results/agent-v3-dist`，新目录缺此历史产物；已修正默认路径并复验7项通过。地图测试服务同类路径也已修正并通过12项。未用复制旧产物或删除断言掩盖失败。

## 默认套件尚未通过项

- `city-state.spec.ts:38`：城市安排：保存后刷新恢复，记录页与导出读取同一行程。
- `flows.spec.ts:6`：景区直达、详情点位、外部地图降级与手机导航。
- `guide.spec.ts:2`：景区地图默认没有模拟场次或热力，显式演示才出现示例路线。
- `journey.spec.ts:61`：空行程只保留明确下一步，助手入口绑定当前行程，设置不重复列行程。

前三项仍断言旧入口文案或旧示意地图；本轮未为交接改业务页面或放宽断言。另一个空行程用例读取了undefined的trip ID，后续需核对异步状态与测试时序，不能直接判断为产品已修复。4项保留为未通过。

## 交付与未完成事项

交付只从经过核对的Git提交导出，附SHA256与逐文件清单。Git作者使用Codex本地交接标识，不修改全局Git身份；未创建远端、上传或发送给队友。原机8017/5177服务、.env、业务库及删除台账未被本轮重启或替换。

仍需团队完成：Windows真机安装启动；补交工作簿evidence；真实入口/营业/日期场次/餐饮供应与交通准入；已知浏览器失败、助手问答与输出复核；真实路线与途中修改走查。交付包提供的是可接手开发基线。

日志和本轮改动前备份保存在项目 `.backups/team-handoff-20261023/`，不进入交付包；它们不是正式业务备份。
