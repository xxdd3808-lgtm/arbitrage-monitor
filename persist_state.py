#!/usr/bin/env python3
"""通过 GitHub API 持久化 state.json 和 feedback.json

替代 git push，绕过网络超时问题（参考 dingshi-renwu 实现）。
并发安全：先拉取远程最新版本，合并本地变更后 PUT。

环境变量：
  GH_TOKEN          - GitHub token (默认使用 GITHUB_TOKEN)
  GITHUB_REPOSITORY - 仓库全名（GitHub Actions 自动设置）
  GITHUB_REF_NAME   - 分支名（GitHub Actions 自动设置）
"""

import base64
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone


# 需要持久化的文件列表
PERSIST_FILES = ["state.json", "feedback.json"]


def persist_one(filename, token, repo, branch):
    """持久化单个文件"""
    api = f"https://api.github.com/repos/{repo}/contents/{filename}"

    # 1. 拉取远程 sha + content
    remote_sha = None
    remote_content = ""
    req = urllib.request.Request(
        api + f"?ref={branch}",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "arbitrage-monitor-bot",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            remote = json.load(r)
        remote_sha = remote.get("sha")
        if remote.get("content"):
            remote_content = base64.b64decode(remote["content"]).decode("utf-8")
        print(f"[INFO] {filename} 远程 sha={remote_sha[:8] if remote_sha else 'None'}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[WARN] {filename} 远程不存在，将创建")
        else:
            body = e.read().decode("utf-8", errors="ignore")[:200]
            print(f"[ERROR] {filename} 拉取失败 HTTP {e.code}: {body}")
            return False
    except Exception as e:
        print(f"[ERROR] {filename} 拉取异常: {e}")
        return False

    # 2. 读取本地
    if not os.path.exists(filename):
        print(f"[INFO] {filename} 本地不存在，跳过")
        return True
    with open(filename, "r", encoding="utf-8") as f:
        local_content = f.read()

    # 3. 合并远程新增条目（仅对 JSON 文件做 dict 合并）
    if remote_sha and remote_content:
        try:
            remote_state = json.loads(remote_content)
            local_state = json.loads(local_content)
            if isinstance(remote_state, dict) and isinstance(local_state, dict):
                merged = dict(remote_state)
                for k, v in local_state.items():
                    if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
                        merged[k] = {**merged[k], **v}
                    else:
                        merged[k] = v
                new_content = json.dumps(merged, ensure_ascii=False, indent=2)
                if new_content != local_content:
                    print(f"[INFO] {filename} 检测到远程更新，已合并")
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    local_content = new_content
        except Exception as e:
            print(f"[WARN] {filename} 合并失败，使用本地版本: {e}")

    # 4. 如果内容相同，跳过
    if remote_content.strip() == local_content.strip():
        print(f"[INFO] {filename} 内容无变化，跳过 PUT")
        return True

    # 5. PUT 更新
    payload = json.dumps({
        "message": f"update {filename} {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "content": base64.b64encode(local_content.encode("utf-8")).decode(),
        "branch": branch,
        "sha": remote_sha if remote_sha else None,
    }).encode("utf-8")
    req = urllib.request.Request(
        api,
        data=payload,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "arbitrage-monitor-bot",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"[OK] {filename} 已提交: HTTP {r.status}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        print(f"[ERROR] {filename} 提交失败 HTTP {e.code}: {body}")
        return False
    except Exception as e:
        print(f"[ERROR] {filename} 提交异常: {e}")
        return False


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "main")

    if not token:
        print("[ERROR] GH_TOKEN / GITHUB_TOKEN 未设置")
        sys.exit(1)
    if not repo:
        print("[ERROR] GITHUB_REPOSITORY 未设置")
        sys.exit(1)

    ok_all = True
    for filename in PERSIST_FILES:
        if not os.path.exists(filename):
            continue
        ok = persist_one(filename, token, repo, branch)
        if not ok:
            ok_all = False

    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
