# 汴梁游记路演展示

2026-10-25：瑞士国际主义风 HTML 路演共 14 页，依据用户提供的文字稿整理。第 5、6、9 页使用用户亲自截取的五张产品图；原始像素保留，不裁掉界面提示或重新绘制答复。

## 打开与操作

- 运行产品后访问 `/pitch`，跳转到 `/assets/pitch/index.html`。
- 离线文件为 `frontend/public/assets/pitch/index.html`，也可使用尾页的“下载离线 HTML”。图片与动效依赖均已内嵌。
- 方向键、PageUp/PageDown 或底部按钮翻页；Escape 打开总览，P 打开演讲者模式。
- 产品图下方“查看原图”打开完整截图，Escape 或关闭按钮退出。
- 每页备注在 `frontend/pitch/notes.json`。未提供现场时长，因此未设自动翻页或逐页讲述分钟数。

## 产品图与讲述位置

| 页面 | 内容 | 图片来源 |
| --- | --- | --- |
| 5 / 从发现开封，到选好这一程 | 首页、发现与素材篮入口 | 用户截图 1、2 |
| 6 / 想问的，直接告诉小汴梁 | 第一楼问答画面 | 用户截图 3 |
| 9 / 把城市位置与游玩内容放在手边 | 城市地图、景区实景图集 | 用户截图 4、5 |

图片保存在 `frontend/pitch/images/user-*.png`，来源和 SHA-256 见同目录 `manifest.json`。地图截图保留路线版本 0、尚无已确认路线及定位失败提示；问答截图不作为其文字事实的独立核验证据；图集不证明目标日期演出可用。

第 7、8 页为固定合成交互示例，演示候选、预览、取消及确认。仅修改页面内存状态，不调用业务 API、模型或交通 Provider，不写入真实旅行。演讲者面板提供相同交互控制，观众屏同步展示。

## 更新与验证

```sh
python3 scripts/build_pitch.py
npm run build --prefix frontend
node scripts/verify_pitch.mjs
```

`npm run build` 的既有 `prebuild` 会调用路演构建。验证脚本默认连接 `http://127.0.0.1:8017`，可用 `PITCH_BASE_URL` 指向独立测试服务；浏览器验证屏蔽并记录业务 API 请求。

```sh
node /Users/mac/.codex/skills/guizang-ppt-skill/scripts/validate-presenter-mode.mjs frontend/public/assets/pitch/index.html
```

机器回执为 `docs/reports/pitch-deck-20261025.json`，浏览器截图位于 `docs/screenshots/pitch-20261025/`。这些检查只验证路演页面，不代表真实路线、现场定位、外部服务或游客效果已通过验收。

本轮前端构建、14 页备注校验、Swiss 结构校验通过；浏览器 9 组检查通过，覆盖五张原图加载与放大关闭、翻页、候选确认、调整取消、5 种尺寸、演讲者与观众屏同步、离线下载。业务 API 和外部资源请求均为 0。原图文件 SHA-256 与来源清单逐一一致。

Swiss 渲染测量有 5 条版式提示：两张分屏页的满高背景触及底部区域，三张图片页的标题间距为 27px（模板建议 32px）。已视觉检查图片页，并通过 1600×900、1280×720、1024×768 的实际文字边界检查；320px、390px 手机布局允许纵向阅读，无横向溢出。

基础模板来自本机 `guizang-ppt-skill/assets/template-swiss.html` 的固定快照，保存在 `frontend/pitch/template-swiss.html`；内嵌 Motion 版本为 11.11.17。产品主题与路演主题独立。
