"""
Stock/Cryptocurrency Price Tracker with Alerts
Tracks price every 60 seconds, stores in DataFrame, calculates SMA, and triggers alerts
"""

import yfinance as yf
import pandas as pd
import time
import os
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import subprocess
from pathlib import Path

# Conditional import for Windows
if sys.platform == 'win32':
    import winsound

# Optional Telegram support
try:
    from telegram import Bot
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('price_tracker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class PriceTracker:
    """Tracks stock/crypto prices and triggers alerts on significant drops"""
    
    def __init__(
        self,
        ticker: str,
        sma_period: int = 10,
        check_interval: int = 60,
        price_drop_threshold: float = 2.0,
        alert_window_minutes: int = 60,
        telegram_bot_token: Optional[str] = None,
        telegram_chat_id: Optional[str] = None,
        use_system_beep: bool = True,
        max_retries: int = 3,
        session_file: Optional[str] = None
    ):
        """
        Initialize the price tracker
        
        Args:
            ticker: Stock symbol (e.g., 'AAPL') or crypto ticker (e.g., 'BTC-USD')
            sma_period: Period for simple moving average calculation
            check_interval: Seconds between price checks (default 60)
            price_drop_threshold: Alert threshold percentage drop (default 2%)
            alert_window_minutes: Time window for calculating price drop (default 60 minutes)
            telegram_bot_token: Telegram bot token for alerts
            telegram_chat_id: Telegram chat ID for alerts
            use_system_beep: Whether to trigger system beep on alert
            max_retries: Maximum retries for failed API calls
            session_file: File to persist/resume tracking sessions
        """
        self.ticker = ticker
        self.sma_period = sma_period
        self.check_interval = check_interval
        self.price_drop_threshold = price_drop_threshold
        self.alert_window_minutes = alert_window_minutes
        self.use_system_beep = use_system_beep
        self.max_retries = max_retries
        self.session_file = session_file or f"{ticker}_session.json"
        
        # Use list for efficient accumulation (convert to DataFrame only when needed)
        self.price_records: List[Dict] = []
        self.last_alert_time: Optional[datetime] = None
        
        # Telegram setup
        self.telegram_bot = None
        self.telegram_chat_id = telegram_chat_id
        if TELEGRAM_AVAILABLE and telegram_bot_token:
            try:
                self.telegram_bot = Bot(token=telegram_bot_token)
                logger.info(f"✓ Telegram bot initialized for chat ID: {telegram_chat_id}")
            except Exception as e:
                logger.warning(f"Failed to initialize Telegram bot: {e}")
        elif telegram_bot_token and not TELEGRAM_AVAILABLE:
            logger.warning("Telegram bot token provided but python-telegram-bot not installed")
        
        # Fetch initial data
        self.ticker_data = yf.Ticker(ticker)
        logger.info(f"✓ Initialized tracker for {ticker}")
        
        # Load previous session if exists
        self.load_session()
    
    def get_current_price(self) -> Optional[float]:
        """Fetch current price using 1-minute interval history (more reliable)"""
        retry_count = 0
        wait_time = 1
        
        while retry_count < self.max_retries:
            try:
                # Use 1-minute interval for real-time data (more reliable than .info)
                hist = self.ticker_data.history(period='1d', interval='1m')
                if not hist.empty:
                    price = hist['Close'].iloc[-1]
                    logger.debug(f"Price fetched: ${price:.2f}")
                    return price
                else:
                    raise ValueError("Empty history data")
            
            except Exception as e:
                retry_count += 1
                if retry_count < self.max_retries:
                    logger.warning(f"Attempt {retry_count}/{self.max_retries} failed: {e}")
                    time.sleep(wait_time)
                    wait_time = min(wait_time * 2, 16)  # Exponential backoff, max 16 seconds
                else:
                    logger.error(f"Failed to fetch price after {self.max_retries} retries: {e}")
                    return None
    
    def calculate_sma(self, price: float) -> Optional[float]:
        """Calculate simple moving average with new price"""
        if len(self.price_records) == 0:
            return None
        
        # Get last (sma_period - 1) prices and add current
        recent_prices = [p['price'] for p in self.price_records[-(self.sma_period - 1):]]
        recent_prices.append(price)
        
        if len(recent_prices) == self.sma_period:
            return sum(recent_prices) / len(recent_prices)
        return None
    
    def load_session(self) -> None:
        """Load previous tracking session if it exists"""
        try:
            if Path(self.session_file).exists():
                with open(self.session_file, 'r') as f:
                    data = json.load(f)
                    self.price_records = data.get('records', [])
                    logger.info(f"✓ Loaded {len(self.price_records)} records from previous session")
        except Exception as e:
            logger.warning(f"Could not load session: {e}")
    
    def save_session(self) -> None:
        """Save current session for resume capability"""
        try:
            with open(self.session_file, 'w') as f:
                json.dump({'records': self.price_records, 'ticker': self.ticker}, f)
                logger.debug(f"Session saved with {len(self.price_records)} records")
        except Exception as e:
            logger.warning(f"Could not save session: {e}")
    
    def get_price_dataframe(self) -> pd.DataFrame:
        """Convert price records list to DataFrame"""
        if not self.price_records:
            return pd.DataFrame(columns=['timestamp', 'price', 'sma'])
        return pd.DataFrame(self.price_records)
    
    def trigger_beep(self, duration: int = 500, frequency: int = 1000):
        """Trigger a system beep alert"""
        if not self.use_system_beep:
            return
        
        try:
            if sys.platform == 'win32':
                # Windows
                winsound.Beep(frequency, duration)
                time.sleep(0.2)
                winsound.Beep(frequency, duration)
            else:
                # Linux/Mac - use system beep command
                os.system('printf "\a"')
                time.sleep(0.2)
                os.system('printf "\a"')
            logger.info("✓ System beep triggered")
        except Exception as e:
            logger.warning(f"Could not trigger beep: {e}")
    
    def send_telegram_alert(self, message: str) -> bool:
        """Send alert via Telegram synchronously"""
        if not self.telegram_bot or not self.telegram_chat_id:
            return False
        
        try:
            self.telegram_bot.send_message(
                chat_id=self.telegram_chat_id,
                text=message
            )
            logger.info("✓ Telegram alert sent")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
    
    def check_price_drop(self) -> Tuple[bool, Optional[Dict]]:
        """
        Check if price has dropped more than threshold in the alert window
        Returns: (alert_triggered, price_change_info)
        """
        if len(self.price_records) < 2:
            return False, None
        
        now = datetime.now()
        window_start = now - timedelta(minutes=self.alert_window_minutes)
        
        # Filter prices within the alert window
        window_prices = [
            p for p in self.price_records 
            if datetime.fromisoformat(p['timestamp']) >= window_start
        ]
        
        if len(window_prices) < 2:
            return False, None
        
        prices = [p['price'] for p in window_prices]
        highest_price = max(prices)
        lowest_price = min(prices)
        current_price = prices[-1]
        
        price_drop_percent = ((highest_price - lowest_price) / highest_price) * 100
        
        if price_drop_percent >= self.price_drop_threshold:
            # Prevent alert spam (max 1 alert per 5 minutes)
            if self.last_alert_time and (now - self.last_alert_time).seconds < 300:
                return False, None
            
            self.last_alert_time = now
            return True, {
                'highest': highest_price,
                'lowest': lowest_price,
                'current': current_price,
                'drop_percent': price_drop_percent,
                'window_minutes': self.alert_window_minutes
            }
        
        return False, None
    
    def add_price_record(self, price: float) -> None:
        """Add a new price record to the history efficiently"""
        sma = self.calculate_sma(price)
        
        record = {
            'timestamp': datetime.now().isoformat(),
            'price': price,
            'sma': sma
        }
        
        self.price_records.append(record)
    
    def format_alert_message(self, info: dict) -> str:
        """Format alert message for display and Telegram"""
        return (
            f"🚨 PRICE DROP ALERT - {self.ticker}\n"
            f"Drop detected: {info['drop_percent']:.2f}% (threshold: {self.price_drop_threshold}%)\n"
            f"Highest: ${info['highest']:.2f}\n"
            f"Lowest: ${info['lowest']:.2f}\n"
            f"Current: ${info['current']:.2f}\n"
            f"Window: Last {info['window_minutes']} minutes\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    
    def print_status(self, price: float) -> None:
        """Print current tracking status"""
        sma_val = self.price_records[-1]['sma'] if len(self.price_records) > 0 else None
        sma_str = f"SMA: ${sma_val:.2f}" if sma_val else "SMA: --"
        
        logger.info(f"{self.ticker} Price: ${price:.2f} | {sma_str} | Records: {len(self.price_records)}")
    
    def export_data(self, filename: str = None) -> str:
        """Export price history to CSV"""
        if filename is None:
            filename = f"{self.ticker}_price_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        df = self.get_price_dataframe()
        if df.empty:
            logger.warning("No data to export")
            return filename
        
        df.to_csv(filename, index=False)
        logger.info(f"✓ Data exported to {filename}")
        return filename
    
    def run(self, duration_seconds: int = None) -> None:
        """
        Start the price tracking loop
        
        Args:
            duration_seconds: How long to track (None = infinite)
        """
        logger.info(f"🚀 Starting price tracker for {self.ticker}")
        logger.info(f"   Check interval: {self.check_interval} seconds")
        logger.info(f"   SMA period: {self.sma_period}")
        logger.info(f"   Alert threshold: {self.price_drop_threshold}% drop in {self.alert_window_minutes} minutes")
        logger.info(f"   System beep alerts: {'ON' if self.use_system_beep else 'OFF'}")
        logger.info(f"   Telegram alerts: {'ON' if self.telegram_bot else 'OFF'}")
        
        start_time = time.time()
        check_count = 0
        alert_count = 0
        
        try:
            while True:
                # Check elapsed time
                if duration_seconds and (time.time() - start_time) > duration_seconds:
                    logger.info(f"⏱️ Duration limit reached ({duration_seconds}s)")
                    break
                
                # Fetch price with retry logic
                price = self.get_current_price()
                if price is None:
                    logger.warning(f"Retrying in {self.check_interval}s...")
                    time.sleep(self.check_interval)
                    continue
                
                # Add record and print status
                self.add_price_record(price)
                self.print_status(price)
                check_count += 1
                
                # Check for price drop alert
                alert_triggered, drop_info = self.check_price_drop()
                if alert_triggered:
                    alert_message = self.format_alert_message(drop_info)
                    logger.warning(f"\n{alert_message}\n")
                    
                    # Trigger beep
                    if self.use_system_beep:
                        self.trigger_beep()
                    
                    # Send Telegram alert (synchronous)
                    if self.telegram_bot:
                        self.send_telegram_alert(alert_message)
                    
                    alert_count += 1
                
                # Save session periodically
                if check_count % 10 == 0:
                    self.save_session()
                
                # Wait before next check
                time.sleep(self.check_interval)
        
        except KeyboardInterrupt:
            logger.info("⏹️ Tracking stopped by user")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
        finally:
            # Final save
            self.save_session()
            
            # Summary
            logger.info("-" * 70)
            logger.info(f"📊 Tracking Summary:")
            logger.info(f"   Total checks: {check_count}")
            logger.info(f"   Alerts triggered: {alert_count}")
            logger.info(f"   Data points collected: {len(self.price_records)}")
            
            if len(self.price_records) > 0:
                prices = [p['price'] for p in self.price_records]
                logger.info(f"   Price range: ${min(prices):.2f} - ${max(prices):.2f}")
            
            # Export data
            if len(self.price_records) > 0:
                self.export_data()


def load_config(config_file: str = "price_tracker_config.json") -> Dict:
    """Load configuration from file or use defaults"""
    default_config = {
        'ticker': 'BTC-USD',
        'sma_period': 10,
        'check_interval': 60,
        'price_drop_threshold': 2.0,
        'alert_window_minutes': 60,
        'use_system_beep': True,
        'telegram_bot_token': None,
        'telegram_chat_id': None,
        'max_retries': 3
    }
    
    # Try to load from config file
    if Path(config_file).exists():
        try:
            with open(config_file, 'r') as f:
                file_config = json.load(f)
                default_config.update(file_config)
                logger.info(f"✓ Loaded config from {config_file}")
        except Exception as e:
            logger.warning(f"Could not load config file: {e}")
    
    # Override with environment variables
    env_overrides = {
        'TRACKER_TICKER': 'ticker',
        'TRACKER_CHECK_INTERVAL': 'check_interval',
        'TRACKER_THRESHOLD': 'price_drop_threshold',
        'TELEGRAM_BOT_TOKEN': 'telegram_bot_token',
        'TELEGRAM_CHAT_ID': 'telegram_chat_id'
    }
    
    for env_var, config_key in env_overrides.items():
        env_val = os.getenv(env_var)
        if env_val:
            if config_key in ['check_interval', 'alert_window_minutes', 'max_retries']:
                default_config[config_key] = int(env_val)
            elif config_key in ['price_drop_threshold']:
                default_config[config_key] = float(env_val)
            else:
                default_config[config_key] = env_val
            logger.info(f"✓ Loaded {config_key} from environment variable")
    
    return default_config


def main():
    """Main entry point - loads config and starts tracker"""
    
    config = load_config()
    
    logger.info("=" * 70)
    logger.info("Price Tracker Configuration")
    logger.info("=" * 70)
    logger.info(f"Ticker: {config['ticker']}")
    logger.info(f"Check interval: {config['check_interval']}s")
    logger.info(f"Alert threshold: {config['price_drop_threshold']}%")
    logger.info(f"Telegram enabled: {bool(config['telegram_bot_token'])}")
    logger.info("=" * 70)
    
    # Create and run tracker
    tracker = PriceTracker(
        ticker=config['ticker'],
        sma_period=config['sma_period'],
        check_interval=config['check_interval'],
        price_drop_threshold=config['price_drop_threshold'],
        alert_window_minutes=config['alert_window_minutes'],
        telegram_bot_token=config['telegram_bot_token'],
        telegram_chat_id=config['telegram_chat_id'],
        use_system_beep=config['use_system_beep'],
        max_retries=config['max_retries']
    )
    
    # Run for infinite duration
    tracker.run(duration_seconds=None)


if __name__ == "__main__":
    main()
