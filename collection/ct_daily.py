"""
日次集計スナップショット -- IEEE TNSM research

方針:
 crt.sh前進ページングはfirehoseに追いつけないため、「今日の分」は実行時点の最新ログを
 一定量サンプリングして当日の新規apexを記録する(一貫サンプリング法)。
 収集時に明確な非サービス(共有インフラ/テナント/ランダム名)は除外し、
 グレー(乱用TLD/IDN/長名等)は削除せず suspect=1 でタグ付け(本判別は後段の分類器)。

- 引数なし : 最新ログをサンプリングして seen に追記し、今日(UTC)を集計
- 日付指定 : 既存 seen からその日付を再集計(サンプリングしない)

  python ct_daily.py
  python ct_daily.py 2026-06-18
"""
import sqlite3, datetime as dt, sys

DB = "ct_data.db"
CSV = "ct_daily.csv"
SAMPLE_N = 50000   # 1日あたりの最新ログサンプル件数(カバレッジ/統計力向上)


def _add_col(c, table, col, decl):
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except Exception:
            pass


def _ensure(c):
    c.execute("""CREATE TABLE IF NOT EXISTS seen(
        apex TEXT PRIMARY KEY, first_seen_ts TEXT, first_seen_date TEXT,
        tld TEXT, sample_host TEXT, suspect INTEGER DEFAULT 0)""")
    c.execute("""CREATE TABLE IF NOT EXISTS daily(
        date TEXT PRIMARY KEY, new_apex INT, new_clean INT, sample_n INT,
        top_tlds TEXT, generated_at TEXT)""")
    _add_col(c, "seen", "suspect", "INTEGER DEFAULT 0")
    _add_col(c, "daily", "new_clean", "INT")
    _add_col(c, "daily", "sample_n", "INT")


def collect_today_sample(n=SAMPLE_N, db=DB):
    """実行時点の最新ログをサンプリングし、新規apexを seen に追記。"""
    from ct_precision import fetch_recent, apex, SHARED
    from ct_quality import should_skip, is_suspect
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    cns = fetch_recent(n)
    c = sqlite3.connect(db, timeout=30)
    _ensure(c)
    added = 0
    for cn in cns:
        ap, host = apex(cn)
        if not ap or should_skip(ap, host, SHARED):
            continue
        tld = ap.rsplit(".", 1)[-1]
        sus = 1 if is_suspect(ap, tld, host) else 0
        cur = c.execute(
            "INSERT OR IGNORE INTO seen(apex,first_seen_ts,first_seen_date,tld,sample_host,suspect)"
            " VALUES(?,?,?,?,?,?)",
            (ap, now.isoformat(timespec="seconds"), today, tld, host, sus))
        if cur.rowcount:
            added += 1
    c.commit()
    c.close()
    return today, len(cns), added


def snapshot(date=None, sample_n=None, db=DB):
    """seen から date(既定=今日UTC)を集計して daily/CSV に upsert(冪等)。"""
    if date is None:
        date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    c = sqlite3.connect(db, timeout=30)
    _ensure(c)
    n = c.execute("SELECT COUNT(*) FROM seen WHERE first_seen_date=?", (date,)).fetchone()[0]
    nclean = c.execute("SELECT COUNT(*) FROM seen WHERE first_seen_date=? AND COALESCE(suspect,0)=0",
                       (date,)).fetchone()[0]
    tlds = c.execute("SELECT tld, COUNT(*) FROM seen WHERE first_seen_date=? AND COALESCE(suspect,0)=0 "
                     "GROUP BY tld ORDER BY COUNT(*) DESC LIMIT 10", (date,)).fetchall()
    top = ";".join(f"{t}:{k}" for t, k in tlds)
    gen = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    prev = c.execute("SELECT sample_n FROM daily WHERE date=?", (date,)).fetchone()
    sn = sample_n if sample_n is not None else (prev[0] if prev else None)
    c.execute("INSERT INTO daily(date,new_apex,new_clean,sample_n,top_tlds,generated_at) "
              "VALUES(?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET new_apex=excluded.new_apex, "
              "new_clean=excluded.new_clean, sample_n=excluded.sample_n, "
              "top_tlds=excluded.top_tlds, generated_at=excluded.generated_at",
              (date, n, nclean, sn, top, gen))
    c.commit()
    rows = c.execute("SELECT date,new_apex,new_clean,sample_n,top_tlds,generated_at "
                     "FROM daily ORDER BY date").fetchall()
    with open(CSV, "w", encoding="utf-8") as f:
        f.write("date,new_apex,new_clean,sample_n,top_tlds,generated_at\n")
        for r in rows:
            f.write(f'{r[0]},{r[1]},{r[2]},{r[3]},"{r[4]}",{r[5]}\n')
    c.close()
    return date, n, nclean, top


if __name__ == "__main__":
    if len(sys.argv) > 1:
        date, n, nclean, top = snapshot(sys.argv[1])
        print(f"[daily/再集計] {date}: 新規apex={n}(クリーン {nclean})")
    else:
        print(f"[daily] 最新ログを{SAMPLE_N}件サンプリング中 ...", flush=True)
        today, sampled, added = collect_today_sample()
        date, n, nclean, top = snapshot(today, sample_n=sampled)
        print(f"[daily] {date}: サンプル{sampled}件 → 新規apex +{added}（当日: 計{n} / クリーン{nclean}）")
    print(f"        クリーンTLD内訳: {top}")
    print(f"        -> daily テーブル / {CSV}")
