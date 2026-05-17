# 打包笔记 — Windows 本机一键安装

目标：Windows 用户、**无 Python**，双击即装即用本机版（前端 + 薄后端）。
本文件是计划与已知坑记录。

> **进度：§6 第 1、2 步已完成**——见各节内 `[DONE]` 标注。

## 本目录文件

| 文件 | 作用 |
|---|---|
| `launcher.py` | 冻结应用入口：绑 127.0.0.1、输出落用户可写目录、起服务后开浏览器 |
| `sales_retro.spec` | PyInstaller one-folder 配置（datas=web_static，excludes=sounddevice 等）|
| `installer.iss` | Inno Setup 脚本：onedir → 单个 `setup.exe`，**按用户安装**（免 UAC）+ 开始菜单/卸载项 |
| `build.ps1` | **Windows 构建**（PyInstaller→自动调 ISCC 出 setup.exe，须在 Windows 上跑）|
| `build.sh` | Linux/macOS 构建 + spec 交叉验证（非 Windows 交付物）|
| `PACKAGING_NOTES.md` | 本文件 |
| `../.github/workflows/windows-build.yml` | GitHub Actions：打 `v*` tag → windows runner 自动出 setup.exe |

## 发布流程（§6 步 4）

```bash
git tag v0.1.0 && git push origin v0.1.0   # 仅打 tag 触发；普通 push 不构建
```

CI 在 `windows-latest` 上：装 Python+Inno Setup → 跑 `build.ps1` → §0 HTTP 冒烟
+ WAV 解码回归 → 上传 `SalesRetro-Setup.exe` artifact，并附到该 tag 的 Release。
手动兜底：仓库 Actions 页 `workflow_dispatch` 可手动触发（不自动跑、不耗额度）。

---

## 0. 已验证状态（冒烟测试，环境 WSL/Linux）

- `cd src && uv pip install .` 依赖安装成功（numpy/openai/soundfile/websockets 等）。
- `python -m sales_retro_agent.web --host 127.0.0.1 --port <port>` 正常启动。
- 已验证：`/api/health`、`/api/default-config`、`/backend.html`、`/app.js` 均 200/正常。
- 未验证（需真实凭证，与代码完整性无关）：火山 ASR 转写（key+音频）、DeepSeek LLM（key）。

**§6 第 2 步交叉验证（Linux PyInstaller onedir，等价验证 spec/launcher）：**

- 构建成功（onedir，~75 MB）。`web_static` 已打包；bundle 内**无 sounddevice/PortAudio**
  （§6-1 解耦在打包链路成立）。
- 跑冻结二进制：launcher 选 8765、在 `~/.local/share/SalesRetro` 建数据目录、
  `web.run` 启动；`/api/health`·`/backend.html`·`/app.js`·`/api/default-config` 全 200。
- §4.1 mp3 实测：soundfile wheel 自带 **libsndfile 1.2.2**（支持 MP3）；真实 90min
  mp3 经 `iter_file_pcm_chunks` 解出 ~6818s 16k 单声道 PCM，与 wav 对照组完全一致。
- 仍待 Windows 实测：在干净 Windows（无 Python）上跑 `build.ps1` 产物，复测上述项
  （soundfile 的 Windows wheel 同样自带 libsndfile 1.2.x，预期一致）。

---

## 1. 打包前应做的代码解耦（重要，但当前未做）

### 1.1 sounddevice / PortAudio 强耦合 — `[DONE]`

> 已落地：`audio_sources.py` 顶部去掉 `import sounddevice`，新增
> `_import_sounddevice()` 惰性导入，仅在 `list_input_devices()` /
> `iter_microphone_pcm_chunks()` 内按需 import。`sounddevice` 已从
> `pyproject.toml` 基础依赖移至可选 extra `mic`。回归测试
> `src/tests/test_lazy_audio_import.py`（3 passed）+ HTTP 冒烟（health/
> backend.html/app.js/default-config 全 200，base venv 无 sounddevice）已验证。
> 下面是原始问题记录，留作背景。

- 现象：`audio_sources.py` 顶部 **无条件 `import sounddevice as sd`**。
- 事实：薄后端 `web.py` 全程**不使用** sounddevice。它只调 `iter_file_pcm_chunks`
  （走 numpy + soundfile）。`sounddevice` 仅被 `iter_microphone_pcm_chunks` 使用，
  那是 CLI 麦克风采集路径，薄后端不触发。
- 后果（不解耦的话）：
  1. 任何环境跑薄后端都被迫安装系统级 PortAudio（本机冒烟测试就因此先失败，
     装了 `libportaudio2` 才通过）。
  2. Windows 一键包要无谓多打包 PortAudio DLL，增大体积 + 抬高杀毒误报面。
- 建议改法（打包前做）：把 `import sounddevice` 改为**惰性导入**——移到
  `list_input_devices()` / `iter_microphone_pcm_chunks()` 函数体内部按需 import，
  模块顶部不再硬依赖。这样薄后端打包可完全不带 PortAudio。
- 风险：极低。仅影响 CLI 麦克风路径的 import 时机，不改其行为。

### 1.2 pyproject 的悬空入口 — `[DONE]`

> 已落地：`src/pyproject.toml` 删除 `[project.scripts] sales-retro` 段。
> 下面是原始记录。

- `src/pyproject.toml` 有 `[project.scripts] sales-retro = "sales_retro_agent.cli:main"`，
  但 `cli.py` 未复制进本副本。
- 影响：不影响 `python -m sales_retro_agent.web`；仅 `sales-retro` 命令不可用。
- 打包时建议：删掉该 `[project.scripts]` 段，避免安装器/构建工具报悬空入口。

---

## 2. 推荐打包方案

**PyInstaller（one-folder）+ Inno Setup 安装器 + 极小启动器入口。**

理由：one-folder 比 one-file 杀毒误报低、启动快；Inno Setup 包成单个 setup.exe，
用户感知仍是「一个安装包 + 开始菜单图标」，全程不碰命令行、不装 Python。

需新增（均为打包文件，放本 `packaging/` 目录，不改 `src/` 业务逻辑）：

1. **启动器**（约 15 行）：绑定 `127.0.0.1:8765` → 起 `web.py` server →
   `webbrowser.open("http://127.0.0.1:8765/backend.html")`。
   绑 loopback 而非 `0.0.0.0`：不触发 Windows 防火墙弹窗，且天然仅本机可访问。
2. **PyInstaller `.spec`**：声明 `sales_retro_agent/web_static` 为 datas；
   numpy / soundfile 用其 PyInstaller hook；解耦 1.1 后**不再需要** PortAudio。
3. **Inno Setup 脚本**：安装目录、开始菜单快捷方式、卸载项。
4. 可选：GitHub Actions **windows runner** 构建流水线产出 setup.exe
   （PyInstaller 不能跨平台构建，必须在 Windows 上打）。

---

## 3. 需打包的依赖与原生库

- 纯 Python：openai、websockets、python-dotenv（hook 无障碍）。
- numpy：PyInstaller 有 hook。
- soundfile → **libsndfile.dll**：PyInstaller 有 hook。
- sounddevice / PortAudio：**1.1 解耦已完成 → 薄后端打包无需 PortAudio**。

---

## 4. 已知风险（按优先级）

1. ~~**mp3 上传解码** 取决于打进去的 libsndfile 版本~~ **[DONE/Linux]**：soundfile
   wheel 自带 libsndfile 1.2.2，mp3 已实测通过（见 §0）。仍需在 Windows 包上复测一次。
2. **杀毒误报**：one-folder + 安装器可显著缓解；进一步需代码签名证书（要花钱，
   测试阶段可不做）。
3. **必须在 Windows 上构建**：建议 GH Actions windows job，避免本地环境漂移。
4. localhost 是安全上下文 → 本机版 `backend.html` 的「实时录音」tab **可用**
   （这是本机部署相对公网 IP 部署的优势，公网 IP 非 HTTPS 会被浏览器拒录音）。

---

## 5. 安全注意

- **不要在随包 `.env` 配真实 key**：`web.py` 的 `/api/default-config` 会把
  `DEEPSEEK_API_KEY` 等**明文**回给前端（`sanitize_config` 只对落盘日志脱敏，
  不管这个接口）。让用户在 `backend.html` 的 key 弹窗里自行填写。
  单用户本机 + 绑 127.0.0.1 时风险可控，但仍以不预置为准。

---

## 6. 落地顺序建议

1. ~~先做 §1.1 sounddevice 惰性导入解耦 + §1.2 删悬空 script 入口。~~ **[DONE]**
2. ~~写启动器 + `.spec`，出 one-folder 包，跑 §0 冒烟 + §4.1 mp3 实测。~~ **[DONE]**
   （Linux 交叉验证完成；Windows 产物待在 Windows 上跑 `build.ps1` 复测）
3. ~~套 Inno Setup 出 setup.exe~~ **[DONE/脚本]**：`installer.iss` 已写好，
   `build.ps1` 已串联 ISCC。**待在 Windows 上**：跑 `build.ps1` 出 `setup.exe`，
   并在干净 Windows（无 Python）上端到端验收（装→开始菜单图标→自动开 backend.html
   →§0 那组冒烟 + §4.1 mp3 复测→卸载干净）。  ← 当前卡在「需 Windows 环境」
4. ~~加 GH Actions windows 构建~~ **[DONE/流水线]**：`.github/workflows/windows-build.yml`
   已写好，打 `v*` tag 即在 windows runner 出 setup.exe + 自动 §0 冒烟。
   **待执行**：由你 `git push` 到 github.com（private 仓库即可）并打第一个 tag 触发；
   首个产物仍需一次真人 Windows 交互验收（装/图标/浏览器/卸载 + §4.1 mp3 复测）。
