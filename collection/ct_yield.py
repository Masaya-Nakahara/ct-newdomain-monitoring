"""
全量ウィンドウ取得による被覆率・歩留まりの実測 (Option A / 柱②の補強)

crt.sh Postgres から「直近の一定時間幅」を上限なしで全件取得し、
  - 真のファイアホース流量(証明書/分, /日換算)
  - 我々の日次サンプル(50k/日)の実被覆率
  - サブドメイン多様性フィルタ後の"新規apex"流量
を実測する。これにより「4.5%サンプルでは検出できないが、全量なら原理的に捕捉できる」
という設計上の対比を定量化する。

  python ct_yield.py [WINDOW_MIN]   直近 WINDOW_MIN 分(CT時刻, 既定15)を全件取得し集計。

出力: ct_data.db の fullwindow(win_start, win_end, minutes, n_certs, n_apex, n_clean_apex, at)
"""
import sys, time, datetime as dt, sqlite3, re
import pg8000.native as pg
from ct_precision import apex, SHARED

DB = "ct_data.db"
RND = re.compile(r"^[a-z0-9]{10,}$")


def _conn():
    import time as _t
    for a in range(10):
        try:
            con = pg.Connection(user="guest", host="crt.sh", port=5432, database="certwatch", timeout=90)
            con.run("SET statement_timeout = 90000")
            return con
        except Exception as ex:
            print(f"    conn retry {a+1}: {repr(ex)[:60]}", flush=True)
            _t.sleep(5 + a * 3)
    return None


def fetch_window(minutes, offset_hours=36, cap=40000, batch=2000, max_fail=10):
    """取り込み完了済みの過去(既定36h前)から minutes 分ぶんを全件取得。"""
    # 開始カーソル = 最新 - offset_hours
    con = _conn()
    if con is None:
        return [], None, None
    mx = con.run("SELECT max(ENTRY_TIMESTAMP) FROM ct_log_entry")[0][0]
    con.close()
    cursor = str(mx - dt.timedelta(hours=offset_hours))
    print(f"[yield] 開始カーソル(取り込み完了帯): {cursor}", flush=True)
    out, fails, t_start = [], 0, None
    while True:
        q = (f"SELECT e.ENTRY_TIMESTAMP, x509_commonName(c.CERTIFICATE) "
             f"FROM ct_log_entry e JOIN certificate c ON c.ID = e.CERTIFICATE_ID "
             f"WHERE e.ENTRY_TIMESTAMP < '{cursor}' ORDER BY e.ENTRY_TIMESTAMP DESC LIMIT {batch}")
        rows = None
        for attempt in range(4):
            con = _conn()
            if con is None:
                time.sleep(4); continue
            try:
                rows = con.run(q); con.close(); break
            except Exception as ex:
                try: con.close()
                except Exception: pass
                print(f"    retry {attempt+1}: {repr(ex)[:70]}", flush=True)
                time.sleep(3 + attempt * 3)
        if rows is None:
            fails += 1
            if fails >= max_fail:
                print(f"    失敗{fails}回、ここまでで返す", flush=True); break
            time.sleep(5); continue
        if not rows:
            break
        if t_start is None:
            t_start = rows[0][0]
        for ts, cn in rows:
            out.append((ts, cn))
        cursor = str(rows[-1][0])
        span_min = (t_start - rows[-1][0]).total_seconds() / 60.0
        print(f"    取得 {len(out)}件  カバー {span_min:.1f}分", flush=True)
        if span_min >= minutes or len(out) >= cap:
            return out, t_start, rows[-1][0]
        if len(rows) < batch:
            break
        time.sleep(0.4)
    return out, t_start, (out[-1][0] if out else None)


def main():
    minutes = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    print(f"[yield] 直近 {minutes}分(CT時刻)を全件取得 ...", flush=True)
    rows, t_start, t_end = fetch_window(minutes)
    if not rows:
        print("取得失敗"); return
    span_min = (t_start - t_end).total_seconds() / 60.0

    n_certs = len(rows)
    apexes = {}
    for ts, cn in rows:
        if not cn:
            continue
        ap, host = apex(cn)
        if ap:
            apexes.setdefault(ap, set()).add(host)
    n_apex = len(apexes)
    # サンプラーと同じ品質フィルタ後の"新規apex候補"
    clean = 0
    for ap, hosts in apexes.items():
        if ap in SHARED:            continue
        if len(hosts) >= 8:         continue
        if RND.match(ap.split(".")[0]): continue
        clean += 1

    # 換算
    per_min = n_certs / span_min if span_min else 0
    daily_certs = per_min * 60 * 24
    apex_per_min = n_apex / span_min if span_min else 0
    daily_apex = apex_per_min * 60 * 24
    clean_per_min = clean / span_min if span_min else 0
    daily_clean = clean_per_min * 60 * 24
    SAMPLE = 50000
    p = min(1.0, SAMPLE / daily_certs) if daily_certs else 0
    cov = 100 * p

    print("\n==== 全量ウィンドウ 実測 ====")
    print(f"実カバー時間幅   : {span_min:.1f}分  ({t_end} 〜 {t_start})")
    print(f"証明書(全件)     : {n_certs}   = {per_min:,.0f}/分  → 日換算 {daily_certs:,.0f}/日")
    print(f"ユニークapex     : {n_apex}   → 日換算 {daily_apex:,.0f}/日")
    print(f"品質フィルタ後apex: {clean}   → 日換算 {daily_clean:,.0f}/日")
    print(f"\n我々の日次サンプル: {SAMPLE:,}証明書/日")
    print(f"==> 実被覆率(証明書ベース): {cov:.2f}%")
    for k in (1, 2, 3, 5):
        catch = 1 - (1 - p) ** k
        print(f"    特定ドメインが早期にk={k}回certを出す場合の捕捉確率(サンプル): {100*catch:.1f}%")
    print(f"    全量処理なら捕捉確率: 100%(全certを見るため)")

    try:
        c = sqlite3.connect(DB, timeout=120); c.execute("PRAGMA busy_timeout=120000")
        c.execute("""CREATE TABLE IF NOT EXISTS fullwindow(
            win_start TEXT, win_end TEXT, minutes REAL, n_certs INT, n_apex INT,
            n_clean_apex INT, daily_certs INT, coverage_pct REAL, at TEXT)""")
        c.execute("INSERT INTO fullwindow VALUES(?,?,?,?,?,?,?,?,?)",
                  (str(t_start), str(t_end), span_min, n_certs, n_apex, clean,
                   int(daily_certs), cov, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
        c.commit(); c.close()
        print("\n-> fullwindow テーブルに保存")
    except Exception as ex:
        print(f"\n(DB保存スキップ: {repr(ex)[:60]})  ※数値は上記の通り")


if __name__ == "__main__":
    main()
