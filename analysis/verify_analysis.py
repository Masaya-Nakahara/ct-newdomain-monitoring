# -*- coding: utf-8 -*-
"""無作為標本の目視確認（100件，分類器の判定を伏せて著者が判定）の結果を分析する。

  python paper_tools/verify_analysis.py

- 分類器の abuse 判定の適合率（gpt-5.5／語彙規則）と，判定されなかった内容ありページでの見逃しの推定
- 内容ありページ全体での3分類の正解率（abuse判定ありの層は全数，それ以外の層は69件で重み付け）
- 目視で abuse と確認したドメインによる，新規・既存の abuse 割合の比較（Fisher，オッズ比，リスク比）
"""
import os, csv, glob, math, sqlite3, datetime as dt
from math import comb

ROOT = "."   # ct_data.db と分類結果の CSV を置くフォルダ
c = sqlite3.connect(os.path.join(ROOT, "ct_data.db"), timeout=60)
d = lambda x: dt.date.fromisoformat(x[:10])
key = {int(r["no"]): r for r in csv.DictReader(open(os.path.join(ROOT, "verify_key.csv"), encoding="utf-8"))}
src = max(glob.glob(os.path.join(ROOT, "verify_labels*.csv")) +
          glob.glob(os.path.join(os.path.expanduser("~"), "Downloads", "verify_labels*.csv")), key=os.path.getmtime)
lab = {int(r["no"]): r["label"].strip() for r in csv.DictReader(open(src, encoding="utf-8-sig"))}
out = []
def P(*a):
    s = " ".join(str(x) for x in a); print(s); out.append(s)


def wilson(k, n, z=1.96):
    if n == 0: return (0, 0)
    p = k / n; dd = 1 + z * z / n; ce = (p + z * z / (2 * n)) / dd
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / dd
    return max(0, ce - h), min(1, ce + h)


def fisher(a, b, cc, dd_):
    n = a + b + cc + dd_; r1 = a + b; c1 = a + cc
    pr = lambda x: comb(r1, x) * comb(n - r1, c1 - x) / comb(n, c1)
    p0 = pr(a)
    return sum(pr(x) for x in range(max(0, c1 - (n - r1)), min(r1, c1) + 1) if pr(x) <= p0 * (1 + 1e-9))


H2L = {"service": "live", "commerce": "live", "business": "live", "blog": "live", "unknown": "live",
       "parking": "non-content", "error": "non-content", "spam": "abuse"}
rows = c.execute("""SELECT p.apex, p.stratum, r.reg_date, a.llm3, a.heur7, a.status, a.text FROM a_plan p
    JOIN a_pages a ON a.apex=p.apex LEFT JOIN rdap r ON r.apex=p.apex""").fetchall()
info = {r[0]: r for r in rows}
heur3 = lambda r: H2L.get(r[4], "non-content") if r[4] else "non-content"
content = lambda r: r[5] not in (None, 0) and len(r[6] or "") >= 100
flag = lambda r: r[3] == "abuse" or r[4] == "spam"
by_apex = {key[k]["apex"]: lab[k] for k in key}
judged = {a: l for a, l in by_apex.items() if l in ("live", "non-content", "abuse")}
P(f"読込 {os.path.basename(src)}  判定 {len(by_apex)}件（判断不能 {sum(l not in ('live','non-content','abuse') for l in by_apex.values())}件）")

# ---------- 1. 適合率と見逃し ----------
P("\n==== 1. abuse 判定の適合率と見逃し ====")
for name, f in (("gpt-5.5", lambda r: r[3] == "abuse"), ("語彙規則", lambda r: r[4] == "spam")):
    fl = [a for a in judged if f(info[a])]
    tp = sum(judged[a] == "abuse" for a in fl); lo, hi = wilson(tp, len(fl))
    wrong = [judged[a] for a in fl if judged[a] != "abuse"]
    P(f"  {name}: abuse判定 {len(fl)}件のうち目視でabuse {tp}件  適合率 {tp/len(fl):.2f}（95%CI {lo:.2f}〜{hi:.2f}）"
      f"  誤りの内訳 live {wrong.count('live')} / non-content {wrong.count('non-content')}")
flagged = [r for r in rows if flag(r)]
rest_content = [r for r in rows if content(r) and not flag(r)]
smp = [a for a in judged if not flag(info[a])]   # 無作為に抽出した非判定の層（判断不能は除く）
fn = sum(judged[a] == "abuse" for a in smp)
P(f"  どちらも abuse としなかった内容ありページ {len(rest_content)}件から無作為に {len(smp)}件 → 目視で abuse {fn}件"
  f"（{100*fn/len(smp):.1f}%，95%CI {100*wilson(fn,len(smp))[0]:.1f}〜{100*wilson(fn,len(smp))[1]:.1f}%）")
true_flag = sum(judged.get(r[0]) == "abuse" for r in flagged)
est_fn = fn / len(smp) * len(rest_content)
est_total = true_flag + est_fn
for name, f in (("gpt-5.5", lambda r: r[3] == "abuse"), ("語彙規則", lambda r: r[4] == "spam")):
    tp = sum(judged.get(r[0]) == "abuse" for r in rows if f(r))
    P(f"  {name} の推定再現率 {tp}/{est_total:.1f} = {tp/est_total:.2f}（abuse判定ありの層の確認済み abuse {true_flag}件＋見逃し推定 {est_fn:.1f}件）")

# ---------- 2. 内容ありページでの正解率（層で重み付け） ----------
P("\n==== 2. 内容ありページでの3分類の正解率（重み付け） ====")
fl_c = [r for r in flagged if content(r) and r[0] in judged]
w_rest = len(rest_content) / len(smp)
for name, g in (("gpt-5.5", lambda r: r[3]), ("語彙規則", heur3)):
    ok_f = sum(g(r) == judged[r[0]] for r in fl_c)
    ok_r = sum(g(info[a]) == judged[a] for a in smp)
    n_all = len(fl_c) + len(rest_content)
    acc = (ok_f + ok_r * w_rest) / n_all
    P(f"  {name}: 正解率 {100*acc:.1f}%（abuse判定ありの層 {ok_f}/{len(fl_c)}，それ以外の層 {ok_r}/{len(smp)}，母数 {n_all}件）")

# ---------- 3. 目視確認に基づく新規・既存の比較 ----------
P("\n==== 3. 目視で abuse と確認したドメインによる新規・既存の比較 ====")
def grp(r):
    if not r[2]: return None
    return "新規" if (d(r[1]) - d(r[2])).days <= 120 else "既存"
for label, sel in (("gpt-5.5の判定（従来）", lambda r: r[3] == "abuse"),
                   ("目視で確認したabuse（abuse判定ありの層＋無作為抽出の層）", lambda r: judged.get(r[0]) == "abuse")):
    cnt = {"新規": [0, 0], "既存": [0, 0]}
    for r in rows:
        g = grp(r)
        if not g: continue
        cnt[g][1] += 1; cnt[g][0] += bool(sel(r))
    (a1, n1), (a2, n2) = cnt["新規"], cnt["既存"]
    orr = (a1 * (n2 - a2)) / ((n1 - a1) * a2) if a2 and n1 > a1 else float("nan")
    rr = (a1 / n1) / (a2 / n2) if a2 else float("nan")
    lo, hi = wilson(a1, a1 + a2)
    P(f"  [{label}] 新規 {a1}/{n1}={100*a1/n1:.1f}%  既存 {a2}/{n2}={100*a2/n2:.1f}%  OR={orr:.2f}  リスク比={rr:.2f}"
      f"  Fisher p={fisher(a1, n1-a1, a2, n2-a2):.1e}  abuseに占める新規 {a1}/{a1+a2}={100*a1/(a1+a2):.1f}%（CI {100*lo:.0f}〜{100*hi:.0f}%）")
# 新規・既存別の gpt-5.5 の適合率
for g in ("新規", "既存"):
    fl = [r for r in rows if r[3] == "abuse" and grp(r) == g and r[0] in judged]
    P(f"  gpt-5.5 の適合率（{g}） {sum(judged[r[0]]=='abuse' for r in fl)}/{len(fl)}")
open(os.path.join(ROOT, "verify_analysis_report.txt"), "w", encoding="utf-8").write("\n".join(out))

# 表2（make_ipsj_assets.py）に載せる主要な値
import json
J = {}
lo_fn, hi_fn = wilson(fn, len(smp))
for name, f, g in (("gpt-5.5", lambda r: r[3] == "abuse", lambda r: r[3]),
                   ("ヒューリスティック", lambda r: r[4] == "spam", heur3)):
    fl = [a for a in judged if f(info[a])]; tp = sum(judged[a] == "abuse" for a in fl)
    ok_f = sum(g(r) == judged[r[0]] for r in fl_c); ok_r = sum(g(info[a]) == judged[a] for a in smp)
    J[name] = {"prec": tp / len(fl), "tp": tp, "nflag": len(fl), "rec": tp / est_total,
               "rec_lo": tp / (true_flag + hi_fn * len(rest_content)), "rec_hi": tp / (true_flag + lo_fn * len(rest_content)),
               "acc": (ok_f + ok_r * w_rest) / (len(fl_c) + len(rest_content))}
json.dump(J, open(os.path.join(ROOT, "verify_metrics.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
