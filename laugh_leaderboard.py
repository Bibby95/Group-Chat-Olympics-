#!/usr/bin/env python3
"""Rank an iMessage group chat by messages sent and laugh reactions.

Usage:
  laugh_leaderboard.py snapshot
  laugh_leaderboard.py list [--top N]
  laugh_leaderboard.py participants --chat ID [--names FILE]
  laugh_leaderboard.py analyze --chat ID [--names FILE] [--tz TZ] [--min-messages N]

Reads a local snapshot of ~/Library/Messages/chat.db (sqlite, opened mode=ro).
Stdlib only, py3.9+.
"""
import argparse, csv, datetime, json, os, re, shutil, sqlite3, sys
from collections import defaultdict

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "out")
SNAPSHOT = os.path.join(DATA_DIR, "chat.db")
APPLE_EPOCH = datetime.datetime(2001, 1, 1, tzinfo=datetime.timezone.utc)
LAUGH = "\U0001F602"
LAUGH_ONLY = re.compile(r"^[\s\U0001F602️]+$")

# associated_message_type: 2000-2005 tapback add (2003=haha), 2006 emoji reaction
# (emoji in associated_message_emoji), 3000-3006 the matching removal.
HAHA_ADD, EMOJI_ADD = 2003, 2006


def connect():
    if not os.path.exists(SNAPSHOT):
        sys.exit("no snapshot; run: laugh_leaderboard.py snapshot")
    return sqlite3.connect(f"file:{SNAPSHOT}?mode=ro", uri=True)


def ts(ns, tz=None):
    if not ns:
        return None
    d = APPLE_EPOCH + datetime.timedelta(seconds=ns / 1e9)  # ns since 2001-01-01
    return d.astimezone(tz) if tz else d


def decode_attributed_body(blob):
    # text=NULL on newer macOS; body is an NSKeyedArchiver typedstream.
    # Grab the first NSString payload, length-prefixed at the '+' marker.
    if blob is None:
        return None
    try:
        b = bytes(blob)
        i = b.find(b"NSString")
        if i < 0:
            return None
        i = b.find(b"+", i)
        if i < 0:
            return None
        i += 1
        ln = b[i]
        if ln == 0x81:
            ln = int.from_bytes(b[i + 1:i + 3], "little"); i += 3
        elif ln == 0x82:
            ln = int.from_bytes(b[i + 1:i + 5], "little"); i += 5
        else:
            i += 1
        return b[i:i + ln].decode("utf-8", "replace")
    except Exception:
        return None


def cmd_snapshot(_):
    src = os.path.expanduser("~/Library/Messages")
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        for f in ("chat.db", "chat.db-wal", "chat.db-shm"):
            p = os.path.join(src, f)
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(DATA_DIR, f))
    except PermissionError:
        sys.exit("needs Full Disk Access for your terminal "
                 "(System Settings > Privacy & Security > Full Disk Access), then re-run")
    print(f"snapshot -> {DATA_DIR}")


def cmd_list(args):
    cur = connect().cursor()
    q = """
    SELECT c.ROWID, c.display_name,
           (SELECT COUNT(*) FROM chat_handle_join j WHERE j.chat_id=c.ROWID),
           (SELECT COUNT(*) FROM chat_message_join cm JOIN message m ON m.ROWID=cm.message_id
             WHERE cm.chat_id=c.ROWID AND m.item_type=0 AND m.associated_message_type=0),
           (SELECT MIN(m.date) FROM chat_message_join cm JOIN message m ON m.ROWID=cm.message_id WHERE cm.chat_id=c.ROWID),
           (SELECT MAX(m.date) FROM chat_message_join cm JOIN message m ON m.ROWID=cm.message_id WHERE cm.chat_id=c.ROWID)
    FROM chat c WHERE c.style=43
    ORDER BY 4 DESC LIMIT ?
    """
    print(f"{'id':>5}  {'msgs':>7}  {'people':>6}  {'first':>10}  {'last':>10}  name")
    for rowid, name, nparts, nmsgs, lo, hi in cur.execute(q, (args.top,)):
        f = ts(lo).strftime("%Y-%m-%d") if lo else "?"
        l = ts(hi).strftime("%Y-%m-%d") if hi else "?"
        print(f"{rowid:>5}  {nmsgs:>7}  {nparts:>6}  {f:>10}  {l:>10}  {name or '-'}")


def chat_rows(cur, chat_id):
    return cur.execute("""
        SELECT m.guid, m.text, m.attributedBody, m.is_from_me, h.id,
               m.item_type, m.associated_message_type, m.associated_message_guid,
               m.associated_message_emoji, m.thread_originator_guid, m.date, m.date_retracted
        FROM chat_message_join cm JOIN message m ON m.ROWID=cm.message_id
        LEFT JOIN handle h ON h.ROWID=m.handle_id
        WHERE cm.chat_id=? ORDER BY m.date""", (chat_id,)).fetchall()


def cmd_participants(args):
    cur = connect().cursor()
    counts = defaultdict(int)
    for r in chat_rows(cur, args.chat):
        if r[5] != 0 or r[6] != 0 or r[11]:
            continue
        counts["me" if r[3] else (r[4] or "?")] += 1
    for h, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"{n:>6}  {h}")
    if not os.path.exists(args.names):
        with open(args.names, "w") as f:
            json.dump({h: ("Me" if h == "me" else "")
                       for h, _ in sorted(counts.items(), key=lambda x: -x[1])}, f, indent=2)
        print(f"\nwrote {args.names}; fill in names (same name on two handles merges them)")


def resolve_name(names, h):
    if h in names and names[h]:
        return names[h]
    if h and "@" in h:
        return f"unknown ({h.split('@')[0][:6]})"
    return f"unknown ({h[-4:]})" if h else "unknown"


def base_guid(aguid):
    # reaction targets look like "p:0/GUID" or "bp:GUID"
    if not aguid:
        return None
    return aguid.split("/", 1)[1] if "/" in aguid else aguid.split(":", 1)[-1]


def cmd_analyze(args):
    tz = ZoneInfo(args.tz) if (ZoneInfo and args.tz) else None
    names = json.load(open(args.names)) if os.path.exists(args.names) else {}
    cur = connect().cursor()
    rows = chat_rows(cur, args.chat)
    if not rows:
        sys.exit(f"chat {args.chat}: no messages")

    msgs = {}                # guid -> (sender, dt)
    sent = defaultdict(int)
    reactions = {}           # (sender, target) -> (type, emoji, date); latest state wins
    laugh_texts = []
    excluded = 0
    for (guid, text, body, from_me, hid, item_type, amt, aguid, aemoji, tguid, date, retracted) in rows:
        sender = "me" if from_me else hid
        if item_type != 0 or retracted:
            excluded += 1
            continue
        if amt != 0:
            if 2000 <= amt <= 3007:
                reactions[(sender, aguid)] = (amt, aemoji, date)
            continue
        d = ts(date, tz)
        msgs[guid] = (sender, d)
        sent[sender] += 1
        t = text if text else decode_attributed_body(body)
        if t and LAUGH_ONLY.match(t) and LAUGH in t:
            laugh_texts.append((sender, tguid, d))

    # laugh = active haha tapback, active emoji reaction containing the laugh emoji,
    # or a laugh-only message that's an explicit thread reply to someone else
    events, unresolved, haha, emoji_taps = [], 0, 0, 0
    for (sender, aguid), (amt, aemoji, date) in reactions.items():
        is_laugh = amt == HAHA_ADD or (amt == EMOJI_ADD and aemoji and LAUGH in aemoji)
        haha += amt == HAHA_ADD
        emoji_taps += amt == EMOJI_ADD and bool(aemoji) and LAUGH in (aemoji or "")
        if not is_laugh:
            continue
        target = msgs.get(base_guid(aguid))
        if target:
            events.append((sender, target[0], ts(date, tz), base_guid(aguid)))
        else:
            unresolved += 1
    unattributed = 0
    for sender, tguid, d in laugh_texts:
        target = msgs.get(tguid) if tguid else None
        if target and target[0] != sender:
            events.append((sender, target[0], d, tguid))
        else:
            unattributed += 1  # bare laugh with no reply thread: don't guess

    received, given = defaultdict(int), defaultdict(int)
    duo, per_msg, by_hour = defaultdict(int), defaultdict(int), defaultdict(int)
    recv_year, sent_year = defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int))
    self_laughs = 0
    for g, r, d, tguid in events:
        if g == r:
            self_laughs += 1
            continue
        given[g] += 1; received[r] += 1
        duo[(g, r)] += 1; per_msg[tguid] += 1
        if d: by_hour[d.hour] += 1; recv_year[d.year][r] += 1
    for s, d in msgs.values():
        if d: sent_year[d.year][s] += 1

    people = {}
    for h in set(list(sent) + list(received) + list(given)):
        p = people.setdefault(resolve_name(names, h), {"sent": 0, "received": 0, "given": 0})
        p["sent"] += sent.get(h, 0); p["received"] += received.get(h, 0); p["given"] += given.get(h, 0)
    total = sum(p["sent"] for p in people.values())
    for p in people.values():
        p["share_pct"] = round(100 * p["sent"] / total, 2) if total else 0
        p["laughs_per_100"] = round(100 * p["received"] / p["sent"], 2) if p["sent"] else None
        p["gives_per_100"] = round(100 * p["given"] / p["sent"], 2) if p["sent"] else None

    top_messages = []
    for tguid, n in sorted(per_msg.items(), key=lambda x: -x[1])[:3]:
        r = cur.execute("SELECT text, attributedBody, is_from_me,"
                        " (SELECT id FROM handle WHERE ROWID=handle_id), date"
                        " FROM message WHERE guid=?", (tguid,)).fetchone()
        t = r[0] if r[0] else decode_attributed_body(r[1])
        top_messages.append({"laughs": n,
                             "sender": resolve_name(names, "me" if r[2] else r[3]),
                             "date": ts(r[4], tz).strftime("%Y-%m-%d"),
                             "text": (t or "(attachment)")[:300]})

    assert sum(given.values()) == sum(received.values())

    results = {
        "total_messages": total,
        "total_laughs": sum(p["received"] for p in people.values()),
        "date_range": [min(d for _, d in msgs.values()).strftime("%Y-%m-%d"),
                       max(d for _, d in msgs.values()).strftime("%Y-%m-%d")],
        "counts": {"haha_tapbacks": haha, "emoji_laugh_tapbacks": emoji_taps,
                   "unresolved_targets": unresolved, "self_laughs_excluded": self_laughs,
                   "laugh_only_messages": len(laugh_texts),
                   "laugh_only_unattributed": unattributed,
                   "excluded_rows": excluded},
        "people": dict(sorted(people.items(), key=lambda x: -x[1]["sent"])),
        "messages_by_year": {y: dict(sorted(v.items(), key=lambda x: -x[1])) for y, v in sorted(sent_year.items())},
        "laughs_received_by_year": {y: dict(sorted(v.items(), key=lambda x: -x[1])) for y, v in sorted(recv_year.items())},
        "top_duos": [{"giver": resolve_name(names, a), "receiver": resolve_name(names, b), "laughs": n}
                     for (a, b), n in sorted(duo.items(), key=lambda x: -x[1])[:10]],
        "laughs_by_hour": dict(sorted(by_hour.items())),
        "most_laughed_messages": top_messages,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "results.json"), "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "leaderboard.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["person", "sent", "share_pct", "laughs_received", "laughs_given",
                    "laughs_per_100", "gives_per_100"])
        for n, p in results["people"].items():
            w.writerow([n, p["sent"], p["share_pct"], p["received"], p["given"],
                        p["laughs_per_100"], p["gives_per_100"]])

    print(f"\n{results['total_messages']:,} messages / {results['total_laughs']:,} laughs / "
          f"{results['date_range'][0]} to {results['date_range'][1]}")

    def board(title, key, fmt, gate=0):
        print(f"\n{title}")
        ranked = [(n, p) for n, p in people.items() if p["sent"] >= gate and p[key]]
        for i, (n, p) in enumerate(sorted(ranked, key=lambda x: -x[1][key])[:10], 1):
            print(f"  {i:>2}. {n:<24} {fmt(p)}")

    board("messages sent", "sent", lambda p: f"{p['sent']:>5}  ({p['share_pct']}%)")
    board("laughs received", "received", lambda p: f"{p['received']:>5}")
    board(f"laughs per 100 messages (min {args.min_messages} sent)", "laughs_per_100",
          lambda p: f"{p['laughs_per_100']:>6}", gate=args.min_messages)
    board("laughs given", "given", lambda p: f"{p['given']:>5}")
    print(f"\nwrote {OUT_DIR}/results.json, {OUT_DIR}/leaderboard.csv")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    p = sub.add_parser("list")
    p.add_argument("--top", type=int, default=15)
    p = sub.add_parser("participants")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--names", default="names.json")
    p = sub.add_parser("analyze")
    p.add_argument("--chat", type=int, required=True)
    p.add_argument("--names", default="names.json")
    p.add_argument("--tz", default=None)
    p.add_argument("--min-messages", type=int, default=50)
    args = ap.parse_args()
    {"snapshot": cmd_snapshot, "list": cmd_list,
     "participants": cmd_participants, "analyze": cmd_analyze}[args.cmd](args)


if __name__ == "__main__":
    main()
