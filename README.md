# laugh-leaderboard

Ranks an iMessage group chat by messages sent, laughs received, laughs given, and
laughs per 100 messages. Local only, stdlib only, macOS + Python 3.9+.

macOS keeps Messages in `~/Library/Messages/chat.db` (sqlite). Tapbacks are rows
with `associated_message_type` (2003 = haha, 2006 = emoji reaction, 3000s =
removals) pointing at the target message's guid, so reactions can be counted
per message and per sender.

## Usage

```sh
python3 laugh_leaderboard.py snapshot                  # copy chat.db to ./data
python3 laugh_leaderboard.py list                      # find your chat id
python3 laugh_leaderboard.py participants --chat 51    # dump senders, write names.json
# edit names.json
python3 laugh_leaderboard.py analyze --chat 51 --tz America/New_York
```

`snapshot` needs Full Disk Access for your terminal (System Settings > Privacy &
Security). Everything after runs against the copy; the live db is never opened.

`participants` writes a `names.json` skeleton mapping handles to display names.
Give two handles the same name to merge one person's phone number and iCloud
email. Blank entries render as `unknown (last-4)`.

`analyze` prints the leaderboards and writes `out/results.json` and
`out/leaderboard.csv`: chat share, laughs given/received, per-100 rates
(`--min-messages` floor, default 50), giver/receiver pairs, per-year splits,
laughs by hour, top messages.

## Counting

- message = user-sent row: `item_type = 0`, not a reaction row, not unsent
- laugh = active haha tapback, active emoji reaction containing 😂, or a
  😂-only message that is an explicit thread reply to someone else's message
- latest reaction state per (sender, target) wins, so removed/changed tapbacks
  don't count
- one 😂😂😂 message = one laugh; bare 😂 with no reply thread is counted as
  unattributed, not credited; self-laughs excluded
- laughs given == laughs received (asserted)

Note: `text` is NULL on recent macOS versions for most rows; the body lives in
the `attributedBody` typedstream blob, which is parsed best-effort.

`data/`, `out/`, and `names.json` are gitignored — don't commit your chat db or
your friends' numbers.

MIT
