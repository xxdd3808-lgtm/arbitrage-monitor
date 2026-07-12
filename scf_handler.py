#!/usr/bin/env python3
"""腾讯云函数 SCF 入口 - 触发 GitHub Actions 执行套利机会扫描

仅做 HTTP POST 到 GitHub API (repository_dispatch)，所有业务逻辑由
GitHub Actions 中的 notify.py 执行。

所需环境变量：
  GITHUB_PAT  - GitHub Classic PAT (repo scope)，必填
  GITHUB_REPO - 仓库全名，默认 xxdd3808-lgtm/arbitrage-monitor
"""

import json
import os
import sys
import urllib.request

GITHUB_REPO = os.environ.get("GITHUB_REPO", "xxdd3808-lgtm/arbitrage-monitor")
GITHUB_PAT = os.environ.get("GITHUB_PAT", "")


def main_handler(event, context):
    """SCF 入口 - 向 GitHub API 发 repository_dispatch 触发 Actions"""
    if not GITHUB_PAT:
        msg = "[ERROR] GITHUB_PAT 环境变量未设置，无法触发 GitHub Actions"
        print(msg)
        return {"statusCode": 500, "body": msg}

    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    payload = json.dumps({"event_type": "scf-trigger"}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"token {GITHUB_PAT}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "SCF-arbitrage-notify",
        },
        method="POST",
    )

    # GitHub repository_dispatch 成功返回 204 No Content
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            print(f"[OK] GitHub repository_dispatch -> {status}")
            return {"statusCode": status, "body": "dispatched"}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        print(f"[ERROR] HTTP {e.code}: {body}")
        return {"statusCode": e.code, "body": f"HTTP {e.code}: {body}"}
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        return {"statusCode": 500, "body": str(e)}


if __name__ == "__main__":
    result = main_handler(None, None)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("statusCode") in (200, 204) else 1)
