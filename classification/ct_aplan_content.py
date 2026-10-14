# -*- coding: utf-8 -*-
"""主軸Aの無作為・層化標本(a_plan, 1,020件)のトップページを取得し、生成AI(gpt-5.5)で3分類する。
RDAP登録日による新旧（登録≤120日／>120日）と内容3分類のクロス集計を出す。

  python ct_aplan_content.py fetch      トップページ取得（a_pages 表）
  python ct_aplan_content.py classify   到達ページを gpt-5.5 で分類（a_pages.llm3）
  python ct_aplan_content.py report     クロス集計
"""
import sys, sqlite3, datetime as dt, math
from concurrent.futures import ThreadPoolExecutor
import ct_classify as K
import classify_gold_spark as G

DB = "ct_data.db"
G.MODEL = "gpt-5.5"


def db():
    c = sqlite3.connect(DB, timeout=180); c.execute("PRAGMA busy_timeout=180000")
    c.execute("""CREATE TABLE IF NOT EXISTS a_pages(apex TEXT PRIMARY KEY, status INT, url TEXT, title TEXT,
                 meta TEXT, text TEXT, heur7 TEXT, llm7 TEXT, llm3 TEXT, fetched_at TEXT)""")
    return c


def fetch_all():
    c = db()
    done = {a for (a,) in c.execute("SELECT apex FROM a_pages")}
    todo = [a for (a,) in c.execute("SELECT apex FROM a_plan") if a not in done]
    print(f"[fetch] {len(todo)}件", flush=True)

    def work(a):
        st, url, html = K.fetch(a)
        title, meta, text = K.title_of(html), K.meta_desc(html), K.strip_text(html)[:3000]
        h7, _ = K.classify(a, a, st, url, title, meta, text)
        return a, st, url, title, meta, text, h7

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with ThreadPoolExecutor(max_workers=16) as ex:
        for i, (a, st, url, ti, me, tx, h7) in enumerate(ex.map(work, todo), 1):
            c.execute("INSERT OR REPLACE INTO a_pages(apex,status,url,title,meta,text,heur7,fetched_at) VALUES(?,?,?,?,?,?,?,?)",
                      (a, st, url, ti, me, tx, h7, now))
            if i % 100 == 0: c.commit(); print(f"  {i}/{len(todo)}", flush=True)
    c.commit(); c.close(); print("[fetch] 完了", flush=True)


def classify_all():
    c = db()
    rows = c.execute("""SELECT apex,title,meta,text FROM a_pages WHERE llm3 IS NULL AND status IS NOT NULL
                        AND (length(text)>=100 OR length(title)>0)""").fetchall()
    print(f"[classify] 到達ページ {len(rows)}件を {G.MODEL} で分類", flush=True)
    for i in range(0, len(rows), 25):
        batch = rows[i:i + 25]
        payload = [{"apex": a, "title": (ti or "")[:120], "meta": (me or "")[:200], "text": (tx or "")[:900]}
                   for a, ti, me, tx in batch]
        try:
            got = G.run_codex(payload)
        except SystemExit as e:
            print("  batch失敗:", str(e)[:120], flush=True); continue
        for a, *_ in batch:
            cat = got.get(a, "unknown")
            c.execute("UPDATE a_pages SET llm7=?, llm3=? WHERE apex=?", (cat, G.MAP3.get(cat, "live"), a))
        c.commit(); print(f"  batch {i // 25 + 1}/{(len(rows) + 24) // 25}", flush=True)
    # 未到達・空ページは non-content
    c.execute("""UPDATE a_pages SET llm3='non-content' WHERE llm3 IS NULL AND
                 (status IS NULL OR (length(COALESCE(text,''))<100 AND length(COALESCE(title,''))=0))""")
    c.commit(); c.close(); print("[classify] 完了", flush=True)


def wilson(k, n, z=1.96):
    if n == 0: return (0, 0)
    p = k / n; d = 1 + z * z / n; ce = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0, ce - h), min(1, ce + h)


def report():
    c = db()
    rows = c.execute("""SELECT p.apex, p.stratum, r.reg_date, a.llm3, a.heur7, a.status FROM a_plan p
        JOIN a_pages a ON a.apex=p.apex LEFT JOIN rdap r ON r.apex=p.apex""").fetchall()
    d = lambda x: dt.date.fromisoformat(x[:10])
    cats = ["live", "non-content", "abuse"]
    def age(r):
        if not r[2]: return "RDAP不明"
        return "新規(登録≤120日)" if (d(r[1]) - d(r[2])).days <= 120 else "既存(登録>120日)"
    reach = sum(1 for r in rows if r[5] is not None)
    print(f"標本 {len(rows)}件  到達 {reach}件 ({100*reach/len(rows):.1f}%)  分類済 {sum(1 for r in rows if r[3])}件")
    for grp in ["全体", "新規(登録≤120日)", "既存(登録>120日)", "RDAP不明"]:
        sub = [r for r in rows if r[3] and (grp == "全体" or age(r) == grp)]
        if not sub: continue
        parts = []
        for ct in cats:
            k = sum(1 for r in sub if r[3] == ct); lo, hi = wilson(k, len(sub))
            parts.append(f"{ct} {100*k/len(sub):.1f}% ({100*lo:.0f}-{100*hi:.0f})")
        print(f"  [{grp}] n={len(sub)}  " + "  ".join(parts))
    # 新規 vs 既存の abuse 割合の差（Fisher 正確検定）
    nw = [r for r in rows if r[3] and age(r).startswith("新規")]
    od = [r for r in rows if r[3] and age(r).startswith("既存")]
    if nw and od:
        a1 = sum(1 for r in nw if r[3] == "abuse"); a2 = sum(1 for r in od if r[3] == "abuse")
        try:
            from scipy.stats import fisher_exact
            _, p = fisher_exact([[a1, len(nw) - a1], [a2, len(od) - a2]])
            print(f"  abuse割合 新規 {a1}/{len(nw)} vs 既存 {a2}/{len(od)}  Fisher p={p:.4f}")
        except ImportError:
            print(f"  abuse割合 新規 {a1}/{len(nw)} vs 既存 {a2}/{len(od)}  (scipy未導入のため検定省略)")
        l1 = sum(1 for r in nw if r[3] == "live"); l2 = sum(1 for r in od if r[3] == "live")
        print(f"  live割合 新規 {l1}/{len(nw)} vs 既存 {l2}/{len(od)}")
    # ヒューリスティックとの一致
    both = [r for r in rows if r[3] and r[4]]
    m7 = {"service": "live", "commerce": "live", "business": "live", "blog": "live", "unknown": "live",
          "parking": "non-content", "error": "non-content", "spam": "abuse"}
    ag = sum(1 for r in both if m7.get(r[4]) == r[3])
    print(f"  ヒューリスティックとの一致 {100*ag/len(both):.1f}% (n={len(both)})")


if __name__ == "__main__":
    m = sys.argv[1] if len(sys.argv) > 1 else "report"
    {"fetch": fetch_all, "classify": classify_all}.get(m, report)()
