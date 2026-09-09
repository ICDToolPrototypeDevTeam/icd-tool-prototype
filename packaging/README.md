# 桌面打包（Windows 单目录版）

本目录用于把 ICD 工具原型（FastAPI 后端 + React 前端）打包为 Windows 桌面版（PyInstaller `--onedir` 单目录）。

## 产物形态

```
dist/ICDTool/
├── ICDTool.exe      # 主程序，双击启动（控制台窗口 + 自动开浏览器）
├── _internal/       # PyInstaller 运行时（解释器 + 依赖 + 资源）
├── static/          # 前端 build 产物（内嵌，同源访问）
├── .env             # 用户可编辑配置（API keys / USE_MOCK_LLM）
└── output/          # 运行时自动生成（分析输出落这里）
```

## 打包步骤

在项目根目录执行（需本机已装 Python 3.11 + Node.js）：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

脚本依次完成：前端 `npm run build` → 准备 `.venv-build` 并装依赖 → PyInstaller 打包 → 复制前端产物到 `static/` → 生成 `.env`。

## 使用

双击 `dist\ICDTool\ICDTool.exe`，浏览器自动打开 `http://127.0.0.1:8000`。

- 默认 `USE_MOCK_LLM=1` 可开箱即用（无需 API key）；要跑真实 LLM 需编辑 exe 同级 `.env` 填入 key。
- 关闭控制台窗口即停止服务；分析输出文件写入 exe 同级 `output/`。

## 兼容性说明

打包相关的路径改造均为「打包环境才生效」的条件逻辑（`sys.frozen` 检测 + `OUTPUT_DIR` 环境变量），
Docker Compose 部署与本地开发行为不变。改动点：

- `backend/app/v4/config.py`：新增 `_base_dir()` / `get_output_root()`，`_ENV_PATH` 改为基目录定位；
- `backend/app/api/v4/{coverage,jobs,outputs,completeness}.py`：output 路径改用 `get_output_root()`；
- `backend/app/main.py`：仅 frozen 时挂载 `static/` + SPA fallback。
