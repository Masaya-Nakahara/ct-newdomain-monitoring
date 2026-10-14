"""
先行性(lead-time)の測定 (IEEE TNSM / Option A の柱②) -- 客観的な日付だけで完結。

正解源: Hacker News の「Show HN」= 新サービスのローンチ告知(日付つき・無料API)。
各サービスのドメインについて、CTの「史上初の証明書」日(first-cert)を取り、
    lead_days = HN登場日 - first_cert日
を測る。lead>0 なら「証明書(CT)がHN露出より先行」= CTは早期シグナルになりうる。

  python ct_leadtime.py hn [N]   直近 N 件(既定60)の Show HN を取得→ドメイン抽出→
                                 first-cert取得→リード時間集計。

出力: ct_data.db の leadtime(apex, hn_date, hn_ts, hn_title, hn_url, points, first_cert_date, lead_days, checked_at)
"""
import sqlite3, sys, datetime as dt, time
from concurrent.futures import ThreadPoolExecutor
import requests
from ct_firstcert import first_cert   # crt.sh から最小 not_before を取る(バックオフ付き)

try:
    import tldextract
    def apex_of(host):
        e = tldextract.extract(host or "")
        return f"{e.domain}.{e.suffix}".lower() if e.domain and e.suffix else ""
except Exception:
    def apex_of(host): return (host or "").lower()

DB = "ct_data.db"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (research service-measurement; academic)"})
# 製品ドメインでない(告知先が別サービス)ものは除外
SKIP = {"github.com", "gitlab.com", "youtube.com", "youtu.be", "twitter.com", "x.com",
        "medium.com", "notion.site", "notion.so", "google.com", "docs.google.com",
        "apps.apple.com", "play.google.com", "news.ycombinator.com", "reddit.com",
        "producthunt.com", "substack.com", "vercel.app", "netlify.app", "herokuapp.com",
        "streamlit.app", "huggingface.co", "replit.com", "linkedin.com", "facebook.com",
        "discord.gg", "discord.com", "loom.com", "figma.com", "dev.to",
        "blogspot.com", "surge.sh", "wordpress.com", "tumblr.com", "gumroad.com",
        "itch.io", "codepen.io", "observablehq.com", "wikipedia.org", "archive.org",
        "sourceforge.net", "bitbucket.org", "gitlab.io", "cloudflare.com", "web.app"}


def show_hn(n):
    """Algolia HN API から Show HN を新しい順に取得。(apex, ts, date, title, url, points) を返す。"""
    out = []; page = 0; seen = set()
    while len(out) < n and page < 20:
        url = ("https://hn.algolia.com/api/v1/search_by_date"
               f"?tags=show_hn&hitsPerPage=100&page={page}")
        try:
            r = S.get(url, timeout=20)
            hits = r.json().get("hits", [])
        except Exception:
            break
        if not hits:
            break
        for h in hits:
            u = h.get("url") or ""
            if not u:
                continue
            host = u.split("//", 1)[-1].split("/", 1)[0].split(":")[0].lower()
            ax = apex_of(host)
            if not ax or host in SKIP or ax in SKIP:
                continue
            if ax in seen:   # apex 重複は最古のHN(=新しい順取得なので後勝ちで上書き)
                pass
            ts = h.get("created_at_i")
            date = dt.datetime.utcfromtimestamp(ts).date().isoformat() if ts else None
            out.append((ax, ts, date, (h.get("title") or "")[:120], u[:120], h.get("points") or 0))
            seen.add(ax)
        page += 1
    # apex 重複除去(最古=最小tsを残す)
    best = {}
    for row in out:
        ax, ts = row[0], row[1]
        if ax not in best or (ts and ts < best[ax][1]):
            best[ax] = row
    return list(best.values())[:n]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "hn"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    c = sqlite3.connect(DB, timeout=180)
    c.execute("PRAGMA busy_timeout=180000")   # 他ジョブのロックを待つ
    c.execute("""CREATE TABLE IF NOT EXISTS leadtime(
        apex TEXT PRIMARY KEY, hn_date TEXT, hn_ts INT, hn_title TEXT, hn_url TEXT,
        points INT, first_cert_date TEXT, lead_days INT, checked_at TEXT)""")
    cached = {a for (a,) in c.execute("SELECT apex FROM leadtime WHERE first_cert_date IS NOT NULL")}

    stories = show_hn(n)
    todo = [s for s in stories if s[0] not in cached]
    print(f"[leadtime] Show HN {len(stories)}件(新規{len(todo)}件) の first-cert を取得 ...", flush=True)

    def work(s):
        ax, ts, date, title, url, pts = s
        fcd, _fcts, _k = first_cert(ax)
        return s, fcd

    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    done = 0
    with ThreadPoolExecutor(max_workers=1) as ex:   # 逐次: crt.shに優しく
        for s, fcd in ex.map(work, todo):
            ax, ts, date, title, url, pts = s
            lead = None
            if fcd and date:
                lead = (dt.date.fromisoformat(date) - dt.date.fromisoformat(fcd)).days
            c.execute("INSERT OR REPLACE INTO leadtime VALUES(?,?,?,?,?,?,?,?,?)",
                      (ax, date, ts, title, url, pts, fcd, lead, now))
            done += 1
            if done % 15 == 0:
                c.commit(); print(f"  ... {done}/{len(todo)}", flush=True)
    c.commit()

    # 集計
    rows = c.execute("SELECT apex,hn_date,hn_title,points,first_cert_date,lead_days FROM leadtime "
                     "WHERE first_cert_date IS NOT NULL AND lead_days IS NOT NULL").fetchall()
    c.close()
    leads = sorted(r[5] for r in rows)
    print("\n==== 先行性(lead-time) 集計 ====")
    print(f"突合できたサービス: {len(rows)} / Show HN {len(stories)}")
    if leads:
        def pctl(p): return leads[min(len(leads)-1, int(p*len(leads)))]
        n_lead = sum(1 for l in leads if l >= 0)
        # 「新規ローンチ」らしい範囲(first-certがHNの直近1年内)に限った中央値も出す
        fresh = sorted(l for l in leads if 0 <= l <= 400)
        print(f"lead_days = HN登場日 - first_cert日  (正=CTが先行)")
        print(f"  median={leads[len(leads)//2]}  p25={pctl(.25)}  p75={pctl(.75)}  min={leads[0]} max={leads[-1]}")
        print(f"  CTが先行(lead>=0): {n_lead}/{len(leads)} ({100*n_lead/len(leads):.0f}%)")
        if fresh:
            print(f"  新規ローンチ帯(0<=lead<=400日) の median={fresh[len(fresh)//2]}日  (n={len(fresh)})")
        print("\n-- 例(lead_days昇順・古い順の両端) --")
        srt = sorted(rows, key=lambda r: (r[5] if r[5] is not None else 0))
        for r in srt[:4] + srt[-4:]:
            ax, hd, title, pts, fcd, ld = r
            print(f"  lead={ld:>5}d  {ax:<22} cert={fcd} HN={hd} ({pts}pt) {title[:42]}")


if __name__ == "__main__":
    main()
