Coder Agent — 使用指南
======================

本目录是自包含发布包，客户机器无需安装 Python/Node 等任何环境。

一、目录内容
------------
Linux（./release.sh 生成）:
  coder-agent                       GUI 启动脚本（自动带 --no-sandbox，直接运行它）
  coder-agent-<ver>.AppImage        GUI 客户端（自包含）
  coder-agent-<ver>-linux/          GUI 解包版（无 FUSE 时用里面的 coder-agent）
  coder-agent-server-<ver>.linux    独立 server（仅远程部署需要）
  coder-agent-server.sh             server 后台启动脚本（同上）
  coder-agent-server.service        可选：systemd 服务模板
  coder-agent-server.token          启动脚本生成的连接 token（远程部署时出现）
  guide.txt                         本说明

Windows（Windows 上 release.bat 生成）:
  coder-agent-<ver>.exe            NSIS 安装包
  coder-agent-<ver>-win\           免安装版（直接运行里面的 coder-agent.exe）
  coder-agent-server-<ver>.win.exe 独立 server（仅远程部署需要）
  coder-agent-server.bat            server 后台启动脚本（同上）

二、单机使用（推荐，GUI 与代码同机）
------------------------------------
1. 启动 GUI:
   Linux:   chmod +x coder-agent-<ver>.AppImage && ./coder-agent
            （coder-agent 是启动脚本，已自动带 --no-sandbox）
            报 FUSE 错误: sudo apt-get install libfuse2
            或用解包版: cd coder-agent-<ver>-linux && ./coder-agent --no-sandbox
   Windows: 运行 coder-agent-<ver>.exe 安装，或免安装运行
            coder-agent-<ver>-win\coder-agent.exe
2. 首次启动等几秒（后端启动中）。
3. 工具栏选择项目目录。
4. 设置 -> Model: 配置 provider / 模型 / API Key（或 Ollama 等本机地址）。
5. 输入任务发送。危险操作会弹审批框。

三、远程部署（GUI 与 server 分机）
----------------------------------
1. server 机器（Linux）后台启动:
     /path/to/release/coder-agent-server.sh start
   - 默认端口 8931。不传 token 时脚本自动生成一个，
     打印在启动输出里，并存到同目录 coder-agent-server.token（权限 600）；
     下次 start 自动复用，token 不会变。
     想自己指定: start 8931 <token>（生成: openssl rand -hex 16）
   - 日志/状态: coder-agent-server.log / coder-agent-server.pid（同目录）
   - 停止: coder-agent-server.sh stop
   - 一台机器只能跑一个 server: 已有实例时再 start 会报错退出
   - 登录自动启动（放 bashrc）:
       echo '/path/to/release/coder-agent-server.sh start' >> ~/.bashrc
     （token 已存在 .token 文件里会自动复用；server 已在跑时登录
     只打印一行警告，不会起第二个）
   - 长期运行也可用 systemd: 改好 coder-agent-server.service 的
     User/ExecStart/token 后
       sudo cp coder-agent-server.service /etc/systemd/system/
       sudo systemctl enable --now coder-agent-server
2. server 机器上配置 LLM: 用同机 GUI 的 设置 -> Model 保存
   （配置存 server 机器的 ~/.coder_agent/config.toml）。
3. GUI 机器: 设置 -> Connection -> 模式 "remote"，填 host（server 的 IP）、
   port、token（启动脚本打印的那个，或 cat coder-agent-server.token 查看），
   Apply & reconnect。
4. 选项目: 工具栏点项目名 -> "+ Add project..."，直接输入 server 机器上的
   路径（如 ~/mygitee/tmp，也接受 user@host:~/path 写法），
   或点 Browse... 浏览 server 目录。

注意:
- 断网/休眠时 server 上的任务继续跑，GUI 重连后自动恢复。
- 一个 server 同时只服务一个 GUI（新连接顶掉旧连接）。
- 监听 0.0.0.0 必须设 token；防火墙放行 8931 端口。

四、数据位置
------------
  配置/记忆:   ~/.coder_agent            (Linux)
               %USERPROFILE%\.coder_agent (Windows)
  GUI 状态:    ~/.config/coder-agent-gui
               %APPDATA%/coder-agent-gui
  会话/快照:   <项目目录>/.agent

五、常见问题
------------
- AppImage 打不开: chmod +x；FUSE 错误装 libfuse2；仍不行用解包版。
- 一直 "Starting backend...": 从终端启动看 [backend] 日志，查安全软件拦截。
- 中文输入: 基于 Chromium，原生支持 ibus/fcitx。
