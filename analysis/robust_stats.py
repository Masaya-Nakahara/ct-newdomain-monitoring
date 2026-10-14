# -*- coding: utf-8 -*-
"""査読で想定される統計上の指摘に答えるための補強計算（結果は標準出力と Excel の補足シートに出す）。

  python paper_tools/robust_stats.py

1. 新規・既存の abuse 割合：日単位のクラスタ・ブートストラップ（収集日を復元抽出）によるオッズ比・リスク比・
   「abuseに占める新規」の95%区間，Wilson区間，Katz法のリスク比区間
2. TLD で層別した Mantel–Haenszel のオッズ比と検定（.com／その他gTLD／ccTLD）
3. 分類器の McNemar 検定（再現率）の Holm 補正，および正解abuse 55件（未到達を含む）での再現率
4. 初回証明書日による新規判定と RDAP 登録日による判定の一致：κ，新規側の感度・特異度
5. 先行日数の打ち切り（180／365／730日）を変えた場合の中央値
"""
import os, csv, math, random, sqlite3, statistics as S, datetime as dt
from math import comb
from collections import defaultdict

ROOT = "."   # ct_data.db と分類結果の CSV を置くフォルダ
c = sqlite3.connect(os.path.join(ROOT, "ct_data.db"), timeout=60)
d = lambda x: dt.date.fromisoformat(x[:10])
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


def mcnemar(b, cc):
    n = b + cc
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, i) for i in range(0, min(b, cc) + 1)) / 2 ** n)


# ---------- 1. 新規・既存の abuse 割合 ----------
rows = c.execute("""SELECT p.apex, p.stratum, r.reg_date, a.llm3 FROM a_plan p JOIN a_pages a ON a.apex=p.apex
                    JOIN rdap r ON r.apex=p.apex WHERE r.reg_date IS NOT NULL AND a.llm3 IS NOT NULL""").fetchall()
isnew = lambda r: (d(r[1]) - d(r[2])).days <= 120
def stats(rs):
    nw = [r for r in rs if isnew(r)]; od = [r for r in rs if not isnew(r)]
    a1 = sum(r[3] == "abuse" for r in nw); a2 = sum(r[3] == "abuse" for r in od)
    return nw, od, a1, a2
nw, od, a1, a2 = stats(rows)
n1, n2 = len(nw), len(od)
orr = (a1 * (n2 - a2)) / ((n1 - a1) * a2); rr = (a1 / n1) / (a2 / n2)
se = math.sqrt(1 / a1 - 1 / n1 + 1 / a2 - 1 / n2)
P("==== 1. 新規・既存の abuse 割合（gpt-5.5，RDAP登録日あり） ====")
P(f"新規 {a1}/{n1}={100*a1/n1:.1f}%  既存 {a2}/{n2}={100*a2/n2:.1f}%  OR={orr:.2f}  リスク比={rr:.2f}"
  f" (Katz 95%CI {math.exp(math.log(rr)-1.96*se):.2f}〜{math.exp(math.log(rr)+1.96*se):.2f})  Fisher p={fisher(a1,n1-a1,a2,n2-a2):.1e}")
lo, hi = wilson(a1, a1 + a2)
P(f"abuseに占める新規 {a1}/{a1+a2}={100*a1/(a1+a2):.1f}%  Wilson 95%CI {100*lo:.0f}〜{100*hi:.0f}%  標本に占める新規 {100*n1/(n1+n2):.1f}%")
byday = defaultdict(list)
for r in rows: byday[r[1][:10]].append(r)
days = sorted(byday); random.seed(1)
bo, br, bs = [], [], []
for _ in range(10000):
    smp = [r for dday in random.choices(days, k=len(days)) for r in byday[dday]]
    _nw, _od, x1, x2 = stats(smp)
    if x2 == 0 or len(_nw) == x1 or x1 == 0: continue
    bo.append((x1 * (len(_od) - x2)) / ((len(_nw) - x1) * x2)); br.append((x1 / len(_nw)) / (x2 / len(_od)))
    bs.append(x1 / (x1 + x2))
q = lambda L, a: sorted(L)[int(a * len(L))]
P(f"日単位ブートストラップ（{len(days)}収集日を復元抽出, 有効{len(bo)}回）: OR 95%区間 {q(bo,.025):.2f}〜{q(bo,.975):.2f}"
  f"  リスク比 {q(br,.025):.2f}〜{q(br,.975):.2f}  abuseに占める新規 {100*q(bs,.025):.0f}〜{100*q(bs,.975):.0f}%"
  f"  OR≤1となった割合 {100*sum(x<=1 for x in bo)/len(bo):.2f}%")

# ---------- 2. TLD 層別 Mantel–Haenszel ----------
CC = lambda t: len(t) == 2
def tgrp(apex):
    t = apex.split(".")[-1]
    return "com" if t == "com" else ("ccTLD" if CC(t) else "その他gTLD")
strata = defaultdict(list)
for r in rows: strata[tgrp(r[0])].append(r)
num = den = 0.0; sum_a = sum_e = sum_v = 0.0
P("\n==== 2. TLD で層別した Mantel–Haenszel ====")
for g, rs in sorted(strata.items()):
    _nw, _od, x1, x2 = stats(rs); m1, m2 = len(_nw), len(_od); n = m1 + m2
    a, b, cc_, dd_ = x1, m1 - x1, x2, m2 - x2
    P(f"  [{g}] 新規 {x1}/{m1}  既存 {x2}/{m2}")
    if n < 2: continue
    num += a * dd_ / n; den += b * cc_ / n
    t1 = a + cc_; t0 = b + dd_
    sum_a += a; sum_e += m1 * t1 / n
    if n > 1: sum_v += m1 * m2 * t1 * t0 / (n * n * (n - 1))
mh = num / den if den else float("inf")
chi = (abs(sum_a - sum_e) - 0.5) ** 2 / sum_v
pmh = math.erfc(math.sqrt(chi / 2))
P(f"MH OR={mh:.2f}  CMH χ²={chi:.2f}（連続性補正）  p={pmh:.1e}")

# ---------- 3. McNemar の Holm 補正と全55件での再現率 ----------
P("\n==== 3. 分類器（正解付き） ====")
base = {r["apex"]: r for r in csv.DictReader(open(os.path.join(ROOT, "llm_gold_spark.csv"), encoding="utf-8"))}
reach = {a for (a,) in c.execute("SELECT apex FROM pages WHERE status IS NOT NULL AND status<>0 AND length(text)>=100")}
preds = {"gpt-5.3-codex-spark": {a: base[a]["llm_spark"] for a in base}}
for t in ("g55a", "g55b", "g55c", "qwen_a"):
    f = os.path.join(ROOT, f"llm_gold_{t}.csv")
    if os.path.exists(f): preds[t] = {r["apex"]: r[f"llm_{t}"] for r in csv.DictReader(open(f, encoding="utf-8"))}
heur = {a: base[a]["heuristic"] for a in base}
ab_r = [a for a in base if a in reach and base[a]["gold"] == "abuse"]
pv = {}
for k, pr in preds.items():
    b_ = sum(1 for a in ab_r if heur[a] == "abuse" and pr.get(a) != "abuse")
    c_ = sum(1 for a in ab_r if heur[a] != "abuse" and pr.get(a) == "abuse")
    pv[k] = mcnemar(b_, c_)
    # 適合率の差（予測abuseのうち誤り）の McNemar：非abuse正解での誤検出の対応比較
    na = [a for a in base if a in reach and base[a]["gold"] != "abuse"]
    fb = sum(1 for a in na if heur[a] == "abuse" and pr.get(a) != "abuse")
    fc = sum(1 for a in na if heur[a] != "abuse" and pr.get(a) == "abuse")
    P(f"  {k}: 再現率 McNemar p={pv[k]:.4f} (heurのみ{b_}/LLMのみ{c_})   誤検出の McNemar p={mcnemar(fb, fc):.3f} (heurのみ{fb}/LLMのみ{fc})")
order = sorted(pv, key=pv.get); m = len(order); adj = {}; run = 0
for i, k in enumerate(order):
    run = max(run, min(1, (m - i) * pv[k])); adj[k] = run
P("  Holm 補正後: " + "  ".join(f"{k}={adj[k]:.3f}" for k in preds))
ab_all = [a for a in base if base[a]["gold"] == "abuse"]
P(f"  正解abuse 全{len(ab_all)}件（未到達含む）での再現率: ヒューリスティック {sum(heur[a]=='abuse' for a in ab_all)/len(ab_all):.2f}"
  f"  spark {sum(preds['gpt-5.3-codex-spark'][a]=='abuse' for a in ab_all)/len(ab_all):.2f}"
  f"（到達{len(ab_r)}件／未到達{len(ab_all)-len(ab_r)}件）")

# ---------- 4. 初回証明書日 vs RDAP 登録日 ----------
P("\n==== 4. 初回証明書日による新規判定の妥当性 ====")
rs = c.execute("""SELECT s.first_seen_date, f.first_cert_date, r.reg_date FROM firstcert f
    JOIN seen s ON s.apex=f.apex JOIN rdap r ON r.apex=f.apex
    WHERE f.first_cert_date IS NOT NULL AND r.reg_date IS NOT NULL""").fetchall()   # 旧主軸Aで初回証明書日を取得できた162件
tp = fn = fp = tn = 0
for st, fc, rg in rs:
    cn = (d(st) - d(fc)).days <= 120; rn = (d(st) - d(rg)).days <= 120
    if rn and cn: tp += 1
    elif rn and not cn: fn += 1
    elif cn and not rn: fp += 1
    else: tn += 1
n = len(rs); po = (tp + tn) / n
pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / n / n
P(f"n={n}  一致率 {100*po:.1f}%  κ={(po-pe)/(1-pe):.3f}  新規側の感度 {tp}/{tp+fn}={tp/max(1,tp+fn):.2f}"
  f"  特異度 {tn}/{tn+fp}={tn/max(1,tn+fp):.3f}  （RDAPで新規 {tp+fn}件，既存 {tn+fp}件）")

# ---------- 5. 先行日数の打ち切り ----------
P("\n==== 5. 先行日数の打ち切りを変えた場合（b_plan 基準） ====")
lt = [(d(h) - d(f)).days for h, f in c.execute(
    "SELECT b.hn_date, l.first_cert_date FROM b_plan b JOIN leadtime l ON l.apex=b.apex WHERE l.first_cert_date IS NOT NULL")]
for cut in (90, 180, 365, 730, 10 ** 6):
    band = [x for x in lt if 0 <= x <= cut]
    qq = S.quantiles(band, n=4)
    P(f"  打ち切り {cut if cut < 10**6 else 'なし'}日: n={len(band)}  中央値 {S.median(band):.0f}日 (p25 {qq[0]:.0f}, p75 {qq[2]:.0f})"
      f"  0〜1日 {100*sum(x<=1 for x in band)/len(band):.0f}%")

open(os.path.join(ROOT, "robust_stats_report.txt"), "w", encoding="utf-8").write("\n".join(out))
