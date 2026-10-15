# -*- coding: utf-8 -*-
"""代码版本历史（不需要安装 git）

  python versions.py save "说明"     有改动就保存一份快照到 versions/<时间>/（没改动就跳过）
  python versions.py list            列出最近 20 个版本，以及每次改了哪些文件
  python versions.py restore <版本>  退回到某个版本（退回前会自动先备份当前状态）

只备份代码和脚本（*.py *.bat *.vbs *.md requirements.txt），不备份密钥、数据、日志。
自动换仓每天运行前会自动保存一次，所以任何改动都能追溯、能撤销。
"""
import os
import sys
import json
import glob
import shutil
import hashlib
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
VDIR = os.path.join(HERE, "versions")
PATTERNS = ["*.py", "*.bat", "*.vbs", "*.md", "requirements.txt"]


def current_files() -> dict:
    out = {}
    for pat in PATTERNS:
        for p in glob.glob(os.path.join(HERE, pat)):
            with open(p, "rb") as f:
                out[os.path.basename(p)] = hashlib.sha256(f.read()).hexdigest()[:16]
    return dict(sorted(out.items()))


def snapshots() -> list:
    if not os.path.isdir(VDIR):
        return []
    res = []
    for d in sorted(os.listdir(VDIR)):
        m = os.path.join(VDIR, d, "manifest.json")
        if os.path.exists(m):
            with open(m, encoding="utf-8") as f:
                res.append(json.load(f))
    return res


def diff(old: dict, new: dict) -> str:
    added = [f for f in new if f not in old]
    removed = [f for f in old if f not in new]
    changed = [f for f in new if f in old and new[f] != old[f]]
    parts = [f"改 {', '.join(changed)}" if changed else "",
             f"新增 {', '.join(added)}" if added else "",
             f"删除 {', '.join(removed)}" if removed else ""]
    return "；".join(p for p in parts if p) or "无变化"


def save(msg="快照", quiet=False):
    files = current_files()
    snaps = snapshots()
    if snaps and snaps[-1]["files"] == files:
        if not quiet:
            print(f"代码没有变化，不需要保存（最新版本 {snaps[-1]['id']}）")
        return snaps[-1]["id"]
    vid = f"{dt.datetime.now():%Y%m%d_%H%M%S}"
    dest = os.path.join(VDIR, vid)
    os.makedirs(dest, exist_ok=True)
    for name in files:
        shutil.copy2(os.path.join(HERE, name), os.path.join(dest, name))
    manifest = {"id": vid, "message": msg, "files": files,
                "changes": diff(snaps[-1]["files"], files) if snaps else "第一个版本"}
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"已保存版本 {vid}：{msg}（{manifest['changes']}）")
    return vid


def list_versions(n=20):
    snaps = snapshots()
    if not snaps:
        return print("还没有任何版本")
    for s in snaps[-n:]:
        print(f"{s['id']}  {s['message']}  —  {s['changes']}")
    now = current_files()
    if now != snaps[-1]["files"]:
        print(f"（当前文件和最新版本相比：{diff(snaps[-1]['files'], now)}，尚未保存）")


def restore(vid):
    src = os.path.join(VDIR, vid)
    if not os.path.exists(os.path.join(src, "manifest.json")):
        return print(f"找不到版本 {vid}，用 list 查看可用版本")
    save(f"退回到 {vid} 之前的自动备份", quiet=True)
    with open(os.path.join(src, "manifest.json"), encoding="utf-8") as f:
        files = json.load(f)["files"]
    for name in files:
        shutil.copy2(os.path.join(src, name), os.path.join(HERE, name))
    extra = [f for f in current_files() if f not in files]
    print(f"已退回到版本 {vid}（恢复了 {len(files)} 个文件）")
    if extra:
        print(f"注意：这些文件是之后才新增的，没有删除：{', '.join(extra)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "save":
        save(" ".join(sys.argv[2:]) or "手动快照")
    elif cmd == "restore" and len(sys.argv) > 2:
        restore(sys.argv[2])
    else:
        list_versions()
