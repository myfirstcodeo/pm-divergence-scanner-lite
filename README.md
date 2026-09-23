# Kalshi vs Polymarket Divergence Scanner (lite)

Free, one-file Python script. Pulls every open Kalshi market and the top Polymarket markets through their public
keyless endpoints, matches contracts that ask the same question, and prints the ten biggest price gaps.

```
python scanner_lite.py
```

No API keys, no accounts, nothing installed. Takes 1 to 3 minutes because Kalshi has over 100,000 open markets.

## How matching works
Text matching with hard vetoes: pairs with different numbers (years, brackets), different places, or a
question-changing word on only one side ("closest", "before", "not", "Democrats") are rejected before scoring.
The score blends Jaccard overlap, containment, and a sequence ratio. Default threshold 0.6.

## Full edition
Unlimited rows, HTML report with links to both markets, JSON output, watch mode that rescans on a timer and
prints only new or changed opportunities, and Telegram alerts through your own bot. One-time purchase: https://instaverb.gumroad.com/l/pm-scanner. Bundle with the tax/P&L kit and Sheets odds functions: https://instaverb.gumroad.com/l/pm-toolkit/LAUNCH49 ($10 off through Sep 24).

Not financial advice. Read both venues' rules before acting on any gap; large gaps are usually rule differences.
