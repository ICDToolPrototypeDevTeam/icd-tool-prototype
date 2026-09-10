# -*- coding: utf-8 -*-
"""打包（PyInstaller）环境启动入口。

双击 ICDTool.exe 后：启动 FastAPI 后端（默认 127.0.0.1:8000）+ 自动打开浏览器。
桌面版前端已内嵌于 exe 同级 static/ 目录，同源访问 /api/v4，无需跨域。
仅作为 PyInstaller 的入口脚本；Docker / 开发环境仍用 `uvicorn app.main:app`。
"""
import os
import threading
import webbrowser

import uvicorn

from app.main import app


def _open_browser(port: int) -> None:
    import time
    time.sleep(1.5)
    webbrowser.open(f'http://127.0.0.1:{port}')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8000'))
    threading.Thread(target=_open_browser, args=(port,), daemon=True).start()
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='info')
