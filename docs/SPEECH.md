# 语音转文字接入

用户于2026-10-20确认：全站辅助工具，使用阿里云API，暂无密钥，先完成结构。此功能仅转写、编辑和复制文字，不解析游览偏好、不改变路线算法。

## 代码与协议

- `frontend/src/components/VoiceInput.vue`：原生对话框、录音状态、权限提示、编辑复制和手动重试。Escape、关闭、取消、组件销毁均清理录音和请求。
- `frontend/src/audio.ts` 与 `frontend/public/audio-recorder.js`：Web Audio / AudioWorklet采样，重采样为16kHz、单声道、16位PCM WAV；录音上限60秒。
- `backend/app/speech.py`：验证WAV真实头部、长度及完整性；单进程最多2个识别请求；通过已有httpx依赖调用百炼，不新增SDK。
- `GET /api/speech/status` 返回 `available` 和 `max_seconds`。
- `POST /api/speech/transcriptions` 接受multipart字段 `file`，只接受0.2–60秒标准WAV、最大2MiB；成功返回 `{"text":"..."}` 并禁止响应缓存。

请求：`POST {DASHSCOPE_BASE_URL}/services/aigc/multimodal-generation/generation`，模型固定 `qwen-audio-3.0-asr-flash`，使用服务端 `DASHSCOPE_API_KEY` 作为Bearer令牌。将WAV作为 `input.messages[].content[].input_audio.data` 的Data URI传入，`parameters` 设置format、sample_rate和即时热词。关闭SSE，兼容官方文档中的 `output.text` 或 `output.output.sentence.text` 返回结构。

官方依据：

- [同步语音识别HTTP API](https://help.aliyun.com/zh/model-studio/fun-asr-flash-recorded-speech-recognition-http-api)
- [热词与识别准确率](https://help.aliyun.com/zh/model-studio/improve-asr-accuracy)

## 错误与数据处理

缺密钥或配置/授权异常返回503；无效格式415；超大文件413；录音时长、完整性或无语音问题422；限流429；上游连接/响应异常502；超时504。用户界面不展示上游原始错误、密钥或内部响应。

录音和转写不写入数据库或应用文件。失败录音仅暂存在浏览器内存，以供用户手动重试；成功、取消或关闭后清除。文字仅在当前页面会话内保留，不写入localStorage；刷新后清除。百炼的数据处理政策另以其服务条款为准。

不自动重试，避免重复收费。取消只终止本地录音或浏览器等待，不能保证撤销已发往上游的识别或费用。当前服务仍沿用本机MVP的监听与权限边界；对外部署前需加入身份验证和按用户额度管理，2个并发限制不等于按用户计费限额。

## 验证边界

后端测试通过MockTransport检查真实请求JSON、热词、音频字节、响应解析、无密钥状态、无效音频拦截、超时、授权错误以及无自动重试。浏览器测试使用Chromium虚拟麦克风，实际执行AudioWorklet采集和WAV上传，并将识别API响应替换为测试文本；检查转写编辑复制、错误重试和麦克风释放。截图中的识别文字是测试数据。

2026-10-20 首次结构验收时没有API Key，未执行付费识别，也未证明模型权限、账号计费、真实识别质量、河南口音效果或手机Safari兼容性。

2026-10-20 本机结果：后端50项测试通过（含22项语音测试）；TypeScript检查及Vite生产构建通过；10项浏览器测试通过（含6项语音测试）。浏览器使用本机Chrome及虚拟麦克风。界面截图位于 `docs/screenshots/voice-*.png`；其中转写成功界面的文字来自测试接口响应。

### 2026-10-22 本机配置与真实接口验证

用户提供北京业务空间API Key与控制台端点后，已将 `DASHSCOPE_API_KEY` 和匹配的 `DASHSCOPE_BASE_URL` 写入本机 `.env`，权限为0600。未改动其他环境配置；密钥未进入前端源码或构建产物。通过现有服务的热重载加载新配置，`/api/speech/status` 返回 `available: true`。

使用macOS本地合成约3.8秒中文语音“我想去清明上河园，游玩两个小时”，直接调用现有后端适配器，真实百炼返回“我想去清明上河园游玩2个小时。”。随后通过正在运行的 `http://127.0.0.1:5177` 页面，以Chrome虚拟麦克风播放该素材，实际执行录音、WAV上传与真实百炼识别，接口返回200，网页收到文字。网页录音约4秒，虚拟音源循环带入下一句开头，完整结果为“我想去清明上河园游玩2个小时。我想。”；这不作为真实用户识别准确率结论。

截图：[真实接口网页验证](screenshots/voice-live-configured-20261022.png)。当日语音后端专项22项测试通过。未使用真实麦克风，未向助手提交消息或修改行程；真实设备麦克风、手机Safari、河南口音及景区噪声效果仍待验证。未核对账户账单或免费额度消耗。

验证命令：

```bash
./scripts/check.sh
npm run test:e2e --prefix frontend
```

没有Playwright自带浏览器时，可复用本机Chrome（本次验证使用此方式）：

```bash
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm run test:e2e --prefix frontend
```
