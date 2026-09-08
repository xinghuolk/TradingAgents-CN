# 快速开始

本文说明 `v1.0.0-preview` 的当前启动方式；返回[文档目录](../README.md)。

## 前置条件

准备 Python 3.11 和 Docker（含 Docker Compose v2）。本地运行前端还需要
Node.js 22 与 Yarn 1.22.22；具体开发环境说明见[开发指南](../development.md)。

## 本地运行

克隆仓库后，在根目录复制配置模板并安装依赖：

```bash
git clone https://github.com/hsliuping/TradingAgents-CN.git
cd TradingAgents-CN
cp .env.example .env
python -m pip install -e .
corepack enable
corepack prepare yarn@1.22.22 --activate
yarn --cwd frontend install --frozen-lockfile
```

在 `.env` 中完成必要配置后，分别启动后端、前端和终端界面：

```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
yarn --cwd frontend dev
python -m cli.main
```

也可以使用 Docker Compose 启动完整服务：

```bash
docker compose up -d
```

下一步请阅读[配置](configuration.md)、[部署](deployment.md)、[开发指南](../development.md)
和[故障排除](troubleshooting.md)。
