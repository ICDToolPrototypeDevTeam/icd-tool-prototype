# ADR-005 Windows 单目录桌面打包

| 项目 | 内容 |
| --- | --- |
| 状态 | **Accepted** |
| 生效范围 | 后端 + 前端 + 打包脚本 + 文档 |
| 提议日期 | 2026-09-09 |
| 关联 ADR | 无（新增能力，不改动 V4 业务链路） |

---

## 背景

ICD工具原型当前以 Docker Compose（FastAPI 后端 + React 前端）方式交付运行，部署方需具备 Docker 环境。为降低非技术用户的使用门槛，需提供一套「免 Docker、双击即用」的 Windows 交付形态：把后端 + 前端打包成单个 Windows 可执行程序，双击启动后端并自动打开浏览器。

关键约束：

1. 打包后的程序**不得破坏**现有 Docker Compose / 开发态启动方式；
2. 前端采用 react-router（`/`、`/correctness`、`/completeness` 三页），浏览器刷新非根路由时后端需回退到 `index.html`（SPA fallback）；
3. 后端多处用 `Path(__file__).resolve().parent...` 定位 `output/` 与 `.env`，打包后 `__file__` 指向 `_internal/` 内部临时目录，路径会失效；
4. 部分模块（`app.v4.profiles.*.hooks`、`uvicorn`）通过 `importlib.import_module` 动态导入，PyInstaller 静态分析会漏包；
5. 依赖中 `pydantic-core` 为 Rust 编译产物，跨 Python 版本（3.11 / 3.12）无通用 wheel，需按本机 Python 版本安装。

## 决策

### D1：采用 PyInstaller `--onedir` 单目录模式（非 `--onefile`）

输出 `dist/ICDTool/`（`ICDTool.exe` + `_internal/` + `static/` + `.env`），启动更快、可读可改；`--onefile` 每次启动解压到临时目录、启动慢且不利于排查，不采用。

### D2：浏览器式桌面形态（非原生 GUI 窗口）

打包入口 `packaging/run.py` 启动 uvicorn（`127.0.0.1:8000`，可用 `PORT` 覆盖）后延时打开默认浏览器；前端同源访问 `/api/v4`，无需跨域。不引入 PyWebView / Electron 等原生窗口方案，避免额外依赖与复杂度。

### D3：用 `sys.frozen` 区分打包态与 Docker / 开发态（向后兼容改造）

- `backend/app/v4/config.py` 新增 `_base_dir()` / `get_output_root()`：`sys.frozen` 时基目录 = exe 同级，否则回退到原 `backend/` 相对定位；`_ENV_PATH` 同理。
- `backend/app/api/v4/{outputs,coverage,jobs,completeness}.py` 的输出路径全部改用 `get_output_root()`。
- `backend/app/main.py` 仅在 `sys.frozen` 时挂载 `static/` 静态资源与 SPA fallback（`/{full_path:path}` catch-all 回退 `index.html`）。

非 frozen 环境（Docker / 开发）完全走原路径，行为字节不变。

### D4：打包脚本 `packaging/build.ps1` 分 5 步

前端 `npm run build` → 建 `.venv-build/` 并安装依赖（`pydantic` 锁定 `>=2.11,<2.13` 以匹配本机 Python 的 `pydantic-core` wheel）→ PyInstaller（`ICDTool.spec`）→ 拷贝前端产物到 `dist/ICDTool/static/` → 生成 `dist/ICDTool/.env`（`USE_MOCK_LLM=1`）。

`ICDTool.spec` 用 `SPECPATH` 定位相对路径；`datas` 收集 `prompts/*.md`、`profiles/*/config.yaml`、`synonyms.yaml`；`hiddenimports` 用 `collect_submodules('app.v4.profiles')` 与 `collect_submodules('uvicorn')` 覆盖动态导入。

### D5：移除 .doc→.docx 转换死代码

遍历确认代码未调用任何 `.doc` 转换逻辑（仅解析 `.docx` / `.xlsx`），打包不包含相关依赖与文件，符合「未用则不留」。

## 原因

- 单目录模式启动快、便于排查，且 `static/`、`.env`、`output/` 与 exe 同级，用户可直观看到产物。
- 浏览器形态复用现有 React 前端与 `/api/v4` 接口，改动最小、无新增 UI 技术栈。
- `sys.frozen` 分支 + `get_output_root()` 把「路径差异」收敛到单一函数，四个 API 模块统一替换，避免散落判断。
- 动态导入（profiles hooks / uvicorn）必须显式 `collect_submodules`，否则打包缺模块导致运行时 `ModuleNotFoundError`。

## 影响

- **后端**：`config.py` 新增 `_base_dir()` / `get_output_root()`；`main.py` 新增 frozen 分支；4 个 API 模块输出路径改用 `get_output_root()`。非 frozen 行为不变。
- **前端**：无改动（本就使用相对 `/api/v4` 路径）。
- **新增目录**：`packaging/`（`ICDTool.spec` / `build.ps1` / `run.py` / `README.md`）。
- **构建产物**：`dist/ICDTool/`（`.gitignore` 已忽略 `dist/` 与 `.venv-build/`）。
- **文档**：`CHANGELOG.md`、`docs/development/development-log.md` 记录；新增本 ADR。

## 替代方案

| 方案 | 不选择理由 |
| --- | --- |
| `--onefile` 单文件 | 每次启动解压到临时目录，启动慢、难以排查、`output/` 路径易错 |
| 原生 GUI 窗口（PyWebView / Electron） | 引入额外依赖与复杂度，浏览器形态已满足需求 |
| 前端改为打包时注入 `baseURL` / 跨域方案 | 前端已用相对 `/api/v4`，同源内嵌天然免跨域，无需改动 |
| 不区分 frozen，直接改死 `output/` 路径 | 破坏 Docker / 开发态，违背向后兼容约束 |

## 状态

**Accepted** —— 已通过 Windows 单目录版本验证（启动、页面刷新、正向 / 反向、报告生成与下载均通过；Docker Compose 回归因本机网络无法访问 Docker Hub 暂未完成，待网络恢复后补验）。
