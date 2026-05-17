# CLAUDE.md — sales_02

面向在本目录工作的 agent 的操作约束。人读的总览见 `README.md`，
打包计划/进度见 `packaging/PACKAGING_NOTES.md`，本文件只放规则。

## 这个仓库是什么

从 `sales_retro` 抽取出的「前端 + 薄后端」最小可运行子集 + Windows 一键打包。
不是开发主仓库，是交付副本。改动应保持这个定位。

## 硬约束（违反会出问题）

1. **`src/` 必须保持纯净源码。** `pip install` / PyInstaller 会生成
   `src/build/`、`src/*.egg-info/`、`packaging/.buildenv/`、`packaging/dist/`、
   `packaging/Output/`、`__pycache__/`。**任何安装/构建动作之后必须清理这些**，
   再向用户报告完成。`.gitignore` 已拦截，但本地树也要干净。
2. **不要在 `audio_sources.py` 顶层 `import sounddevice`。** 已惰性化
   （`_import_sounddevice()`），薄后端不得依赖 PortAudio。
   `src/tests/test_lazy_audio_import.py` 会守护此项——别绕过它。
3. **`sounddevice` 是可选 extra `[mic]`，不进基础依赖。** 薄后端 `pip install .`
   不得拉入它；CLI 麦克风路径才需要 `pip install ".[mic]"`。
4. **入口只有 `python -m sales_retro_agent.web`。** 没有 `cli.py`，
   `pyproject.toml` 的 `[project.scripts]` 已删，别加回指向不存在模块的入口。
5. **`src/`（可运行程序）与 `packaging/`（打包胶水）分离是刻意的。**
   `launcher.py` 等打包文件不要塞进 `src/`。
6. **这是 LLM-only 薄后端交付**：根路由 `/` 必须给 `backend.html`（不是
   rules-only 的 `index.html`，后者已改为重定向到 backend.html）；
   `default_config()` 的 `coachEngine` 固定 `"llm"`；`backend.html` 引擎下拉
   只留 DeepSeek LLM。别把规则引擎选项 / 纯前端页作为默认加回来。

## 标准变更工作流（本项目特化全局规则）

改任何非平凡内容后，全部做完再报完成：

1. 改代码。
2. 更新受影响文档面：`README.md`、`packaging/PACKAGING_NOTES.md`、本文件。
3. 加/改测试（`src/tests/`）。
4. 跑回归并确认绿：

```bash
cd src
uv venv .venv && . .venv/bin/activate
uv pip install ".[test]"          # 注意：不带 [mic]，借此验证薄后端不依赖 sounddevice
python -m pytest -q
# HTTP 冒烟：起服务后 curl /api/health /backend.html /app.js /api/default-config 应全 200
deactivate && cd .. && rm -rf src/.venv src/build src/*.egg-info && \
  find . -name __pycache__ -type d -exec rm -rf {} +   # 回归后立刻清理（见硬约束 1）
```

## 不要替用户做的事

- **不 `git push`、不打 tag、不创建 Release。** 这些是对外发布动作。
  Windows 构建流水线靠 push `v*` tag 触发，由用户执行。
- 不在仓库内放真实 key / `.env`。`web.py` 的 `/api/default-config` 会把
  服务端 key 明文回前端——keys 由终端用户在浏览器自填。

## 平台事实（别浪费时间试图绕过）

- PyInstaller 与 Inno Setup **不能在 Linux 跨平台构建** Windows 产物。
  本机只能做 Linux 等价验证；Windows 成品靠 `.github/workflows/windows-build.yml`
  在 windows runner 上出，或真人 Windows 跑 `packaging/build.ps1`。
- 浏览器 WebSocket 不能带自定义 header → 火山 ASR 无法纯前端直连（背景，
  见历史分析）。`backend.html` 的「实时录音」需安全上下文：`127.0.0.1`/HTTPS 可用，
  裸 `http://公网IP` 会被浏览器拒。
