# -*- coding: utf-8 -*-
"""第3回の否定的査読への対応で追加した集計（既存データのみ）。

  python paper_tools/extra_checks.py   → extra_checks_report.txt

1. 証明書履歴による新規判定の感度・特異度・陽性的中率（RDAP を基準，162件）
2. 目視で確認した abuse での新規・既存の比較で，見逃しの推定値を既存ドメインに寄せた場合のオッズ比
3. 収集日ごとの新規観測ドメイン数（品質フィルタ後）で重み付けした，既存の割合と abuse の割合
"""
import os, csv, math, sqlite3, collections, datetime as dt
from math import comb
ROOT = "."   # ct_data.db と分類結果の CSV を置くフォルダ
c = sqlite3.connect("file:" + os.path.join(ROOT, "ct_data.db") + "?mode=ro", uri=True)
d = lambda x: dt.date.fromisoformat(x[:10])
out = []
def P(*a):
    s = " ".join(str(x) for x in a); print(s); out.append(s)
def wilson(k, n, z=1.96):
    p = k / n; dd = 1 + z * z / n; ce = (p + z * z / (2 * n)) / dd
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / dd
    return max(0, ce - h), min(1, ce + h)
def fisher(a, b, cc, dd_):
    n = a + b + cc + dd_; r1 = a + b; c1 = a + cc
    pr = lambda x: comb(r1, x) * comb(n - r1, c1 - x) / comb(n, c1)
    p0 = pr(a)
    return sum(pr(x) for x in range(max(0, c1 - (n - r1)), min(r1, c1) + 1) if pr(x) <= p0 * (1 + 1e-9))

# ---------- 1 ----------
P("==== 1. 証明書履歴による新規判定（基準：RDAP の登録日，閾値120日） ====")
rs = c.execute("""SELECT s.first_seen_date, f.first_cert_date, r.reg_date FROM firstcert f
    JOIN seen s ON s.apex=f.apex JOIN rdap r ON r.apex=f.apex
    WHERE f.first_cert_date IS NOT NULL AND r.reg_date IS NOT NULL""").fetchall()
tp = fn = fp = tn = 0
for st, fc, rg in rs:
    cert_new = (d(st) - d(fc)).days <= 120; reg_new = (d(st) - d(rg)).days <= 120
    tp += cert_new and reg_new; fn += (not cert_new) and reg_new; fp += cert_new and not reg_new; tn += not cert_new and not reg_new
days = collections.Counter(st for st, _, _ in rs)
P(f"  対象 {len(rs)}件（初観測日: {dict(sorted(days.items()))}）")
for name, k, n in (("感度（RDAPで新規のうち証明書履歴でも新規）", tp, tp + fn), ("特異度（RDAPで既存のうち証明書履歴でも既存）", tn, tn + fp),
                   ("陽性的中率（証明書履歴で新規のうちRDAPでも新規）", tp, tp + fp), ("陰性的中率", tn, tn + fn)):
    lo, hi = wilson(k, n); P(f"  {name}: {k}/{n} = {k/n:.3f}（95%CI {lo:.2f}〜{hi:.2f}）")

# ---------- 2 ----------
P("\n==== 2. 目視で確認した abuse：見逃しを既存ドメインに寄せた場合 ====")
key = {int(r["no"]): r["apex"] for r in csv.DictReader(open(os.path.join(ROOT, "verify_key.csv"), encoding="utf-8"))}
lab = {key[int(r["no"])]: r["label"] for r in csv.DictReader(open(os.path.join(ROOT, "verify_labels_v2_20261018.csv"), encoding="utf-8-sig"))}
rows = c.execute("""SELECT p.apex, p.stratum, r.reg_date, a.llm3, a.heur7, a.status, a.text FROM a_plan p
    JOIN a_pages a ON a.apex=p.apex LEFT JOIN rdap r ON r.apex=p.apex""").fetchall()
grp = lambda r: None if not r[2] else ("新規" if (d(r[1]) - d(r[2])).days <= 120 else "既存")
content = lambda r: r[5] not in (None, 0) and len(r[6] or "") >= 100
flag = lambda r: r[3] == "abuse" or r[4] == "spam"
rest = [r for r in rows if content(r) and not flag(r)]
smp = [a for a, l in lab.items() if l in ("live", "non-content", "abuse") and not flag(next(r for r in rows if r[0] == a))]
miss_rate = sum(lab[a] == "abuse" for a in smp) / len(smp); lo, hi = wilson(sum(lab[a] == "abuse" for a in smp), len(smp))
cnt = {"新規": [0, 0], "既存": [0, 0]}
for r in rows:
    g = grp(r)
    if g: cnt[g][1] += 1; cnt[g][0] += lab.get(r[0]) == "abuse"
rest_g = collections.Counter(grp(r) for r in rest)
P(f"  確認済み abuse：新規 {cnt['新規'][0]}/{cnt['新規'][1]}  既存 {cnt['既存'][0]}/{cnt['既存'][1]}")
P(f"  どちらの分類器も abuse としなかった内容のあるページ {len(rest)}件（新規 {rest_g['新規']}，既存 {rest_g['既存']}，登録日不明 {rest_g[None]}）"
  f"  見逃しの割合 {100*miss_rate:.1f}%（95%CI {100*lo:.1f}〜{100*hi:.1f}%）")
for name, rate, mode in (("見逃しを層の件数に応じて配分（点推定）", miss_rate, "prop"), ("見逃しをすべて既存に寄せる（点推定）", miss_rate, "exist"),
                         ("見逃しをすべて既存に寄せる（95%CIの上限）", hi, "exist")):
    a1, n1 = cnt["新規"]; a2, n2 = cnt["既存"]
    if mode == "prop": a1 += rate * rest_g["新規"]; a2 += rate * rest_g["既存"]
    else: a2 += rate * (rest_g["新規"] + rest_g["既存"])
    b1, b2 = round(a1), round(a2)   # 検定とオッズ比は四捨五入した件数でそろえる
    orr = (b1 / (n1 - b1)) / (b2 / (n2 - b2)); rr = (b1 / n1) / (b2 / n2)
    p = fisher(b1, n1 - b1, b2, n2 - b2)
    P(f"  {name}: 新規 {a1:.1f}/{n1}  既存 {a2:.1f}/{n2} → 四捨五入 {b1}/{n1}={100*b1/n1:.1f}% と {b2}/{n2}={100*b2/n2:.1f}%  OR={orr:.2f}  RR={rr:.2f}  Fisher p={p:.1e}")

# ---------- 3 ----------
P("\n==== 3. 収集日ごとの新規観測ドメイン数で重み付け ====")
day_n = {r[0]: r[1] for r in c.execute("SELECT date, new_clean FROM daily WHERE valid=1")}
per = collections.defaultdict(lambda: {"n": 0, "dated": 0, "exist": 0, "new": 0, "new_ab": 0, "ex_ab": 0})
for r in rows:
    day = r[1][:10]; x = per[day]; x["n"] += 1
    g = grp(r)
    if g:
        x["dated"] += 1; x["exist"] += g == "既存"; x["new"] += g == "新規"
        x["new_ab"] += g == "新規" and r[3] == "abuse"; x["ex_ab"] += g == "既存" and r[3] == "abuse"
miss = [k for k in per if k not in day_n]
P(f"  収集日 {len(per)}日（新規観測数が不明な日 {len(miss)}日は除く）  重み = その日の新規観測数 / その日の標本数")
w = {k: day_n[k] / per[k]["n"] for k in per if k in day_n}
sw = lambda f: sum(w[k] * per[k][f] for k in w)
P(f"  既存の割合：重みなし {100*sum(per[k]['exist'] for k in per)/sum(per[k]['dated'] for k in per):.1f}%  重み付き {100*sw('exist')/sw('dated'):.1f}%")
nr, er = sw("new_ab") / sw("new"), sw("ex_ab") / sw("exist")
P(f"  gpt-5.5 の abuse の割合：新規 {100*nr:.1f}%  既存 {100*er:.1f}%  リスク比 {nr/er:.2f}  オッズ比 {(nr/(1-nr))/(er/(1-er)):.2f}（重み付き）")
open(os.path.join(ROOT, "extra_checks_report.txt"), "w", encoding="utf-8").write("\n".join(out))
