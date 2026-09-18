from datetime import datetime, date
import pytz

IST = pytz.timezone("Asia/Kolkata")


class MarketCalendar:
    """
    BSE/NSE market holiday calendar.
    Covers 2025 and 2026 NSE trading holidays.
    """

    def __init__(self):
        self.holidays = set([
            # ── 2025 NSE Holidays ──────────────────────────
            date(2025, 2, 26),   # Mahashivratri
            date(2025, 3, 14),   # Holi
            date(2025, 3, 31),   # Id-Ul-Fitr (Ramadan)
            date(2025, 4, 10),   # Shri Ram Navami
            date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
            date(2025, 4, 18),   # Good Friday
            date(2025, 5, 1),    # Maharashtra Day
            date(2025, 8, 15),   # Independence Day
            date(2025, 8, 27),   # Ganesh Chaturthi
            date(2025, 10, 2),   # Mahatma Gandhi Jayanti
            date(2025, 10, 21),  # Diwali (Laxmi Puja)
            date(2025, 10, 22),  # Diwali (Balipratipada)
            date(2025, 11, 5),   # Prakash Gurpurb Sri Guru Nanak Dev Ji
            date(2025, 12, 25),  # Christmas
            # ── 2026 NSE Holidays ──────────────────────────
            date(2026, 1, 26),   # Republic Day
            date(2026, 3, 3),    # Mahashivratri
            date(2026, 3, 20),   # Holi
            date(2026, 3, 31),   # Id-Ul-Fitr
            date(2026, 4, 3),    # Good Friday
            date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
            date(2026, 5, 1),    # Maharashtra Day
            date(2026, 8, 15),   # Independence Day
            date(2026, 9, 11),   # Eid-E-Milad
            date(2026, 10, 2),   # Mahatma Gandhi Jayanti
            date(2026, 10, 20),  # Dussehra
            date(2026, 11, 9),   # Diwali (Laxmi Puja)
            date(2026, 11, 10),  # Diwali (Balipratipada)
            date(2026, 12, 25),  # Christmas
        ])

    def is_trading_day(self, check_date: date = None) -> bool:
        if check_date is None:
            check_date = datetime.now(IST).date()

        if check_date.weekday() >= 5:  # Saturday=5, Sunday=6
            return False

        return check_date not in self.holidays

    def is_market_open(self, check_time: datetime = None) -> bool:
        if check_time is None:
            check_time = datetime.now(IST)

        if not self.is_trading_day(check_time.date()):
            return False

        t = check_time.time()
        # Market hours: 09:15 – 15:30 IST
        if t.hour < 9 or (t.hour == 9 and t.minute < 15):
            return False
        if t.hour > 15 or (t.hour == 15 and t.minute >= 30):
            return False
        return True

    def get_next_expiry(self, current_date: date = None, target_weekday: int = 1) -> date:
        """
        Get the next expiry date. Defaults to Tuesday (1) as requested by user.
        If the target day is a holiday, shifts back to the previous trading day.
        """
        if current_date is None:
            current_date = datetime.now(IST).date()
            
        current_weekday = current_date.weekday()
        days_to_add = (target_weekday - current_weekday + 7) % 7
        expiry = current_date + __import__('datetime').timedelta(days=days_to_add)
        
        while not self.is_trading_day(expiry):
            expiry -= __import__('datetime').timedelta(days=1)
            
        return expiry

    def get_expiry_str_dd_mmm(self) -> str:
        """Returns format: DD MMM (e.g. 15 JUL)"""
        expiry = self.get_next_expiry()
        return expiry.strftime("%d %b").upper()

    def get_expiry_str_yyyy_mm_dd(self) -> str:
        """Returns format: YYYY-MM-DD"""
        expiry = self.get_next_expiry()
        return expiry.strftime("%Y-%m-%d")
