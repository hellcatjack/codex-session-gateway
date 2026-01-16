# 安全与敏感信息规范

## 绝不提交的内容
- `.env`、`.env.*`（仅允许提交 `.env.example`）
- 任何真实的 API Key / Token / 密码 / 私钥
- 运行期产物（日志、数据库、会话 JSONL、临时文件）

## 配置与本地环境约定
- 本项目通过 `.env` 提供真实配置，`.env.example` 仅保留占位符。
- Telegram Bot Token 仅存放于本地环境变量或 `.env` 文件。
- `data/`、`logs/`、`.pytest_cache/`、`.venv/` 等运行目录必须保持忽略状态。

## 日志与输出规范
- 日志不得输出 Token、密码、私钥、授权头等敏感信息。
- 如果需要排查问题，优先输出脱敏后的摘要信息。

## 审计与自查
- 提交前建议运行：
  - `rg -n "(?i)(api[_-]?key|secret|token|password|passwd|bearer|authorization|client_secret|private_key|BEGIN (RSA|EC|OPENSSH) PRIVATE KEY)"`
  - `rg -n "(AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|xox[baprs]-|ghp_[A-Za-z0-9]{20,}|-----BEGIN|PRIVATE KEY-----)"`
- 发现敏感信息请立即撤销提交并更换密钥。
