# BMW i4 watchdog

Hourly Claude Code cloud routine runs `python3 watch.py`, which scrapes autobazar.eu, auto.bazos.sk
and AutoScout24 (DE+AT) for BMW i4 listings and pushes new ones / price drops to ntfy.sh.
`seen.json` is the state (committed back after every run). Delete it to re-seed silently.
