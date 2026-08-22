"""Content definitions for the 30 EAGLE-X tutorial videos.

Each video is a list of segments. A segment renders as one slide while its
narration plays. Slide content mirrors the ACTUAL dashboard UI so viewers
learn what they will really see.
"""

EAGLE = "EAGLE-X"

VIDEOS: list[dict] = [
    {
        "n": 1, "slug": "open-eaglex-and-log-in", "cat": "Beginner",
        "title": "How to Open EAGLE-X and Log In",
        "segments": [
            ("Welcome to EAGLE-X",
             ["Statistical market analysis for Deriv indices", "Real WebSocket tick data", "No profit promises — only data"],
             "Welcome to EAGLE X. This platform analyses Deriv synthetic indices using real tick data from the live WebSocket feed. It never promises profits. It shows you evidence."),
            ("Opening the app",
             ["Open the dashboard URL", "Splash screen plays for 8 seconds", "Dashboard loads the Starting XI"],
             "Open your EAGLE X address in any browser. A short splash animation plays, then the dashboard loads automatically. The dashboard is arranged as a football formation called the Starting Eleven."),
            ("The header",
             ["LIVE DATA or DEMO DATA badge", "Market selector on the right", "Latest tick price shown"],
             "At the top you will always see a data badge. Green means live Deriv ticks. Amber means demo ticks. Next to it, the symbol selector switches between volatility ten, twenty five, fifty, seventy five, and one hundred."),
        ],
    },
    {
        "n": 2, "slug": "dashboard-layout", "cat": "Beginner",
        "title": "Understanding the Dashboard Layout",
        "segments": [
            ("The Starting XI",
             ["Eleven panels in a 4-3-3 formation", "GK, CB, LB, RB, DMF … just like football", "Second XI below is the bench"],
             "The dashboard is a football squad. Eleven panels in a four three three formation. The goalkeeper is the Risk Engine. Below the Starting Eleven is the bench, called the Second Eleven."),
            ("Reading panels",
             ["Every panel has a title and a badge", "Rows are label / value pairs", "Colours: green good, red danger"],
             "Each panel has a position code and a status badge. Rows are simple label and value pairs. Green values are healthy, red values need attention."),
            ("Moving around",
             ["Scroll vertically", "Market selector applies to all panels", "Panels refresh automatically"],
             "You scroll vertically through all panels. Changing the market selector updates every panel at once. Panels refresh by themselves every few seconds."),
        ],
    },
    {
        "n": 3, "slug": "read-the-header", "cat": "Beginner",
        "title": "How to Read the Header",
        "segments": [
            ("The top bar",
             ["EAGLE-X logo on the left", "Data badge: LIVE DATA or DEMO DATA", "Latest tick price next to badges"],
             "The header shows the platform name, then the data badge, then the latest tick price for the selected market."),
            ("What the badge means",
             ["Green LIVE — ticks come from Deriv", "Amber DEMO — deterministic fallback", "They are never mixed"],
             "A green badge means the ticks are real Deriv ticks. An amber badge means the demo generator is active, for example when the server location is blocked by Deriv. Live and demo data are never mixed together."),
        ],
    },
    {
        "n": 4, "slug": "check-intelligence-engine", "cat": "Beginner",
        "title": "How to Check the Intelligence Engine",
        "segments": [
            ("The CB panel",
             ["Signal badge: STRONG, WEAK, NEUTRAL", "Data Quality score", "Volatility and Movement regimes"],
             "The Intelligence Engine is the centre back of the squad. Its badge tells you how much the data supports trading. STRONG DATA SUPPORT means evidence is firm. NEUTRAL means do nothing."),
            ("Interpreting the rows",
             ["Data Quality above seventy is good", "Volatility LOW is calmer", "Anomalies should be near zero"],
             "Look at the rows. A data quality score above seventy is reliable. Low volatility is calmer. A high anomaly count means something unusual is happening."),
        ],
    },
    {
        "n": 5, "slug": "scan-all-markets", "cat": "Beginner",
        "title": "How to Scan All Markets",
        "segments": [
            ("Quick market comparison",
             ["The scan-all endpoint", "Ranks all five markets", "Score, signal, quality per market"],
             "The scan-all feature ranks all five volatility markets by score so you know where the best evidence currently lives."),
            ("Picking the best market",
             ["Highest score first", "Prefer STRONG_DATA_SUPPORT", "Avoid high anomaly counts"],
             "Pick the market with the highest score, a strong signal and low anomalies. Then select it in the header market picker."),
        ],
    },
    {
        "n": 6, "slug": "place-matches-trade", "cat": "Beginner",
        "title": "How to Place a MATCHES Trade",
        "segments": [
            ("MATCHES explained",
             ["You win if the final digit equals yours", "Fair probability is ten percent", "Pays roughly nine to one"],
             "A MATCHES contract wins when the last digit of the final tick equals your chosen digit. The fair chance is ten percent, which is why it pays about nine to one."),
            ("Using Trade Planner",
             ["Symbol and contract selectors", "Stake and duration inputs", "Click PLACE TRADE"],
             "Open the Trade Planner panel. Choose your symbol, pick the DIGITMATCH contract, set your stake and duration, then press PLACE TRADE."),
            ("Reading confirmation",
             ["Status shows success or error", "Journal records every trade", "Paper mode is safe practice"],
             "After you place the trade the panel shows success or an error. Every attempt is written into the Trade Journal. Paper mode lets you practise safely."),
        ],
    },
    {
        "n": 7, "slug": "place-differs-trade", "cat": "Beginner",
        "title": "How to Place a DIFFERS Trade",
        "segments": [
            ("DIFFERS explained",
             ["Wins if final digit differs from yours", "Fair probability is ninety percent", "Low payout, high frequency"],
             "DIFFERS wins when the final digit is NOT your digit. That is nine out of ten in fair conditions, so the payout is small."),
            ("When to consider it",
             ["STARVING digit is the candidate", "Digit Hacker psychology tab helps", "Deviation must be negative enough"],
             "Look for a starving digit, one that appears much less than ten percent. The Digit Hacker psychology tab shows this clearly."),
        ],
    },
    {
        "n": 8, "slug": "place-odd-trade", "cat": "Beginner",
        "title": "How to Place an ODD Trade",
        "segments": [
            ("ODD contract",
             ["Last digit must be odd", "Fair chance fifty percent", "Check Digit Hacker contract tab"],
             "An ODD contract wins if the last digit is odd. Fair chance is fifty percent. Use the Digit Hacker contract tab to see if odd digits are running above fifty percent."),
        ],
    },
    {
        "n": 9, "slug": "place-even-trade", "cat": "Beginner",
        "title": "How to Place an EVEN Trade",
        "segments": [
            ("EVEN contract",
             ["Last digit must be even", "Same fifty percent fair chance", "Confirmation matters"],
             "EVEN wins when the last digit is even. Same structure as the ODD contract. Always check the deviation on the Digit Hacker contract tab before you act."),
        ],
    },
    {
        "n": 10, "slug": "place-over-under-trades", "cat": "Beginner",
        "title": "How to Place OVER and UNDER Trades",
        "segments": [
            ("OVER contract",
             ["Final digit greater than barrier", "Barrier zero to eight", "Probability varies by barrier"],
             "OVER wins when the final digit is above your chosen barrier. A barrier of four wins roughly sixty percent in fair conditions, so payouts adjust accordingly."),
            ("UNDER contract",
             ["Final digit below the barrier", "Barrier one to nine", "Same logic in reverse"],
             "UNDER wins when the final digit is below the barrier. The logic is exactly the same, but flipped."),
        ],
    },
    {
        "n": 11, "slug": "use-the-tick-timer", "cat": "Intermediate",
        "title": "How to Use the Tick Timer",
        "segments": [
            ("Tick Timer panel",
             ["GREEN above 1.5 seconds", "YELLOW one to one point five", "RED under half a second"],
             "The Tick Timer counts down to the next tick. Green means you have time. Yellow means be quick. Red means a tick is about to arrive and your window is closed."),
            ("Why timing matters",
             ["Digit outcomes tick by tick", "Enter in GREEN or YELLOW", "Never chase a RED timer"],
             "Digit based contracts resolve tick by tick. Enter while the timer is green or yellow, and never chase a trade on a red timer."),
        ],
    },
    {
        "n": 12, "slug": "read-market-master", "cat": "Intermediate",
        "title": "How to Read Market Master",
        "segments": [
            ("Market Master panel",
             ["Six contract types ranked", "Top recommendation in blue", "Confidence percent per contract"],
             "Market Master ranks all six contract types for your market. The top recommendation is highlighted and the confidence of every contract is shown."),
            ("Acting on it",
             ["Also check data quality", "Avoid trading with anomalies", "Weight comes from the signal"],
             "Market Master combines digit frequencies with signal quality, but you still confirm data quality and a clean anomaly count before trading."),
        ],
    },
    {
        "n": 13, "slug": "digit-hacker-frequency", "cat": "Intermediate",
        "title": "Digit Hacker – Frequency Tab",
        "segments": [
            ("Frequency tab",
             ["Bars for digits zero to nine", "Entropy and balance metrics", "Most and least frequent digits"],
             "The Frequency tab in the Digit Hacker shows the count for each digit zero to nine. Entropy and balance tell you how fair the distribution currently is."),
            ("Sample sizes",
             ["Choose 10 to 1000 ticks", "Small windows react faster", "Large windows are steadier"],
             "You can change the window from ten ticks to one thousand. Small windows react quickly. Large windows give steadier readings."),
        ],
    },
    {
        "n": 14, "slug": "digit-hacker-psychology", "cat": "Intermediate",
        "title": "Digit Hacker – Psychology Tab",
        "segments": [
            ("Psychology tab",
             ["OVERFED digit above ten percent", "CONFIRMATION is second most frequent", "STARVING digit below ten percent"],
             "The Psychology tab labels the three most interesting digits. The overfed digit appears more than fair. The starving digit less than fair. Confirmation is the runner up."),
            ("Turn it into a plan",
             ["OVERFED suggests MATCHES", "STARVING suggests DIFFERS", "Confidence from deviation size"],
             "An overfed digit may justify a MATCHES trade. A starving digit may justify a DIFFERS trade. Bigger deviation means more confidence, but never certainty."),
        ],
    },
    {
        "n": 15, "slug": "digit-hacker-contract", "cat": "Intermediate",
        "title": "Digit Hacker – Contract Tab",
        "segments": [
            ("Contract tab",
             ["MATCHES, DIFFERS, ODD, EVEN, OVER, UNDER", "Observed versus expected percent", "Deviation drives the verdict"],
             "The Contract tab converts the digit distribution into evidence for each of the six contract types. You see the observed percentage, the fair percentage, and the deviation."),
        ],
    },
    {
        "n": 16, "slug": "digit-hacker-predictor", "cat": "Intermediate",
        "title": "Digit Hacker – Predictor Tab",
        "segments": [
            ("Predictor tab",
             ["Top candidate digit", "Confidence from deviation", "Evidence sentence included"],
             "The Predictor tab reduces everything to one candidate digit, a confidence percentage, and the sentence that explains why."),
        ],
    },
    {
        "n": 17, "slug": "digit-hacker-gaps", "cat": "Intermediate",
        "title": "Digit Hacker – Gaps Tab",
        "segments": [
            ("Gaps tab",
             ["Current gap per digit", "Maximum gap per digit", "Long gaps revert eventually"],
             "The Gaps tab shows how many ticks have passed since each digit appeared, and the all time record per digit. Long gaps eventually revert, but there is no exact timing."),
        ],
    },
    {
        "n": 18, "slug": "use-paper-mode", "cat": "Intermediate",
        "title": "How to Use Paper Mode",
        "segments": [
            ("Paper mode",
             ["Simulated trades, no real money", "Same analytics pipeline", "Learn without risk"],
             "Paper mode runs the same analytics engine but simulates outcomes instead of touching your account. It is the safe way to learn."),
            ("Treat it seriously",
             ["Journal every trade", "Watch data quality", "Practise stop discipline"],
             "Take paper trades seriously. Check the journal afterwards and practise obeying stop loss rules."),
        ],
    },
    {
        "n": 19, "slug": "start-stop-auto-trader", "cat": "Intermediate",
        "title": "How to Start and Stop Auto Trader",
        "segments": [
            ("Auto Trader panel",
             ["START PAPER button", "STOP button always available", "Status: stopped or running"],
             "In the Auto Trader panel you press START PAPER to begin autonomous trading in paper mode. Press STOP to halt it at any time."),
            ("What happens when it runs",
             ["Scans all markets each cycle", "Places paper trades on evidence", "Cool downs enforce discipline"],
             "While it runs, the bot scans all markets continuously, places paper trades when evidence is strong and pauses according to cooldown rules."),
        ],
    },
    {
        "n": 20, "slug": "read-activity-log", "cat": "Intermediate",
        "title": "How to Read the Activity Log",
        "segments": [
            ("Activity log",
             ["Timestamped bot events", "Starts, trades, results, stops", "Oldest trimmed automatically"],
             "The activity log is the bot's timeline. You will see when it started, what it recommended, every trade, the result, and the stop."),
        ],
    },
    {
        "n": 21, "slug": "strategy-builder-load-template", "cat": "Advanced",
        "title": "Strategy Builder – Load a Template",
        "segments": [
            ("Templates",
             ["MATCHES on OVERFED digit", "DIFFERS on STARVING digit", "Odd even swing templates"],
             "The Strategy Builder ships with ready made templates such as MATCHES on overfed digit and DIFFERS on starving digit. Loading one gives you a complete working strategy."),
        ],
    },
    {
        "n": 22, "slug": "strategy-builder-custom", "cat": "Advanced",
        "title": "Strategy Builder – Build a Custom Strategy",
        "segments": [
            ("Custom strategies",
             ["Name, type, symbol, stake", "Evidence and quality thresholds", "Money management method"],
             "To build your own strategy you name it, pick a strategy type and symbol, set the stake and duration, then tune evidence, quality and confidence thresholds."),
        ],
    },
    {
        "n": 23, "slug": "strategy-builder-save-export", "cat": "Advanced",
        "title": "Strategy Builder – Save, Export, and Import",
        "segments": [
            ("Save and share",
             ["CREATE saves to the engine", "Strategies list shows all", "Sessions run on demand"],
             "Press CREATE to save your strategy to the engine. The list shows everything you have built, and sessions can be started and stopped on demand."),
        ],
    },
    {
        "n": 24, "slug": "run-a-backtest", "cat": "Advanced",
        "title": "How to Run a Backtest",
        "segments": [
            ("Backtesting panel",
             ["Symbol and tick count inputs", "Press RUN", "Win rate and profit results"],
             "In the Backtesting panel you choose the symbol and the number of ticks, press RUN, and get the strategy's historical performance."),
            ("Reading results",
             ["Win rate percent", "Total trades and equity", "Past performance is not a promise"],
             "Results include win rate, total trades and net profit. Remember: a good backtest is still only history."),
        ],
    },
    {
        "n": 25, "slug": "backtest-optimization", "cat": "Advanced",
        "title": "Backtest Optimization and Walk-Forward",
        "segments": [
            ("Choosing parameters",
             ["Small versus large windows", "Confidence thresholds", "Avoid overfitting"],
             "When you change parameters, validate on windows you did not tune on. Overfitting is the most common trap."),
        ],
    },
    {
        "n": 26, "slug": "deploy-strategy-auto-trader", "cat": "Advanced",
        "title": "How to Deploy a Strategy to Auto Trader",
        "segments": [
            ("Deploy",
             ["Validate in backtests", "Paper trade it first", "Move to live only when ready"],
             "Before letting a strategy handle real money, backtest it, then paper trade it. Only switch to live when it behaves well in paper mode."),
        ],
    },
    {
        "n": 27, "slug": "copy-trading-follow", "cat": "Advanced",
        "title": "Copy Trading – Follow a Leader",
        "segments": [
            ("Following leaders",
             ["Browse the leader list", "Registration inside the platform", "Followers receive the trades"],
             "Copy Trading lets you follow registered leaders. Their trades are mirrored into your follower account with proportionate sizing."),
        ],
    },
    {
        "n": 28, "slug": "copy-trading-leader", "cat": "Advanced",
        "title": "Copy Trading – Register as a Leader",
        "segments": [
            ("Becoming a leader",
             ["Register in the Copy Trading panel", "Performance is public", "Discipline attracts followers"],
             "Register yourself as a leader in the Copy Trading panel. Your performance becomes visible, and followers can join your style."),
        ],
    },
    {
        "n": 29, "slug": "portfolio-manager-track", "cat": "Advanced",
        "title": "Portfolio Manager – Track Assets",
        "segments": [
            ("Adding assets",
             ["Category, name, quantity, price", "Value updates automatically", "Watch concentration"],
             "The Portfolio Manager lets you add assets by category, name, quantity and price. It tracks your value as things move."),
        ],
    },
    {
        "n": 30, "slug": "full-day-session", "cat": "Advanced",
        "title": "Full Day Trading Session (Live)",
        "segments": [
            ("A complete session",
             ["Check header badge first", "Scan all markets", "Watch the bot obey its rules"],
             "A full day starts by confirming the data badge is green, scanning all markets, then letting the Auto Trader work while it obeys its rules."),
            ("End of day",
             ["STOP the bot", "Review the journal", "Small steps, strict risk"],
             "At the end of the session, stop the bot and review the Trade Journal. Slow, disciplined days compound."),
        ],
    },
    {
        "n": 31, "slug": "connect-deriv-api", "cat": "Intermediate",
        "title": "How to Connect Your Deriv API (Go Live)",
        "segments": [
            ("Before you connect",
             ["Amber DEMO DATA badge = practice mode", "Green LIVE DATA badge = your real account", "Your token is never shown and never sent to chat"],
             "Before you connect, EAGLE X runs on demo ticks and the header badge glows amber. Connecting your Deriv account switches everything to your real live feed. Your token is never displayed on screen and never sent to any chat. It travels encrypted, straight to your own server, and is checked with Deriv before it is stored."),
            ("Get your Deriv API token",
             ["Open api.deriv.com and sign in", "Settings, then API Token, then create token", "Tick Read — add Trade only when ready for live"],
             "Open api dot deriv dot com and sign in. Go to Settings, then API Token, and create a new token. Tick the Read scope so EAGLE X can see your account. Add the Trade scope only when you are ready to place live trades. Copy the token immediately — Deriv shows it only once."),
            ("Connect in EAGLE-X",
             ["Find the CONNECT DERIV panel on the dashboard", "CONNECT WITH DERIV opens Deriv's own login page", "Or PASTE TOKEN into the secure field, then CONNECT"],
             "On the dashboard, find the Connect Deriv panel. The safest route is the Connect with Deriv button — it opens Deriv's own login page, and Deriv hands your account back to EAGLE X automatically. Or press Paste Token, drop your token into the secure field, and press Connect. Validation takes a few seconds."),
            ("What you will see when it works",
             ["Badge flips from amber to green: LIVE DATA", "Panel shows account ID, currency, real balance", "GK now sizes stakes from your REAL balance"],
             "The moment it connects, three things change on your screen. The header badge flips from amber demo data to green live data. The Connect Deriv panel shows your account I D, your currency, and your real balance. And the goalkeeper now sizes every stake and every stop loss from that real balance — never from a made up number."),
            ("Start safely — Pep's rule protects you",
             ["Always START PAPER first — watch the team board", "START LIVE only when confident", "Two straight losses and the striker is benched — no chasing"],
             "Always start in paper mode first and watch the team board move with the market. When you are confident, press Start Live. Remember Pep's rule: if the striker misses twice in a row, he is benched — the team regroups, tight marking begins, and no one chases a lost trade. The stop loss always defends you. If any live step fails, the trade aborts loudly — never a silent loss."),
        ],
    },
]
