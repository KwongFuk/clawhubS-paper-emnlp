#!/usr/bin/env python3
"""
标注工具 Web 服务（含完整 Diff 视图）
用法: python3 app.py [--annotator 1|2] [--port 5050]
访问 http://localhost:5050
"""

import csv
import json
import os
import sys
import argparse
import difflib
import shutil
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

CSV_PATH = Path(__file__).parent / "annotation_sheet.csv"
JSONL_PATH = Path(__file__).parent.parent / "data" / "version_transitions_enriched.jsonl"
BACKUP_DIR = Path(__file__).parent / "annotation_backups"
BACKUP_DIR.mkdir(exist_ok=True)

ANNOTATOR_NUM = 1

# ── Data loading ──────────────────────────────────────────────────────────────

def load_rows():
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def save_rows(rows):
    if not rows: return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy(CSV_PATH, BACKUP_DIR / f"backup_{ts}.csv")
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

_PAIR_CACHE = {}
def get_pair(pair_id: str) -> dict:
    if pair_id not in _PAIR_CACHE:
        if JSONL_PATH.exists():
            with open(JSONL_PATH, encoding="utf-8") as f:
                for line in f:
                    p = json.loads(line)
                    _PAIR_CACHE[p["pair_id"]] = p
    return _PAIR_CACHE.get(pair_id, {})


# ── Diff generation ───────────────────────────────────────────────────────────

def make_diff_html(prev_text: str, curr_text: str, v_prev: str = "?", v_curr: str = "?", max_lines: int = 120) -> str:
    """Generate side-by-side HTML diff with highlighted additions/deletions."""
    prev_lines = (prev_text or "（旧版本无内容）").splitlines()[:max_lines]
    curr_lines = (curr_text or "（新版本无内容）").splitlines()[:max_lines]

    sm = difflib.SequenceMatcher(None, prev_lines, curr_lines, autojunk=False)
    opcodes = sm.get_opcodes()

    left_html, right_html = [], []

    def esc(s):
        return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for line in prev_lines[i1:i2]:
                left_html.append(f'<div class="dl eq">{esc(line) or "&nbsp;"}</div>')
                right_html.append(f'<div class="dl eq">{esc(line) or "&nbsp;"}</div>')
        elif tag == "replace":
            for line in prev_lines[i1:i2]:
                left_html.append(f'<div class="dl del">{esc(line) or "&nbsp;"}</div>')
            for line in curr_lines[j1:j2]:
                right_html.append(f'<div class="dl add">{esc(line) or "&nbsp;"}</div>')
            # Pad shorter side
            n_del, n_add = i2-i1, j2-j1
            for _ in range(max(n_del, n_add) - n_del):
                left_html.append('<div class="dl pad">&nbsp;</div>')
            for _ in range(max(n_del, n_add) - n_add):
                right_html.append('<div class="dl pad">&nbsp;</div>')
        elif tag == "delete":
            for line in prev_lines[i1:i2]:
                left_html.append(f'<div class="dl del">{esc(line) or "&nbsp;"}</div>')
                right_html.append('<div class="dl pad">&nbsp;</div>')
        elif tag == "insert":
            for line in curr_lines[j1:j2]:
                left_html.append('<div class="dl pad">&nbsp;</div>')
                right_html.append(f'<div class="dl add">{esc(line) or "&nbsp;"}</div>')

    if not left_html:
        left_html = ['<div class="dl eq">（无内容）</div>']
        right_html = ['<div class="dl eq">（无内容）</div>']

    truncated = ""
    if len(prev_lines) >= max_lines or len(curr_lines) >= max_lines:
        truncated = f'<div style="color:#f59e0b;font-size:12px;padding:6px 0">⚠️ 内容过长，仅显示前 {max_lines} 行</div>'

    left_body = "".join(left_html)
    right_body = "".join(right_html)
    v_prev_esc = v_prev.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    v_curr_esc = v_curr.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return (
        truncated +
        '<div class="diff-container">'
        '<div class="diff-pane">'
        '<div class="diff-header">📄 旧版本 &nbsp;<span class="v-badge">' + v_prev_esc + '</span></div>'
        '<div class="diff-body">' + left_body + '</div>'
        '</div>'
        '<div class="diff-pane">'
        '<div class="diff-header">📄 新版本 &nbsp;<span class="v-badge">' + v_curr_esc + '</span></div>'
        '<div class="diff-body">' + right_body + '</div>'
        '</div>'
        '</div>'
    )


# ── HTML builder ──────────────────────────────────────────────────────────────

def build_html(rows, idx):
    idx = max(0, min(idx, len(rows) - 1))
    row = rows[idx]
    total = len(rows)
    lbl_field = f"annotator_{ANNOTATOR_NUM}_label"
    rsn_field = f"annotator_{ANNOTATOR_NUM}_reason"
    done = sum(1 for r in rows if r.get(lbl_field,"").strip())
    progress_pct = int(done / total * 100)
    current_label = row.get(lbl_field, "")
    current_reason = row.get(rsn_field, "")

    # Load full text pair
    pair = get_pair(row["pair_id"])
    prev_text = pair.get("prev_skill_text", "")
    curr_text = pair.get("curr_skill_text", "")
    v_prev = row.get("v_prev", "?")
    v_curr = row.get("v_curr", "?")

    has_text = bool(prev_text or curr_text)

    # Diff
    if has_text:
        diff_html = make_diff_html(prev_text, curr_text, v_prev=v_prev, v_curr=v_curr)
    else:
        diff_html = '<div style="color:#64748b;padding:20px;text-align:center">⚠️ 本样本无完整 SKILL.md 文本，请根据摘要信息判断</div>'

    # Risk keywords
    kws_raw = row.get("new_risk_keywords", "")
    kws_html = ""
    fam_map = {
        "execution": ("🔴 执行/Shell", "#fca5a5"),
        "secret": ("🔑 凭证/密钥", "#fdba74"),
        "network": ("🌐 网络请求", "#93c5fd"),
        "file_system": ("📁 文件系统", "#6ee7b7"),
        "agentic": ("🤖 自主代理", "#c4b5fd"),
    }
    if kws_raw:
        for part in kws_raw.split(";"):
            part = part.strip()
            if ":" in part:
                fam, kws = part.split(":", 1)
                fam_display, fam_color = fam_map.get(fam.strip(), (fam.strip(), "#e5e7eb"))
                kws_html += f'<span class="kw-tag" style="background:{fam_color}">{fam_display}: <code>{kws.strip()}</code></span> '

    # Options
    def opt(val, emoji, zh, desc, color):
        checked = "checked" if current_label == val else ""
        sel = "selected" if current_label == val else ""
        return f"""
        <label class="option-card {sel}">
          <input type="radio" name="label" value="{val}" {checked}>
          <div class="opt-inner">
            <div class="opt-top"><span class="dot" style="background:{color}"></span><strong>{emoji} {zh}</strong></div>
            <div class="opt-desc">{desc}</div>
          </div>
        </label>"""

    opts = (
        opt("CONFIRMED","✅","确认：信任漂移","新版本加入高风险能力，changelog <strong>未充分披露</strong>","#22c55e") +
        opt("FALSE_POSITIVE","❌","假阳性","changelog 已充分描述变化，或新关键词在上下文中无害","#ef4444") +
        opt("UNCLEAR","❓","不确定","信息不足，或内容太复杂无法判断","#f59e0b")
    )

    # Navigation
    prev_unlabeled = next((i for i in range(idx-1,-1,-1) if not rows[i].get(lbl_field,"").strip()), None)
    next_unlabeled = next((i for i in range(idx+1,total) if not rows[i].get(lbl_field,"").strip()), None)
    nav = ""
    if idx > 0:         nav += f'<a href="/?idx={idx-1}" class="btn-nav">◀ 上一条</a>'
    if idx < total-1:   nav += f'<a href="/?idx={idx+1}" class="btn-nav">下一条 ▶</a>'
    if next_unlabeled is not None:
        nav += f'<a href="/?idx={next_unlabeled}" class="btn-jump">⏭ 下一个未标注</a>'

    auto_label_map = {
        "T1_silent_injection":"T1 静默能力注入","T2_credential_creep":"T2 凭证范围扩张",
        "T3a_doc_lagged":"T3a 文档滞后扩张","T3b_transparency_triggered":"T3b 平台审查触发",
        "T5_covert_agentic":"T5 隐性代理扩张","T6_malicious_marker":"⚠️ T6 恶意标记（已确认）","benign":"良性",
    }
    auto_labels = " | ".join(auto_label_map.get(p.strip(),p.strip())
                              for p in row.get("auto_label","").split("|") if p.strip())

    ces = float(row.get("ces_drift",0) or 0)
    sds = float(row.get("sds",1) or 1)
    ces_color = "#ef4444" if ces>0.2 else "#f59e0b" if ces>0 else "#22c55e"
    sds_color = "#ef4444" if sds<0.3 else "#f59e0b" if sds<0.6 else "#22c55e"

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TrustDrift 标注 — {row['sample_id']}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;font-size:14px}}
.header{{background:linear-gradient(135deg,#1e3a5f,#0f2040);padding:14px 24px;
         display:flex;align-items:center;justify-content:space-between;
         border-bottom:1px solid #1e40af;position:sticky;top:0;z-index:100}}
.header h1{{font-size:16px;color:#60a5fa;font-weight:700}}
.prog-wrap{{text-align:right}}
.progress-bar{{background:#1e293b;border-radius:8px;height:5px;width:180px;overflow:hidden;margin-left:auto}}
.progress-fill{{background:linear-gradient(90deg,#3b82f6,#8b5cf6);height:100%;width:{progress_pct}%}}
.prog-text{{color:#64748b;font-size:12px;margin-top:3px}}
.layout{{display:grid;grid-template-columns:1fr 420px;gap:0;min-height:calc(100vh - 53px)}}
/* LEFT: diff panel */
.diff-panel{{border-right:1px solid #1e293b;overflow-y:auto;padding:16px 20px}}
.diff-panel-title{{font-size:11px;font-weight:600;color:#64748b;text-transform:uppercase;
                   letter-spacing:.05em;margin-bottom:12px}}
.diff-container{{display:grid;grid-template-columns:1fr 1fr;gap:2px;font-family:'JetBrains Mono',monospace;font-size:12px;line-height:1.5}}
.diff-pane{{min-width:0}}
.diff-header{{background:#1e293b;padding:6px 10px;font-size:11px;color:#64748b;
              border-radius:6px 6px 0 0;border:1px solid #334155;border-bottom:none}}
.v-badge{{background:#334155;color:#94a3b8;border-radius:4px;padding:1px 6px;font-size:10px}}
.diff-body{{background:#0f172a;border:1px solid #334155;border-radius:0 0 6px 6px;
            overflow-x:auto;max-height:calc(100vh - 160px);overflow-y:auto}}
.dl{{padding:1px 8px;white-space:pre;min-height:20px}}
.dl.eq{{color:#94a3b8}}
.dl.add{{background:#14532d55;color:#86efac;border-left:3px solid #22c55e}}
.dl.del{{background:#7f1d1d55;color:#fca5a5;border-left:3px solid #ef4444}}
.dl.pad{{color:transparent;background:#0f172a33}}
/* RIGHT: annotation panel */
.ann-panel{{background:#0f172a;overflow-y:auto;padding:16px 18px;display:flex;flex-direction:column;gap:12px}}
.card{{background:#1e293b;border-radius:12px;padding:16px;border:1px solid #334155}}
.card-title{{font-size:11px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px}}
.skill-id{{font-size:15px;font-weight:700;color:#f8fafc;word-break:break-all}}
.vbadge{{display:inline-block;background:#0f172a;border:1px solid #334155;
          border-radius:5px;padding:2px 8px;font-size:11px;color:#94a3b8;margin:4px 0 8px}}
.auto-badge{{background:#1e3a5f;color:#93c5fd;border:1px solid #3b82f6;
             border-radius:5px;padding:2px 8px;font-size:11px;font-weight:500}}
.metrics{{display:flex;gap:8px;margin-top:8px}}
.metric{{background:#0f172a;border-radius:8px;padding:8px 12px;text-align:center;flex:1}}
.metric-val{{font-size:18px;font-weight:700}}
.metric-lbl{{font-size:10px;color:#64748b;margin-top:2px}}
.field-label{{font-size:11px;font-weight:600;color:#64748b;text-transform:uppercase;
              letter-spacing:.04em;margin-bottom:5px;margin-top:12px}}
.text-block{{background:#0f172a;border-radius:7px;padding:10px 12px;font-size:13px;
             line-height:1.65;color:#cbd5e1;border-left:3px solid #334155;
             max-height:130px;overflow-y:auto}}
.kw-tag{{display:inline-block;border-radius:5px;padding:3px 8px;font-size:12px;margin:2px;
          color:#1e293b;font-weight:500}}
.kw-tag code{{font-family:'JetBrains Mono',monospace;font-size:11px}}
/* Options */
.options-grid{{display:flex;flex-direction:column;gap:8px;margin-top:4px}}
.option-card{{background:#0f172a;border:2px solid #334155;border-radius:10px;
              padding:12px 14px;cursor:pointer;transition:border-color .15s}}
.option-card input{{display:none}}
.option-card.selected,.option-card:hover{{border-color:#3b82f6}}
.opt-inner{{pointer-events:none}}
.opt-top{{display:flex;align-items:center;gap:8px;margin-bottom:4px}}
.dot{{width:9px;height:9px;border-radius:50%;flex-shrink:0}}
.opt-top strong{{font-size:13px;color:#f8fafc}}
.opt-desc{{font-size:11px;color:#94a3b8;line-height:1.5;padding-left:17px}}
.reason-box{{width:100%;background:#0f172a;border:1px solid #334155;border-radius:8px;
             padding:10px;color:#e2e8f0;font-size:13px;margin-top:10px;
             min-height:65px;resize:vertical;font-family:'Inter',sans-serif}}
.reason-box:focus{{outline:none;border-color:#3b82f6}}
.btn-submit{{background:linear-gradient(135deg,#3b82f6,#8b5cf6);color:#fff;
             border:none;border-radius:9px;padding:11px;font-size:14px;font-weight:600;
             cursor:pointer;margin-top:10px;width:100%}}
.btn-submit:hover{{opacity:.9}}
.nav-row{{display:flex;gap:8px;flex-wrap:wrap}}
.btn-nav{{background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:7px;
           padding:7px 14px;text-decoration:none;font-size:13px}}
.btn-nav:hover{{background:#334155;color:#e2e8f0}}
.btn-jump{{background:#1e3a5f;color:#60a5fa;border:1px solid #3b82f6;
            border-radius:7px;padding:7px 14px;text-decoration:none;font-size:13px;font-weight:600}}
.btn-jump:hover{{background:#1e40af}}
.util-row{{display:flex;gap:8px}}
.no-text-msg{{color:#64748b;font-size:12px;padding:8px 0}}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>🔍 TrustDrift 标注工具 &nbsp;·&nbsp; 标注员 {ANNOTATOR_NUM}</h1>
    <div style="color:#64748b;font-size:12px">样本 {idx+1}/{total} &nbsp;|&nbsp; {row['sample_id']}</div>
  </div>
  <div class="prog-wrap">
    <div class="progress-bar"><div class="progress-fill"></div></div>
    <div class="prog-text">已完成 {done}/{total} ({progress_pct}%)</div>
  </div>
</div>

<div class="layout">

  <!-- LEFT: DIFF -->
  <div class="diff-panel">
    <div class="diff-panel-title">📋 SKILL.md 内容变化（旧版 vs 新版）</div>
    {diff_html}
    {'<div class="no-text-msg">① 红色行 = 旧版本删除的内容 &nbsp; ② 绿色行 = 新版本新增的内容 &nbsp; ③ 灰色行 = 未变化</div>' if has_text else ''}
  </div>

  <!-- RIGHT: ANNOTATION -->
  <div class="ann-panel">

    <!-- Skill meta -->
    <div class="card">
      <div class="card-title">📦 Skill 信息</div>
      <div class="skill-id">{row['skill_id']}</div>
      <span class="vbadge">v{v_prev} → v{v_curr}</span>
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        <span style="font-size:11px;color:#64748b">自动标签：</span>
        <span class="auto-badge">{auto_labels or '未分类'}</span>
      </div>
      <div class="metrics">
        <div class="metric"><div class="metric-val" style="color:{ces_color}">{ces:.3f}</div><div class="metric-lbl">CES 漂移</div></div>
        <div class="metric"><div class="metric-val" style="color:{sds_color}">{sds:.3f}</div><div class="metric-lbl">SDS 披露</div></div>
      </div>
    </div>

    <!-- Description + Changelog -->
    <div class="card">
      <div class="field-label">📝 Skill 描述（用户可见）</div>
      <div class="text-block">{row.get('description','（无）') or '（无）'}</div>
      <div class="field-label">📋 Changelog</div>
      <div class="text-block">{row.get('changelog','（无）') or '（无）'}</div>
      <div class="field-label">⚠️ 新增风险关键词</div>
      {kws_html if kws_html else '<span class="no-text-msg">无新增风险关键词</span>'}
    </div>

    <!-- Annotation form -->
    <div class="card">
      <div class="card-title">✏️ 标注（参考左侧 Diff）</div>
      <form method="POST" action="/annotate">
        <input type="hidden" name="idx" value="{idx}">
        <div class="options-grid">
          {opts}
        </div>
        <div class="field-label" style="margin-top:12px">💬 标注理由（必填）</div>
        <textarea class="reason-box" name="reason"
          placeholder="例：changelog 只写了'更新文档'，但左侧 Diff 可见新版本新增了 rm -rf 和 subprocess 调用，明显未披露...">{current_reason}</textarea>
        <button type="submit" class="btn-submit">💾 保存 → 下一条</button>
      </form>
    </div>

    <!-- Navigation -->
    <div class="card" style="padding:12px">
      <div class="nav-row">
        {nav}
      </div>
      <div class="util-row" style="margin-top:10px">
        <a href="/stats" class="btn-nav">📊 统计</a>
        <a href="/export" class="btn-nav">📥 导出 CSV</a>
      </div>
    </div>

  </div>
</div>
</body></html>"""


def build_stats_html(rows):
    total = len(rows)
    rows_out = []
    for n in [1, 2]:
        lbl = f"annotator_{n}_label"
        done = sum(1 for r in rows if r.get(lbl,"").strip())
        confirmed = sum(1 for r in rows if r.get(lbl)=="CONFIRMED")
        fp = sum(1 for r in rows if r.get(lbl)=="FALSE_POSITIVE")
        unclear = sum(1 for r in rows if r.get(lbl)=="UNCLEAR")
        rows_out.append(f"<tr><td>标注员 {n}</td><td>{done}/{total}</td><td style='color:#22c55e'>{confirmed}</td><td style='color:#ef4444'>{fp}</td><td style='color:#f59e0b'>{unclear}</td></tr>")

    both = sum(1 for r in rows if r.get("annotator_1_label","").strip() and r.get("annotator_2_label","").strip())
    agree = sum(1 for r in rows if r.get("annotator_1_label","").strip() and r.get("annotator_1_label")==r.get("annotator_2_label"))

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>标注统计</title>
<style>body{{font-family:Inter,sans-serif;background:#0f172a;color:#e2e8f0;padding:40px;max-width:700px;margin:0 auto}}
h1{{color:#60a5fa;margin-bottom:24px}}
table{{width:100%;border-collapse:collapse;background:#1e293b;border-radius:12px;overflow:hidden;margin-bottom:20px}}
th{{background:#334155;padding:12px;text-align:left;font-size:12px;color:#94a3b8}}
td{{padding:12px;border-bottom:1px solid #334155}}
.big{{font-size:32px;font-weight:700;color:#f8fafc}} .lbl{{font-size:11px;color:#64748b;margin-top:4px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0}}
.crd{{background:#1e293b;border-radius:10px;padding:14px;text-align:center}}
a{{color:#60a5fa}}</style></head><body>
<h1>📊 标注进度统计</h1>
<div class="grid">
  <div class="crd"><div class="big">{total}</div><div class="lbl">总样本</div></div>
  <div class="crd"><div class="big">{both}</div><div class="lbl">双人完成</div></div>
  <div class="crd"><div class="big">{f'{100*agree//max(both,1)}%' if both else 'N/A'}</div><div class="lbl">初步一致率</div></div>
</div>
<table><tr><th>标注员</th><th>已完成</th><th>确认</th><th>假阳性</th><th>不确定</th></tr>
{"".join(rows_out)}</table>
<p><a href="/">← 返回标注</a></p></body></html>"""


# ── HTTP handler ───────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/export":
            data = CSV_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=annotation_sheet.csv")
            self.end_headers()
            self.wfile.write(data)
            return

        rows = load_rows()

        if parsed.path == "/stats":
            html = build_stats_html(rows).encode("utf-8")
        else:
            idx = int(qs.get("idx", ["0"])[0])
            html = build_html(rows, idx).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        params = parse_qs(body)

        idx = int(params.get("idx", ["0"])[0])
        label = params.get("label", [""])[0]
        reason = params.get("reason", [""])[0]

        rows = load_rows()
        if 0 <= idx < len(rows):
            lbl_f = f"annotator_{ANNOTATOR_NUM}_label"
            rsn_f = f"annotator_{ANNOTATOR_NUM}_reason"
            rows[idx][lbl_f] = label
            rows[idx][rsn_f] = reason
            save_rows(rows)

            # Find next unlabeled
            next_idx = idx + 1
            for i in range(idx + 1, len(rows)):
                if not rows[i].get(lbl_f, "").strip():
                    next_idx = i
                    break

        self.send_response(303)
        self.send_header("Location", f"/?idx={next_idx}")
        self.end_headers()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global ANNOTATOR_NUM
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotator", type=int, default=1, choices=[1, 2])
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    ANNOTATOR_NUM = args.annotator

    # Pre-load JSONL cache
    print(f"  正在加载 SKILL.md 文本数据...")
    _ = get_pair("")  # trigger cache load
    print(f"  已加载 {len(_PAIR_CACHE)} 个文本对")

    print(f"\n{'='*50}")
    print(f"  TrustDrift 标注工具（含 Diff 视图）")
    print(f"  标注员: {ANNOTATOR_NUM}")
    print(f"{'='*50}")
    print(f"\n  ➜ 浏览器打开: http://localhost:{args.port}\n")

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止。")


if __name__ == "__main__":
    main()
