# 运行依赖版本与许可记录

根据本项目安装包 metadata 与随包 LICENSE 核查；完整锁定版本见 backend/requirements.lock。没有复制 HTS-RPR 实现。部署分发时保留对应第三方版权和许可通知。

| Package | Version | Declared license | Notice |
| --- | --- | --- | --- |
| absl-py | 2.5.1 | Apache-2.0 | [absl-py-AUTHORS](licenses/absl-py-AUTHORS), [absl-py-1-LICENSE](licenses/absl-py-1-LICENSE) |
| annotated-doc | 0.0.5 | MIT | [annotated-doc-LICENSE](licenses/annotated-doc-LICENSE) |
| annotated-types | 0.8.0 | MIT | [annotated-types-LICENSE](licenses/annotated-types-LICENSE) |
| anyio | 4.15.1 | MIT | [anyio-LICENSE](licenses/anyio-LICENSE) |
| certifi | 2026.10.18 | MPL-2.0 | [certifi-LICENSE](licenses/certifi-LICENSE) |
| click | 8.5.0 | BSD-3-Clause | [click-LICENSE.txt](licenses/click-LICENSE.txt) |
| fastapi | 0.148.0 | MIT | [fastapi-LICENSE](licenses/fastapi-LICENSE) |
| h11 | 0.16.0 | MIT | [h11-LICENSE.txt](licenses/h11-LICENSE.txt) |
| httpcore | 1.0.9 | BSD-3-Clause | [httpcore-LICENSE.md](licenses/httpcore-LICENSE.md) |
| httpx | 0.28.1 | BSD-3-Clause | [httpx-LICENSE.md](licenses/httpx-LICENSE.md) |
| idna | 3.20 | BSD-3-Clause | [idna-LICENSE.md](licenses/idna-LICENSE.md) |
| immutabledict | 4.3.1 | MIT | [immutabledict-LICENSE](licenses/immutabledict-LICENSE) |
| iniconfig | 2.3.1 | MIT | [iniconfig-LICENSE](licenses/iniconfig-LICENSE) |
| networkx | 3.7 | BSD-3-Clause | [networkx-LICENSE.txt](licenses/networkx-LICENSE.txt) |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | [numpy-LICENSE.txt](licenses/numpy-LICENSE.txt), [numpy-1-LICENSE.txt](licenses/numpy-1-LICENSE.txt), [numpy-2-COPYING](licenses/numpy-2-COPYING), [numpy-3-LICENSE](licenses/numpy-3-LICENSE), [numpy-4-dragon4_LICENSE.txt](licenses/numpy-4-dragon4_LICENSE.txt), [numpy-5-LICENSE.md](licenses/numpy-5-LICENSE.md), [numpy-6-LICENSE](licenses/numpy-6-LICENSE), [numpy-7-LICENSE.md](licenses/numpy-7-LICENSE.md), [numpy-8-LICENSE.txt](licenses/numpy-8-LICENSE.txt), [numpy-9-LICENSE](licenses/numpy-9-LICENSE), [numpy-10-LICENSE.md](licenses/numpy-10-LICENSE.md), [numpy-11-LICENSE.md](licenses/numpy-11-LICENSE.md), [numpy-12-LICENSE.md](licenses/numpy-12-LICENSE.md), [numpy-13-LICENSE.md](licenses/numpy-13-LICENSE.md), [numpy-14-LICENSE.md](licenses/numpy-14-LICENSE.md), [numpy-15-LICENSE.md](licenses/numpy-15-LICENSE.md), [numpy-16-LICENSE.md](licenses/numpy-16-LICENSE.md) |
| opentelemetry-api | 1.45.1 | Apache-2.0 | [opentelemetry-api-LICENSE](licenses/opentelemetry-api-LICENSE) |
| ortools | 9.15.6755 | Apache 2.0 | [ortools-LICENSE](licenses/ortools-LICENSE) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | [packaging-LICENSE](licenses/packaging-LICENSE), [packaging-1-LICENSE.APACHE](licenses/packaging-1-LICENSE.APACHE), [packaging-2-LICENSE.BSD](licenses/packaging-2-LICENSE.BSD) |
| pandas | 3.0.6 | BSD License | [pandas-LICENSE](licenses/pandas-LICENSE) |
| pluggy | 1.7.0 | MIT | [pluggy-LICENSE](licenses/pluggy-LICENSE) |
| protobuf | 6.33.6 | 3-Clause BSD License | [protobuf-LICENSE](licenses/protobuf-LICENSE) |
| pydantic | 2.14.1 | MIT | [pydantic-LICENSE](licenses/pydantic-LICENSE) |
| pydantic_core | 2.50.1 | MIT | [pydantic_core-LICENSE](licenses/pydantic_core-LICENSE) |
| Pygments | 2.21.0 | BSD-2-Clause | [Pygments-AUTHORS](licenses/Pygments-AUTHORS), [Pygments-1-LICENSE](licenses/Pygments-1-LICENSE) |
| pytest | 9.1.1 | MIT | [pytest-LICENSE](licenses/pytest-LICENSE) |
| python-dateutil | 2.9.0.post0 | Dual License | [python-dateutil-LICENSE](licenses/python-dateutil-LICENSE) |
| python-dotenv | 1.2.4 | BSD-3-Clause | [python-dotenv-LICENSE](licenses/python-dotenv-LICENSE) |
| python-multipart | 0.0.32 | Apache-2.0 | [python-multipart-LICENSE.txt](licenses/python-multipart-LICENSE.txt) |
| six | 1.17.0 | MIT | [six-LICENSE](licenses/six-LICENSE) |
| starlette | 1.9.0 | BSD-3-Clause | [starlette-LICENSE.md](licenses/starlette-LICENSE.md) |
| typing-inspection | 0.4.4 | MIT | [typing-inspection-LICENSE](licenses/typing-inspection-LICENSE) |
| typing_extensions | 4.16.0 | PSF-2.0 | [typing_extensions-LICENSE](licenses/typing_extensions-LICENSE) |
| uvicorn | 0.54.0 | BSD-3-Clause | [uvicorn-LICENSE.md](licenses/uvicorn-LICENSE.md) |

OR-Tools 使用 Apache-2.0，NetworkX 和 Pandas 使用 BSD-3-Clause；未引入需商业求解器许可的 Gurobi。OR-Tools 的传递依赖 absl-py、protobuf、immutabledict 同列于上表。NumPy 等包包含多个第三方组件，须一并保留其随包完整许可文本。

前端既有 Vue、Vue Router、Lucide、Vite、TypeScript、Playwright 版本及许可见下面的安装包记录；本次未新增前端运行依赖。

| Frontend package | Version | License |
| --- | --- | --- |
| vue | 3.5.44 | MIT |
| vue-router | 4.6.4 | MIT |
| lucide-vue-next | 0.468.0 | ISC |
| vite | 7.3.7 | MIT |
| typescript | 5.9.3 | Apache-2.0 |
| @vitejs/plugin-vue | 6.0.10 | MIT |
| vue-tsc | 3.3.13 | MIT |
| @playwright/test | 1.64.1 | Apache-2.0 |
| playwright-core | 1.64.1 | Apache-2.0 |

本次路演页还随仓库保留了以下第三方内容：

| Content | Version | License | Notice |
| --- | --- | --- | --- |
| 路演模板与相关模板代码 | 本地模板版本 | AGPL-3.0 | [模板许可证](../frontend/pitch/LICENSE.template)；模板来源和改动范围见 [路演展示](PITCH_DECK.md) |
| motion | 11.11.17 | MIT | [motion LICENSE](licenses/motion-11.11.17-LICENSE) |
| framer-motion | 11.11.17 | MIT | [framer-motion LICENSE](licenses/framer-motion-11.11.17-LICENSE) |

路演页的 `motion.min.js` 与模板相关文件按上表保留版权和许可文本。仓库没有据此自动选择整体开源许可证；如果公开发布，团队仍需单独决定项目自身的许可证，并确保其与 AGPL 模板部分的分发方式一致。

景区日程同步新增的 PostgreSQL 驱动也保留随包许可：

| Package | Version | Declared license | Notice |
| --- | --- | --- | --- |
| psycopg | 3.3.6 | LGPL-3.0-only | [psycopg LICENSE](licenses/psycopg-LICENSE.txt) |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | [psycopg-binary LICENSE](licenses/psycopg-binary-LICENSE.txt) |

## 团队交接的数据处理依赖（2026-10-23）

补充安装清单见 `backend/requirements-data.lock`。源码包不内置这些二进制包；以下保留本机已安装数据依赖的许可通知。Windows额外使用tzdata 2025.2，其许可随安装包分发。

| Package | Version | License | Notice |
| --- | --- | --- | --- |
| PyYAML | 6.0.3 | MIT | [PyYAML-LICENSE](licenses/PyYAML-LICENSE) |
| beautifulsoup4 | 4.15.0 | MIT License | [beautifulsoup4-AUTHORS](licenses/beautifulsoup4-AUTHORS), [beautifulsoup4-LICENSE](licenses/beautifulsoup4-LICENSE) |
| soupsieve | 3.0.4 | MIT | [soupsieve-LICENSE.md](licenses/soupsieve-LICENSE.md) |
| openpyxl | 3.1.5 | MIT | Installed package metadata |
| et_xmlfile | 2.0.0 | MIT | [et_xmlfile-AUTHORS.txt](licenses/et_xmlfile-AUTHORS.txt) |
| pdfplumber | 0.11.10 | MIT License | [pdfplumber-LICENSE.txt](licenses/pdfplumber-LICENSE.txt) |
| pdfminer.six | 20260107 | MIT | [pdfminer.six-LICENSE](licenses/pdfminer.six-LICENSE) |
| charset-normalizer | 3.5.2 | MIT | [charset-normalizer-LICENSE](licenses/charset-normalizer-LICENSE) |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | [cryptography-LICENSE](licenses/cryptography-LICENSE), [cryptography-LICENSE.APACHE](licenses/cryptography-LICENSE.APACHE), [cryptography-LICENSE.BSD](licenses/cryptography-LICENSE.BSD) |
| cffi | 2.1.1 | MIT-0 | [cffi-LICENSE](licenses/cffi-LICENSE) |
| pycparser | 3.12 | BSD-3-Clause | [pycparser-LICENSE](licenses/pycparser-LICENSE) |
| pillow | 13.0.0 | MIT-CMU | [pillow-LICENSE](licenses/pillow-LICENSE) |
| pypdfium2 | 5.14.0 | BSD-3-Clause, Apache-2.0, dependency licenses | Installed package metadata |
