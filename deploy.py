#!/usr/bin/env python3
"""一键部署到腾讯云函数 SCF

SCF 仅作为轻量触发器（~50 行）：HTTP POST 到 GitHub API 触发 repository_dispatch。
所有业务逻辑由 GitHub Actions 中的 notify.py 执行，SCF 不需要打包 akshare 等重依赖。

前置条件：
  - tccli 已配置（~/.tccli/default.credential）
  - 环境变量 GITHUB_PAT 已设置（GitHub Classic PAT, repo scope）

用法：
  GITHUB_PAT=ghp_xxx python3 deploy.py
"""

import base64
import json
import os
import sys
import time
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
FUNCTION_NAME = "arbitrage-notify"
REGION = "ap-shanghai"  # 和 dingshi-renwu 同区域
HANDLER = "scf_handler.main_handler"
RUNTIME = "Python3.7"   # Python 3.6 已 EOL，SCF 推荐 3.7+
TIMEOUT = 30            # 秒，仅做一次 HTTP POST，30 秒足够
MEMORY_SIZE = 128       # MB，触发器内存占用极小


def read_creds():
    """从 tccli 配置读取永久凭证"""
    cred_file = Path.home() / ".tccli" / "default.credential"
    if not cred_file.exists():
        print("[ERROR] 未找到 tccli 凭证，请先运行 `tccli configure`")
        sys.exit(1)
    with open(cred_file) as f:
        return json.load(f)


def make_client():
    """构造 SCF client"""
    from tencentcloud.common import credential
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.scf.v20180416 import scf_client

    creds = read_creds()
    cred = credential.Credential(creds["secretId"], creds["secretKey"])
    http = HttpProfile(endpoint="scf.tencentcloudapi.com")
    profile = ClientProfile()
    profile.httpProfile = http
    return scf_client.ScfClient(cred, REGION, profile)


def package_code():
    """打包 scf_handler.py 为 zip（仅几 KB）"""
    print("\n[1/3] 打包代码...")
    src = SCRIPT_DIR / "scf_handler.py"
    if not src.exists():
        print(f"[ERROR] {src} 不存在")
        sys.exit(1)
    zip_path = SCRIPT_DIR / "scf_deploy.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(src, "scf_handler.py")
    size_kb = zip_path.stat().st_size / 1024
    print(f"  打包完成: {zip_path.name} ({size_kb:.1f} KB)")
    return zip_path


def env_vars():
    """构造环境变量列表"""
    github_pat = os.environ.get("GITHUB_PAT", "")
    if not github_pat:
        print("[ERROR] 环境变量 GITHUB_PAT 未设置")
        print("  需要 GitHub Classic PAT (repo scope) 用于触发 repository_dispatch")
        print("  生成路径: GitHub -> Settings -> Developer settings -> Personal access tokens -> Tokens (classic)")
        sys.exit(1)

    return [
        {"Key": "GITHUB_PAT", "Value": github_pat},
        {"Key": "GITHUB_REPO", "Value": "xxdd3808-lgtm/arbitrage-monitor"},
    ]


def _call_with_retry(fn, description, max_retries=5, delay=5):
    """调用 SCF API，对 Updating 状态冲突自动重试"""
    last_err = None
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            msg = str(e)
            if "Updating" in msg and "无法进行此操作" in msg:
                print(f"  [WAIT] {description} - 函数更新中，{delay}秒后重试 ({attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            raise
    raise last_err


def deploy_function(zip_path):
    """部署或更新 SCF 函数"""
    print("\n[2/3] 部署 SCF 函数...")
    from tencentcloud.scf.v20180416 import models

    client = make_client()
    with open(zip_path, "rb") as f:
        zip_b64 = base64.b64encode(f.read()).decode()

    variables = env_vars()

    # 检查函数是否已存在
    try:
        req = models.GetFunctionRequest()
        req.FunctionName = FUNCTION_NAME
        client.GetFunction(req)
        exists = True
    except Exception as e:
        if "ResourceNotFound" in str(e) or "FunctionName" in str(e):
            exists = False
        else:
            print(f"[ERROR] 检查函数存在性失败: {e}")
            sys.exit(1)

    if exists:
        print(f"  函数 {FUNCTION_NAME} 已存在，更新代码...")
        req = models.UpdateFunctionCodeRequest()
        req.FunctionName = FUNCTION_NAME
        req.Handler = HANDLER
        req.ZipFile = zip_b64
        client.UpdateFunctionCode(req)

        print("  更新函数配置...")
        time.sleep(3)

        def _update_config():
            req = models.UpdateFunctionConfigurationRequest()
            req.FunctionName = FUNCTION_NAME
            req.Timeout = TIMEOUT
            req.MemorySize = MEMORY_SIZE
            req.Runtime = RUNTIME
            req.Environment = {"Variables": variables}
            client.UpdateFunctionConfiguration(req)

        _call_with_retry(_update_config, "UpdateFunctionConfiguration")
        print("  函数更新完成")
    else:
        print(f"  创建新函数 {FUNCTION_NAME}...")
        req = models.CreateFunctionRequest()
        req.FunctionName = FUNCTION_NAME
        req.Runtime = RUNTIME
        req.Handler = HANDLER
        req.Timeout = TIMEOUT
        req.MemorySize = MEMORY_SIZE
        req.Description = "套利机会扫描（轻量触发器）"
        req.Code = {"ZipFile": zip_b64}
        req.Environment = {"Variables": variables}
        client.CreateFunction(req)
        print("  函数创建完成")


def setup_trigger():
    """配置定时触发器"""
    print("\n[3/3] 配置定时触发器...")
    from tencentcloud.scf.v20180416 import models

    client = make_client()
    trigger_name = "daily-cron"

    # 删除已有触发器（如果存在）
    try:
        req = models.DeleteTriggerRequest()
        req.FunctionName = FUNCTION_NAME
        req.TriggerName = trigger_name
        req.Type = "timer"
        client.DeleteTrigger(req)
        print(f"  已删除旧触发器 {trigger_name}")
    except Exception:
        pass

    # SCF timer trigger: TriggerDesc 直接传 cron 表达式
    # 7 字段 cron 按北京时间解读：秒 分 时 日 月 周 年
    # 0 0 10 * * * * = 北京时间每天 10:00:00
    req = models.CreateTriggerRequest()
    req.FunctionName = FUNCTION_NAME
    req.TriggerName = trigger_name
    req.Type = "timer"
    req.TriggerDesc = "0 0 10 * * * *"
    req.Enable = "OPEN"
    client.CreateTrigger(req)
    print(f"  触发器创建成功: 每天北京时间 10:00")


def main():
    print("=" * 60)
    print("腾讯云函数 SCF 部署 - 套利机会扫描（轻量触发器）")
    print("=" * 60)
    print(f"区域: {REGION}")
    print(f"函数名: {FUNCTION_NAME}")
    print(f"运行时: {RUNTIME}")
    print(f"定时: 每天北京时间 10:00")

    zip_path = package_code()
    deploy_function(zip_path)
    setup_trigger()

    print("\n" + "=" * 60)
    print("部署完成！")
    print(f"  - SCF 每天北京时间 10:00 触发")
    print(f"  - SCF POST repository_dispatch 到 GitHub API")
    print(f"  - GitHub Actions 执行 notify.py 扫描+推送")
    print(f"\n手动测试:")
    print(f"  tccli scf Invoke --FunctionName {FUNCTION_NAME} --region {REGION}")
    print("=" * 60)


if __name__ == "__main__":
    main()
