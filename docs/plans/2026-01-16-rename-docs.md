# Codex Session Gateway Renaming Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将项目文档与模板服务文件统一为新名称“Codex Session Gateway”，并更新 systemd 示例命令。

**Architecture:** 仅修改文档与部署模板，无需更改核心代码。通过 README 与 `deploy/` 模板统一名称与服务名。

**Tech Stack:** Markdown 文档、systemd 单元文件。

### Task 1: 更新 README 项目名称与描述

**Files:**
- Modify: `README.md`

**Step 1: 更新标题与简介**

将标题从“Codex Telegram Shell”改为“Codex Session Gateway”，并在简介中强调“通过 Telegram 绑定 Session ID 收发 Codex 指令与结果”。

**Step 2: 更新 systemd 示例名称**

将 `codex-telegram-shell` 替换为 `codex-session-gateway`，并同步更新示例命令中的服务文件名。

**Step 3: 提交**

```bash
git add README.md
git commit -m "docs: rename project in README"
```

### Task 2: 更新 systemd 服务模板文件名与描述

**Files:**
- Move: `deploy/codex-telegram-shell.service` → `deploy/codex-session-gateway.service`
- Modify: `deploy/codex-session-gateway.service`

**Step 1: 更新 Description 与文件名**

把 Description 改为 `Codex Session Gateway`，文件名也替换为新名称。

**Step 2: 更新 README 引用**

确保 README 引用新的服务文件名。

**Step 3: 提交**

```bash
git add deploy/codex-session-gateway.service README.md
git commit -m "docs: rename systemd service template"
```

### Task 3: 验证与收尾

**Files:**
- Modify: `README.md`

**Step 1: 快速检查关键词引用**

Run: `rg -n "codex-telegram-shell|Codex Telegram Shell" README.md deploy`
Expected: 无匹配。

**Step 2: 运行测试确保无副作用**

Run: `pytest`
Expected: 通过。

**Step 3: 提交**

```bash
git add README.md
git commit -m "docs: finalize rename references"
```
