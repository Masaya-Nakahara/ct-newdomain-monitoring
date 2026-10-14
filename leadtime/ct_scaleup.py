# -*- coding: utf-8 -*-
"""主軸A/Bのスケールアップ（論文用・無作為抽出・欠測の記録つき）

  python ct_scaleup.py firstcert N   clean seen を収集日で層化し無作為N件の first-cert を取得（主軸A）
  python ct_scaleup.py leadtime M [分] 過去24か月の Show HN を月ごとに無作為M件取得し first-cert と突合（主軸B）。計画は b_plan に保存し再利用。[分]は実行時間の上限
  python ct_scaleup.py report        A/B の集計（Wilson 95%CI、月別、欠測率）

処理順は層をまたいでシャッフルするため、途中で止めても標本は偏らない。
結果は既存 firstcert / leadtime テーブルに追記し、標本の帰属と取得状態を scale_sample に記録する。
"""
import sqlite3, sys, random, time, math, datetime as dt
import requests
from ct_leadtime import apex_of, SKIP

DB = "ct_data.db"
WARMUP_END = "2026-06-29"          # 収集開始直後の2週間は warm-up として別集計
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (research service-measurement; academic)"})
random.seed(20260907)


def _norm(s):
    return (s or "").strip().lower().lstrip("*.")


def first_cert2(apex, attempts=5):
    """crt.sh JSON から最小 not_before を返す。status: ok / empty(該当なし) / fail(取得失敗)。"""
    url = f"https://crt.sh/?q={apex}&output=json"
    data = None
    for attempt in range(attempts):
        try:
            r = S.get(url, timeout=30)
            if r.status_code == 200 and r.text.strip():
                data = r.json()
                break
        except Exception:
            pass
        time.sleep(2.0 * (attempt + 1))
    if data is None:
        return None, None, 0, "fail"
    nb_min = ts_min = None; n = 0
    for e in data:
        names = set()
        for f in (e.get("common_name"), e.get("name_value")):
            for part in (f or "").split("\n"):
                names.add(_norm(part))
        if not any(nm == apex or nm.endswith("." + apex) for nm in names):
            continue
        n += 1
        nb = e.get("not_before"); ts = e.get("entry_timestamp")
        if nb and (nb_min is None or nb < nb_min): nb_min = nb
        if ts and (ts_min is None or ts < ts_min): ts_min = ts
    return (nb_min[:10] if nb_min else None), ts_min, n, ("ok" if nb_min else "empty")


def db():
    c = sqlite3.connect(DB, timeout=180)
    c.execute("PRAGMA busy_timeout=180000")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS scale_sample(
        apex TEXT, kind TEXT, stratum TEXT, status TEXT, at TEXT, PRIMARY KEY(apex, kind))""")
    c.execute("""CREATE TABLE IF NOT EXISTS firstcert(
        apex TEXT PRIMARY KEY, first_cert_date TEXT, first_cert_ts TEXT, n_certs INT, checked_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS leadtime(
        apex TEXT PRIMARY KEY, hn_date TEXT, hn_ts INT, hn_title TEXT, hn_url TEXT,
        points INT, first_cert_date TEXT, lead_days INT, checked_at TEXT)""")
    return c


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ---------------- 主軸A: 無作為・層化 first-cert ----------------
def run_firstcert(N):
    c = db()
    dates = [d for (d,) in c.execute(
        "SELECT DISTINCT first_seen_date FROM seen WHERE COALESCE(suspect,0)=0 "
        "AND first_seen_date>='2026-06-16' ORDER BY first_seen_date")]
    per = math.ceil(N / len(dates))
    done_ok = {a for (a,) in c.execute("SELECT apex FROM scale_sample WHERE kind='A' AND status IN ('ok','empty')")}
    plan = []
    for d in dates:
        pool = [a for (a,) in c.execute(
            "SELECT apex FROM seen WHERE COALESCE(suspect,0)=0 AND first_seen_date=?", (d,))]
        random.shuffle(pool)
        plan += [(a, d) for a in pool[:per]]
    random.shuffle(plan)
    todo = [(a, d) for a, d in plan if a not in done_ok]
    print(f"[A] 収集日 {len(dates)}日 × {per}件 = 計画{len(plan)}件（未処理{len(todo)}件）", flush=True)
    t0 = time.time()
    for i, (a, d) in enumerate(todo, 1):
        fcd, fts, n, st = first_cert2(a, attempts=8)
        if st == "ok":
            c.execute("INSERT OR REPLACE INTO firstcert VALUES(?,?,?,?,?)", (a, fcd, fts, n, now()))
        c.execute("INSERT OR REPLACE INTO scale_sample VALUES(?,?,?,?,?)", (a, "A", d, st, now()))
        c.commit()
        if i % 25 == 0:
            el = time.time() - t0
            print(f"  [A] {i}/{len(todo)}  {el/i:.1f}s/件  残り約{(len(todo)-i)*el/i/60:.0f}分", flush=True)
    c.close()
    print("[A] 完了", flush=True)


def plan_a(N):
    """主軸Aの無作為・層化標本を作って a_plan に保存する（RDAP 照合用。crt.sh を使わない）。"""
    c = db()
    c.execute("CREATE TABLE IF NOT EXISTS a_plan(apex TEXT PRIMARY KEY, stratum TEXT)")
    dates = [d for (d,) in c.execute(
        "SELECT DISTINCT first_seen_date FROM seen WHERE COALESCE(suspect,0)=0 "
        "AND first_seen_date>='2026-06-16' ORDER BY first_seen_date")]
    per = math.ceil(N / len(dates))
    n = 0
    for d in dates:
        pool = [a for (a,) in c.execute(
            "SELECT apex FROM seen WHERE COALESCE(suspect,0)=0 AND first_seen_date=?", (d,))]
        random.shuffle(pool)
        for a in pool[:per]:
            c.execute("INSERT OR IGNORE INTO a_plan VALUES(?,?)", (a, d)); n += 1
    c.commit(); c.close()
    print(f"[A-plan] 収集日 {len(dates)}日 × {per}件 = {n}件を a_plan に保存", flush=True)


# ---------------- 主軸B: 過去24か月の Show HN ----------------
def showhn_month(y, m, M):
    start = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(y + (m == 12), (m % 12) + 1, 1, tzinfo=dt.timezone.utc)
    days = list(range((end - start).days)); random.shuffle(days)
    out = {}
    for dd in days[:4]:   # 月内の無作為な4日から候補を集める
        d0 = start + dt.timedelta(days=dd); d1 = d0 + dt.timedelta(days=1)
        url = ("https://hn.algolia.com/api/v1/search_by_date?tags=show_hn&hitsPerPage=200"
               f"&numericFilters=created_at_i>={int(d0.timestamp())},created_at_i<{int(d1.timestamp())}")
        try:
            hits = S.get(url, timeout=25).json().get("hits", [])
        except Exception:
            continue
        for h in hits:
            u = h.get("url") or ""
            if not u: continue
            host = u.split("//", 1)[-1].split("/", 1)[0].split(":")[0].lower()
            ax = apex_of(host)
            if not ax or host in SKIP or ax in SKIP: continue
            ts = h.get("created_at_i")
            if ax not in out or ts < out[ax][1]:
                out[ax] = (ax, ts, dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat(),
                           (h.get("title") or "")[:120], u[:120], h.get("points") or 0)
        time.sleep(0.5)
    rows = list(out.values()); random.shuffle(rows)
    return rows[:M]


def run_leadtime(M, max_min=None):
    """max_min: 実行時間の上限（分）。crt.sh が日本時間の午前以外はほぼ応答しないため，朝の定時実行で使う。"""
    c = db()
    cached = {a for (a,) in c.execute("SELECT apex FROM leadtime WHERE first_cert_date IS NOT NULL")}
    done = {a for (a,) in c.execute("SELECT apex FROM scale_sample WHERE kind='B' AND status IN ('ok','empty')")}
    has_plan = c.execute("SELECT name FROM sqlite_master WHERE name='b_plan'").fetchone() and \
        c.execute("SELECT COUNT(*) FROM b_plan").fetchone()[0] > 0
    if has_plan:   # 保存済みの計画を使う（未試行を先に，失敗分の再試行を後に）
        tried = {a for (a,) in c.execute("SELECT apex FROM scale_sample WHERE kind='B'")}
        plan = [((a, ts, d_, ti, u, p), s) for a, s, d_, ts, ti, u, p in c.execute(
            "SELECT apex, month, hn_date, hn_ts, hn_title, hn_url, points FROM b_plan ORDER BY apex")]
        random.shuffle(plan)
        plan.sort(key=lambda x: x[0][0] in tried)
        print(f"[B] 保存済みの計画 {len(plan)}件を使用", flush=True)
        return _leadtime_loop(c, plan, done, cached, max_min)
    today = dt.date.today()
    months = []
    y, m = today.year, today.month
    for _ in range(24):
        m -= 1
        if m == 0: y, m = y - 1, 12
        months.append((y, m))
    plan = []
    for (y, m) in months:
        rows = showhn_month(y, m, M)
        plan += [(r, f"{y}-{m:02d}") for r in rows]
        print(f"  [B] {y}-{m:02d}: 候補{len(rows)}件", flush=True)
    random.shuffle(plan)
    # 計画を保存（再起動後の再開と，RDAP登録日の付与に使う）
    c.execute("""CREATE TABLE IF NOT EXISTS b_plan(apex TEXT PRIMARY KEY, month TEXT, hn_date TEXT, hn_ts INT,
                 hn_title TEXT, hn_url TEXT, points INT, planned_at TEXT)""")
    c.executemany("INSERT OR IGNORE INTO b_plan VALUES(?,?,?,?,?,?,?,?)",
                  [(r[0], s, r[2], r[1], r[3], r[4], r[5], now()) for r, s in plan])
    c.commit()
    return _leadtime_loop(c, plan, done, cached, max_min)


def _leadtime_loop(c, plan, done, cached, max_min=None, stop_after_fails=15):
    todo = [(r, s) for r, s in plan if r[0] not in done and r[0] not in cached]
    print(f"[B] {now()} 計画{len(plan)}件（未処理{len(todo)}件）", flush=True)
    t0 = time.time(); fails = 0
    for i, (r, s) in enumerate(todo, 1):
        if max_min and time.time() - t0 > max_min * 60:
            print(f"[B] {now()} 時間上限 {max_min}分で中断（{i-1}件処理）", flush=True); break
        ax, ts, date, title, url, pts = r
        fcd, _fts, _n, st = first_cert2(ax, attempts=8)
        lead = (dt.date.fromisoformat(date) - dt.date.fromisoformat(fcd)).days if fcd else None
        if st == "ok":
            c.execute("INSERT OR REPLACE INTO leadtime VALUES(?,?,?,?,?,?,?,?,?)",
                      (ax, date, ts, title, url, pts, fcd, lead, now()))
        c.execute("INSERT OR REPLACE INTO scale_sample VALUES(?,?,?,?,?)", (ax, "B", s, st, now()))
        c.commit()
        fails = fails + 1 if st == "fail" else 0
        if fails >= stop_after_fails:   # crt.sh が過負荷のときは再試行を浴びせず止める
            print(f"[B] {now()} 連続{fails}件失敗のため中断（crt.sh 過負荷）", flush=True); break
        if i % 25 == 0:
            el = time.time() - t0
            print(f"  [B] {i}/{len(todo)}  {el/i:.1f}s/件  残り約{(len(todo)-i)*el/i/60:.0f}分", flush=True)
    else:
        print("[B] 完了", flush=True)
    c.close()


# ---------------- 集計 ----------------
def wilson(k, n, z=1.96):
    if n == 0: return (0, 0)
    p = k / n; d = 1 + z * z / n; ce = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0, ce - h), min(1, ce + h)


def report():
    import statistics
    c = db()
    print("==== 主軸A: 新規観測ドメインの初回証明書（無作為・層化） ====")
    st = dict(c.execute("SELECT status, COUNT(*) FROM scale_sample WHERE kind='A' GROUP BY status").fetchall())
    tot = sum(st.values())
    print(f"  試行 {tot}件  ok={st.get('ok',0)}  empty={st.get('empty',0)}  fail={st.get('fail',0)}"
          f"  (取得成功率 {100*st.get('ok',0)/max(tot,1):.1f}%)")
    rows = c.execute("""SELECT s.apex, s.stratum, f.first_cert_date, f.n_certs FROM scale_sample s
        JOIN firstcert f ON f.apex=s.apex WHERE s.kind='A' AND s.status='ok'""").fetchall()
    def gap(r): return (dt.date.fromisoformat(r[1]) - dt.date.fromisoformat(r[2])).days
    for label, sub in [("全期間", rows),
                       ("warm-up除外(06-30以降)", [r for r in rows if r[1] > WARMUP_END])]:
        n = len(sub); new = sum(1 for r in sub if gap(r) <= 120)
        lo, hi = wilson(n - new, n)
        print(f"  [{label}] n={n}  既存(>120日)={n-new} ({100*(n-new)/max(n,1):.1f}%, CI {100*lo:.1f}-{100*hi:.1f})"
              f"  新規(≤120日)={new}")
        if sub:
            g = sorted(gap(r) for r in sub)
            print(f"      経過日数 中央値={statistics.median(g):.0f}日  "
                  f"≤30={sum(x<=30 for x in g)} 31-120={sum(30<x<=120 for x in g)} 121-365={sum(120<x<=365 for x in g)}"
                  f" 366-730={sum(365<x<=730 for x in g)} >730={sum(x>730 for x in g)}  負(証明書が観測後)={sum(x<0 for x in g)}")
    print("  月別（既存の割合）:")
    by = {}
    for r in rows: by.setdefault(r[1][:7], []).append(gap(r))
    for mth in sorted(by):
        g = by[mth]; old = sum(1 for x in g if x > 120)
        print(f"    {mth}: n={len(g):4d}  既存 {100*old/len(g):.1f}%")
    # 欠測バイアス: 失敗/空のドメインの特徴（TLD）
    fails = c.execute("SELECT s.apex FROM scale_sample s WHERE s.kind='A' AND s.status!='ok'").fetchall()
    if fails:
        from collections import Counter
        tl = Counter(a.split(".")[-1] for (a,) in fails).most_common(6)
        tl_ok = Counter(r[0].split(".")[-1] for r in rows).most_common(6)
        print(f"  欠測TLD上位: {tl}\n  成功TLD上位: {tl_ok}")

    print("\n==== 主軸B: 先行性（Show HN 過去24か月・無作為） ====")
    stb = dict(c.execute("SELECT status, COUNT(*) FROM scale_sample WHERE kind='B' GROUP BY status").fetchall())
    totb = sum(stb.values())
    print(f"  試行 {totb}件  ok={stb.get('ok',0)}  empty={stb.get('empty',0)}  fail={stb.get('fail',0)}")
    lt_new = [r for r in c.execute("""SELECT l.lead_days, s.stratum FROM scale_sample s JOIN leadtime l
        ON l.apex=s.apex WHERE s.kind='B' AND s.status='ok' AND l.lead_days IS NOT NULL""")]
    lt_all = [x for (x,) in c.execute("SELECT lead_days FROM leadtime WHERE lead_days IS NOT NULL")]
    for label, L in [("新規標本(24か月無作為)", [x for x, _ in lt_new]), ("全体(旧179件+新規)", lt_all)]:
        if not L: continue
        n = len(L); pre = sum(1 for x in L if x >= 0); lo, hi = wilson(pre, n)
        band = sorted(x for x in L if 0 <= x <= 365)
        q = statistics.quantiles(band, n=4) if len(band) >= 4 else [0, 0, 0]
        print(f"  [{label}] 突合n={n}  先行率={100*pre/n:.1f}% (CI {100*lo:.1f}-{100*hi:.1f})  負={n-pre}件")
        if band:
            print(f"      1年以内帯 n={len(band)}  中央値={statistics.median(band):.0f}日 (p25={q[0]:.0f}, p75={q[2]:.0f})"
                  f"  30日以上={100*sum(x>=30 for x in band)/len(band):.0f}%  90日以上={100*sum(x>=90 for x in band)/len(band):.0f}%"
                  f"  0-1日={sum(x<=1 for x in band)}件  1年超(古ドメイン再利用)={sum(1 for x in L if x>365)}件")
    c.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"
    if mode == "firstcert": run_firstcert(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif mode == "plan_a": plan_a(int(sys.argv[2]) if len(sys.argv) > 2 else 1000)
    elif mode == "leadtime": run_leadtime(int(sys.argv[2]) if len(sys.argv) > 2 else 35,
                                          int(sys.argv[3]) if len(sys.argv) > 3 else None)
    else: report()
