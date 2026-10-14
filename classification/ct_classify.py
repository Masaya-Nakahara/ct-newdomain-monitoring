"""
コンテンツ分類器 (案A: ヒューリスティック) -- IEEE TNSM research
ドメインのホームページを取得(キャッシュ)し分類する。
カテゴリ: service/commerce/business/blog/parking/spam/error/unknown

  python ct_classify.py [N]     取得(未キャッシュのみ)＋分類。既定 N=300
  python ct_classify.py reclass 取得せずキャッシュから再分類のみ(高速・反復用)

出力: ct_data.db の pages(取得キャッシュ) / classified(分類) / classify_results.csv
"""
import sqlite3, re, sys, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import requests

DB = "ct_data.db"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (research service-measurement; academic)"})
try:
    import tldextract
    def apex_of(host):
        e = tldextract.extract(host or "")
        return f"{e.domain}.{e.suffix}".lower() if e.domain and e.suffix else ""
except Exception:
    def apex_of(host): return (host or "").lower()

KW = {
 "parking": r"(domain (is )?for sale|buy this domain|buy domain|parked (free|domain)|is parked|"
            r"godaddy|sedo\.com|hugedomains|dan\.com|afternic|coming soon|under construction|"
            r"website is being created|hier entsteht|default web page|apache2 (ubuntu|debian) default|"
            r"welcome to nginx|account suspended|site not found|placeholder page|this domain (is|may)|"
            r"expired domain|renew (your|this) domain)",
 "service": r"(sign ?up|signup|log ?in|login|get started|start (for )?free|free trial|start your free|"
            r"pricing|dashboard|create (an )?account|book a demo|request a demo|our platform|try it free|"
            r"api (key|reference|docs|documentation)|no credit card|integrations?|onboarding|workspace|"
            r"14.day trial|open source|自動化|無料で(始|試))",
 "commerce": r"(add to cart|add to basket|shop now|checkout|free shipping|in stock|your cart|"
             r"buy now|product details|our products|カート|購入|送料無料|価格|在庫|加入购物车|立即购买)",
 "business": r"(contact us|about us|our services|our team|opening hours|get a quote|our clients|"
             r"located in|call us|©\s?20|all rights reserved|会社概要|お問い合わせ|営業時間|"
             r"nos services|impressum|kontakt|unternehmen|clinic|dental|law firm|restaurant|hotel)",
 "blog": r"(read more|recent posts|latest posts|categories|archive|posted (on|by)|leave a comment|"
         r"subscribe to|newsletter|my blog|続きを読む|カテゴリー|最近の投稿)",
 "spam": r"(casino|slot(s| machine| online)|betting|sportsbook|poker|gambling|порно|porn|xxx|xnxx|"
         r"escort|nude|onlyfans|leaked|viagra|cialis|crypto ?giveaway|free ?money|loan approval|"
         r"投注|彩票|博彩|赌场|优惠|bonus code|18\+|replica watches|essay writing service|"
         r"казино|ставки|bahis|apostas|jogo do bicho|slot ?gacor|situs)",
}
KW = {k: re.compile(v, re.I | re.S) for k, v in KW.items()}


def fetch(domain):
    for scheme in ("https://", "http://"):
        try:
            r = S.get(scheme + domain, timeout=9, allow_redirects=True)
            if r.status_code >= 400:
                continue
            return r.status_code, r.url, r.text[:80000]
        except Exception:
            continue
    return None, None, None


def strip_text(html):
    h = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", html or "")
    h = re.sub(r"(?s)<!--.*?-->", " ", h)
    t = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", t).strip()


def meta_desc(html):
    m = re.search(r'(?is)<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]*content=["\'](.*?)["\']', html or "")
    return (m.group(1).strip() if m else "")


def title_of(html):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html or "")
    return re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else ""


def _ndistinct(rx, s):
    return len(set(m.group(0).lower() for m in rx.finditer(s)))


def classify(domain, host, status, final_url, title, meta, text):
    sig = []
    if status is None:
        return "error", "unreachable"
    if len(text) < 100 and not title:
        return "error", "empty"
    tld = domain.rsplit(".", 1)[-1]
    tm = f"{title} {meta}".lower()
    body = text[:4000].lower()
    hay = tm + " " + body
    fap = apex_of(re.sub(r"^https?://", "", final_url or "").split("/")[0])
    if fap and fap != domain and domain not in fap:
        sig.append("redirect_offsite")
    hits = {k: bool(rx.search(hay)) for k, rx in KW.items()}
    subsvc = bool(re.match(r"^(app|api|dashboard|console|portal|dev|staging)\.", host or ""))
    if subsvc:
        sig.append("app_subdomain")
    for k, v in hits.items():
        if v:
            sig.append(k)
    if hits["parking"]:
        return "parking", "|".join(sig)
    if KW["spam"].search(tm) or _ndistinct(KW["spam"], body) >= 2:   # 強い証拠のみ
        return "spam", "|".join(sig)
    if hits["service"] or subsvc or tld in ("app", "dev"):
        return "service", "|".join(sig)
    if hits["commerce"]:
        return "commerce", "|".join(sig)
    if hits["business"]:
        return "business", "|".join(sig)
    if hits["blog"]:
        return "blog", "|".join(sig)
    if len(text) < 200 and "redirect_offsite" not in sig:
        return "parking", "|".join(sig)
    return "unknown", "|".join(sig)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "300"
    reclass = (arg == "reclass")
    n = 300 if reclass else int(arg)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS pages(
        apex TEXT PRIMARY KEY, host TEXT, status INT, url TEXT, title TEXT, meta TEXT,
        text TEXT, fetched_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS classified(
        apex TEXT PRIMARY KEY, tld TEXT, category TEXT, title TEXT, url TEXT, signals TEXT,
        status INT, fetched_at TEXT)""")
    rows = c.execute("SELECT apex,tld,sample_host FROM seen WHERE COALESCE(suspect,0)=0 "
                     "ORDER BY first_seen_ts DESC LIMIT ?", (n,)).fetchall()
    cached = {r[0]: r for r in c.execute("SELECT apex,host,status,url,title,meta,text FROM pages")}

    if not reclass:
        todo = [r for r in rows if r[0] not in cached]
        print(f"[classify] 対象 {len(rows)} / 未取得 {len(todo)} を取得 ...", flush=True)
        def fetch_row(row):
            apex, tld, host = row
            st, url, html = fetch(apex)
            return (apex, host, st or 0, url or "", title_of(html), meta_desc(html), strip_text(html)[:8000])
        with ThreadPoolExecutor(max_workers=24) as ex:
            fetched = list(ex.map(fetch_row, todo))
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for p in fetched:
            c.execute("INSERT OR REPLACE INTO pages(apex,host,status,url,title,meta,text,fetched_at) "
                      "VALUES(?,?,?,?,?,?,?,?)", (*p, now))
            cached[p[0]] = p[:7]
        c.commit()
    else:
        print(f"[classify] キャッシュから再分類のみ({len(cached)}件保有)", flush=True)

    # 分類(キャッシュから)
    import csv
    from collections import Counter
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    res = []
    for apex, tld, host in rows:
        p = cached.get(apex)
        if not p:
            continue
        _, phost, status, url, title, meta, text = p
        status = status if status else None
        cat, sigs = classify(apex, phost or host, status, url, title or "", meta or "", text or "")
        c.execute("INSERT OR REPLACE INTO classified(apex,tld,category,title,url,signals,status,fetched_at) "
                  "VALUES(?,?,?,?,?,?,?,?)", (apex, tld, cat, (title or "")[:100], url, sigs, status or 0, now))
        res.append({"apex": apex, "tld": tld, "category": cat, "title": (title or "")[:100], "url": url, "signals": sigs})
    c.commit()
    c.close()

    with open("classify_results.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["apex", "tld", "pred_category", "true_category", "title", "url", "signals"])
        for r in res:
            w.writerow([r["apex"], r["tld"], r["category"], "", r["title"], r["url"], r["signals"]])

    dist = Counter(r["category"] for r in res)
    print("\n==== 分類結果 ====")
    for cat, k in dist.most_common():
        print(f"  {cat:<10} {k:>4}  ({100*k/max(1,len(res)):.0f}%)")
    print("\n-- カテゴリ別 例 --")
    for cat in ["service", "commerce", "business", "blog", "parking", "spam"]:
        ex = [r for r in res if r["category"] == cat][:5]
        if ex:
            print(f"[{cat}]")
            for r in ex:
                print(f"   {r['apex']:<26} {r['title'][:46]}")
    print("\n-> classify_results.csv / classified・pages テーブル")


if __name__ == "__main__":
    main()
