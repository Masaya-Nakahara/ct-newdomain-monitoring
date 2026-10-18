# -*- coding: utf-8 -*-
"""正解190件の層化（ヒューリスティック分類器の3分類で live 90／non-content 60／abuse 40 を抽出）を，
母集団3,336件の層の大きさで重み付けして，分類器の性能を推定する（査読の指摘への対応）。

  python paper_tools/weighted_gold.py

- 重み = 母集団の層の件数 / 抽出した件数。母集団の層は，同じヒューリスティック分類器を3,336件にかけ直して求める
  （正解190件の記録された判定と190件すべてで一致することを確認する）
- 評価の対象は，内容のあるページ（応答があり本文が100字以上）のうち著者が判断できたもの
- 95%信頼区間は，層ごとに190件を復元抽出するブートストラップ（2,000回）
"""
import os, csv, sqlite3, random, collections
import sys
ROOT = "."   # ct_data.db と分類結果の CSV を置くフォルダ
sys.path.insert(0, ROOT)   # ct_classify.py（ヒューリスティック分類器）を読み込む
from ct_classify import classify
H2L = {"service": "live", "commerce": "live", "business": "live", "blog": "live", "unknown": "live",
       "parking": "non-content", "error": "non-content", "spam": "abuse"}
c = sqlite3.connect("file:" + os.path.join(ROOT, "ct_data.db") + "?mode=ro", uri=True)
pop = {}
for a, st, u, t, m, x in c.execute("SELECT apex, status, url, title, meta, text FROM pages"):
    pop[a] = H2L.get(classify(a, None, st, u, t or "", m or "", x or "")[0], "non-content")
base = {r["apex"]: r for r in csv.DictReader(open(os.path.join(ROOT, "llm_gold_spark.csv"), encoding="utf-8"))}
assert all(pop[a] == base[a]["heuristic"] for a in base), "層の再現に失敗"
N = collections.Counter(pop.values()); n = collections.Counter(base[a]["heuristic"] for a in base)
W = {h: N[h] / n[h] for h in n}
reach = {a for (a,) in c.execute("SELECT apex FROM pages WHERE status IS NOT NULL AND status<>0 AND length(text)>=100")}
P = {"ヒューリスティック": {a: base[a]["heuristic"] for a in base}, "gpt-5.3-codex-spark": {a: base[a]["llm_spark"] for a in base}}
for t, name in (("g55a", "gpt-5.5（a）"), ("g55b", "gpt-5.5（b）"), ("g55c", "gpt-5.5（c）"), ("qwen_a", "Qwen2.5-7B-Instruct")):
    P[name] = {r["apex"]: r[f"llm_{t}"] for r in csv.DictReader(open(os.path.join(ROOT, f"llm_gold_{t}.csv"), encoding="utf-8"))}
CL3 = ("live", "non-content", "abuse")


def metrics(sample):
    ks = [a for a in sample if a in reach and base[a]["gold"] in CL3]
    out = {}
    for k, pr in P.items():
        w = lambda a: W[base[a]["heuristic"]]
        tot = sum(w(a) for a in ks); acc = sum(w(a) for a in ks if pr[a] == base[a]["gold"]) / tot
        ab = [a for a in ks if base[a]["gold"] == "abuse"]; pa = [a for a in ks if pr[a] == "abuse"]
        rec = sum(w(a) for a in ab if pr[a] == "abuse") / sum(w(a) for a in ab)
        prec = sum(w(a) for a in pa if base[a]["gold"] == "abuse") / sum(w(a) for a in pa) if pa else float("nan")
        out[k] = (acc, rec, prec)
    return out


strata = collections.defaultdict(list)
for a in base: strata[base[a]["heuristic"]].append(a)
est = metrics(list(base)); rng = random.Random(42); boot = []
for _ in range(2000):
    s = [x for h, L in strata.items() for x in rng.choices(L, k=len(L))]
    boot.append(metrics(s))
q = lambda v: (sorted(v)[int(0.025 * len(v))], sorted(v)[int(0.975 * len(v)) - 1])
lines = [f"母集団3,336件の層（ヒューリスティック分類器の3分類）: {dict(N)}  抽出: {dict(n)}  重み: " +
         ", ".join(f"{h}={W[h]:.2f}" for h in W),
         f"評価の対象: 内容のあるページのうち判断できた {len([a for a in base if a in reach and base[a]['gold'] in CL3])} 件",
         "分類器: 重み付き正解率 / abuse再現率 / abuse適合率（95%CI，層別ブートストラップ2,000回）"]
for k in P:
    acc, rec, prec = est[k]
    ca, cr, cp = (q([b[k][i] for b in boot if b[k][i] == b[k][i]]) for i in range(3))
    lines.append(f"  {k}: 正解率 {100*acc:.1f}%（{100*ca[0]:.1f}〜{100*ca[1]:.1f}）  再現率 {rec:.2f}（{cr[0]:.2f}〜{cr[1]:.2f}）"
                 f"  適合率 {prec:.2f}（{cp[0]:.2f}〜{cp[1]:.2f}）")
for k in ("gpt-5.3-codex-spark", "gpt-5.5（a）", "Qwen2.5-7B-Instruct"):
    d = [b[k][1] - b["ヒューリスティック"][1] for b in boot]
    lines.append(f"  再現率の差（{k} − ヒューリスティック）: {est[k][1]-est['ヒューリスティック'][1]:+.2f}（95%CI {q(d)[0]:+.2f}〜{q(d)[1]:+.2f}）")
open(os.path.join(ROOT, "weighted_gold_report.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
