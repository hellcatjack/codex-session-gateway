# Sanitize Git History Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 清除历史提交中的敏感标识（用户 ID / resume_id 等），并安全地强制推送清理后的历史。

**Architecture:** 使用 Git 历史重写工具（优先 `git filter-repo`，不可用则退回 `git filter-branch`）对所有提交进行字符串替换；完成后验证历史中不再存在泄露内容，并强制推送到远端。

**Tech Stack:** Git, shell, ripgrep

### Task 1: 盘点历史中的泄露字符串

**Files:**
- No file changes

**Step 1: 在历史中搜索已知泄露内容**

Run: `git log -S "<LEAKED_USER_ID>" -S "<LEAKED_RESUME_ID>" --oneline`
Expected: 至少命中包含泄露内容的提交

**Step 2: 搜索 Telegram token 形态**

Run: `git rev-list --all | xargs -I{} git grep -nE "[0-9]{5,}:[A-Za-z0-9_-]{20,}|AA[A-Za-z0-9_-]{20,}" {}`
Expected: 无输出或记录到命中位置

### Task 2: 历史重写（字符串替换）

**Files:**
- No file changes (history rewrite only)

**Step 1: 准备替换规则文件**

Create: `/tmp/replace-text.txt`

```
<LEAKED_USER_ID>==>123456789
<LEAKED_RESUME_ID>==>resume-xxxx-xxxx
/app/stocklean==>/app/project-alpha
```

**Step 2: 优先使用 git filter-repo**

Run: `git filter-repo --replace-text /tmp/replace-text.txt`
Expected: 重写完成并更新 refs

**Step 3: 若 filter-repo 不可用，回退 filter-branch**

Run: `git filter-branch -f --tree-filter 'perl -pi -e "s/<LEAKED_USER_ID>/123456789/g; s/<LEAKED_RESUME_ID>/resume-xxxx-xxxx/g; s#/app/stocklean#/app/project-alpha#g" $(git ls-files)' -- --all`
Expected: 重写完成

### Task 3: 验证历史清理结果

**Files:**
- No file changes

**Step 1: 再次搜索泄露字符串**

Run: `git rev-list --all | xargs -I{} git grep -nE "<LEAKED_USER_ID>|<LEAKED_RESUME_ID>" {}`
Expected: 无输出

**Step 2: 再次搜索 token 形态**

Run: `git rev-list --all | xargs -I{} git grep -nE "[0-9]{5,}:[A-Za-z0-9_-]{20,}|AA[A-Za-z0-9_-]{20,}" {}`
Expected: 无输出

### Task 4: 强制推送到远端

**Files:**
- No file changes

**Step 1: 强制推送**

Run: `git push --force-with-lease origin main`
Expected: 推送成功

**Step 2: 记录提醒**

通知所有协作者需要重新拉取历史（`git fetch --all` + 重置分支）。
