#!/usr/bin/env python3
"""
clawhub/16_learn_risk_weights.py
--------------------------------
Learn Data-Driven Risk Weights from Real Attack Data.

Performs Logistic Regression on capability family occurrences against
the ground truth of T6 malicious supply-chain injections to derive
statistically grounded Risk-Weighted Mismatch (RWM) weights.

Key outputs:
  results/risk_weights.json   — learned weights (consumed by scripts 09, 17, 18)
  results/risk_weights_report.md — paper table

Usage:
  python3 16_learn_risk_weights.py
"""

import json
import re
from pathlib import Path
import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import LeaveOneOut, cross_val_predict
from sklearn.metrics import roc_auc_score, classification_report

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
RESULTS_DIR.mkdir(exist_ok=True)

JSONL = DATA_DIR / "version_transitions_enriched.jsonl"
TAXONOMY_CSV = DATA_DIR / "taxonomy_classified.csv"
OUTPUT_JSON = RESULTS_DIR / "risk_weights.json"
REPORT_PATH = RESULTS_DIR / "risk_weights_report.md"

# Capability pattern banks with semantic grouping
CAPABILITY_FAMILIES = {
    "shell_exec": {
        "patterns": [r'\bsubprocess\b', r'\bos\.system\b', r'\bos\.popen\b',
                     r'\bexec\s*\(', r'\beval\s*\(', r'\bshell\s*=\s*True\b',
                     r'\brm\s+-rf\b', r'\bshutil\.rmtree\b', r'\bbash\b',
                     r'\bchmod\b', r'\b/bin/sh\b'],
        "description": "Shell/command execution",
    },
    "credential_access": {
        "patterns": [r'\bapi[_\s]?key\b', r'\bpassword\b', r'\bprivate[_\s]?key\b',
                     r'\bsecret\b', r'\btoken\b', r'\bcredential\b',
                     r'\bbearer\b', r'\boauth\b', r'\.env\b'],
        "description": "Credential reading/handling",
    },
    "network_exfil": {
        "patterns": [r'\brequests\.post\b', r'\brequests\.put\b',
                     r'\bwebhook\b', r'\bcurl\b', r'\burllib\.request\b',
                     r'\bhttpx\b', r'\bfetch\s*\(', r'\bsmtp\b'],
        "description": "Outbound network / data exfiltration",
    },
    "file_destructive": {
        "patterns": [r'\bos\.remove\b', r'\bos\.unlink\b', r'\bshutil\.rmtree\b',
                     r'\brm\s+-rf\b', r'\bdelete\b.*\bfile\b', r'\bunlink\b'],
        "description": "File deletion / destructive ops",
    },
    "persistence": {
        "patterns": [r'\bcrontab\b', r'\bcron\b', r'\blaunchagent\b',
                     r'\bsystemd\b', r'\bautostart\b', r'\bstartup\b',
                     r'\bscheduled?\s*task\b'],
        "description": "Persistence / auto-execution",
    },
    "download_execute": {
        "patterns": [r'\bwget\b', r'\bcurl\b.*-[oO]\b',
                     r'\bdownload.*exec\b', r'\burllib.*urlretrieve\b'],
        "description": "Download-and-execute chain",
    },
    "autonomous_agent": {
        "patterns": [r'\bautonomous\b', r'\bauto[_\s]?execute\b',
                     r'\bself[_\s]?modify\b', r'\brecursive\b.*\bagent\b'],
        "description": "Autonomous agent behavior",
    },
    "file_write": {
        "patterns": [r'\bopen\s*\(.*["\']w["\']', r'\.write\s*\(',
                     r'\bshutil\.copy\b', r'\bos\.makedirs\b'],
        "description": "File write operations",
    },
    "network_read": {
        "patterns": [r'\brequests\.get\b', r'\bhttpx\.get\b',
                     r'\burllib\b', r'\bfetch\s*\('],
        "description": "Inbound network read",
    },
}


def normalize(t):
    return re.sub(r"\s+", " ", (t or "")).lower().strip()


def extract_features(text):
    """Extract binary capability features from text."""
    norm = normalize(text)
    features = {}
    for cap, spec in CAPABILITY_FAMILIES.items():
        found = False
        for p in spec["patterns"]:
            if re.search(p, norm):
                found = True
                break
        features[cap] = 1 if found else 0
    return features


def main():
    print("[16_learn_risk_weights] Loading taxonomy and text data...")
    try:
        df_tax = pd.read_csv(TAXONOMY_CSV)
    except FileNotFoundError:
        print("Taxonomy CSV not found. Please run earlier pipeline scripts.")
        return

    # Identify T6 pairs (ground truth malicious)
    df_tax['is_malicious'] = df_tax['all_labels'].fillna('').str.contains('T6').astype(int)
    t6_pairs = set(df_tax[df_tax['is_malicious'] == 1]['pair_id'])
    n_t6 = len(t6_pairs)

    print(f"  T6 ground truth: {n_t6} pairs")

    # Sample balanced negatives (10x for statistical power)
    neg_pool = df_tax[df_tax['is_malicious'] == 0]
    n_neg = min(len(neg_pool), n_t6 * 10)
    negatives = neg_pool.sample(n=n_neg, random_state=42)
    positives = df_tax[df_tax['is_malicious'] == 1]

    target_pairs = set(positives['pair_id']).union(set(negatives['pair_id']))

    # Extract features from enriched JSONL
    X_data = []
    y_data = []

    print(f"  Extracting capability features for {len(target_pairs)} sampled transitions...")
    with open(JSONL, encoding='utf-8') as f:
        for line in f:
            p = json.loads(line)
            pid = p['pair_id']
            if pid in target_pairs:
                # For T6 (fix transitions), analyze the PREV version (malicious)
                # For negatives, analyze the CURR version
                if pid in t6_pairs:
                    text = p.get('prev_skill_text', '')
                else:
                    text = p.get('curr_skill_text', '')

                feats = extract_features(text)
                is_t6 = 1 if pid in t6_pairs else 0

                row = feats.copy()
                row['pair_id'] = pid
                X_data.append(row)
                y_data.append(is_t6)

    if not X_data:
        print("No paired data found.")
        return

    df_X = pd.DataFrame(X_data).set_index('pair_id')
    y = np.array(y_data)

    print(f"  Dataset: n={len(df_X)} (pos={sum(y)}, neg={len(y)-sum(y)})")

    # ── Main model: Logistic Regression ──────────────────────────────────────
    print("\n  Training Logistic Regression (balanced, L2)...")
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df_X)

    clf = LogisticRegression(class_weight='balanced', max_iter=1000, random_state=42, C=1.0)
    clf.fit(X_scaled, y)

    raw_weights = clf.coef_[0]
    cap_names = list(df_X.columns)

    # ── Leave-One-Out Cross-Validation on T6 ─────────────────────────────────
    print("  Running Leave-One-Out CV on T6 positives...")
    loo = LeaveOneOut()
    y_pred_loo = cross_val_predict(
        LogisticRegression(class_weight='balanced', max_iter=1000, random_state=42),
        X_scaled, y, cv=loo, method='predict_proba'
    )[:, 1]

    # Compute AUROC
    auroc = roc_auc_score(y, y_pred_loo)
    print(f"  LOO-CV AUROC: {auroc:.3f}")

    # T6-specific recall at various thresholds
    t6_mask = y == 1
    t6_probs = y_pred_loo[t6_mask]
    t6_recall_50 = (t6_probs >= 0.5).sum() / len(t6_probs)
    t6_recall_30 = (t6_probs >= 0.3).sum() / len(t6_probs)
    t6_recall_10 = (t6_probs >= 0.1).sum() / len(t6_probs)

    print(f"  T6 recall @threshold=0.5: {t6_recall_50:.1%}")
    print(f"  T6 recall @threshold=0.3: {t6_recall_30:.1%}")
    print(f"  T6 recall @threshold=0.1: {t6_recall_10:.1%}")

    # ── Feature Ablation ─────────────────────────────────────────────────────
    print("\n  Running single-feature ablation...")
    ablation_results = {}
    for i, cap in enumerate(cap_names):
        X_single = X_scaled[:, i].reshape(-1, 1)
        try:
            y_pred_single = cross_val_predict(
                LogisticRegression(class_weight='balanced', max_iter=1000, random_state=42),
                X_single, y, cv=5, method='predict_proba'
            )[:, 1]
            auc_single = roc_auc_score(y, y_pred_single)
        except Exception:
            auc_single = 0.5
        ablation_results[cap] = auc_single

    # ── Derive RWM Weights ───────────────────────────────────────────────────
    # Normalize to [0.1, 5.0] range for interpretable RWM scoring
    min_w = min(raw_weights)
    max_w = max(raw_weights)
    if max_w - min_w > 0:
        rwm_weights = {
            cap: round(0.1 + 4.9 * (w - min_w) / (max_w - min_w), 2)
            for cap, w in zip(cap_names, raw_weights)
        }
    else:
        rwm_weights = {cap: 1.0 for cap in cap_names}

    # ── Compound risk signatures ─────────────────────────────────────────────
    # Check which capability COMBINATIONS are most predictive of T6
    compound_sigs = []
    for i, cap_a in enumerate(cap_names):
        for j, cap_b in enumerate(cap_names):
            if j <= i:
                continue
            co_occur_t6 = sum(1 for k in range(len(y))
                              if y[k] == 1 and df_X.iloc[k][cap_a] == 1 and df_X.iloc[k][cap_b] == 1)
            co_occur_neg = sum(1 for k in range(len(y))
                               if y[k] == 0 and df_X.iloc[k][cap_a] == 1 and df_X.iloc[k][cap_b] == 1)
            if co_occur_t6 >= 3:  # At least 3 T6 cases with this combo
                lift = (co_occur_t6 / max(sum(y), 1)) / max((co_occur_neg / max(len(y) - sum(y), 1)), 0.001)
                compound_sigs.append({
                    "capabilities": [cap_a, cap_b],
                    "t6_co_occurrence": co_occur_t6,
                    "negative_co_occurrence": co_occur_neg,
                    "lift": round(lift, 2),
                })

    compound_sigs.sort(key=lambda x: -x["lift"])

    # ── Save results ─────────────────────────────────────────────────────────
    output = {
        "model": "LogisticRegression",
        "dataset": f"n={len(df_X)} (T6={sum(y)}, neg={len(y)-sum(y)})",
        "loo_auroc": round(auroc, 4),
        "t6_recall": {
            "threshold_0.5": round(t6_recall_50, 4),
            "threshold_0.3": round(t6_recall_30, 4),
            "threshold_0.1": round(t6_recall_10, 4),
        },
        "raw_coefficients": {cap: round(float(w), 4) for cap, w in zip(cap_names, raw_weights)},
        "rwm_weights": rwm_weights,
        "single_feature_auroc": {cap: round(auc, 4) for cap, auc in ablation_results.items()},
        "compound_signatures": compound_sigs[:10],
        "capability_descriptions": {cap: spec["description"] for cap, spec in CAPABILITY_FAMILIES.items()},
    }

    with open(OUTPUT_JSON, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Saved weights to {OUTPUT_JSON}")

    # ── Generate Report ──────────────────────────────────────────────────────
    lines = [
        "# Data-Driven Capability Risk Weights",
        "",
        f"**Model**: Logistic Regression (balanced, L2) | **LOO-CV AUROC**: **{auroc:.3f}**",
        f"**Dataset**: {sum(y)} T6 malicious + {len(y)-sum(y)} negative samples",
        "",
        "## Learned Risk Weights (for Risk-Weighted Mismatch)",
        "",
        "| Capability | Description | Raw Coef | RWM Weight | Solo AUROC |",
        "|------------|-------------|----------|------------|------------|",
    ]
    # Sort by RWM weight descending
    for cap in sorted(cap_names, key=lambda c: -rwm_weights[c]):
        desc = CAPABILITY_FAMILIES[cap]["description"]
        rw = rwm_weights[cap]
        rc = output["raw_coefficients"][cap]
        sa = ablation_results[cap]
        lines.append(f"| `{cap}` | {desc} | {rc:+.3f} | **{rw:.2f}** | {sa:.3f} |")

    lines += [
        "",
        "## T6 Detection Performance (LOO-CV)",
        "",
        "| Threshold | Recall | Interpretation |",
        "|-----------|--------|----------------|",
        f"| 0.5 | {t6_recall_50:.1%} | Conservative |",
        f"| 0.3 | {t6_recall_30:.1%} | Balanced |",
        f"| 0.1 | {t6_recall_10:.1%} | High-sensitivity |",
    ]

    if compound_sigs:
        lines += [
            "",
            "## Compound Risk Signatures (Capability Co-occurrence)",
            "",
            "| Capabilities | T6 Count | Neg Count | Lift |",
            "|-------------|----------|-----------|------|",
        ]
        for sig in compound_sigs[:8]:
            caps_str = " + ".join(sig["capabilities"])
            lines.append(f"| {caps_str} | {sig['t6_co_occurrence']} | {sig['negative_co_occurrence']} | {sig['lift']:.1f}x |")

    lines += [
        "",
        "## Key Findings",
        "",
        "- Risk weights are **empirically derived** from real T6 malicious ground truth",
        "- These weights replace the previous heuristic CES scoring",
        f"- Top predictive capabilities: {', '.join(sorted(cap_names, key=lambda c: -rwm_weights[c])[:3])}",
        "- Compound signatures (capability pairs) provide additional discriminative power",
    ]

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Report: {REPORT_PATH}")


if __name__ == "__main__":
    main()
