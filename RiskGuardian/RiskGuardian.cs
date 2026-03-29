using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using ATAS.DataFeedsCore;
using ATAS.Indicators;
using OFT.Rendering.Context;
using OFT.Rendering.Tools;

namespace RiskGuardian
{
    [DisplayName("x-trade.ai Risk")]
    [Category("Custom")]
    public class RiskGuardian : Indicator
    {
        #region === FIELDS ===

        private bool _isLocked;
        private string _lockReason = "";
        private decimal _dailyHighPnL;
        private int _consecutiveLosses;
        private int _totalTradesToday;
        private int _losingTradesToday;
        private decimal _previousClosedPnL;
        private DateTime _lastTradeDate;
        private DateTime _lockTime;
        private readonly List<MyTrade> _todayTrades = new();

        // Settings lock
        private bool _settingsLocked;
        private string _lockFilePath;
        private string _journalFilePath;
        private string _dataDir;

        // Journal: last events for on-chart display
        private readonly List<string> _journalLines = new();
        private const int MaxJournalDisplay = 6;

        #endregion

        #region === USER PARAMETERS — Daily Loss ===

        [Display(Name = "Enable Daily Loss Limit", GroupName = "1. Daily Loss", Order = 1)]
        public bool EnableDailyLoss { get; set; } = true;

        [Display(Name = "Max Daily Loss ($)", GroupName = "1. Daily Loss", Order = 2,
            Description = "Maximum allowed loss for the day. Flattens and locks when reached.")]
        [Range(1, 1000000)]
        public decimal MaxDailyLoss { get; set; } = 500;

        #endregion

        #region === USER PARAMETERS — Trailing Daily Stop ===

        [Display(Name = "Enable Trailing Daily Stop", GroupName = "2. Trailing Daily Stop", Order = 1)]
        public bool EnableTrailingDailyStop { get; set; } = true;

        [Display(Name = "Max Drawdown From Daily High ($)", GroupName = "2. Trailing Daily Stop", Order = 2,
            Description = "If P&L drops this amount from the daily peak, locks trading. Protects gains.")]
        [Range(1, 1000000)]
        public decimal MaxDrawdownFromHigh { get; set; } = 300;

        #endregion

        #region === USER PARAMETERS — Profit Target ===

        [Display(Name = "Enable Daily Profit Target", GroupName = "3. Profit Target", Order = 1)]
        public bool EnableProfitTarget { get; set; } = false;

        [Display(Name = "Daily Profit Target ($)", GroupName = "3. Profit Target", Order = 2,
            Description = "Locks trading once this profit is reached. Lock in your gains.")]
        [Range(1, 1000000)]
        public decimal DailyProfitTarget { get; set; } = 1000;

        #endregion

        #region === USER PARAMETERS — Per Trade ===

        [Display(Name = "Enable Max Loss Per Trade", GroupName = "4. Per Trade", Order = 1)]
        public bool EnableMaxLossPerTrade { get; set; } = false;

        [Display(Name = "Max Loss Per Trade ($)", GroupName = "4. Per Trade", Order = 2,
            Description = "Auto-closes position if unrealized loss on current trade exceeds this.")]
        [Range(1, 1000000)]
        public decimal MaxLossPerTrade { get; set; } = 200;

        #endregion

        #region === USER PARAMETERS — Consecutive Losses ===

        [Display(Name = "Enable Consecutive Loss Limit", GroupName = "5. Consecutive Losses", Order = 1)]
        public bool EnableConsecutiveLosses { get; set; } = true;

        [Display(Name = "Max Consecutive Losses", GroupName = "5. Consecutive Losses", Order = 2,
            Description = "Locks trading after X losing trades in a row.")]
        [Range(1, 100)]
        public int MaxConsecutiveLosses { get; set; } = 3;

        #endregion

        #region === USER PARAMETERS — Trade Count ===

        [Display(Name = "Enable Max Trades Per Day", GroupName = "6. Trade Count", Order = 1)]
        public bool EnableMaxTrades { get; set; } = true;

        [Display(Name = "Max Trades Per Day", GroupName = "6. Trade Count", Order = 2,
            Description = "Total number of round-trip trades allowed per day.")]
        [Range(1, 1000)]
        public int MaxTradesPerDay { get; set; } = 10;

        #endregion

        #region === USER PARAMETERS — Max Position Size ===

        [Display(Name = "Enable Max Position Size", GroupName = "7. Position Size", Order = 1)]
        public bool EnableMaxPosition { get; set; } = true;

        [Display(Name = "Max Contracts/Lots", GroupName = "7. Position Size", Order = 2,
            Description = "Maximum position size allowed at any time.")]
        [Range(1, 10000)]
        public int MaxPositionSize { get; set; } = 5;

        #endregion

        #region === USER PARAMETERS — Trading Hours ===

        [Display(Name = "Enable Trading Hours", GroupName = "8. Trading Hours", Order = 1)]
        public bool EnableTradingHours { get; set; } = false;

        [Display(Name = "Start Hour (0-23)", GroupName = "8. Trading Hours", Order = 2)]
        [Range(0, 23)]
        public int StartHour { get; set; } = 9;

        [Display(Name = "Start Minute", GroupName = "8. Trading Hours", Order = 3)]
        [Range(0, 59)]
        public int StartMinute { get; set; } = 30;

        [Display(Name = "End Hour (0-23)", GroupName = "8. Trading Hours", Order = 4)]
        [Range(0, 23)]
        public int EndHour { get; set; } = 16;

        [Display(Name = "End Minute", GroupName = "8. Trading Hours", Order = 5)]
        [Range(0, 59)]
        public int EndMinute { get; set; } = 0;

        #endregion

        #region === USER PARAMETERS — Cooldown ===

        [Display(Name = "Enable Cooldown After Lock", GroupName = "9. Cooldown", Order = 1)]
        public bool EnableCooldown { get; set; } = false;

        [Display(Name = "Cooldown Minutes", GroupName = "9. Cooldown", Order = 2)]
        [Range(0, 1440)]
        public int CooldownMinutes { get; set; } = 0;

        #endregion

        #region === USER PARAMETERS — Display ===

        [Display(Name = "Show Dashboard On Chart", GroupName = "10. Display", Order = 1)]
        public bool ShowDashboard { get; set; } = true;

        #endregion

        #region === CONSTRUCTOR ===

        public RiskGuardian()
            : base(true)
        {
            Name = "x-trade.ai Risk";
            EnableCustomDrawing = true;
            SubscribeToDrawingEvents(DrawingLayouts.Final);
            DenyToChangePanel = true;
        }

        #endregion

        #region === LIFECYCLE ===

        protected override void OnInitialize()
        {
            // Setup data directory
            _dataDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "xtrade-ai");
            Directory.CreateDirectory(_dataDir);

            var today = DateTime.Now.ToString("yyyy-MM-dd");
            _lockFilePath = Path.Combine(_dataDir, $"lock_{today}.json");
            _journalFilePath = Path.Combine(_dataDir, $"journal_{today}.csv");

            // Initialize journal CSV if new day
            if (!File.Exists(_journalFilePath))
            {
                File.WriteAllText(_journalFilePath,
                    "Time,Event,Direction,Price,Volume,PnL,ClosedPnL,TotalPnL,Trades,ConsecLosses,Reason\n");
            }

            // Try to load locked settings
            LoadLockedSettings();

            ResetDailyCounters();
            LogJournal("INIT", "", 0, 0, "x-trade.ai started" + (_settingsLocked ? " [SETTINGS LOCKED]" : ""));
        }

        #endregion

        #region === SETTINGS LOCK ===

        private void LoadLockedSettings()
        {
            try
            {
                if (!File.Exists(_lockFilePath))
                {
                    // No lock for today — save current settings as lock
                    SaveSettingsLock();
                    _settingsLocked = true;
                    return;
                }

                // Lock exists — force settings from file
                var json = File.ReadAllText(_lockFilePath);
                var s = JsonSerializer.Deserialize<SettingsLock>(json);
                if (s == null || s.Date != DateTime.Now.ToString("yyyy-MM-dd"))
                {
                    // Expired lock, create new one
                    SaveSettingsLock();
                    _settingsLocked = true;
                    return;
                }

                // Apply locked settings — user cannot override
                EnableDailyLoss = s.EnableDailyLoss;
                MaxDailyLoss = s.MaxDailyLoss;
                EnableTrailingDailyStop = s.EnableTrailingDailyStop;
                MaxDrawdownFromHigh = s.MaxDrawdownFromHigh;
                EnableProfitTarget = s.EnableProfitTarget;
                DailyProfitTarget = s.DailyProfitTarget;
                EnableMaxLossPerTrade = s.EnableMaxLossPerTrade;
                MaxLossPerTrade = s.MaxLossPerTrade;
                EnableConsecutiveLosses = s.EnableConsecutiveLosses;
                MaxConsecutiveLosses = s.MaxConsecutiveLosses;
                EnableMaxTrades = s.EnableMaxTrades;
                MaxTradesPerDay = s.MaxTradesPerDay;
                EnableMaxPosition = s.EnableMaxPosition;
                MaxPositionSize = s.MaxPositionSize;
                EnableTradingHours = s.EnableTradingHours;
                StartHour = s.StartHour;
                StartMinute = s.StartMinute;
                EndHour = s.EndHour;
                EndMinute = s.EndMinute;
                EnableCooldown = s.EnableCooldown;
                CooldownMinutes = s.CooldownMinutes;

                _settingsLocked = true;
            }
            catch
            {
                _settingsLocked = false;
            }
        }

        private void SaveSettingsLock()
        {
            try
            {
                var s = new SettingsLock
                {
                    Date = DateTime.Now.ToString("yyyy-MM-dd"),
                    LockedAt = DateTime.Now.ToString("HH:mm:ss"),
                    EnableDailyLoss = EnableDailyLoss,
                    MaxDailyLoss = MaxDailyLoss,
                    EnableTrailingDailyStop = EnableTrailingDailyStop,
                    MaxDrawdownFromHigh = MaxDrawdownFromHigh,
                    EnableProfitTarget = EnableProfitTarget,
                    DailyProfitTarget = DailyProfitTarget,
                    EnableMaxLossPerTrade = EnableMaxLossPerTrade,
                    MaxLossPerTrade = MaxLossPerTrade,
                    EnableConsecutiveLosses = EnableConsecutiveLosses,
                    MaxConsecutiveLosses = MaxConsecutiveLosses,
                    EnableMaxTrades = EnableMaxTrades,
                    MaxTradesPerDay = MaxTradesPerDay,
                    EnableMaxPosition = EnableMaxPosition,
                    MaxPositionSize = MaxPositionSize,
                    EnableTradingHours = EnableTradingHours,
                    StartHour = StartHour,
                    StartMinute = StartMinute,
                    EndHour = EndHour,
                    EndMinute = EndMinute,
                    EnableCooldown = EnableCooldown,
                    CooldownMinutes = CooldownMinutes
                };
                var json = JsonSerializer.Serialize(s, new JsonSerializerOptions { WriteIndented = true });
                File.WriteAllText(_lockFilePath, json);
            }
            catch { }
        }

        private class SettingsLock
        {
            public string Date { get; set; } = "";
            public string LockedAt { get; set; } = "";
            public bool EnableDailyLoss { get; set; }
            public decimal MaxDailyLoss { get; set; }
            public bool EnableTrailingDailyStop { get; set; }
            public decimal MaxDrawdownFromHigh { get; set; }
            public bool EnableProfitTarget { get; set; }
            public decimal DailyProfitTarget { get; set; }
            public bool EnableMaxLossPerTrade { get; set; }
            public decimal MaxLossPerTrade { get; set; }
            public bool EnableConsecutiveLosses { get; set; }
            public int MaxConsecutiveLosses { get; set; }
            public bool EnableMaxTrades { get; set; }
            public int MaxTradesPerDay { get; set; }
            public bool EnableMaxPosition { get; set; }
            public int MaxPositionSize { get; set; }
            public bool EnableTradingHours { get; set; }
            public int StartHour { get; set; }
            public int StartMinute { get; set; }
            public int EndHour { get; set; }
            public int EndMinute { get; set; }
            public bool EnableCooldown { get; set; }
            public int CooldownMinutes { get; set; }
        }

        #endregion

        #region === JOURNAL ===

        private void LogJournal(string eventType, string direction, decimal price, decimal volume, string reason)
        {
            try
            {
                var now = DateTime.Now;
                var totalPnL = GetTotalDailyPnL();
                var closedPnL = GetClosedPnL();
                var openPnL = GetOpenPnL();

                var line = string.Format(CultureInfo.InvariantCulture,
                    "{0},{1},{2},{3},{4},{5},{6},{7},{8},{9},{10}",
                    now.ToString("HH:mm:ss"),
                    eventType,
                    direction,
                    price,
                    volume,
                    openPnL,
                    closedPnL,
                    totalPnL,
                    _totalTradesToday,
                    _consecutiveLosses,
                    reason.Replace(",", ";"));

                File.AppendAllText(_journalFilePath, line + "\n");

                // Keep last lines for on-chart display
                var displayLine = $"{now:HH:mm:ss} | {eventType} | {reason}";
                if (eventType == "TRADE")
                    displayLine = $"{now:HH:mm:ss} | {direction} {volume}@{price:F2} | P&L: ${totalPnL:F2}";

                _journalLines.Add(displayLine);
                if (_journalLines.Count > MaxJournalDisplay)
                    _journalLines.RemoveAt(0);
            }
            catch { }
        }

        #endregion

        #region === CORE LOGIC ===

        protected override void OnCalculate(int bar, decimal value)
        {
            if (bar != CurrentBar - 1)
                return;

            CheckDayReset();

            // Re-enforce locked settings on every tick
            if (_settingsLocked)
                LoadLockedSettings();

            if (_isLocked && EnableCooldown && CooldownMinutes > 0)
            {
                if (DateTime.Now - _lockTime >= TimeSpan.FromMinutes(CooldownMinutes))
                    Unlock();
            }

            CheckPnLLimits();
        }

        protected override void OnNewMyTrade(MyTrade trade)
        {
            CheckDayReset();

            _todayTrades.Add(trade);
            _totalTradesToday = CountRoundTrips();

            var totalPnL = GetTotalDailyPnL();
            if (totalPnL > _dailyHighPnL)
                _dailyHighPnL = totalPnL;

            UpdateConsecutiveLosses();

            // Log trade to journal
            LogJournal("TRADE",
                "",
                trade.Price, trade.Volume, "");

            if (EnableMaxTrades && _totalTradesToday >= MaxTradesPerDay)
            { LockTrading($"MAX TRADES ({MaxTradesPerDay})"); return; }

            if (EnableConsecutiveLosses && _consecutiveLosses >= MaxConsecutiveLosses)
            { LockTrading($"{_consecutiveLosses} CONSEC. LOSSES"); return; }

            if (EnableDailyLoss && totalPnL <= -MaxDailyLoss)
            { LockTrading($"DAILY LOSS (-${MaxDailyLoss})"); return; }

            if (EnableProfitTarget && totalPnL >= DailyProfitTarget)
            { LockTrading($"PROFIT TARGET (${DailyProfitTarget})"); return; }

            if (EnableTrailingDailyStop && _dailyHighPnL > 0 && totalPnL <= _dailyHighPnL - MaxDrawdownFromHigh)
            { LockTrading($"TRAILING STOP from peak"); return; }
        }

        protected override void OnNewOrder(Order order)
        {
            CheckDayReset();

            if (_isLocked)
            {
                TryCancelOrder(order);
                LogJournal("BLOCKED", "", 0, 0, _lockReason);
                return;
            }

            if (EnableTradingHours && !IsWithinTradingHours())
            {
                TryCancelOrder(order);
                LogJournal("BLOCKED", "", 0, 0, "Outside trading hours");
                return;
            }

            if (EnableMaxPosition)
            {
                var pos = TradingManager.Position;
                var currentSize = pos != null ? Math.Abs(pos.OpenVolume) : 0m;
                if (currentSize + order.QuantityToFill > MaxPositionSize)
                {
                    TryCancelOrder(order);
                    LogJournal("BLOCKED", "", 0, 0, $"Max position ({MaxPositionSize})");
                    return;
                }
            }
        }

        #endregion

        #region === P&L MONITORING ===

        private void CheckPnLLimits()
        {
            var totalPnL = GetTotalDailyPnL();

            if (totalPnL > _dailyHighPnL)
                _dailyHighPnL = totalPnL;

            if (EnableMaxLossPerTrade)
            {
                var openPnL = GetOpenPnL();
                if (openPnL <= -MaxLossPerTrade)
                    FlattenAll($"MAX LOSS/TRADE (-${MaxLossPerTrade})");
            }

            if (!_isLocked && EnableDailyLoss && totalPnL <= -MaxDailyLoss)
                LockTrading($"DAILY LOSS (-${MaxDailyLoss})");

            if (!_isLocked && EnableTrailingDailyStop && _dailyHighPnL > 0 && totalPnL <= _dailyHighPnL - MaxDrawdownFromHigh)
                LockTrading($"TRAILING STOP from peak");

            if (!_isLocked && EnableProfitTarget && totalPnL >= DailyProfitTarget)
                LockTrading($"PROFIT TARGET (${DailyProfitTarget})");
        }

        #endregion

        #region === ACTIONS ===

        private void TryCancelOrder(Order order)
        {
            try
            {
                if (order.State == OrderStates.Active)
                    _ = TradingManager.CancelOrderAsync(order);
            }
            catch { }
        }

        private void LockTrading(string reason)
        {
            if (_isLocked) return;
            _isLocked = true;
            _lockReason = reason;
            _lockTime = DateTime.Now;
            FlattenAll(reason);
            LogJournal("LOCK", "", 0, 0, reason);
        }

        private void FlattenAll(string reason)
        {
            try
            {
                foreach (var order in TradingManager.Orders.ToList())
                {
                    if (order.State == OrderStates.Active)
                        _ = TradingManager.CancelOrderAsync(order);
                }
            }
            catch { }

            try
            {
                var position = TradingManager.Position;
                if (position != null && position.OpenVolume != 0)
                    _ = TradingManager.ClosePositionAsync(position);
            }
            catch { }

            LogJournal("FLATTEN", "", 0, 0, reason);
        }

        private void Unlock()
        {
            _isLocked = false;
            _lockReason = "";
            LogJournal("UNLOCK", "", 0, 0, "Cooldown expired");
        }

        #endregion

        #region === HELPERS ===

        private decimal GetOpenPnL()
        {
            var pos = TradingManager.Position;
            if (pos == null || pos.OpenVolume == 0) return 0;
            return pos.UnrealizedPnL;
        }

        private decimal GetClosedPnL()
        {
            var pos = TradingManager.Position;
            if (pos == null) return 0;
            return pos.RealizedPnL;
        }

        private decimal GetTotalDailyPnL()
        {
            return GetClosedPnL() + GetOpenPnL();
        }

        private void CheckDayReset()
        {
            var today = DateTime.Now.Date;
            if (_lastTradeDate != today)
            {
                ResetDailyCounters();
                _lastTradeDate = today;

                // Update paths for new day
                var todayStr = DateTime.Now.ToString("yyyy-MM-dd");
                _lockFilePath = Path.Combine(_dataDir, $"lock_{todayStr}.json");
                _journalFilePath = Path.Combine(_dataDir, $"journal_{todayStr}.csv");

                if (!File.Exists(_journalFilePath))
                {
                    File.WriteAllText(_journalFilePath,
                        "Time,Event,Direction,Price,Volume,PnL,ClosedPnL,TotalPnL,Trades,ConsecLosses,Reason\n");
                }

                // New day = new lock from current settings
                SaveSettingsLock();
                _settingsLocked = true;
            }
        }

        private void ResetDailyCounters()
        {
            _isLocked = false;
            _lockReason = "";
            _dailyHighPnL = 0;
            _consecutiveLosses = 0;
            _totalTradesToday = 0;
            _losingTradesToday = 0;
            _previousClosedPnL = 0;
            _todayTrades.Clear();
            _journalLines.Clear();
        }

        private int CountRoundTrips()
        {
            return _todayTrades.Count / 2;
        }

        private void UpdateConsecutiveLosses()
        {
            var pos = TradingManager.Position;
            var vol = pos != null ? pos.OpenVolume : 0m;

            if (vol == 0 && _todayTrades.Count >= 2)
            {
                var currentClosed = GetClosedPnL();
                var lastRTPnL = currentClosed - _previousClosedPnL;
                _previousClosedPnL = currentClosed;

                if (lastRTPnL < 0)
                { _consecutiveLosses++; _losingTradesToday++; }
                else if (lastRTPnL > 0)
                { _consecutiveLosses = 0; }
            }
        }

        private bool IsWithinTradingHours()
        {
            var start = new TimeSpan(StartHour, StartMinute, 0);
            var end = new TimeSpan(EndHour, EndMinute, 0);
            var current = DateTime.Now.TimeOfDay;

            return start <= end
                ? current >= start && current <= end
                : current >= start || current <= end;
        }

        #endregion

        #region === CHART RENDERING ===

        protected override void OnRender(RenderContext context, DrawingLayouts layout)
        {
            if (layout != DrawingLayouts.Final || !ShowDashboard)
                return;

            var totalPnL = GetTotalDailyPnL();
            var openPnL = GetOpenPnL();
            var x = ChartInfo.PriceChartContainer.Region.X + 10;
            var y = ChartInfo.PriceChartContainer.Region.Y + 10;
            var lh = 17;

            // Calculate height based on content
            var rows = 9 + (_journalLines.Count > 0 ? _journalLines.Count + 1 : 0);
            if (EnableDailyLoss) rows++;
            if (EnableTrailingDailyStop && _dailyHighPnL > 0) rows++;
            if (EnableTradingHours) rows++;

            var bgColor = _isLocked ? Color.FromArgb(210, 60, 0, 0) : Color.FromArgb(200, 0, 0, 0);
            context.FillRectangle(bgColor, new Rectangle(x - 5, y - 5, 340, lh * rows + 15));

            var fb = new RenderFont("Arial", 10, FontStyle.Bold);
            var fn = new RenderFont("Arial", 9);
            var fs = new RenderFont("Arial", 8);

            // === HEADER ===
            var hc = _isLocked ? Color.Red : Color.LimeGreen;
            context.DrawString(_isLocked ? $"LOCKED: {_lockReason}" : "x-trade.ai ACTIVE", fb, hc, x, y);
            y += lh + 2;

            // Settings lock indicator
            var lockColor = _settingsLocked ? Color.FromArgb(255, 180, 180, 0) : Color.Gray;
            context.DrawString(_settingsLocked ? "SETTINGS LOCKED FOR TODAY" : "Settings: unlocked", fs, lockColor, x, y);
            y += lh;

            // === METRICS ===
            context.DrawString($"Daily P&L: ${totalPnL:F2}", fn, totalPnL >= 0 ? Color.LimeGreen : Color.Red, x, y);
            y += lh;

            context.DrawString($"Daily High: ${_dailyHighPnL:F2}", fn, Color.Cyan, x, y);
            y += lh;

            context.DrawString($"Open P&L: ${openPnL:F2}", fn, openPnL >= 0 ? Color.LimeGreen : Color.Orange, x, y);
            y += lh;

            context.DrawString($"Trades: {_totalTradesToday}" + (EnableMaxTrades ? $" / {MaxTradesPerDay}" : ""), fn, Color.White, x, y);
            y += lh;

            var lc = _consecutiveLosses >= MaxConsecutiveLosses - 1 ? Color.Orange : Color.White;
            context.DrawString($"Consec. Losses: {_consecutiveLosses}" + (EnableConsecutiveLosses ? $" / {MaxConsecutiveLosses}" : ""), fn, lc, x, y);
            y += lh;

            var pos = TradingManager.Position;
            var pv = pos != null ? pos.OpenVolume : 0m;
            context.DrawString($"Position: {pv}" + (EnableMaxPosition ? $" (max {MaxPositionSize})" : ""), fn, Color.White, x, y);
            y += lh;

            if (EnableDailyLoss)
            {
                var pct = MaxDailyLoss != 0 ? Math.Abs(totalPnL / MaxDailyLoss * 100) : 0;
                context.DrawString($"Daily Loss: {pct:F0}% of ${MaxDailyLoss}", fn, pct > 80 ? Color.Red : pct > 50 ? Color.Orange : Color.Gray, x, y);
                y += lh;
            }

            if (EnableTrailingDailyStop && _dailyHighPnL > 0)
            {
                var dd = _dailyHighPnL - totalPnL;
                var dp = MaxDrawdownFromHigh != 0 ? dd / MaxDrawdownFromHigh * 100 : 0;
                context.DrawString($"Trail DD: ${dd:F0} / ${MaxDrawdownFromHigh} ({dp:F0}%)", fn, dp > 80 ? Color.Red : dp > 50 ? Color.Orange : Color.Gray, x, y);
                y += lh;
            }

            if (EnableTradingHours)
            {
                var ok = IsWithinTradingHours();
                context.DrawString($"Hours: {StartHour}:{StartMinute:D2}-{EndHour}:{EndMinute:D2} " + (ok ? "[OK]" : "[OUT]"), fn, ok ? Color.Gray : Color.Red, x, y);
                y += lh;
            }

            // === JOURNAL (last events) ===
            if (_journalLines.Count > 0)
            {
                y += 4;
                context.DrawString("--- Journal ---", fs, Color.FromArgb(255, 100, 100, 100), x, y);
                y += lh;

                foreach (var line in _journalLines)
                {
                    var lineColor = Color.FromArgb(255, 160, 160, 160);
                    if (line.Contains("LOCK") || line.Contains("FLATTEN")) lineColor = Color.FromArgb(255, 255, 80, 80);
                    else if (line.Contains("BUY")) lineColor = Color.FromArgb(255, 80, 200, 120);
                    else if (line.Contains("SELL")) lineColor = Color.FromArgb(255, 255, 120, 80);
                    else if (line.Contains("BLOCKED")) lineColor = Color.FromArgb(255, 255, 180, 0);

                    context.DrawString(line, fs, lineColor, x, y);
                    y += lh;
                }
            }
        }

        #endregion
    }
}
