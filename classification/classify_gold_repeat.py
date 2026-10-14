# -*- coding: utf-8 -*-
"""生成AI分類の再現性検証: 同一入力を指定モデルで再分類し、初回(llm_gold_spark.csv)との一致率と
McNemar 検定を算出する。

  python classify_gold_repeat.py MODEL TAG    例: gpt-5.3-codex-spark r2 / gpt-5.4-mini mini

出力: llm_gold_<TAG>.csv, repeat_report.txt（追記）
"""
import sys, csv, sqlite3, datetime as dt
from math import comb
import classify_gold_spark as G

MODEL = sys.argv[1]; TAG = sys.argv[2]
G.MODEL = MODEL


def mcnemar(b, c):
    n = b + c
    if n == 0: return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n)


def main():
    base = {r["apex"]: r for r in csv.DictReader(open("llm_gold_spark.csv", encoding="utf-8"))}
    c = sqlite3.connect(G.DB, timeout=120)
    pages = {a: (ti, m, tx) for a, ti, m, tx in c.execute(
        "SELECT apex,title,meta,text FROM pages WHERE status IS NOT NULL AND status<>0 AND length(text)>=100")}
    content = [a for a in base if a in pages]
    pred = {}
    for i in range(0, len(content), 25):
        batch = content[i:i + 25]
        payload = [{"apex": a, "title": (pages[a][0] or "")[:120], "meta": (pages[a][1] or "")[:200],
                    "text": (pages[a][2] or "")[:900]} for a in batch]
        got = G.run_codex(payload)
        for a in batch:
            pred[a] = G.MAP3.get(got.get(a, "unknown"), "live")
        print(f"  [{TAG}] batch {i // 25 + 1} done", flush=True)
    with open(f"llm_gold_{TAG}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["apex", "gold", "heuristic", "llm_first", f"llm_{TAG}"])
        for a in content: w.writerow([a, base[a]["gold"], base[a]["heuristic"], base[a]["llm_spark"], pred[a]])
    n = len(content)
    agree = sum(1 for a in content if pred[a] == base[a]["llm_spark"])
    acc = sum(1 for a in content if pred[a] == base[a]["gold"])
    ab = [a for a in content if base[a]["gold"] == "abuse"]
    rec = sum(1 for a in ab if pred[a] == "abuse")
    b_ = sum(1 for a in ab if base[a]["heuristic"] == "abuse" and pred[a] != "abuse")
    c_ = sum(1 for a in ab if base[a]["heuristic"] != "abuse" and pred[a] == "abuse")
    pa = [a for a in content if pred[a] == "abuse"]
    prec = sum(1 for a in pa if base[a]["gold"] == "abuse") / len(pa) if pa else 0
    lines = [f"=== {dt.datetime.now().isoformat(timespec='seconds')}  model={MODEL} tag={TAG}  n={n} ===",
             f"  初回(spark)との一致率: {100 * agree / n:.1f}% ({agree}/{n})",
             f"  正解率: {100 * acc / n:.1f}%   abuse再現率: {rec}/{len(ab)}={rec / len(ab):.2f}   abuse適合率: {prec:.2f}",
             f"  abuse再現 McNemar vs ヒューリスティック: heurのみ{b_} / LLMのみ{c_}  p={mcnemar(b_, c_):.3f}"]
    open("repeat_report.txt", "a", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
