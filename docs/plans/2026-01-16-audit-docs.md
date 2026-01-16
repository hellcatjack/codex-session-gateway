# Audit + Documentation Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 添加结构化安全审计文档与安全说明，并完善 README 链接与说明，避免敏感信息泄露风险。

**Architecture:** 在 `docs/audits/` 产出一次性审计报告，在 `docs/security.md` 固化敏感信息与配置规范，同时更新 `README.md` 链接与操作说明。无需改动核心逻辑代码。

**Tech Stack:** Markdown 文档；`rg`/`git` 命令辅助审计。

### Task 1: 准备审计目录与安全文档骨架

**Files:**
- Create: `docs/audits/2026-01-16-security-audit.md`
- Create: `docs/security.md`
- Modify: `README.md`

**Step 1: 编写审计报告骨架**

```markdown
# 2026-01-16 安全审计报告

## 目标

## 审计范围

## 审计方法

## 发现与结论

## 建议与下一步
```

**Step 2: 编写安全与敏感信息规范文档骨架**

```markdown
# 安全与敏感信息规范

## 绝不提交的内容

## 配置与本地环境约定

## 日志与输出规范

## 审计与自查
```

**Step 3: 更新 README 链接与说明**

在 README 增加“安全与审计”章节，链接 `docs/security.md` 与 `docs/audits/2026-01-16-security-audit.md`。

**Step 4: 记录修改并提交**

```bash
git add docs/security.md docs/audits/2026-01-16-security-audit.md README.md
git commit -m "docs: add security guidance and audit report"
```

### Task 2: 填写审计结果与具体发现

**Files:**
- Modify: `docs/audits/2026-01-16-security-audit.md`
- Modify: `docs/security.md`

**Step 1: 扫描常见敏感信息模式**

Run: `rg -n "(?i)(api[_-]?key|secret|token|password|passwd|bearer|authorization|client_secret|private_key|BEGIN (RSA|EC|OPENSSH) PRIVATE KEY)"`

Expected: 仅出现测试或示例文本；不得出现真实密钥。

**Step 2: 扫描典型密钥前缀**

Run: `rg -n "(AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|xox[baprs]-|ghp_[A-Za-z0-9]{20,}|-----BEGIN|PRIVATE KEY-----)"`

Expected: 无匹配。

**Step 3: 填写审计报告结论**

- 记录扫描结果
- 记录 `.env` 的风险提示与使用规范
- 记录 `.env.example` 示例合法性

**Step 4: 强化安全规范**

- 增加禁止提交 `.env`/日志/会话数据说明
- 说明日志输出不得包含 token/密钥

**Step 5: 提交**

```bash
git add docs/audits/2026-01-16-security-audit.md docs/security.md
git commit -m "docs: fill audit results and security rules"
```

### Task 3: 验证与收尾

**Files:**
- Modify: `README.md`

**Step 1: 验证 README 说明完整**

检查运行说明是否包含安全提示与审计入口。

**Step 2: 运行测试确认无副作用**

Run: `pytest`
Expected: 通过

**Step 3: 最终提交**

```bash
git add README.md
git commit -m "docs: link security docs"
```
