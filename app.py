"""
NAOYA'S MIDDLEMAN SERVICE - Complete Telegram Bot
Single file implementation with hardcoded config and dynamic UPI QR generation
"""

import asyncio
import logging
import re
import json
import sqlite3
import random
import string
import io
import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

import qrcode
from PIL import Image
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatPermissions, ChatMember, Message, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler,
    TypeHandler, ApplicationHandlerStop
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from flask import Flask, request

# ============================================
# OBFUSCATED CONFIGURATION
# Binary encoding is obfuscation, not encryption.
# ============================================

def _binary_text(value: str) -> str:
    """Decode space-separated 8-bit binary bytes at runtime."""
    return bytes(int(byte, 2) for byte in value.split()).decode("utf-8")


def _binary_int(value: str) -> int:
    return int(_binary_text(value))

class Config:
    # ====== REQUIRED: Replace these with your actual values ======
    
    # Bot Token from @BotFather
    BOT_TOKEN = _binary_text("00111000 00110110 00110001 00111000 00111001 00110101 00110010 00110101 00111001 00110001 00111010 01000001 01000001 01001000 01001111 00101101 01101101 01000110 01001000 00110101 01110011 01010011 01010001 01101001 01011001 01000101 01001000 00111000 01110110 01001101 01101110 01101011 01001011 01101010 01100101 01110000 01000010 01010000 01111001 01101100 01100011 00110011 01001111 01100111 01110111 01000101")
    
    # Super Owner Telegram User IDs (comma-separated for multiple)
    SUPER_OWNER_IDS = [_binary_int("00110111 00111000 00110010 00111000 00110111 00110001 00110111 00110101 00110000 00110101")]
    
    # Owner Telegram User IDs (comma-separated for multiple)
    OWNER_IDS = [_binary_int("00111000 00111001 00110111 00110101 00111001 00110110 00111001 00110000 00111000 00110100")]
    
    # Admin Telegram User IDs (comma-separated for multiple)
    ADMIN_IDS = [_binary_int("00110111 00110100 00110111 00110011 00110001 00110101 00110100 00111001 00110111 00110110")]
    
    # ====== OPTIONAL: Configure these as needed ======
    
    # MM Service Display Name
    MM_USERNAME = _binary_text("01000000 01101110 01100001 01101111 01111001 01100001 01110011 01001101 01001101")
    
    # Default Currency (INR or USD)
    DEFAULT_CURRENCY = _binary_text("01001001 01001110 01010010")
    
    # UPI Payment Configuration
    UPI_ID = _binary_text("01110010 01101001 01110100 01101001 01101011 00101110 01100010 01101000 01100001 01110100 01110100 01100001 01100011 01101000 01100001 01110010 01111001 01100001 01000000 01100110 01100001 01101101")
    UPI_PAYEE_NAME = _binary_text("01001110 01100001 01101111 01111001 01100001 00100000 01001101 01001101")
    
    # Private Channels (Create these and add bot as admin)
    DEAL_LOG_CHANNEL_ID = _binary_int("00101101 00110001 00110000 00110000 00110100 00110010 00110001 00111000 00110010 00110100 00110101 00110001 00110100 00110011")
    PAYMENT_LOG_CHANNEL_ID = _binary_int("00101101 00110001 00110000 00110000 00110100 00110011 00110001 00111000 00111000 00111001 00110001 00110011 00110010 00111001")
    SUPPORT_GROUP_ID = _binary_int("00101101 00110001 00110000 00110000 00110011 00111001 00110001 00110101 00110101 00110011 00111001 00110000 00110110 00111000")
    
    # Fee Configuration
    DEFAULT_FEE_PERCENT = 1.0  # 1%
    MIN_INR_FEE = 10.0         # ₹10 minimum
    MIN_USD_FEE = 0.50         # $0.50 minimum
    
    # Group Name Templates (use {deal_id} as placeholder)
    GROUP_NAME_HOLD_TEMPLATE = "🔒 FUND HOLDING | #NM{deal_id}"
    GROUP_NAME_COMPLETED_TEMPLATE = "✅ DEAL COMPLETED | #NM{deal_id}"
    GROUP_NAME_LOCKED_TEMPLATE = "🔒 DEAL LOCKED | #NM{deal_id}"
    
    # GC Trigger Words for auto-promotion
    TRIGGER_WORDS = ["mm", "middleman", "middlemen", "escrow"]
    GROUP_TRIGGER_COOLDOWN_SECONDS = 5 * 60
    
    # Database Path
    # Vercel's project filesystem is read-only; /tmp is writable per instance.
    DB_PATH = "/tmp/naoya_mm.db" if os.getenv("VERCEL") else "naoya_mm.db"
    
    @classmethod
    def validate(cls):
        """Validate configuration"""
        if cls.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            raise ValueError("Please set your BOT_TOKEN in Config")
        if not cls.SUPER_OWNER_IDS:
            raise ValueError("Please set at least one SUPER_OWNER_ID")
        return True

# ============================================
# DATABASE SETUP
# ============================================

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(Config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn

@contextmanager
def db_transaction():
    """Database transaction context manager"""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_database():
    """Initialize database tables"""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                display_name TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                is_blocked INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS deals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id TEXT UNIQUE NOT NULL,
                connection_token TEXT UNIQUE NOT NULL,
                buyer_id INTEGER NOT NULL,
                seller_id INTEGER NOT NULL,
                mm_id INTEGER,
                item_description TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'INR',
                payment_method TEXT NOT NULL,
                mm_fee REAL NOT NULL,
                seller_net_amount REAL NOT NULL,
                terms TEXT,
                status TEXT DEFAULT 'created',
                group_id INTEGER,
                original_group_title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (buyer_id) REFERENCES users(id),
                FOREIGN KEY (seller_id) REFERENCES users(id),
                FOREIGN KEY (mm_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS deal_confirmations (
                deal_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                confirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (deal_id, user_id),
                FOREIGN KEY (deal_id) REFERENCES deals(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS deal_release_actions (
                deal_id INTEGER PRIMARY KEY,
                buyer_action TEXT,
                seller_action TEXT,
                seller_upi_id TEXT,
                seller_qr_file_id TEXT,
                payout_proof_file_id TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (deal_id) REFERENCES deals(id)
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                method TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                reference TEXT,
                proof_file_id TEXT,
                proof_caption TEXT,
                verified_by_id INTEGER,
                verified_at TIMESTAMP,
                upi_qr_generated_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (deal_id) REFERENCES deals(id),
                FOREIGN KEY (verified_by_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS vouches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id INTEGER UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                review TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (deal_id) REFERENCES deals(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                deal_id INTEGER,
                assigned_admin_id INTEGER,
                status TEXT DEFAULT 'open',
                subject TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (deal_id) REFERENCES deals(id),
                FOREIGN KEY (assigned_admin_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER NOT NULL,
                deal_id TEXT,
                action TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT,
                metadata TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (actor_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS configuration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                updated_by_id INTEGER,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (updated_by_id) REFERENCES users(id)
            );

            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
            CREATE INDEX IF NOT EXISTS idx_deals_deal_id ON deals(deal_id);
            CREATE INDEX IF NOT EXISTS idx_deals_connection_token ON deals(connection_token);
            CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp);
            """)
        # Repair deals created by older builds where payment verification was
        # recorded but the deal status stayed at `confirmed`.
        conn.execute(
            """UPDATE deals SET status = 'payment_held', updated_at = CURRENT_TIMESTAMP
               WHERE status = 'confirmed'
                 AND id IN (SELECT deal_id FROM payments WHERE status = 'verified')"""
        )

        # Insert default configuration
        conn.execute("""
            INSERT OR IGNORE INTO configuration (key, value) VALUES
                ('fee_percent', ?),
                ('min_inr_fee', ?),
                ('min_usd_fee', ?),
                ('fee_tiers', '{}'),
                ('upi_id', ?),
                ('upi_payee_name', ?),
                ('mm_username', ?),
                ('service_status', 'running')
        """, (
            str(Config.DEFAULT_FEE_PERCENT),
            str(Config.MIN_INR_FEE),
            str(Config.MIN_USD_FEE),
            Config.UPI_ID,
            Config.UPI_PAYEE_NAME,
            Config.MM_USERNAME
        ))

# ============================================
# UPI QR GENERATOR
# ============================================

def generate_upi_uri(upi_id: str, payee_name: str, amount: float, currency: str = "INR", 
                     transaction_note: str = None) -> str:
    """
    Generate UPI payment URI
    
    Args:
        upi_id: UPI ID (e.g., ritik.bhattacharya@fam)
        payee_name: Payee display name
        amount: Amount to pay
        currency: Currency code (INR/USD)
        transaction_note: Optional transaction note
    
    Returns:
        UPI URI string
    """
    # URL encode the payee name
    import urllib.parse
    encoded_name = urllib.parse.quote(payee_name)
    
    # Build base URI
    uri = f"upi://pay?pa={upi_id}&pn={encoded_name}&am={amount:.2f}&cu={currency}"
    
    # Add transaction note if provided
    if transaction_note:
        encoded_note = urllib.parse.quote(transaction_note)
        uri += f"&tn={encoded_note}"
    
    return uri

def generate_qr_code(upi_uri: str, size: int = 300) -> bytes:
    """
    Generate QR code image from UPI URI
    
    Args:
        upi_uri: UPI URI string
        size: QR code size in pixels
    
    Returns:
        Bytes of PNG image
    """
    # Create QR code instance
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    
    # Add data
    qr.add_data(upi_uri)
    qr.make(fit=True)
    
    # Create image
    img = qr.make_image(fill_color="black", back_color="white")
    
    # Resize if needed
    if size:
        img = img.resize((size, size), Image.Resampling.LANCZOS)
    
    # Convert to bytes
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    
    return img_bytes.getvalue()

def generate_payment_qr(deal_id: str, amount: float, currency: str = "INR") -> Tuple[bytes, str]:
    """
    Generate payment QR for a deal
    
    Args:
        deal_id: Deal ID
        amount: Amount to pay
        currency: Currency code
    
    Returns:
        Tuple of (QR image bytes, UPI URI)
    """
    # Get configuration
    upi_id = get_config('upi_id') or Config.UPI_ID
    payee_name = get_config('upi_payee_name') or Config.UPI_PAYEE_NAME
    
    if not upi_id:
        raise ValueError("UPI ID not configured")
    
    # Only support INR and USD
    if currency not in ['INR', 'USD']:
        raise ValueError(f"Currency {currency} not supported for UPI QR")
    
    # Generate UPI URI with transaction note
    note = f"Payment for Deal {deal_id}"
    upi_uri = generate_upi_uri(upi_id, payee_name, amount, currency, note)
    
    # Generate QR code
    qr_bytes = generate_qr_code(upi_uri)
    
    return qr_bytes, upi_uri

# ============================================
# DATABASE HELPERS
# ============================================

def get_user(telegram_id: int) -> Optional[Dict]:
    """Get user by Telegram ID"""
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()
        return dict(user) if user else None

def get_user_by_id(user_id: int) -> Optional[Dict]:
    """Get user by database ID"""
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
        return dict(user) if user else None

def get_or_create_user(telegram_id: int, username: str = None, display_name: str = None) -> Dict:
    """Get or create user"""
    with db_transaction() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()
        
        if user:
            return dict(user)
        
        # Check if this is a super owner, owner, or admin
        role = 'user'
        if telegram_id in Config.SUPER_OWNER_IDS:
            role = 'super_owner'
        elif telegram_id in Config.OWNER_IDS:
            role = 'owner'
        elif telegram_id in Config.ADMIN_IDS:
            role = 'admin'
        
        conn.execute(
            """INSERT INTO users (telegram_id, username, display_name, role)
               VALUES (?, ?, ?, ?)""",
            (telegram_id, username or f"user_{telegram_id}", display_name or "User", role)
        )
        
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ).fetchone()
        return dict(user)

def update_user_role(telegram_id: int, role: str) -> bool:
    """Update user role"""
    with db_transaction() as conn:
        conn.execute(
            "UPDATE users SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = ?",
            (role, telegram_id)
        )
        return conn.rowcount > 0

def get_config(key: str) -> Optional[str]:
    """Get configuration value"""
    with get_db() as conn:
        result = conn.execute(
            "SELECT value FROM configuration WHERE key = ?",
            (key,)
        ).fetchone()
        return result['value'] if result else None

def set_config(key: str, value: str, updated_by_id: int = None) -> bool:
    """Set configuration value"""
    with db_transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO configuration (key, value, updated_by_id, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
            (key, value, updated_by_id)
        )
        return True


SERVICE_STOPPED_MESSAGE = (
    "🛑 Naoya's Middleman Service is currently stopped by the Owner.\n\n"
    "New deals and bot actions are paused. Your existing deal data is safe.\n"
    "Please contact the Owner to resume the service."
)


def service_is_running() -> bool:
    return (get_config('service_status') or 'running') == 'running'


def is_super_owner_id(telegram_id: int) -> bool:
    return telegram_id in Config.SUPER_OWNER_IDS or get_user_role(telegram_id) == 'super_owner'


async def service_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause regular service traffic while stopped; superowners retain access."""
    if service_is_running():
        return
    user = update.effective_user
    # Superowners can still use the complete bot while service traffic is
    # paused, including panels, support, audits, and resume controls.
    if user and is_super_owner_id(user.id):
        return
    data = update.callback_query.data if update.callback_query else None
    message_text = update.effective_message.text if update.effective_message else ''
    allowed = is_super_owner_id(user.id) and (
        data == 'super_resume' or
        (message_text or '').split()[0].split('@')[0].lower() in ('/resume', '/startservice')
        if message_text else data == 'super_resume'
    )
    if allowed:
        return
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(SERVICE_STOPPED_MESSAGE)
    elif update.effective_message:
        await update.effective_message.reply_text(SERVICE_STOPPED_MESSAGE)
    raise ApplicationHandlerStop


async def resume_service_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_super_owner_id(update.effective_user.id):
        await update.message.reply_text(SERVICE_STOPPED_MESSAGE)
        return
    set_config('service_status', 'running', get_or_create_user(update.effective_user.id)['id'])
    await update.message.reply_text(
        "✅ Naoya's Middleman Service has been resumed by the Superowner.\n\n"
        "Hint: use /help to view the workflow."
    )


async def case_insensitive_command_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route slash commands without caring about command-letter casing."""
    message = update.effective_message
    if not message or not message.text or not message.text.startswith('/'):
        return
    parts = message.text.split()
    command = parts[0][1:].split('@', 1)[0].lower()
    context.args = parts[1:]
    handlers = {
        'start': start,
        's': start,
        'help': help_command,
        'cancel': cancel,
        'resume': resume_service_command,
        'startservice': resume_service_command,
        'myid': myid_command,
        'status': status_command,
        'deals': deals_command,
        'message': message_user_command,
        'reply': dispute_reply_command,
        'addadmin': add_admin_command,
        'removeadmin': remove_admin_command,
        'lock': group_lock_handler,
        'hold': group_hold_handler,
        'held': group_hold_handler,
        'release': group_release_handler,
    }
    handler = handlers.get(command)
    if handler:
        await handler(update, context)
        raise ApplicationHandlerStop

# ============================================
# UTILITY FUNCTIONS
# ============================================

def generate_deal_id() -> str:
    """Generate unique deal ID"""
    return f"NM{''.join(random.choices(string.digits, k=6))}"

def generate_connection_token() -> str:
    """Generate unique connection token"""
    return f"NM{''.join(random.choices(string.ascii_uppercase + string.digits, k=8))}"

def encode_user_id(telegram_id: int) -> str:
    """Create a stable, shareable Naoya MM ID for a Telegram user."""
    alphabet = string.digits + string.ascii_uppercase
    value = int(telegram_id)
    encoded = '0' if value == 0 else ''
    while value:
        value, remainder = divmod(value, 36)
        encoded = alphabet[remainder] + encoded
    return f"NMU{encoded}"

def decode_user_id(naoya_id: str) -> Optional[int]:
    """Decode a stable Naoya MM ID, returning None for invalid input."""
    value = naoya_id.strip().upper()
    if not value.startswith('NMU') or len(value) <= 3:
        return None
    encoded = value[3:]
    if not re.fullmatch(r'[0-9A-Z]+', encoded):
        return None
    try:
        return int(encoded, 36)
    except ValueError:
        return None

def generate_unique_deal_id() -> str:
    """Generate a deal ID that is unique in the database."""
    for _ in range(20):
        deal_id = generate_deal_id()
        try:
            with get_db() as conn:
                exists = conn.execute("SELECT 1 FROM deals WHERE deal_id = ?", (deal_id,)).fetchone()
            if not exists:
                return deal_id
        except sqlite3.OperationalError:
            return deal_id
    raise RuntimeError("Could not generate a unique deal ID")

def is_authorized(telegram_id: int, required_role: str = 'user') -> bool:
    """Check if user has required role"""
    user = get_user(telegram_id)
    if not user:
        return False
    
    roles = {'super_owner': 4, 'owner': 3, 'admin': 2, 'user': 1}
    return roles.get(user['role'], 0) >= roles.get(required_role, 0)

def get_user_role(telegram_id: int) -> str:
    """Get user role"""
    user = get_user(telegram_id)
    return user['role'] if user else 'user'

def format_currency(amount: float, currency: str = "INR") -> str:
    """Format currency amount"""
    if currency == "INR":
        return f"₹{amount:,.2f}"
    elif currency == "USD":
        return f"${amount:,.2f}"
    else:
        return f"{amount:,.2f} {currency}"

def calculate_fee(amount: float, currency: str = "INR") -> tuple:
    """Calculate MM fee"""
    # Get fee configuration
    fee_percent = float(get_config('fee_percent') or Config.DEFAULT_FEE_PERCENT)
    min_fee = float(get_config('min_inr_fee') if currency == 'INR' else get_config('min_usd_fee') or Config.MIN_INR_FEE)
    
    # Check tiered fees
    fee_tiers = json.loads(get_config('fee_tiers') or '{}')
    for tier_range, tier_percent in sorted(fee_tiers.items(), key=lambda x: float(x[0].split('-')[0])):
        low, high = map(float, tier_range.split('-'))
        if low <= amount <= high:
            fee_percent = float(tier_percent)
            break
    
    fee = amount * (fee_percent / 100)
    fee = max(fee, min_fee)
    return fee, fee_percent

def get_status_display(status: str) -> str:
    """Get user-friendly status display"""
    status_map = {
        'created': '📋 Created',
        'awaiting_confirmation': '⏳ Awaiting Confirmation',
        'confirmed': '✅ Confirmed',
        'awaiting_payment': '💰 Awaiting Payment',
        'payment_pending_verification': '⏳ Payment Pending Verification',
        'payment_rejected': '❌ Payment Rejected',
        'payment_held': '🔒 Payment Held',
        'awaiting_delivery': '📦 Awaiting Delivery',
        'delivered': '📦 Delivered',
        'awaiting_buyer_confirmation': '⏳ Awaiting Buyer Confirmation',
        'release_requested': '💸 Release Requested',
        'completed': '✅ Completed',
        'cancelled': '❌ Cancelled',
        'disputed': '⚠️ Disputed',
        'refunded': '↩️ Refunded'
    }
    return status_map.get(status, status.replace('_', ' ').title())

def can_transition_to(current_status: str, new_status: str) -> bool:
    """Check if status transition is allowed"""
    transitions = {
        'created': ['awaiting_confirmation', 'cancelled'],
        'awaiting_confirmation': ['confirmed', 'cancelled'],
        'confirmed': ['awaiting_payment', 'payment_pending_verification', 'payment_held', 'cancelled'],
        'awaiting_payment': ['payment_pending_verification', 'cancelled'],
        'payment_pending_verification': ['payment_held', 'payment_rejected', 'disputed'],
        'payment_rejected': ['awaiting_payment', 'cancelled'],
        'payment_held': ['awaiting_delivery', 'release_requested', 'disputed', 'refunded'],
        'awaiting_delivery': ['delivered', 'release_requested', 'disputed'],
        'delivered': ['awaiting_buyer_confirmation', 'release_requested', 'payment_held', 'disputed'],
        'awaiting_buyer_confirmation': ['release_requested', 'disputed'],
        'release_requested': ['completed', 'payment_held', 'disputed'],
        'completed': [],
        'cancelled': [],
        'disputed': ['payment_held', 'release_requested', 'refunded', 'completed', 'cancelled'],
        'refunded': []
    }
    return new_status in transitions.get(current_status, [])

def _insert_audit(conn, actor_id: int, action: str, deal_id: str = None,
                  old_status: str = None, new_status: str = None,
                  metadata: dict = None):
    """Insert an audit row using an already-open database connection."""
    conn.execute(
        """INSERT INTO audit_logs (actor_id, deal_id, action, old_status, new_status, metadata)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (actor_id, deal_id, action, old_status, new_status,
         json.dumps(metadata) if metadata else None)
    )

def log_audit(actor_id: int, action: str, deal_id: str = None,
              old_status: str = None, new_status: str = None,
              metadata: dict = None):
    """Log audit entry in its own transaction when no transaction is active."""
    with db_transaction() as conn:
        _insert_audit(conn, actor_id, action, deal_id, old_status, new_status, metadata)

# ============================================
# DEAL SERVICE
# ============================================

def create_deal(buyer_id: int, seller_id: int, mm_id: int,
                item_description: str, amount: float, currency: str,
                payment_method: str, terms: str = None) -> Dict:
    """Create a new deal"""
    # Calculate fee
    fee, fee_percent = calculate_fee(amount, currency)
    seller_net = amount - fee
    
    deal_id = generate_unique_deal_id()
    connection_token = generate_connection_token()
    
    with db_transaction() as conn:
        # Get user IDs from telegram IDs
        buyer = get_user(buyer_id)
        seller = get_user(seller_id)
        mm = get_user(mm_id) if mm_id else None
        
        if not buyer or not seller:
            raise ValueError("Buyer or seller not found")
        
        cursor = conn.execute(
            """INSERT INTO deals (
                deal_id, connection_token, buyer_id, seller_id, mm_id,
                item_description, amount, currency, payment_method,
                mm_fee, seller_net_amount, terms, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                deal_id, connection_token,
                buyer['id'], seller['id'], mm['id'] if mm else None,
                item_description, amount, currency, payment_method,
                fee, seller_net, terms, 'created'
            )
        )
        
        deal = conn.execute(
            "SELECT * FROM deals WHERE id = ?",
            (cursor.lastrowid,)
        ).fetchone()
        
        _insert_audit(conn, buyer_id, 'DEAL_CREATED', deal_id)
        
        return dict(deal)

def get_deal(deal_id: str) -> Optional[Dict]:
    """Get deal by ID"""
    with get_db() as conn:
        deal = conn.execute(
            "SELECT * FROM deals WHERE deal_id = ?",
            (deal_id,)
        ).fetchone()
        return dict(deal) if deal else None


def get_release_actions(deal: Dict) -> Dict:
    """Return persisted buyer/seller release choices for a deal."""
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO deal_release_actions (deal_id) VALUES (?)",
            (deal['id'],),
        )
        row = conn.execute(
            "SELECT * FROM deal_release_actions WHERE deal_id = ?", (deal['id'],)
        ).fetchone()
    return dict(row)


def set_release_action(deal: Dict, role: str, action: str) -> None:
    column = 'buyer_action' if role == 'buyer' else 'seller_action'
    with db_transaction() as conn:
        conn.execute(
            f"""INSERT INTO deal_release_actions (deal_id, {column}, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(deal_id) DO UPDATE SET {column} = excluded.{column},
                updated_at = CURRENT_TIMESTAMP""",
            (deal['id'], action),
        )


def save_payout_detail(deal: Dict, upi_id: str = None, qr_file_id: str = None) -> None:
    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO deal_release_actions (deal_id, seller_upi_id, seller_qr_file_id)
               VALUES (?, ?, ?)
               ON CONFLICT(deal_id) DO UPDATE SET
               seller_upi_id = COALESCE(excluded.seller_upi_id, seller_upi_id),
               seller_qr_file_id = COALESCE(excluded.seller_qr_file_id, seller_qr_file_id),
               updated_at = CURRENT_TIMESTAMP""",
            (deal['id'], upi_id, qr_file_id),
        )


def save_payout_proof(deal: Dict, proof_file_id: str) -> None:
    with db_transaction() as conn:
        conn.execute(
            """INSERT INTO deal_release_actions (deal_id, payout_proof_file_id)
               VALUES (?, ?)
               ON CONFLICT(deal_id) DO UPDATE SET
               payout_proof_file_id = excluded.payout_proof_file_id,
               updated_at = CURRENT_TIMESTAMP""",
            (deal['id'], proof_file_id),
        )

def get_deal_confirmation_count(deal: Dict) -> int:
    """Return how many of the two participants have confirmed a deal."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS count FROM deal_confirmations
               WHERE deal_id = ? AND user_id IN (?, ?)""",
            (deal['id'], deal['buyer_id'], deal['seller_id'])
        ).fetchone()
    return int(row['count'])

def get_deals_by_user(telegram_id: int, status: str = None) -> List[Dict]:
    """Get deals for a user"""
    user = get_user(telegram_id)
    if not user:
        return []
    
    with get_db() as conn:
        query = """
            SELECT d.*, 
                   u1.display_name as buyer_name, u2.display_name as seller_name
            FROM deals d
            LEFT JOIN users u1 ON d.buyer_id = u1.id
            LEFT JOIN users u2 ON d.seller_id = u2.id
            WHERE d.buyer_id = ? OR d.seller_id = ?
        """
        params = [user['id'], user['id']]
        
        if status:
            query += " AND d.status = ?"
            params.append(status)
        
        query += " ORDER BY d.created_at DESC"
        
        deals = conn.execute(query, params).fetchall()
        return [dict(d) for d in deals]

def update_deal_status(deal_id: str, new_status: str, actor_id: int) -> bool:
    """Update deal status with validation"""
    deal = get_deal(deal_id)
    if not deal:
        return False
    
    if not can_transition_to(deal['status'], new_status):
        return False
    
    with db_transaction() as conn:
        conn.execute(
            """UPDATE deals SET status = ?, updated_at = CURRENT_TIMESTAMP,
               completed_at = CASE WHEN ? = 'completed' THEN CURRENT_TIMESTAMP ELSE completed_at END
               WHERE deal_id = ?""",
            (new_status, new_status, deal_id)
        )
        
        _insert_audit(
            conn,
            actor_id, 'DEAL_STATUS_CHANGE', deal_id,
            deal['status'], new_status
        )
        
        return True

# ============================================
# PAYMENT SERVICE WITH QR GENERATION
# ============================================

def create_payment(deal_id: str, amount: float, currency: str,
                   method: str, proof_file_id: str = None,
                   reference: str = None, caption: str = None) -> Optional[Dict]:
    """Create payment record"""
    deal = get_deal(deal_id)
    if not deal:
        return None
    
    with db_transaction() as conn:
        cursor = conn.execute(
            """INSERT INTO payments (
                deal_id, amount, currency, method, proof_file_id,
                reference, proof_caption, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (deal['id'], amount, currency, method, proof_file_id,
             reference, caption, 'pending')
        )
        
        payment = conn.execute(
            "SELECT * FROM payments WHERE id = ?",
            (cursor.lastrowid,)
        ).fetchone()

    # Update after the payment insert transaction has committed. Calling the
    # status service from inside the transaction can lock SQLite.
    update_deal_status(deal_id, 'payment_pending_verification', deal['buyer_id'])
    return dict(payment)

def generate_payment_qr_for_deal(deal_id: str) -> Tuple[bytes, str, str]:
    """
    Generate payment QR for a deal
    
    Args:
        deal_id: Deal ID
    
    Returns:
        Tuple of (QR image bytes, UPI URI, error message or None)
    """
    deal = get_deal(deal_id)
    if not deal:
        return None, None, "Deal not found"
    
    # Check if payment method is UPI
    if deal['payment_method'].upper() != 'UPI':
        return None, None, f"Payment method is {deal['payment_method']}, not UPI"
    
    # Check currency
    if deal['currency'] not in ['INR', 'USD']:
        return None, None, f"Currency {deal['currency']} not supported for UPI QR"
    
    try:
        # Generate QR
        qr_bytes, upi_uri = generate_payment_qr(
            deal_id,
            deal['amount'],
            deal['currency']
        )
        
        # Record QR generation
        with db_transaction() as conn:
            conn.execute(
                """UPDATE payments 
                   SET upi_qr_generated_at = CURRENT_TIMESTAMP 
                   WHERE deal_id = ? AND status = 'pending'""",
                (deal['id'],)
            )
        
        return qr_bytes, upi_uri, None
        
    except Exception as e:
        return None, None, f"Failed to generate QR: {str(e)}"

def verify_payment(payment_id: int, verified_by_id: int, approve: bool) -> bool:
    """Verify or reject payment"""
    with db_transaction() as conn:
        payment = conn.execute(
            "SELECT * FROM payments WHERE id = ?",
            (payment_id,)
        ).fetchone()
        
        if not payment:
            return False
        
        if payment['status'] != 'pending':
            return False
        
        new_status = 'verified' if approve else 'rejected'
        conn.execute(
            """UPDATE payments SET status = ?, verified_by_id = ?, verified_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (new_status, verified_by_id, payment_id)
        )
        
        deal = conn.execute(
            "SELECT * FROM deals WHERE id = ?",
            (payment['deal_id'],)
        ).fetchone()

    # Complete related writes after the payment transaction commits so SQLite
    # does not receive a nested write transaction.
    if approve:
        update_deal_status(deal['deal_id'], 'payment_held', verified_by_id)
    else:
        update_deal_status(deal['deal_id'], 'payment_rejected', verified_by_id)

    log_audit(
        verified_by_id,
        'PAYMENT_APPROVED' if approve else 'PAYMENT_REJECTED',
        deal['deal_id']
    )
    return True

def get_payment(deal_id: str) -> Optional[Dict]:
    """Get payment for deal"""
    deal = get_deal(deal_id)
    if not deal:
        return None
    
    with get_db() as conn:
        payment = conn.execute(
            "SELECT * FROM payments WHERE deal_id = ? ORDER BY created_at DESC LIMIT 1",
            (deal['id'],)
        ).fetchone()
        return dict(payment) if payment else None

def get_active_deal_for_user(telegram_id: int) -> Optional[Dict]:
    """Return the most recent active deal for a Telegram user."""
    user = get_user(telegram_id)
    if not user:
        return None

    active_statuses = (
        # Messages are relayed only after both participants confirm.
        'confirmed', 'awaiting_payment',
        'payment_pending_verification', 'payment_held', 'payment_rejected',
        'awaiting_delivery', 'delivered', 'awaiting_buyer_confirmation',
        'release_requested', 'disputed'
    )
    placeholders = ','.join('?' for _ in active_statuses)
    with get_db() as conn:
        deal = conn.execute(
            f"""SELECT * FROM deals
                WHERE (buyer_id = ? OR seller_id = ?)
                  AND status IN ({placeholders})
                ORDER BY updated_at DESC LIMIT 1""",
            (user['id'], user['id'], *active_statuses)
        ).fetchone()
    return dict(deal) if deal else None

async def relay_deal_message(context: ContextTypes.DEFAULT_TYPE,
                             message: Message,
                             deal: Dict) -> None:
    """Relay a participant's message to the other participant and deal channel."""
    # Deal chat is private and participant-only. Never relay group traffic.
    if not message.chat or message.chat.type != 'private':
        return
    sender = message.from_user
    sender_db_user = get_user(sender.id) if sender else None
    if not sender_db_user or sender_db_user['id'] not in (deal['buyer_id'], deal['seller_id']):
        return
    sender_name = sender.full_name if sender else 'Participant'
    sender_tag = f"@{sender.username}" if sender and sender.username else sender_name
    header = (
        f"💬 Deal Message · {deal['deal_id']}\n"
        f"From: {sender_tag}"
    )

    # Send a readable header and copy the original message to the deal log.
    if Config.DEAL_LOG_CHANNEL_ID:
        try:
            await context.bot.send_message(
                Config.DEAL_LOG_CHANNEL_ID, header
            )
            await context.bot.copy_message(
                chat_id=Config.DEAL_LOG_CHANNEL_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except Exception as error:
            logging.error("Failed to forward deal message to channel: %s", error)

    # Relay the same message privately to the other deal participant.
    sender_telegram_id = sender.id if sender else None
    for participant_db_id in (deal['buyer_id'], deal['seller_id']):
        participant = get_user_by_id(participant_db_id)
        if not participant or participant['telegram_id'] == sender_telegram_id:
            continue
        try:
            await context.bot.send_message(
                participant['telegram_id'], header
            )
            await context.bot.copy_message(
                chat_id=participant['telegram_id'],
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except Exception as error:
            logging.error("Failed to relay deal message to %s: %s", participant['telegram_id'], error)

async def notify_deal_participants(context: ContextTypes.DEFAULT_TYPE,
                                   deal: Dict,
                                   text: str) -> None:
    """Notify both participants about a deal event."""
    for participant_db_id in (deal['buyer_id'], deal['seller_id']):
        participant = get_user_by_id(participant_db_id)
        if not participant:
            continue
        try:
            await context.bot.send_message(
                participant['telegram_id'], text, parse_mode=ParseMode.MARKDOWN
            )
        except Exception as error:
            logging.error("Failed to notify participant %s: %s", participant['telegram_id'], error)


async def notify_deal_user(context: ContextTypes.DEFAULT_TYPE,
                           deal: Dict,
                           participant_db_id: int,
                           text: str) -> None:
    """Notify exactly one participant; useful once the buyer exits the flow."""
    participant = get_user_by_id(participant_db_id)
    if not participant:
        return
    try:
        await context.bot.send_message(participant['telegram_id'], text)
    except Exception as error:
        logging.warning("Could not notify deal participant %s: %s", participant['telegram_id'], error)


async def begin_payout_collection(context: ContextTypes.DEFAULT_TYPE,
                                  deal: Dict,
                                  seller_telegram_id: int) -> None:
    """Ask the seller for payout details after both sides release."""
    seller = get_user_by_id(deal['seller_id'])
    if not seller or seller['telegram_id'] != seller_telegram_id:
        return
    text = (
        f"💸 **Both parties approved release for {deal['deal_id']}**\n\n"
        "Step 1/2 — send the seller's UPI ID.\n"
        "Hint: send only the UPI ID, for example `name@upi`.\n\n"
        "Step 2/2 — after that, send a clear QR image for the same account.\n"
        "The MM will verify the details, deduct the agreed fee, and request payout proof."
    )
    await context.bot.send_message(seller_telegram_id, text, parse_mode=ParseMode.MARKDOWN)

async def edit_callback_message(query, text: str, **kwargs) -> None:
    """Edit either a text message or a photo/document caption safely."""
    try:
        message = query.message
        if message and (message.photo or message.document or message.video):
            await query.edit_message_caption(caption=text, **kwargs)
        else:
            await query.edit_message_text(text=text, **kwargs)
    except Exception as error:
        # A channel post may not be editable in some Telegram configurations.
        # Still show the user the result instead of crashing the callback.
        logging.warning("Could not edit callback message: %s", error)
        if query.message:
            fallback_kwargs = {key: value for key, value in kwargs.items()
                               if key != 'reply_markup'}
            await query.message.reply_text(text, **fallback_kwargs)

# ============================================
# KEYBOARD BUILDERS
# ============================================

def get_main_keyboard(user_role: str = 'user') -> InlineKeyboardMarkup:
    """Get main keyboard based on role"""
    keyboard = [
        [InlineKeyboardButton("🤝 Start New Deal", callback_data="new_deal")],
        [InlineKeyboardButton("📋 My Deals", callback_data="my_deals")],
        [InlineKeyboardButton("🆘 Support", callback_data="support")],
    ]
    
    if user_role == 'admin':
        keyboard.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    
    if user_role == 'owner':
        keyboard.append([InlineKeyboardButton("👑 Owner Panel", callback_data="owner_panel")])

    if user_role == 'super_owner':
        keyboard.append([InlineKeyboardButton("👑 Super Owner Panel", callback_data="super_panel")])
    
    return InlineKeyboardMarkup(keyboard)

def get_deal_keyboard(deal_id: str, status: str, user_role: str,
                      participant_role: str = None) -> InlineKeyboardMarkup:
    """Get deal-specific keyboard"""
    keyboard = []
    
    if status == 'created':
        keyboard.append([InlineKeyboardButton("✅ Confirm Deal", callback_data=f"confirm_deal_{deal_id}")])
    elif status == 'awaiting_confirmation':
        keyboard.append([InlineKeyboardButton("✅ Confirm Deal", callback_data=f"confirm_deal_{deal_id}")])
    elif status == 'confirmed':
        if participant_role in (None, 'buyer'):
            keyboard.append([InlineKeyboardButton("💰 Pay Now", callback_data=f"pay_{deal_id}")])
    elif status == 'payment_held':
        if participant_role in (None, 'buyer', 'seller'):
            keyboard.append([InlineKeyboardButton("💸 Release Payment", callback_data=f"release_payment_{deal_id}")])
            keyboard.append([InlineKeyboardButton("🔒 Hold / Dispute", callback_data=f"hold_payment_{deal_id}")])
        if participant_role in (None, 'seller'):
            keyboard.append([InlineKeyboardButton("📦 Mark Delivered", callback_data=f"deliver_{deal_id}")])
    elif status == 'delivered':
        if participant_role in (None, 'buyer'):
            keyboard.append([InlineKeyboardButton("✅ Accept Delivery", callback_data=f"accept_{deal_id}")])
    elif status == 'release_requested':
        keyboard.append([InlineKeyboardButton("💸 Request Release", callback_data=f"request_release_{deal_id}")])

    if status not in ['confirmed', 'payment_pending_verification', 'payment_held',
                      'awaiting_delivery', 'delivered', 'completed', 'cancelled']:
        keyboard.append([InlineKeyboardButton("❌ Cancel Deal", callback_data=f"cancel_deal_{deal_id}")])
    
    if status not in ['completed', 'cancelled']:
        keyboard.append([InlineKeyboardButton("⚠️ Open Dispute", callback_data=f"dispute_{deal_id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    
    return InlineKeyboardMarkup(keyboard)

def get_owner_panel() -> InlineKeyboardMarkup:
    """Get owner panel keyboard"""
    keyboard = [
        [InlineKeyboardButton("🤝 Active Deals", callback_data="owner_active_deals")],
        [InlineKeyboardButton("💰 Payment Verification", callback_data="owner_payments")],
        [InlineKeyboardButton("💸 Release Requests", callback_data="owner_releases")],
        [InlineKeyboardButton("⚠️ Disputes", callback_data="owner_disputes")],
        [InlineKeyboardButton("👥 Users", callback_data="owner_users")],
        [InlineKeyboardButton("👨‍💼 Admin Management", callback_data="owner_admins")],
        [InlineKeyboardButton("💵 Fee Configuration", callback_data="owner_fees")],
        [InlineKeyboardButton("🏦 Payment Configuration", callback_data="owner_payment_config")],
        [InlineKeyboardButton("📊 Statistics", callback_data="owner_stats")],
        [InlineKeyboardButton("📜 Audit Logs", callback_data="owner_audit")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_super_owner_panel() -> InlineKeyboardMarkup:
    """Get super owner panel keyboard"""
    keyboard = [
        [InlineKeyboardButton("👑 Owner Management", callback_data="super_owners")],
        [InlineKeyboardButton("👥 Admin Management", callback_data="super_admins")],
        [InlineKeyboardButton("⚙️ Global Configuration", callback_data="super_config")],
        [InlineKeyboardButton("📊 System Statistics", callback_data="super_stats")],
        [InlineKeyboardButton("📜 Complete Audit Logs", callback_data="super_audit")],
        [InlineKeyboardButton("💾 Backup", callback_data="super_backup")],
        [InlineKeyboardButton("♻️ Restore", callback_data="super_restore")],
        [InlineKeyboardButton("🛑 System Shutdown", callback_data="super_shutdown")],
        [InlineKeyboardButton("▶️ Resume Service", callback_data="super_resume")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ============================================
# BOT HANDLERS
# ============================================

# Conversation states
SELECTING_ACTION, AWAITING_CONNECTION_ID, AWAITING_DEAL_DETAILS, AWAITING_PAYMENT_PROOF = range(4)

HELP_TEXT = """🤝 **NAOYA'S MIDDLEMAN BOT — HELP**

**User commands**
/start — Open your dashboard and get your partner ID
/help — Show this help message
/cancel — Cancel the current operation

**Deal flow**
1. Press **Start New Deal**.
2. Enter your partner's Telegram ID.
3. Choose whether you are the buyer or seller.
4. Enter the deal details.
5. Both parties confirm the deal.
6. The buyer pays and sends a screenshot/UTR.
7. An owner verifies the payment.
8. The seller delivers, the buyer accepts, and the owner releases funds.

Messages sent during an active deal are relayed to the other participant and logged in the deal channel.

**Private helper commands**
/myid — Show your Naoya MM ID
/status [DEAL_ID] — Show a deal's current status
/deals — List your active deals
/hold [DEAL_ID] — Keep payment held
/release [DEAL_ID] — Request release after delivery

**Group behavior**
/lock — Lock a deal group; only a Telegram group admin can use it.
The bot stays silent in groups unless someone explicitly types: mm, middleman,
middlemen, or escrow.

Admins and owners also have panel buttons for support, disputes, payments, and active deals."""

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the complete user guide."""
    await update.message.reply_text(
        HELP_TEXT,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🤝 Start a Deal", callback_data="new_deal")],
            [InlineKeyboardButton("🆘 Support", callback_data="support")],
        ])
    )

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast a message to all users (owner only)."""
    if not is_authorized(update.effective_user.id, 'owner'):
        await update.message.reply_text("❌ Owner access required.")
        return
    text = ' '.join(context.args).strip()
    if not text:
        context.user_data['deal_state'] = 'awaiting_broadcast'
        await update.message.reply_text("📢 Send the broadcast message now, or type /cancel.")
        return
    await send_broadcast(context, text)
    await update.message.reply_text("✅ Broadcast sent to all reachable users.")

async def message_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a direct message to a user by Telegram ID (admin/owner)."""
    if not is_authorized(update.effective_user.id, 'admin'):
        await update.message.reply_text("❌ Admin access required.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /message <telegram_id> <message>")
        return
    try:
        recipient_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Telegram ID must be numeric.")
        return
    await context.bot.send_message(recipient_id, ' '.join(context.args[1:]))
    await update.message.reply_text("✅ Message sent.")

async def dispute_reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to both parties in a deal dispute."""
    if not is_authorized(update.effective_user.id, 'admin'):
        await update.message.reply_text("❌ Admin access required.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reply <DEAL_ID> <message>")
        return
    deal = get_deal(context.args[0].upper())
    if not deal:
        await update.message.reply_text("❌ Deal not found.")
        return
    reply = f"🛡️ **Admin message for deal {deal['deal_id']}**\n\n" + ' '.join(context.args[1:])
    await notify_deal_participants(context, deal, reply)
    await update.message.reply_text("✅ Reply sent to both deal participants.", parse_mode=ParseMode.MARKDOWN)

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id, 'owner'):
        await update.message.reply_text("❌ Owner access required.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /addadmin <telegram_id>")
        return
    telegram_id = int(context.args[0])
    target = get_or_create_user(telegram_id)
    update_user_role(telegram_id, 'admin')
    if telegram_id not in Config.ADMIN_IDS:
        Config.ADMIN_IDS.append(telegram_id)
    try:
        await context.bot.send_message(telegram_id, "✅ You have been added as an admin of Naoya's Middleman Bot.")
    except Exception as error:
        logging.warning("Could not notify new admin %s: %s", telegram_id, error)
    await update.message.reply_text(f"✅ {telegram_id} is now an admin.")

async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id, 'owner'):
        await update.message.reply_text("❌ Owner access required.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /removeadmin <telegram_id>")
        return
    telegram_id = int(context.args[0])
    update_user_role(telegram_id, 'user')
    Config.ADMIN_IDS[:] = [admin_id for admin_id in Config.ADMIN_IDS if admin_id != telegram_id]
    try:
        await context.bot.send_message(telegram_id, "ℹ️ Your admin access has been removed.")
    except Exception as error:
        logging.warning("Could not notify removed admin %s: %s", telegram_id, error)
    await update.message.reply_text(f"✅ Admin access removed for {telegram_id}.")

async def send_broadcast(context: ContextTypes.DEFAULT_TYPE, text: str) -> int:
    """Send a broadcast and return the number of successful deliveries."""
    with get_db() as conn:
        users = conn.execute("SELECT telegram_id FROM users WHERE is_blocked = 0").fetchall()
    delivered = 0
    for row in users:
        try:
            await context.bot.send_message(row['telegram_id'], f"📢 Announcement\n\n{text}")
            delivered += 1
        except Exception as error:
            logging.warning("Broadcast failed for %s: %s", row['telegram_id'], error)
    return delivered

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    telegram_id = user.id
    
    # Get or create user
    db_user = get_or_create_user(telegram_id, user.username, user.full_name)
    
    # Check if user is blocked
    if db_user.get('is_blocked', 0):
        await update.message.reply_text("❌ You have been blocked from using this bot.")
        return
    
    # Generate connection ID if not exists
    context.user_data['connection_id'] = encode_user_id(telegram_id)
    
    welcome_text = f"""
🤝 **NAOYA'S MIDDLEMAN SERVICE**

Welcome {user.full_name}!

I help facilitate secure middleman deals between buyers and sellers.

**Your Naoya MM ID:** `{context.user_data['connection_id']}`

Share this ID with your deal partner to connect.

━━━━━━━━━━━━━━━━━━━━

🔹 Start a new deal
🔹 View your deals
🔹 Get support

*Choose an option below:*
"""
    
    role = db_user['role']
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_keyboard(role)
    )

    # If this user was added to a deal before opening the bot, deliver the
    # pending confirmation prompt now. Telegram cannot proactively message a
    # user until they have started a chat with the bot.
    pending_deals = [
        d for d in get_deals_by_user(telegram_id)
        if d['status'] == 'awaiting_confirmation'
        and get_deal_confirmation_count(d) < 2
    ]
    for pending in pending_deals[:10]:
        try:
            await update.message.reply_text(
                f"🤝 DEAL AWAITING YOUR CONFIRMATION\n\n"
                f"Deal: {pending['deal_id']}\n"
                f"Item: {pending['item_description']}\n"
                f"Amount: {format_currency(pending['amount'], pending['currency'])}\n"
                f"Payment: {pending['payment_method']}\n\n"
                "Hint: both buyer and seller must confirm before messages and payment can proceed.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Confirm Deal", callback_data=f"confirm_deal_{pending['deal_id']}")],
                    [InlineKeyboardButton("❌ Cancel Deal", callback_data=f"cancel_deal_{pending['deal_id']}")],
                ])
            )
        except Exception as error:
            logging.warning("Could not send pending confirmation prompt: %s", error)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries"""
    query = update.callback_query
    await query.answer()
    
    user = update.effective_user
    telegram_id = user.id
    data = query.data
    
    db_user = get_or_create_user(telegram_id, user.username, user.full_name)
    
    if db_user.get('is_blocked', 0):
        await query.edit_message_text("❌ You have been blocked from using this bot.")
        return
    
    # Main menu
    if data == "main_menu":
        await query.edit_message_text(
            "🤝 **NAOYA'S MIDDLEMAN SERVICE**\n\nChoose an option:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_keyboard(db_user['role'])
        )
        return
    
    # New deal
    if data == "new_deal":
        context.user_data['deal_state'] = 'awaiting_connection'
        await query.edit_message_text(
            "🤝 **Start New Deal**\n\n"
            "Please enter your partner's Telegram ID or Naoya MM ID:\n\n"
            "Example Telegram ID: `8975969084`\n"
            "Example Naoya MM ID: `NM4A7B2C9D`\n\n"
            "Or type /cancel to cancel.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # My deals
    if data == "my_deals":
        deals = get_deals_by_user(telegram_id)
        if not deals:
            await query.edit_message_text(
                "📋 **Your Deals**\n\nYou have no deals yet.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]])
            )
            return
        
        text = "📋 **Your Deals**\n\n"
        for deal in deals[:10]:
            text += f"🆔 {deal['deal_id']} - {get_status_display(deal['status'])}\n"
            text += f"💰 {format_currency(deal['amount'], deal['currency'])}\n\n"
        
        if len(deals) > 10:
            text += f"... and {len(deals) - 10} more\n\n"
        
        keyboard = []
        for deal in deals[:5]:
            keyboard.append([InlineKeyboardButton(
                f"{deal['deal_id']} - {get_status_display(deal['status'])}",
                callback_data=f"view_deal_{deal['deal_id']}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # View deal
    if data.startswith("view_deal_"):
        deal_id = data.replace("view_deal_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]))
            return
        
        text = f"""
🆔 **Deal:** {deal['deal_id']}

📊 **Status:** {get_status_display(deal['status'])}
📦 **Item:** {deal['item_description']}
💰 **Amount:** {format_currency(deal['amount'], deal['currency'])}
💵 **MM Fee:** {format_currency(deal['mm_fee'], deal['currency'])}
💸 **Seller Net:** {format_currency(deal['seller_net_amount'], deal['currency'])}
📌 **Terms:** {deal['terms'] or 'Not specified'}

Created: {deal['created_at']}
"""
        
        # Get participants
        buyer = get_user_by_id(deal['buyer_id'])
        seller = get_user_by_id(deal['seller_id'])
        if buyer:
            text += f"\n👤 **Buyer:** @{buyer['username'] or 'N/A'}"
        if seller:
            text += f"\n👤 **Seller:** @{seller['username'] or 'N/A'}"

        participant_role = None
        if db_user['id'] == deal['buyer_id']:
            participant_role = 'buyer'
        elif db_user['id'] == deal['seller_id']:
            participant_role = 'seller'
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_deal_keyboard(deal_id, deal['status'], db_user['role'], participant_role)
        )
        return
    
    # Confirm deal
    if data.startswith("confirm_deal_"):
        deal_id = data.replace("confirm_deal_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        
        # Check if user is participant
        user = get_user(telegram_id)
        if not user or (user['id'] not in [deal['buyer_id'], deal['seller_id']]):
            await query.edit_message_text("❌ You are not a participant in this deal.")
            return
        
        if deal['status'] not in ['created', 'awaiting_confirmation']:
            await query.edit_message_text(
                f"ℹ️ This deal is already {get_status_display(deal['status'])}.",
                reply_markup=get_deal_keyboard(deal_id, deal['status'], db_user['role'])
            )
            return

        # Record this participant's confirmation. INSERT OR IGNORE makes an
        # old button safe to press repeatedly.
        with db_transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO deal_confirmations (deal_id, user_id) VALUES (?, ?)",
                (deal['id'], user['id'])
            )

        confirmation_count = get_deal_confirmation_count(deal)
        if confirmation_count < 2:
            await query.edit_message_text(
                f"✅ **Your confirmation was recorded.**\n\n"
                f"Waiting for the other participant ({confirmation_count}/2 confirmed).",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_deal_keyboard(
                    deal_id, 'awaiting_confirmation', db_user['role'],
                    'buyer' if user['id'] == deal['buyer_id'] else 'seller'
                )
            )
            return

        # Both unique participants have confirmed, so move the deal forward.
        update_deal_status(deal_id, 'confirmed', telegram_id)
        
        await query.edit_message_text(
            f"✅ **Deal Confirmed!**\n\n"
            f"Deal {deal_id} has been confirmed.\n\n"
            f"💰 Amount: {format_currency(deal['amount'], deal['currency'])}\n"
            f"Please proceed to make the payment.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_deal_keyboard(
                deal_id, 'confirmed', db_user['role'],
                'buyer' if user['id'] == deal['buyer_id'] else 'seller'
            )
        )
        return

    # Choose buyer/seller role before collecting deal details.
    if data in ["choose_role_buyer", "choose_role_seller"]:
        partner_id = context.user_data.get('pending_partner_telegram_id')
        if not partner_id:
            await query.edit_message_text("❌ Deal session expired. Please press Start New Deal again.")
            return
        context.user_data['my_deal_role'] = 'buyer' if data.endswith('buyer') else 'seller'
        context.user_data['partner_telegram_id'] = partner_id
        context.user_data['deal_state'] = 'awaiting_deal_details'
        await query.edit_message_text(
            f"✅ Role selected: **{context.user_data['my_deal_role'].title()}**\n\n"
            "Please describe the item/service being traded:",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Cancel deal
    if data.startswith("cancel_deal_"):
        deal_id = data.replace("cancel_deal_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return

        user = get_user(telegram_id)
        if not user or user['id'] not in [deal['buyer_id'], deal['seller_id']]:
            await query.edit_message_text("❌ You are not a participant in this deal.")
            return

        if deal['status'] in ['completed', 'cancelled', 'payment_held',
                              'awaiting_delivery', 'delivered']:
            await query.edit_message_text(
                f"ℹ️ This deal can no longer be cancelled because it is "
                f"{get_status_display(deal['status'])}.",
                reply_markup=get_deal_keyboard(deal_id, deal['status'], db_user['role'])
            )
            return

        if update_deal_status(deal_id, 'cancelled', telegram_id):
            cancelled_deal = get_deal(deal_id)
            if cancelled_deal:
                await notify_deal_participants(
                    context,
                    cancelled_deal,
                    f"❌ **Deal Cancelled**\n\nDeal `{deal_id}` was cancelled by a participant."
                )
            await query.edit_message_text(
                f"❌ **Deal Cancelled**\n\nDeal {deal_id} has been cancelled.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_deal_keyboard(deal_id, 'cancelled', db_user['role'])
            )
        else:
            await query.edit_message_text("❌ This deal cannot be cancelled in its current state.")
        return
    
    # Pay with QR Generation
    if data.startswith("pay_"):
        deal_id = data.replace("pay_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        
        if deal['status'] != 'confirmed':
            await query.edit_message_text(f"❌ Cannot pay in current status: {get_status_display(deal['status'])}")
            return
        
        # Check if payment method is UPI
        if deal['payment_method'].upper() != 'UPI':
            await query.edit_message_text(
                f"❌ Payment method is {deal['payment_method']}. UPI QR generation only available for UPI payments.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"view_deal_{deal_id}")]])
            )
            return
        
        # Generate QR Code
        await query.edit_message_text(
            "⏳ Generating payment QR code...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        qr_bytes, upi_uri, error = generate_payment_qr_for_deal(deal_id)
        
        if error:
            await query.edit_message_text(
                f"❌ {error}\n\n"
                f"Please contact support for assistance.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"view_deal_{deal_id}")]])
            )
            return
        
        # Get UPI ID
        upi_id = get_config('upi_id') or Config.UPI_ID
        
        payment_text = f"""
💳 **PAYMENT DETAILS**

🆔 Deal: {deal_id}

💰 **Deal Amount:** {format_currency(deal['amount'], deal['currency'])}
💵 **MM Fee:** {format_currency(deal['mm_fee'], deal['currency'])}
💳 **Amount to Pay:** {format_currency(deal['amount'], deal['currency'])}

🏦 **Payment Method:** UPI
📲 **UPI ID:** `{upi_id}`

📱 Scan the QR code below to pay.

⚠️ Please verify the recipient and amount in your UPI application before confirming the payment.

After payment, click the button below to submit your payment proof.

⏳ **STATUS:** PAYMENT PENDING VERIFICATION
"""
        
        # Store payment info for later
        context.user_data['payment_deal_id'] = deal_id
        
        # Send QR image
        try:
            await query.message.reply_photo(
                photo=io.BytesIO(qr_bytes),
                caption=payment_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ I've Made the Payment", callback_data=f"payment_done_{deal_id}")],
                    [InlineKeyboardButton("🔄 Regenerate QR", callback_data=f"pay_{deal_id}")],
                    [InlineKeyboardButton("🔙 Back", callback_data=f"view_deal_{deal_id}")]
                ])
            )
            # Delete the "generating" message
            await query.message.delete()
        except Exception as e:
            await query.edit_message_text(
                f"❌ Failed to send QR code: {str(e)}\n\n"
                f"Please use the UPI ID manually:\n`{upi_id}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ I've Made the Payment", callback_data=f"payment_done_{deal_id}")],
                    [InlineKeyboardButton("🔙 Back", callback_data=f"view_deal_{deal_id}")]
                ])
            )
        return
    
    # Payment done
    if data.startswith("payment_done_"):
        deal_id = data.replace("payment_done_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        
        context.user_data['payment_deal_id'] = deal_id
        context.user_data['deal_state'] = 'awaiting_payment_proof'
        
        await edit_callback_message(
            query,
            f"💰 **Payment Submission**\n\n"
            f"Deal: {deal_id}\n"
            f"Amount: {format_currency(deal['amount'], deal['currency'])}\n\n"
            f"Please send a screenshot/photo of your payment along with the UTR/reference number.\n\n"
            f"⚠️ **IMPORTANT:** Your payment will NOT be marked as received until an Owner verifies it.\n\n"
            f"Type /cancel to cancel.",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # ============================================
    # [Previous handlers for deliver, accept, release, etc.]
    # ============================================

    # Buyer/seller release decision after payment is held.
    if data.startswith("hold_payment_") or data.startswith("release_payment_"):
        deal_id = data.split('_', 2)[2]
        deal = get_deal(deal_id)
        user = get_user(telegram_id)
        if not deal or not user or user['id'] not in (deal['buyer_id'], deal['seller_id']):
            await query.edit_message_text("❌ Deal not found or you are not a participant.")
            return
        if deal['status'] == 'confirmed':
            # Reconcile a stale release button if payment was already
            # approved but the deal row was not refreshed in the user's UI.
            payment = get_payment(deal_id)
            if payment and payment.get('status') == 'verified':
                update_deal_status(deal_id, 'payment_held', telegram_id)
                deal = get_deal(deal_id) or deal
            else:
                await query.edit_message_text(
                    "⏳ Payment approval is still pending. Release choices will appear once the MM verifies the payment."
                )
                return
        if deal['status'] not in ('payment_held', 'disputed'):
            await query.edit_message_text("⏳ Release choices are not available at this step yet.")
            return
        role = 'buyer' if user['id'] == deal['buyer_id'] else 'seller'
        action = 'hold' if data.startswith('hold_payment_') else 'release'
        actions = get_release_actions(deal)

        if action == 'release' and role == 'seller' and actions.get('buyer_action') != 'release':
            await query.edit_message_text(
                "⏳ The buyer must choose **Release Payment** first.\n\n"
                "Hint: until then, the seller cannot release funds.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        if action == 'hold' and role == 'seller' and actions.get('buyer_action') != 'release':
            await query.edit_message_text("ℹ️ Seller hold is available after the buyer approves release.")
            return

        set_release_action(deal, role, action)
        actions = get_release_actions(deal)

        if role == 'buyer' and action == 'hold':
            if deal['status'] == 'payment_held':
                update_deal_status(deal_id, 'disputed', telegram_id)
            await notify_deal_user(
                context, get_deal(deal_id) or deal, deal['seller_id'],
                f"⚠️ **Deal {deal_id} is pending buyer release.**\n\n"
                "The buyer placed the payment on hold. You cannot release or receive payout until the buyer releases it."
            )
            await query.edit_message_text("🔒 Payment held. The seller has been informed that the order is pending.")
            return

        if actions.get('buyer_action') == 'release' and actions.get('seller_action') == 'release':
            update_deal_status(deal_id, 'release_requested', telegram_id)
            seller = get_user_by_id(deal['seller_id'])
            if seller:
                if role == 'seller':
                    context.user_data['deal_state'] = 'awaiting_release_upi'
                    context.user_data['release_deal_id'] = deal_id
                await begin_payout_collection(context, get_deal(deal_id) or deal, seller['telegram_id'])
            await notify_deal_user(
                context, get_deal(deal_id) or deal, deal['seller_id'],
                f"✅ Both parties released deal {deal_id}.\n\n"
                "Send your UPI ID and QR when prompted. The MM will handle fee deduction and payout proof."
            )
            await query.edit_message_text("✅ Both releases received. The seller has been asked for payout details.")
            return

        if role == 'buyer' and action == 'release':
            await notify_deal_user(
                context, get_deal(deal_id) or deal, deal['seller_id'],
                f"✅ Buyer released deal {deal_id}.\n\n"
                "Hint: choose Release Payment to start payout, or Hold Payment to keep funds protected."
            )
        await query.edit_message_text(
            "✅ Your choice was recorded.\n\n"
            "Hint: buyer release is required before seller payout can start."
        )
        return

    # Deliver
    if data.startswith("deliver_"):
        deal_id = data.replace("deliver_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        
        user = get_user(telegram_id)
        if not user or user['id'] != deal['seller_id']:
            await query.edit_message_text("❌ Only the seller can mark delivery.")
            return
        
        if deal['status'] != 'payment_held':
            await query.edit_message_text(f"❌ Cannot deliver in current status: {get_status_display(deal['status'])}")
            return
        
        update_deal_status(deal_id, 'delivered', telegram_id)
        
        await query.edit_message_text(
            f"📦 **Delivery Marked!**\n\n"
            f"Deal {deal_id} has been marked as delivered.\n\n"
            f"The buyer has been notified.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_deal_keyboard(deal_id, 'delivered', db_user['role'])
        )
        return
    
    # Accept delivery
    if data.startswith("accept_"):
        deal_id = data.replace("accept_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        
        user = get_user(telegram_id)
        if not user or user['id'] != deal['buyer_id']:
            await query.edit_message_text("❌ Only the buyer can accept delivery.")
            return
        
        if deal['status'] != 'delivered':
            await query.edit_message_text(f"❌ Cannot accept in current status: {get_status_display(deal['status'])}")
            return
        
        # Accepting delivery is the buyer's release decision. The seller still
        # must release (or hold) before payout can begin.
        set_release_action(deal, 'buyer', 'release')
        update_deal_status(deal_id, 'payment_held', telegram_id)
        await query.edit_message_text(
            f"✅ **Delivery Accepted — Buyer Released**\n\n"
            f"Deal {deal_id} has been accepted.\n\n"
            "The seller must now select Release Payment or Hold Payment.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_deal_keyboard(deal_id, 'payment_held', db_user['role'], 'buyer')
        )
        await notify_deal_participants(
            context, get_deal(deal_id) or deal,
            f"✅ Buyer released deal {deal_id}.\n\n"
            "Hint for seller: choose Release Payment to start payout, or Hold Payment to keep funds protected."
        )
        return
    
    # Request release
    if data.startswith("request_release_"):
        deal_id = data.replace("request_release_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        
        if deal['status'] != 'release_requested':
            await query.edit_message_text(f"❌ Cannot request release in current status: {get_status_display(deal['status'])}")
            return
        
        # Notify owners
        await query.edit_message_text(
            f"💸 **Release Requested**\n\n"
            f"Deal {deal_id} release has been requested.\n\n"
            f"An Owner will review and approve the release shortly.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Forward to payment channel if configured
        if Config.PAYMENT_LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    Config.PAYMENT_LOG_CHANNEL_ID,
                    f"💸 **RELEASE REQUESTED**\n\n"
                    f"Deal: {deal_id}\n"
                    f"Amount: {format_currency(deal['amount'], deal['currency'])}\n"
                    f"Seller Net: {format_currency(deal['seller_net_amount'], deal['currency'])}\n\n"
                    f"Click below to approve release:",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Approve Release", callback_data=f"approve_release_{deal_id}")],
                        [InlineKeyboardButton("❌ Reject Release", callback_data=f"reject_release_{deal_id}")]
                    ])
                )
            except Exception:
                pass
        
        return
    
    # ============================================
    # OWNER PANEL
    # ============================================
    
    if data == "owner_panel":
        if get_user_role(telegram_id) != 'owner':
            await query.edit_message_text("❌ You are not authorized to use the Owner Panel.")
            return
        
        await query.edit_message_text(
            "👑 **OWNER PANEL**\n\n"
            "Manage deals, payments, and system settings.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_owner_panel()
        )
        return
    
    if data == "admin_panel":
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ You are not authorized to use the Admin Panel.")
            return
        
        await query.edit_message_text(
            "⚙️ **ADMIN PANEL**\n\n"
            "Support and deal management.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Active Deals", callback_data="admin_deals")],
                [InlineKeyboardButton("⚠️ Disputes", callback_data="admin_disputes")],
                [InlineKeyboardButton("🆘 Support Tickets", callback_data="admin_support")],
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
            ])
        )
        return
    
    # Owner - Active deals
    if data == "owner_disputes":
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Owner access required.")
            return
        with get_db() as conn:
            deals = conn.execute("SELECT deal_id, amount, currency FROM deals WHERE status = 'disputed' ORDER BY updated_at DESC LIMIT 20").fetchall()
        text = "⚠️ **DISPUTES**\n\n" + ("No open disputes." if not deals else "")
        keyboard = []
        for deal in deals:
            text += f"🆔 {deal['deal_id']} — {format_currency(deal['amount'], deal['currency'])}\n"
            keyboard.append([InlineKeyboardButton(deal['deal_id'], callback_data=f"view_deal_{deal['deal_id']}"),
                             InlineKeyboardButton("Reply", callback_data=f"reply_dispute_{deal['deal_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "owner_users":
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Owner access required.")
            return
        with get_db() as conn:
            users = conn.execute("SELECT telegram_id, username, role, is_blocked FROM users WHERE role != 'super_owner' ORDER BY created_at DESC LIMIT 50").fetchall()
        text = "👥 **USERS**\n\n" + "\n".join(
            f"{u['telegram_id']} — @{u['username'] or 'N/A'} — {u['role']}" + (" 🚫" if u['is_blocked'] else "") for u in users
        )
        await query.edit_message_text(text[:4000] or "No users.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]]))
        return

    if data == "owner_admins":
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Owner access required.")
            return
        with get_db() as conn:
            admins = conn.execute("SELECT telegram_id, username, display_name, role FROM users WHERE role IN ('admin', 'owner') ORDER BY role").fetchall()
        text = "👨‍💼 **STAFF**\n\n" + "\n".join(
            f"{a['role'].upper()}: {a['display_name']} — {a['telegram_id']}" for a in admins
        )
        await query.edit_message_text(text[:4000] or "No staff users.",
                                      reply_markup=InlineKeyboardMarkup([
                                          [InlineKeyboardButton("➕ Add Admin", callback_data="prompt_add_admin"),
                                           InlineKeyboardButton("➖ Remove Admin", callback_data="prompt_remove_admin")],
                                          [InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]
                                      ]))
        return

    # Owner - Active deals
    if data == "owner_active_deals":
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        with get_db() as conn:
            deals = conn.execute(
                "SELECT * FROM deals WHERE status NOT IN ('completed', 'cancelled') ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        
        if not deals:
            await query.edit_message_text(
                "No active deals.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]])
            )
            return
        
        text = "🤝 **Active Deals**\n\n"
        keyboard = []
        for deal in deals[:10]:
            text += f"🆔 {deal['deal_id']} - {get_status_display(deal['status'])}\n"
            text += f"💰 {format_currency(deal['amount'], deal['currency'])}\n\n"
            keyboard.append([InlineKeyboardButton(
                f"{deal['deal_id']} - {get_status_display(deal['status'])}",
                callback_data=f"view_deal_{deal['deal_id']}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Owner - Payment verification
    if data == "owner_payments":
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        with get_db() as conn:
            payments = conn.execute(
                """SELECT p.*, d.deal_id AS public_deal_id FROM payments p
                   JOIN deals d ON p.deal_id = d.id
                   WHERE p.status = 'pending'
                   ORDER BY p.created_at DESC LIMIT 20"""
            ).fetchall()
        
        if not payments:
            await query.edit_message_text(
                "No pending payments.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]])
            )
            return
        
        text = "💰 **Payment Verification**\n\n"
        for payment in payments:
            text += f"🆔 {payment['public_deal_id']}\n"
            text += f"💰 {format_currency(payment['amount'], payment['currency'])}\n"
            text += f"📝 {payment['reference'] or 'No reference'}\n\n"
        
        keyboard = []
        for payment in payments[:10]:
            keyboard.append([InlineKeyboardButton(
                f"{payment['public_deal_id']} - {format_currency(payment['amount'], payment['currency'])}",
                callback_data=f"verify_payment_{payment['id']}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Verify payment
    if data.startswith("verify_payment_"):
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        payment_id = int(data.replace("verify_payment_", ""))
        
        with get_db() as conn:
            payment = conn.execute(
                """SELECT p.*, d.deal_id AS public_deal_id FROM payments p
                   JOIN deals d ON p.deal_id = d.id
                   WHERE p.id = ?""",
                (payment_id,)
            ).fetchone()
        
        if not payment:
            await query.edit_message_text("❌ Payment not found.")
            return
        
        if payment['status'] != 'pending':
            await query.edit_message_text("❌ Payment already processed.")
            return
        
        text = f"""
💰 **PAYMENT VERIFICATION**

Deal: {payment['public_deal_id']}
Amount: {format_currency(payment['amount'], payment['currency'])}
Reference: {payment['reference'] or 'N/A'}

Status: ⏳ PENDING VERIFICATION
"""
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve Payment", callback_data=f"approve_payment_{payment_id}")],
                [InlineKeyboardButton("❌ Reject Payment", callback_data=f"reject_payment_{payment_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data="owner_payments")]
            ])
        )
        return
    
    # Approve payment
    if data.startswith("approve_payment_"):
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        payment_id = int(data.replace("approve_payment_", ""))
        with get_db() as conn:
            payment = conn.execute(
                """SELECT p.*, d.deal_id AS public_deal_id FROM payments p
                   JOIN deals d ON p.deal_id = d.id WHERE p.id = ?""",
                (payment_id,)
            ).fetchone()
        if not payment or payment['status'] != 'pending':
            await edit_callback_message(query, "❌ Payment is missing or already processed.")
            return

        result = verify_payment(payment_id, telegram_id, True)
        
        if not result:
            await query.edit_message_text("❌ Failed to approve payment.")
            return

        approved_deal = get_deal(payment['public_deal_id'])
        approval_text = (
            f"✅ **Payment Approved**\n\n"
            f"Deal: `{payment['public_deal_id']}`\n"
            f"Amount: {format_currency(payment['amount'], payment['currency'])}\n"
            "The payment is held securely.\n\n"
            "Step 1: buyer chooses Release Payment or Hold Payment.\n"
            "Step 2: seller chooses Release Payment or Hold Payment.\n"
            "Hint: payout starts only after both choose Release Payment."
        )
        if approved_deal:
            for participant_db_id in (approved_deal['buyer_id'], approved_deal['seller_id']):
                participant = get_user_by_id(participant_db_id)
                if participant:
                    try:
                        await context.bot.send_message(
                            participant['telegram_id'], approval_text,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("💸 Release Payment", callback_data=f"release_payment_{approved_deal['deal_id']}")],
                                [InlineKeyboardButton("🔒 Hold Payment", callback_data=f"hold_payment_{approved_deal['deal_id']}")],
                            ])
                        )
                    except Exception as error:
                        logging.warning("Could not send payment decision to %s: %s", participant['telegram_id'], error)
        if Config.PAYMENT_LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    Config.PAYMENT_LOG_CHANNEL_ID,
                    approval_text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as error:
                logging.error("Failed to notify payment channel: %s", error)
        
        await edit_callback_message(
            query,
            "✅ **Payment Approved!**\n\n"
            "Payment has been verified and is now held.\n"
            "Seller has been notified.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]])
        )
        return
    
    # Reject payment
    if data.startswith("reject_payment_"):
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        payment_id = int(data.replace("reject_payment_", ""))
        with get_db() as conn:
            payment = conn.execute(
                """SELECT p.*, d.deal_id AS public_deal_id FROM payments p
                   JOIN deals d ON p.deal_id = d.id WHERE p.id = ?""",
                (payment_id,)
            ).fetchone()
        if not payment or payment['status'] != 'pending':
            await edit_callback_message(query, "❌ Payment is missing or already processed.")
            return

        result = verify_payment(payment_id, telegram_id, False)
        
        if not result:
            await query.edit_message_text("❌ Failed to reject payment.")
            return

        rejected_deal = get_deal(payment['public_deal_id'])
        rejection_text = (
            f"❌ **Payment Rejected**\n\n"
            f"Deal: `{payment['public_deal_id']}`\n"
            "The payment proof was rejected. Please review the details and submit proof again."
        )
        if rejected_deal:
            await notify_deal_participants(context, rejected_deal, rejection_text)
        if Config.PAYMENT_LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    Config.PAYMENT_LOG_CHANNEL_ID,
                    rejection_text,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as error:
                logging.error("Failed to notify payment channel: %s", error)
        
        await edit_callback_message(
            query,
            "❌ **Payment Rejected**\n\n"
            "Payment has been rejected.\n"
            "Buyer has been notified.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]])
        )
        return
    
    # Owner - Release requests
    if data == "owner_releases":
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        with get_db() as conn:
            deals = conn.execute(
                "SELECT * FROM deals WHERE status = 'release_requested' ORDER BY updated_at DESC"
            ).fetchall()
        
        if not deals:
            await query.edit_message_text(
                "No release requests.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]])
            )
            return
        
        text = "💸 **Release Requests**\n\n"
        keyboard = []
        for deal in deals:
            text += f"🆔 {deal['deal_id']}\n"
            text += f"💰 {format_currency(deal['amount'], deal['currency'])}\n\n"
            keyboard.append([InlineKeyboardButton(
                f"{deal['deal_id']} - {format_currency(deal['amount'], deal['currency'])}",
                callback_data=f"process_release_{deal['deal_id']}"
            )])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="owner_panel")])
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Process release
    if data.startswith("process_release_"):
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        deal_id = data.replace("process_release_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        
        if deal['status'] != 'release_requested':
            await query.edit_message_text(f"❌ Deal is not in release requested state.")
            return
        
        text = f"""
💸 **RELEASE APPROVAL**

Deal: {deal_id}
Amount: {format_currency(deal['amount'], deal['currency'])}
MM Fee: {format_currency(deal['mm_fee'], deal['currency'])}
Seller Net: {format_currency(deal['seller_net_amount'], deal['currency'])}

Approve release? This will mark the deal as completed.
"""
        
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve Release", callback_data=f"approve_release_{deal_id}")],
                [InlineKeyboardButton("❌ Reject Release", callback_data=f"reject_release_{deal_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data="owner_releases")]
            ])
        )
        return
    
    # Start the final payout-proof step. The owner must upload proof; the bot
    # never marks a transfer complete merely because a button was pressed.
    if data.startswith("approve_payout_"):
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Owner access required.")
            return
        deal_id = data.replace("approve_payout_", "")
        deal = get_deal(deal_id)
        if not deal or deal['status'] != 'release_requested':
            await query.edit_message_text("❌ This payout is no longer pending.")
            return
        context.user_data['deal_state'] = 'awaiting_payout_proof'
        context.user_data['release_deal_id'] = deal_id
        await query.edit_message_text(
            f"✅ **Payout approved for processing — {deal_id}**\n\n"
            "Step 1: pay the seller's net amount after deducting the MM fee.\n"
            "Step 2: upload the payment screenshot here.\n\n"
            "Hint: the order is completed only after the screenshot is uploaded.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if data.startswith("reject_payout_"):
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Owner access required.")
            return
        deal_id = data.replace("reject_payout_", "")
        deal = get_deal(deal_id)
        if deal and deal['status'] == 'release_requested':
            update_deal_status(deal_id, 'disputed', telegram_id)
            await notify_deal_user(
                context, get_deal(deal_id) or deal, deal['seller_id'],
                f"⚠️ Payout for {deal_id} is paused for MM review. The payment remains protected."
            )
        await query.edit_message_text("⚠️ Payout paused and participants notified.")
        return

    # Approve release
    if data.startswith("approve_release_"):
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        deal_id = data.replace("approve_release_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        
        if deal['status'] not in ['release_requested', 'disputed']:
            await query.edit_message_text(f"❌ Cannot approve release in current status.")
            return

        # Legacy release buttons now enter the same safe payout workflow.
        # Completion requires payout proof, so an approval click alone cannot
        # close the order.
        actions = get_release_actions(deal)
        if actions.get('buyer_action') != 'release' or actions.get('seller_action') != 'release':
            await query.edit_message_text(
                "⏳ Both buyer and seller must select Release Payment first.\n\n"
                "Hint: seller can use /release " + deal_id + " after buyer approval."
            )
            return
        context.user_data['deal_state'] = 'awaiting_payout_proof'
        context.user_data['release_deal_id'] = deal_id
        await query.edit_message_text(
            f"✅ **Payout processing — {deal_id}**\n\n"
            "Pay the seller's net amount after deducting the MM fee, then upload the payout screenshot here.\n\n"
            "Hint: the order completes only after proof is uploaded.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
        
        update_deal_status(deal_id, 'completed', telegram_id)
        
        # Get users for notification
        buyer = get_user_by_id(deal['buyer_id'])
        seller = get_user_by_id(deal['seller_id'])
        
        release_text = f"""
💸 **PAYMENT RELEASED**

━━━━━━━━━━━━━━━━━━━━

🆔 Deal: {deal_id}

👤 Buyer: @{buyer['username'] if buyer else 'N/A'}
👤 Seller: @{seller['username'] if seller else 'N/A'}

💰 Deal Amount: {format_currency(deal['amount'], deal['currency'])}
💵 MM Fee: {format_currency(deal['mm_fee'], deal['currency'])}
💰 Seller Net Amount: {format_currency(deal['seller_net_amount'], deal['currency'])}

━━━━━━━━━━━━━━━━━━━━

✅ Deal completed successfully!

⭐ Please leave your honest review for Naoya's MM Service.
"""
        
        # Send to all participants
        for participant_id in [deal['buyer_id'], deal['seller_id']]:
            participant = get_user_by_id(participant_id)
            if participant:
                try:
                    await context.bot.send_message(
                        participant['telegram_id'],
                        release_text,
                        parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⭐ Leave Review", callback_data=f"review_{deal_id}")],
                            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
                        ])
                    )
                except Exception:
                    pass
        
        await query.edit_message_text(
            f"✅ **Release Approved!**\n\nDeal {deal_id} completed.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]])
        )
        return
    
    # Owner - Fee configuration
    if data in ["set_fee_percent", "set_min_inr", "set_min_usd", "set_upi", "set_payee_name"]:
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Owner access required.")
            return
        state_map = {
            "set_fee_percent": "awaiting_fee_percent",
            "set_min_inr": "awaiting_min_inr",
            "set_min_usd": "awaiting_min_usd",
            "set_upi": "awaiting_upi",
            "set_payee_name": "awaiting_payee_name",
        }
        context.user_data['deal_state'] = state_map[data]
        prompts = {
            "set_fee_percent": "Enter the default fee percentage:",
            "set_min_inr": "Enter the minimum INR fee:",
            "set_min_usd": "Enter the minimum USD fee:",
            "set_upi": "Enter the UPI ID:",
            "set_payee_name": "Enter the UPI payee name:",
        }
        await query.edit_message_text(f"📝 {prompts[data]}\n\nType /cancel to stop.")
        return

    if data in ["prompt_add_admin", "prompt_remove_admin"]:
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Owner access required.")
            return
        context.user_data['deal_state'] = 'awaiting_add_admin' if data == 'prompt_add_admin' else 'awaiting_remove_admin'
        await query.edit_message_text("Send the admin's numeric Telegram ID, or type /cancel.")
        return

    if data.startswith("reject_release_"):
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Owner access required.")
            return
        deal_id = data.replace("reject_release_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        if deal['status'] != 'release_requested':
            await query.edit_message_text(f"❌ Cannot reject release while deal is {get_status_display(deal['status'])}.")
            return
        if update_deal_status(deal_id, 'disputed', telegram_id):
            await notify_deal_participants(
                context, deal,
                f"⚠️ **Release paused for deal {deal_id}.** An owner rejected the release request and opened a review."
            )
            await query.edit_message_text(
                f"⚠️ Release rejected and deal {deal_id} moved to dispute review.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]])
            )
        else:
            await query.edit_message_text("❌ Failed to reject release.")
        return

    if data == "owner_fees":
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        fee_percent = get_config('fee_percent') or str(Config.DEFAULT_FEE_PERCENT)
        min_inr = get_config('min_inr_fee') or str(Config.MIN_INR_FEE)
        min_usd = get_config('min_usd_fee') or str(Config.MIN_USD_FEE)
        
        await query.edit_message_text(
            f"💵 **Fee Configuration**\n\n"
            f"Default Fee: {fee_percent}%\n"
            f"Minimum INR Fee: ₹{min_inr}\n"
            f"Minimum USD Fee: ${min_usd}\n\n"
            f"Use the buttons below to update:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Update Default Fee", callback_data="set_fee_percent")],
                [InlineKeyboardButton("📝 Update Minimum INR", callback_data="set_min_inr")],
                [InlineKeyboardButton("📝 Update Minimum USD", callback_data="set_min_usd")],
                [InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]
            ])
        )
        return
    
    # Owner - Payment configuration
    if data == "owner_payment_config":
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        upi_id = get_config('upi_id') or Config.UPI_ID
        payee_name = get_config('upi_payee_name') or Config.UPI_PAYEE_NAME
        
        await query.edit_message_text(
            f"🏦 **Payment Configuration**\n\n"
            f"UPI ID: `{upi_id}`\n"
            f"Payee Name: {payee_name}\n\n"
            f"Use the buttons below to update:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Update UPI ID", callback_data="set_upi")],
                [InlineKeyboardButton("📝 Update Payee Name", callback_data="set_payee_name")],
                [InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]
            ])
        )
        return
    
    # Owner - Statistics
    if data == "owner_stats":
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        with get_db() as conn:
            stats = conn.execute("""
                SELECT 
                    COUNT(*) as total_deals,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status IN ('created', 'awaiting_confirmation', 'confirmed', 
                                            'awaiting_payment', 'payment_pending_verification', 
                                            'payment_held', 'awaiting_delivery', 'delivered',
                                            'awaiting_buyer_confirmation', 'release_requested') THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN status = 'disputed' THEN 1 ELSE 0 END) as disputed,
                    SUM(amount) as total_volume,
                    SUM(mm_fee) as total_fees
                FROM deals
            """).fetchone()
            
            user_count = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()
            vouch_count = conn.execute("SELECT COUNT(*) as count, AVG(rating) as avg_rating FROM vouches").fetchone()
        
        await query.edit_message_text(
            f"📊 **Statistics**\n\n"
            f"📋 Total Deals: {stats['total_deals'] or 0}\n"
            f"✅ Completed: {stats['completed'] or 0}\n"
            f"🔄 Active: {stats['active'] or 0}\n"
            f"⚠️ Disputed: {stats['disputed'] or 0}\n"
            f"💰 Total Volume: {format_currency(stats['total_volume'] or 0)}\n"
            f"💵 Total Fees: {format_currency(stats['total_fees'] or 0)}\n"
            f"👥 Total Users: {user_count['count'] or 0}\n"
            f"⭐ Avg Rating: {vouch_count['avg_rating'] or 0:.1f}/5\n"
            f"📝 Total Reviews: {vouch_count['count'] or 0}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]])
        )
        return
    
    # Owner - Audit logs
    if data == "owner_audit":
        if not is_authorized(telegram_id, 'owner'):
            await query.edit_message_text("❌ Unauthorized.")
            return
        
        with get_db() as conn:
            logs = conn.execute(
                """SELECT a.*, u.username, u.display_name 
                   FROM audit_logs a
                   LEFT JOIN users u ON a.actor_id = u.id
                   ORDER BY a.timestamp DESC LIMIT 20"""
            ).fetchall()
        
        text = "📜 **Recent Audit Logs**\n\n"
        for log in logs:
            text += f"🕐 {log['timestamp'][:16]}\n"
            text += f"👤 {log['display_name'] or log['username'] or 'Unknown'}\n"
            text += f"📌 {log['action']}\n"
            if log['deal_id']:
                text += f"🆔 Deal: {log['deal_id']}\n"
            text += "\n"
        
        await query.edit_message_text(
            text[:4000],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="owner_panel")]])
        )
        return
    
    # Support
    if data == "support":
        await query.edit_message_text(
            "🆘 **Support**\n\n"
            "How can we help you?\n\n"
            "Please describe your issue and we'll get back to you.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Create Support Ticket", callback_data="create_ticket")],
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
            ])
        )
        return
    
    # Review
    if data.startswith("review_"):
        deal_id = data.replace("review_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        
        if deal['status'] != 'completed':
            await query.edit_message_text("❌ Can only review completed deals.")
            return
        
        # Check if user already reviewed
        with get_db() as conn:
            existing = conn.execute(
                "SELECT * FROM vouches WHERE deal_id = ? AND user_id = ?",
                (deal['id'], get_user(telegram_id)['id'])
            ).fetchone()
        
        if existing:
            await query.edit_message_text("✅ You already reviewed this deal.")
            return
        
        # Store deal_id for review
        context.user_data['review_deal_id'] = deal_id
        
        await query.edit_message_text(
            f"⭐ **Leave a Review**\n\n"
            f"Deal: {deal_id}\n\n"
            f"Rate your experience (1-5):\n\n"
            f"⭐ 1 - Very Poor\n"
            f"⭐⭐ 2 - Poor\n"
            f"⭐⭐⭐ 3 - Average\n"
            f"⭐⭐⭐⭐ 4 - Good\n"
            f"⭐⭐⭐⭐⭐ 5 - Excellent",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐", callback_data=f"rate_1_{deal_id}"),
                 InlineKeyboardButton("⭐⭐", callback_data=f"rate_2_{deal_id}"),
                 InlineKeyboardButton("⭐⭐⭐", callback_data=f"rate_3_{deal_id}"),
                 InlineKeyboardButton("⭐⭐⭐⭐", callback_data=f"rate_4_{deal_id}"),
                 InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data=f"rate_5_{deal_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data=f"view_deal_{deal_id}")]
            ])
        )
        return
    
    # Rating
    if data.startswith("rate_"):
        parts = data.split("_")
        rating = int(parts[1])
        deal_id = parts[2]
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        
        context.user_data['review_rating'] = rating
        context.user_data['review_deal_id'] = deal_id
        
        await query.edit_message_text(
            f"⭐ **Rating: {rating}/5**\n\n"
            f"Please write your review message:\n\n"
            f"Type your review below. Type /cancel to cancel.",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['deal_state'] = 'awaiting_review'
        return
    
    # Support-ticket and staff callbacks.
    if data == "create_ticket" or data.startswith("create_ticket_"):
        if data.startswith("create_ticket_"):
            context.user_data['ticket_deal_id'] = data.replace("create_ticket_", "")
        context.user_data['deal_state'] = 'awaiting_support_ticket'
        await query.edit_message_text(
            "📝 **Create Support Ticket**\n\nDescribe your issue in one message. Include the Deal ID if relevant.\n\nType /cancel to stop.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "super_panel":
        if get_user_role(telegram_id) != 'super_owner':
            await query.edit_message_text("❌ Super owner access required.")
            return
        await query.edit_message_text("👑 **SUPER OWNER PANEL**\n\nFull system control and staff management.", parse_mode=ParseMode.MARKDOWN, reply_markup=get_super_owner_panel())
        return

    if data == "super_resume":
        if not is_super_owner_id(telegram_id):
            await query.edit_message_text("❌ Superowner access required.")
            return
        set_config('service_status', 'running', db_user['id'])
        await query.edit_message_text(
            "✅ Naoya's Middleman Service has been resumed.\n\n"
            "Hint: use the panel buttons or /help to continue.",
            reply_markup=get_super_owner_panel()
        )
        return

    if data in {"super_owners", "super_admins", "super_config", "super_stats", "super_audit", "super_backup", "super_restore", "super_shutdown"}:
        if get_user_role(telegram_id) != 'super_owner':
            await query.edit_message_text("❌ Super owner access required.")
            return
        if data == 'super_stats':
            with get_db() as conn:
                users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()['n']
                deals = conn.execute("SELECT COUNT(*) AS n FROM deals").fetchone()['n']
                tickets = conn.execute("SELECT COUNT(*) AS n FROM support_tickets WHERE status != 'closed'").fetchone()['n']
            text = f"📊 **SYSTEM STATISTICS**\n\nUsers: {users}\nDeals: {deals}\nOpen tickets: {tickets}"
        elif data in ('super_owners', 'super_admins'):
            role = 'owner' if data == 'super_owners' else 'admin'
            with get_db() as conn:
                staff = conn.execute("SELECT telegram_id, display_name FROM users WHERE role = ?", (role,)).fetchall()
            text = f"👥 **{role.upper()} MANAGEMENT**\n\n" + ("\n".join(f"{x['display_name']} — {x['telegram_id']}" for x in staff) or "None configured.")
        else:
            text = {
                'super_config': "⚙️ **GLOBAL CONFIGURATION**\n\nUse the Owner Panel for fee and payment settings.",
                'super_audit': "📜 **COMPLETE AUDIT**\n\nUse the Owner Panel audit viewer for recent audit entries.",
                'super_backup': "💾 **BACKUP**\n\nCopy `naoya_mm.db` while the bot is stopped.",
                'super_restore': "♻️ **RESTORE**\n\nStop the bot and replace `naoya_mm.db` with your backup.",
                'super_shutdown': None,
            }[data]
            if data == 'super_shutdown':
                set_config('service_status', 'stopped', db_user['id'])
                text = (
                    "🛑 **SERVICE STOPPED**\n\n"
                    "New orders and bot actions are now paused.\n"
                    "The service is displayed as stopped by the Owner.\n\n"
                    "Only the Superowner can resume it."
                )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Super Owner Panel", callback_data="super_panel")]]))
        return

    if data == "admin_deals":
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        with get_db() as conn:
            deals = conn.execute("SELECT deal_id, status, amount, currency FROM deals WHERE status NOT IN ('completed', 'cancelled') ORDER BY updated_at DESC LIMIT 30").fetchall()
        text = "📋 **ACTIVE DEALS**\n\n" + ("No active deals." if not deals else "")
        keyboard = []
        for deal in deals:
            text += f"🆔 {deal['deal_id']} — {get_status_display(deal['status'])} — {format_currency(deal['amount'], deal['currency'])}\n"
            keyboard.append([InlineKeyboardButton(deal['deal_id'], callback_data=f"view_deal_{deal['deal_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "admin_disputes":
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        with get_db() as conn:
            disputes = conn.execute("SELECT deal_id, amount, currency FROM deals WHERE status = 'disputed' ORDER BY updated_at DESC LIMIT 30").fetchall()
        text = "⚠️ **DISPUTES**\n\n" + ("No open disputes." if not disputes else "")
        keyboard = []
        for dispute in disputes:
            text += f"🆔 {dispute['deal_id']} — {format_currency(dispute['amount'], dispute['currency'])}\n"
            keyboard.append([InlineKeyboardButton(dispute['deal_id'], callback_data=f"view_deal_{dispute['deal_id']}"),
                             InlineKeyboardButton("Reply", callback_data=f"reply_dispute_{dispute['deal_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "admin_support":
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        with get_db() as conn:
            tickets = conn.execute("SELECT t.id, t.status, u.telegram_id, u.username FROM support_tickets t JOIN users u ON t.user_id = u.id WHERE t.status != 'closed' ORDER BY t.created_at DESC LIMIT 20").fetchall()
        text = "🆘 **SUPPORT QUEUE**\n\n" + ("No open tickets." if not tickets else "")
        keyboard = []
        for ticket in tickets:
            text += f"#{ticket['id']} — {ticket['status']} — @{ticket['username'] or ticket['telegram_id']}\n"
            keyboard.append([InlineKeyboardButton(f"#{ticket['id']} Review", callback_data=f"admin_ticket_{ticket['id']}"), InlineKeyboardButton("Reply", callback_data=f"reply_ticket_{ticket['id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("admin_ticket_"):
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        ticket_id = int(data.replace("admin_ticket_", ""))
        with get_db() as conn:
            ticket = conn.execute("SELECT t.*, u.telegram_id, u.username FROM support_tickets t JOIN users u ON t.user_id = u.id WHERE t.id = ?", (ticket_id,)).fetchone()
        if not ticket:
            await query.edit_message_text("❌ Ticket not found.")
            return
        await query.edit_message_text(f"🆘 **Ticket #{ticket_id}**\n\nUser: @{ticket['username'] or 'N/A'} (`{ticket['telegram_id']}`)\nStatus: {ticket['status']}\n\n{ticket['description']}", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✉️ Reply", callback_data=f"reply_ticket_{ticket_id}"), InlineKeyboardButton("✅ Close", callback_data=f"close_ticket_{ticket_id}")], [InlineKeyboardButton("🔙 Back", callback_data="admin_support")]]))
        return

    if data.startswith("reply_ticket_"):
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        context.user_data['ticket_id'] = int(data.replace("reply_ticket_", ""))
        context.user_data['deal_state'] = 'awaiting_ticket_reply'
        await query.edit_message_text("✉️ Send your reply now, or type /cancel.")
        return

    if data.startswith("close_ticket_"):
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        ticket_id = int(data.replace("close_ticket_", ""))
        with db_transaction() as conn:
            ticket = conn.execute("SELECT user_id FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone()
            conn.execute("UPDATE support_tickets SET status = 'closed', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (ticket_id,))
        if ticket:
            recipient = get_user_by_id(ticket['user_id'])
            if recipient:
                await context.bot.send_message(recipient['telegram_id'], f"✅ Support ticket #{ticket_id} has been closed.")
        await query.edit_message_text("✅ Ticket closed.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_support")]]))
        return

    if data.startswith("dispute_"):
        deal_id = data.replace("dispute_", "")
        deal = get_deal(deal_id)
        user = get_user(telegram_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        if not user or user['id'] not in [deal['buyer_id'], deal['seller_id']]:
            await query.edit_message_text("❌ Only deal participants can open a dispute.")
            return
        if update_deal_status(deal_id, 'disputed', telegram_id):
            await notify_deal_participants(context, deal, f"⚠️ **Dispute opened for deal {deal_id}.** An admin will review it.")
            await query.edit_message_text("⚠️ Dispute opened. An admin will contact both parties.")
        else:
            await query.edit_message_text(f"❌ Cannot open a dispute while deal is {get_status_display(deal['status'])}.")
        return

    if data.startswith("reply_dispute_"):
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin or owner access required.")
            return
        context.user_data['dispute_deal_id'] = data.replace("reply_dispute_", "")
        context.user_data['deal_state'] = 'awaiting_dispute_reply'
        await query.edit_message_text("🛡️ Send your dispute reply now, or type /cancel.")
        return

    # Safe fallback for an old/stale inline button.
    await query.edit_message_text(
        "ℹ️ This menu item is outdated. Please open the menu again.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])
    )

# ============================================
# MESSAGE HANDLERS
# ============================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular messages"""
    message = update.message
    # All deal workflows are private. Group messages are handled only by the
    # dedicated trigger-word handler and /lock command handler.
    if not message or not message.chat or message.chat.type != 'private':
        return
    user = update.effective_user
    telegram_id = user.id
    text = message.text
    
    # Get or create user
    db_user = get_or_create_user(telegram_id, user.username, user.full_name)
    
    # Check if blocked
    if db_user.get('is_blocked', 0):
        await message.reply_text("❌ You have been blocked from using this bot.")
        return

    # Callback-only branches below are kept inactive for ordinary messages.
    data = ""
    query = None

    if data == "create_ticket":
        context.user_data['deal_state'] = 'awaiting_support_ticket'
        await query.edit_message_text(
            "📝 **Create Support Ticket**\n\n"
            "Describe your issue in one message. Include your Deal ID if relevant.\n\n"
            "Type /cancel to stop.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "super_panel":
        if get_user_role(telegram_id) != 'super_owner':
            await query.edit_message_text("❌ Super owner access required.")
            return
        await query.edit_message_text(
            "👑 **SUPER OWNER PANEL**\n\nFull system control and staff management.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_super_owner_panel()
        )
        return

    if data in {"super_owners", "super_admins", "super_config", "super_stats", "super_audit",
                "super_backup", "super_restore", "super_shutdown"}:
        if get_user_role(telegram_id) != 'super_owner':
            await query.edit_message_text("❌ Super owner access required.")
            return
        if data == 'super_stats':
            with get_db() as conn:
                users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()['n']
                deals = conn.execute("SELECT COUNT(*) AS n FROM deals").fetchone()['n']
                tickets = conn.execute("SELECT COUNT(*) AS n FROM support_tickets WHERE status != 'closed'").fetchone()['n']
            text = f"📊 **SYSTEM STATISTICS**\n\nUsers: {users}\nDeals: {deals}\nOpen tickets: {tickets}"
        elif data == 'super_config':
            text = "⚙️ **GLOBAL CONFIGURATION**\n\nUse the Owner Panel for fee and payment settings."
        elif data == 'super_backup':
            text = "💾 **BACKUP**\n\nThe SQLite database is stored in `naoya_mm.db`. Copy that file while the bot is stopped to create a backup."
        elif data == 'super_restore':
            text = "♻️ **RESTORE**\n\nStop the bot and replace `naoya_mm.db` with your backup, then restart it."
        elif data == 'super_shutdown':
            text = "🛑 Service shutdown is controlled by the Superowner. Use the resume control to start it again."
        else:
            with get_db() as conn:
                role = 'owner' if data == 'super_owners' else 'admin'
                staff = conn.execute("SELECT telegram_id, display_name FROM users WHERE role = ?", (role,)).fetchall()
            text = f"👥 **{role.upper()} MANAGEMENT**\n\n" + ("\n".join(f"{x['display_name']} — {x['telegram_id']}" for x in staff) or "None configured.")
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN,
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Super Owner Panel", callback_data="super_panel")]]))
        return

    # Admin active deals
    if data == "admin_deals":
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        with get_db() as conn:
            deals = conn.execute(
                "SELECT deal_id, status, amount, currency FROM deals WHERE status NOT IN ('completed', 'cancelled') ORDER BY updated_at DESC LIMIT 30"
            ).fetchall()
        text = "📋 **ACTIVE DEALS**\n\n" + ("No active deals." if not deals else "")
        keyboard = []
        for deal in deals:
            text += f"🆔 {deal['deal_id']} — {get_status_display(deal['status'])} — {format_currency(deal['amount'], deal['currency'])}\n"
            keyboard.append([InlineKeyboardButton(deal['deal_id'], callback_data=f"view_deal_{deal['deal_id']}")])
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Admin support queue
    if data == "admin_support":
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        with get_db() as conn:
            tickets = conn.execute(
                """SELECT t.*, u.telegram_id, u.username FROM support_tickets t
                   JOIN users u ON t.user_id = u.id WHERE t.status != 'closed'
                   ORDER BY t.created_at DESC LIMIT 20"""
            ).fetchall()
        text = "🆘 **SUPPORT QUEUE**\n\n"
        keyboard = []
        for ticket in tickets:
            text += f"#{ticket['id']} — {ticket['status']} — @{ticket['username'] or ticket['telegram_id']}\n"
            keyboard.append([InlineKeyboardButton(f"#{ticket['id']} Review", callback_data=f"admin_ticket_{ticket['id']}"),
                             InlineKeyboardButton("Reply", callback_data=f"reply_ticket_{ticket['id']}")])
        if not tickets:
            text += "No open tickets."
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data.startswith("admin_ticket_"):
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        ticket_id = int(data.replace("admin_ticket_", ""))
        with get_db() as conn:
            ticket = conn.execute(
                """SELECT t.*, u.telegram_id, u.username FROM support_tickets t
                   JOIN users u ON t.user_id = u.id WHERE t.id = ?""", (ticket_id,)
            ).fetchone()
        if not ticket:
            await query.edit_message_text("❌ Ticket not found.")
            return
        await query.edit_message_text(
            f"🆘 **Ticket #{ticket_id}**\n\n"
            f"User: @{ticket['username'] or 'N/A'} (`{ticket['telegram_id']}`)\n"
            f"Status: {ticket['status']}\n\n{ticket['description']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✉️ Reply", callback_data=f"reply_ticket_{ticket_id}"),
                 InlineKeyboardButton("✅ Close", callback_data=f"close_ticket_{ticket_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_support")]
            ])
        )
        return

    if data.startswith("reply_ticket_"):
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        context.user_data['ticket_id'] = int(data.replace("reply_ticket_", ""))
        context.user_data['deal_state'] = 'awaiting_ticket_reply'
        await query.edit_message_text("✉️ Send your reply now, or type /cancel.")
        return

    if data.startswith("close_ticket_"):
        if not is_authorized(telegram_id, 'admin'):
            await query.edit_message_text("❌ Admin access required.")
            return
        ticket_id = int(data.replace("close_ticket_", ""))
        with db_transaction() as conn:
            ticket = conn.execute("SELECT user_id FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone()
            conn.execute("UPDATE support_tickets SET status = 'closed', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (ticket_id,))
        if ticket:
            recipient = get_user_by_id(ticket['user_id'])
            if recipient:
                await context.bot.send_message(recipient['telegram_id'], f"✅ Support ticket #{ticket_id} has been closed.")
        await query.edit_message_text("✅ Ticket closed.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_support")]]))
        return

    if data.startswith("dispute_"):
        deal_id = data.replace("dispute_", "")
        deal = get_deal(deal_id)
        if not deal:
            await query.edit_message_text("❌ Deal not found.")
            return
        user = get_user(telegram_id)
        if not user or user['id'] not in [deal['buyer_id'], deal['seller_id']]:
            await query.edit_message_text("❌ Only deal participants can open a dispute.")
            return
        if update_deal_status(deal_id, 'disputed', telegram_id):
            await notify_deal_participants(context, deal, f"⚠️ **Dispute opened for deal {deal_id}.** An admin will review it.")
            await query.edit_message_text("⚠️ Dispute opened. An admin will contact both parties.")
        else:
            await query.edit_message_text(f"❌ Cannot open a dispute while deal is {get_status_display(deal['status'])}.")
        return

    if context.user_data.get('deal_state') in {'awaiting_add_admin', 'awaiting_remove_admin'}:
        if not text or not text.strip().isdigit():
            await message.reply_text("❌ Send a numeric Telegram ID.")
            return
        target_id = int(text.strip())
        adding = context.user_data['deal_state'] == 'awaiting_add_admin'
        if adding:
            get_or_create_user(target_id)
            update_user_role(target_id, 'admin')
            if target_id not in Config.ADMIN_IDS:
                Config.ADMIN_IDS.append(target_id)
            notice = "✅ You have been added as an admin of Naoya's Middleman Bot."
            result = f"✅ {target_id} added as admin."
        else:
            update_user_role(target_id, 'user')
            Config.ADMIN_IDS[:] = [x for x in Config.ADMIN_IDS if x != target_id]
            notice = "ℹ️ Your admin access has been removed."
            result = f"✅ Admin access removed for {target_id}."
        try:
            await context.bot.send_message(target_id, notice)
        except Exception as error:
            logging.warning("Could not notify admin change for %s: %s", target_id, error)
        context.user_data['deal_state'] = None
        await message.reply_text(result)
        return

    if context.user_data.get('deal_state') in {
        'awaiting_fee_percent', 'awaiting_min_inr', 'awaiting_min_usd',
        'awaiting_upi', 'awaiting_payee_name'
    }:
        if not text:
            await message.reply_text("❌ Please send the setting as text.")
            return
        setting_state = context.user_data['deal_state']
        if setting_state == 'awaiting_fee_percent':
            try:
                value = float(text.strip())
                if value < 0 or value > 100:
                    raise ValueError
            except ValueError:
                await message.reply_text("❌ Enter a percentage from 0 to 100.")
                return
            set_config('fee_percent', str(value), db_user['id'])
        elif setting_state == 'awaiting_min_inr':
            try:
                value = float(text.strip())
                if value < 0:
                    raise ValueError
            except ValueError:
                await message.reply_text("❌ Enter a valid positive amount.")
                return
            set_config('min_inr_fee', str(value), db_user['id'])
        elif setting_state == 'awaiting_min_usd':
            try:
                value = float(text.strip())
                if value < 0:
                    raise ValueError
            except ValueError:
                await message.reply_text("❌ Enter a valid positive amount.")
                return
            set_config('min_usd_fee', str(value), db_user['id'])
        elif setting_state == 'awaiting_upi':
            set_config('upi_id', text.strip(), db_user['id'])
        else:
            set_config('upi_payee_name', text.strip(), db_user['id'])
        context.user_data['deal_state'] = None
        await message.reply_text("✅ Setting updated.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Owner Panel", callback_data="owner_panel")]]))
        return

    if context.user_data.get('deal_state') == 'awaiting_ticket_reply':
        if not text:
            await message.reply_text("❌ Please send a text reply.")
            return
        ticket_id = context.user_data.get('ticket_id')
        with get_db() as conn:
            ticket = conn.execute(
                "SELECT user_id FROM support_tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
        if not ticket:
            await message.reply_text("❌ Ticket not found.")
            context.user_data['deal_state'] = None
            return
        recipient = get_user_by_id(ticket['user_id'])
        if recipient:
            await context.bot.send_message(recipient['telegram_id'], f"🆘 **Support Reply for Ticket #{ticket_id}**\n\n{text.strip()}", parse_mode=ParseMode.MARKDOWN)
        with db_transaction() as conn:
            conn.execute("UPDATE support_tickets SET status = 'in_progress', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (ticket_id,))
        context.user_data['deal_state'] = None
        await message.reply_text("✅ Reply sent to the ticket owner.")
        return

    if context.user_data.get('deal_state') == 'awaiting_dispute_reply':
        if not text:
            await message.reply_text("❌ Please send a text reply.")
            return
        deal_id = context.user_data.get('dispute_deal_id')
        deal = get_deal(deal_id) if deal_id else None
        if not deal:
            await message.reply_text("❌ Deal not found.")
            context.user_data['deal_state'] = None
            return
        await notify_deal_participants(
            context, deal,
            f"🛡️ **Admin/Owner message for dispute {deal_id}**\n\n{text.strip()}"
        )
        if Config.DEAL_LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(Config.DEAL_LOG_CHANNEL_ID, f"🛡️ Dispute {deal_id}: {text.strip()}")
            except Exception as error:
                logging.warning("Could not log dispute reply: %s", error)
        context.user_data['deal_state'] = None
        await message.reply_text("✅ Dispute reply sent to both parties.")
        return

    if context.user_data.get('deal_state') == 'awaiting_broadcast':
        if not text:
            await message.reply_text("❌ Please send the broadcast as text, or type /cancel.")
            return
        if not is_authorized(telegram_id, 'owner'):
            context.user_data['deal_state'] = None
            await message.reply_text("❌ Owner access required.")
            return
        delivered = await send_broadcast(context, text.strip())
        context.user_data['deal_state'] = None
        await message.reply_text(f"✅ Broadcast sent to {delivered} user(s).")
        return

    if context.user_data.get('deal_state') == 'awaiting_support_ticket':
        if not text:
            await message.reply_text("❌ Please describe the issue as text.")
            return
        ticket_deal_id = context.user_data.pop('ticket_deal_id', None)
        with db_transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO support_tickets (user_id, subject, description, status)
                   VALUES (?, ?, ?, 'open')""",
                (db_user['id'], f"Support request{f' — {ticket_deal_id}' if ticket_deal_id else ''}", text.strip())
            )
            ticket_id = cursor.lastrowid
        context.user_data['deal_state'] = None
        ticket_text = (
            f"🆘 **New Support Ticket #{ticket_id}**\n\n"
            f"From: @{user.username or 'N/A'} (`{telegram_id}`)\n"
            f"Deal: {ticket_deal_id or 'Not specified'}\n"
            f"Message:\n{text.strip()}"
        )
        for staff_id in Config.OWNER_IDS + Config.ADMIN_IDS + Config.SUPER_OWNER_IDS:
            try:
                await context.bot.send_message(
                    staff_id, ticket_text, parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("👁 Review Ticket", callback_data=f"admin_ticket_{ticket_id}")],
                        [InlineKeyboardButton("✉️ Reply", callback_data=f"reply_ticket_{ticket_id}")],
                    ])
                )
            except Exception as error:
                logging.warning("Could not notify staff about ticket %s: %s", ticket_id, error)
        if Config.SUPPORT_GROUP_ID:
            try:
                await context.bot.send_message(Config.SUPPORT_GROUP_ID, ticket_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as error:
                logging.warning("Could not notify support group: %s", error)
        await message.reply_text(
            f"✅ Support ticket #{ticket_id} created. An admin will reply soon.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])
        )
        return
    
    # Handle connection ID entry
    if context.user_data.get('deal_state') == 'awaiting_connection':
        connection_id = text.strip()

        # Accept a Telegram user ID directly. This is useful for configured
        # owners/super owners, whose numeric IDs are not connection tokens.
        if connection_id.isdigit():
            partner_telegram_id = int(connection_id)
            if partner_telegram_id == telegram_id:
                await message.reply_text("❌ You cannot use your own Telegram ID as the partner ID.")
                return

            partner = get_or_create_user(partner_telegram_id)
            if partner.get('is_blocked', 0):
                await message.reply_text("❌ This partner is blocked from using the bot.")
                return

            context.user_data['pending_partner_telegram_id'] = partner_telegram_id
            context.user_data['connection_token'] = None

            await message.reply_text(
                "🤝 **Partner accepted!**\n\n"
                f"Partner Telegram ID: `{partner_telegram_id}`\n\n"
                "Choose your role for this deal:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 I am the Buyer", callback_data="choose_role_buyer")],
                    [InlineKeyboardButton("📦 I am the Seller", callback_data="choose_role_seller")],
                ])
            )
            return

        # Stable Naoya MM user IDs (for example: NMU4K8J2).
        decoded_partner_id = decode_user_id(connection_id)
        if decoded_partner_id is not None:
            if decoded_partner_id == telegram_id:
                await message.reply_text("❌ You cannot use your own Naoya MM ID.")
                return
            partner = get_or_create_user(decoded_partner_id)
            if partner.get('is_blocked', 0):
                await message.reply_text("❌ This partner is blocked from using the bot.")
                return
            context.user_data['pending_partner_telegram_id'] = decoded_partner_id
            context.user_data['connection_token'] = None
            await message.reply_text(
                "🤝 **Partner accepted!**\n\nChoose your role for this deal:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 I am the Buyer", callback_data="choose_role_buyer")],
                    [InlineKeyboardButton("📦 I am the Seller", callback_data="choose_role_seller")],
                ])
            )
            return
        
        # Check if connection ID exists
        with get_db() as conn:
            deal = conn.execute(
                "SELECT * FROM deals WHERE connection_token = ?",
                (connection_id,)
            ).fetchone()
        
        if not deal:
            await message.reply_text(
                "❌ Invalid Connection ID.\n\n"
                "Please enter a valid Naoya MM ID or type /cancel.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        
        # Check if user is already in this deal
        if deal['buyer_id'] == db_user['id'] or deal['seller_id'] == db_user['id']:
            await message.reply_text("❌ You are already in this deal.")
            context.user_data['deal_state'] = None
            return
        
        # Check if both slots are filled
        if deal['buyer_id'] and deal['seller_id']:
            await message.reply_text("❌ This deal already has both participants.")
            context.user_data['deal_state'] = None
            return
        
        # Assign user to deal
        with db_transaction() as conn:
            if deal['buyer_id'] is None:
                conn.execute(
                    "UPDATE deals SET buyer_id = ? WHERE connection_token = ?",
                    (db_user['id'], connection_id)
                )
            else:
                conn.execute(
                    "UPDATE deals SET seller_id = ? WHERE connection_token = ?",
                    (db_user['id'], connection_id)
                )
        
        # Now start deal details collection
        context.user_data['deal_state'] = 'awaiting_deal_details'
        context.user_data['connection_token'] = connection_id
        
        await message.reply_text(
            "🤝 **Connection Established!**\n\n"
            "Now let's set up the deal details.\n\n"
            "Please describe the item/service being traded:",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Handle deal details - Item description
    if context.user_data.get('deal_state') == 'awaiting_deal_details':
        context.user_data['item_description'] = text.strip()
        context.user_data['deal_state'] = 'awaiting_amount'
        
        await message.reply_text(
            "📦 Item description saved!\n\n"
            "Now enter the deal amount:\n\n"
            "Example: 500\n"
            "(Currency will be set to INR by default)",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Handle amount
    if context.user_data.get('deal_state') == 'awaiting_amount':
        try:
            amount = float(text.strip())
            if amount <= 0:
                raise ValueError("Amount must be positive")
        except ValueError:
            await message.reply_text("❌ Please enter a valid positive number.")
            return
        
        context.user_data['amount'] = amount
        context.user_data['deal_state'] = 'awaiting_currency'
        
        await message.reply_text(
            f"💰 Amount: {format_currency(amount, 'INR')}\n\n"
            "Enter currency (INR/USD) or type /skip for INR:",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Handle currency
    if context.user_data.get('deal_state') == 'awaiting_currency':
        currency = text.strip().upper()
        if currency not in ['INR', 'USD']:
            if text.lower() == '/skip':
                currency = 'INR'
            else:
                await message.reply_text("❌ Please enter INR or USD, or type /skip for INR.")
                return
        
        context.user_data['currency'] = currency
        context.user_data['deal_state'] = 'awaiting_payment_method'
        
        await message.reply_text(
            f"💰 Currency: {currency}\n\n"
            "Enter payment method (e.g., UPI, Bank Transfer, Crypto):",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Handle payment method
    if context.user_data.get('deal_state') == 'awaiting_payment_method':
        payment_method = text.strip()
        context.user_data['payment_method'] = payment_method
        context.user_data['deal_state'] = 'awaiting_terms'
        
        await message.reply_text(
            f"🏦 Payment Method: {payment_method}\n\n"
            "Enter deal terms and conditions (or type /skip for none):",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    # Handle terms
    if context.user_data.get('deal_state') == 'awaiting_terms':
        if text.lower() == '/skip':
            terms = None
        else:
            terms = text.strip()
        
        # Create the deal
        try:
            # Calculate fee
            amount = context.user_data['amount']
            currency = context.user_data['currency']
            fee, fee_percent = calculate_fee(amount, currency)

            connection_token = context.user_data.get('connection_token')
            if connection_token:
                with get_db() as conn:
                    deal_data = conn.execute(
                        "SELECT * FROM deals WHERE connection_token = ?",
                        (connection_token,)
                    ).fetchone()

                if not deal_data:
                    await message.reply_text("❌ Deal not found. Please start over with /start.")
                    context.user_data['deal_state'] = None
                    return

                with db_transaction() as conn:
                    conn.execute(
                        """UPDATE deals SET
                           item_description = ?, amount = ?, currency = ?,
                           payment_method = ?, terms = ?, mm_fee = ?,
                           seller_net_amount = ?, status = 'awaiting_confirmation'
                           WHERE connection_token = ?""",
                        (context.user_data['item_description'], amount, currency,
                         context.user_data['payment_method'], terms, fee, amount - fee,
                         connection_token)
                    )
                    deal = conn.execute(
                        "SELECT * FROM deals WHERE connection_token = ?", (connection_token,)
                    ).fetchone()
            else:
                partner_telegram_id = context.user_data.get('partner_telegram_id')
                partner = get_user(partner_telegram_id) if partner_telegram_id else None
                if not partner:
                    await message.reply_text("❌ Partner not found. Please start over with /start.")
                    context.user_data['deal_state'] = None
                    return

                current_user = get_user(telegram_id)
                if context.user_data.get('my_deal_role') == 'seller':
                    buyer, seller = partner, current_user
                else:
                    buyer, seller = current_user, partner
                deal_id = generate_unique_deal_id()
                connection_token = generate_connection_token()
                with db_transaction() as conn:
                    cursor = conn.execute(
                        """INSERT INTO deals (
                           deal_id, connection_token, buyer_id, seller_id,
                           item_description, amount, currency, payment_method,
                           mm_fee, seller_net_amount, terms, status
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (deal_id, connection_token, buyer['id'], seller['id'],
                         context.user_data['item_description'], amount, currency,
                         context.user_data['payment_method'], fee, amount - fee,
                         terms, 'awaiting_confirmation')
                    )
                    deal = conn.execute(
                        "SELECT * FROM deals WHERE id = ?", (cursor.lastrowid,)
                    ).fetchone()
            
            # Get participants
            buyer_user = get_user_by_id(deal['buyer_id'])
            seller_user = get_user_by_id(deal['seller_id'])
            
            # This message contains user-supplied item/terms/payment text.
            # Send it as plain text: Telegram Markdown parsing can reject
            # underscores, unmatched symbols, or other ordinary user input.
            deal_text = f"""
🤝 DEAL CREATED!

🆔 Deal: {deal['deal_id']}
📊 Status: Awaiting Confirmation

👤 Buyer: @{buyer_user['username'] or 'N/A'}
👤 Seller: @{seller_user['username'] or 'N/A'}

📦 Item: {deal['item_description']}
💰 Amount: {format_currency(deal['amount'], deal['currency'])}
💵 MM Fee: {format_currency(deal['mm_fee'], deal['currency'])}
💰 Seller Net: {format_currency(deal['seller_net_amount'], deal['currency'])}
🏦 Payment: {deal['payment_method']}
📌 Terms: {deal['terms'] or 'Not specified'}

━━━━━━━━━━━━━━━━━━━━

⚠️ Both parties please confirm the deal details.
"""
            
            # Record every deal attempt in the configured deal channel.
            if Config.DEAL_LOG_CHANNEL_ID:
                try:
                    await context.bot.send_message(
                        Config.DEAL_LOG_CHANNEL_ID,
                        "📋 DEAL ATTEMPT RECORDED\n\n" + deal_text
                    )
                except Exception as error:
                    logging.warning("Could not log deal creation: %s", error)

            # Send to both participants independently. One unavailable chat
            # must never prevent the other participant from receiving theirs.
            delivered = 0
            failed = []
            for participant_id in [deal['buyer_id'], deal['seller_id']]:
                participant = get_user_by_id(participant_id)
                if participant:
                    try:
                        await context.bot.send_message(
                            participant['telegram_id'],
                            deal_text,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("✅ Confirm Deal", callback_data=f"confirm_deal_{deal['deal_id']}")],
                                [InlineKeyboardButton("❌ Cancel Deal", callback_data=f"cancel_deal_{deal['deal_id']}")],
                                [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
                            ])
                        )
                        delivered += 1
                    except Exception as e:
                        failed.append(participant['telegram_id'])
                        logging.error("Failed to send deal notification to %s: %s", participant['telegram_id'], e)

            notification_status = f"Deal confirmation sent to {delivered}/2 participants."
            if failed:
                notification_status += "\n⚠️ The other participant must open the bot and press /start before Telegram allows private messages."
            
            await message.reply_text(
                "✅ **Deal Created!**\n\n"
                f"{notification_status}\n"
                f"Deal ID: {deal['deal_id']}\n\n"
                "The deal will proceed once both parties confirm.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])
            )
            
            context.user_data['deal_state'] = None
            
        except Exception as e:
            logging.error(f"Error creating deal: {e}")
            await message.reply_text(f"❌ Error creating deal: {str(e)}")
            context.user_data['deal_state'] = None
        return
    
    # Handle payment proof
    if context.user_data.get('deal_state') == 'awaiting_payment_proof':
        deal_id = context.user_data.get('payment_deal_id')
        if not deal_id:
            await message.reply_text("❌ Deal not found. Please start over.")
            context.user_data['deal_state'] = None
            return
        
        # Get payment details from message
        reference = text.strip() if text else (message.caption.strip() if message.caption else None)
        proof_file_id = None
        caption = None
        
        if message.photo:
            proof_file_id = message.photo[-1].file_id
            caption = message.caption
        elif message.document:
            proof_file_id = message.document.file_id
            caption = message.caption
        else:
            # Just text - ask for screenshot
            await message.reply_text(
                "📸 Please send a screenshot/photo of your payment.\n\n"
                "You can also include the UTR/reference number in the caption."
            )
            return
        
        # Create payment record
        deal = get_deal(deal_id)
        if not deal:
            await message.reply_text("❌ Deal not found.")
            context.user_data['deal_state'] = None
            return
        
        payment = create_payment(
            deal_id,
            deal['amount'],
            deal['currency'],
            deal['payment_method'],
            proof_file_id,
            reference or 'N/A',
            caption
        )
        
        if not payment:
            await message.reply_text("❌ Failed to record payment.")
            return
        
        # Notify payment channel
        if Config.PAYMENT_LOG_CHANNEL_ID:
            try:
                proof_msg = f"""
💰 **PAYMENT VERIFICATION**

Deal: {deal_id}
Buyer: @{user.username or 'N/A'}
Amount: {format_currency(deal['amount'], deal['currency'])}
Reference: {reference or 'N/A'}

Status: ⏳ PENDING VERIFICATION
"""
                
                if proof_file_id:
                    if message.photo:
                        await context.bot.send_photo(
                            Config.PAYMENT_LOG_CHANNEL_ID,
                            proof_file_id,
                            caption=proof_msg,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_payment_{payment['id']}")],
                                [InlineKeyboardButton("❌ Reject", callback_data=f"reject_payment_{payment['id']}")]
                            ])
                        )
                    else:
                        await context.bot.send_document(
                            Config.PAYMENT_LOG_CHANNEL_ID,
                            proof_file_id,
                            caption=proof_msg,
                            parse_mode=ParseMode.MARKDOWN,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("✅ Approve", callback_data=f"approve_payment_{payment['id']}")],
                                [InlineKeyboardButton("❌ Reject", callback_data=f"reject_payment_{payment['id']}")]
                            ])
                        )
                else:
                    await context.bot.send_message(
                        Config.PAYMENT_LOG_CHANNEL_ID,
                        proof_msg,
                        parse_mode=ParseMode.MARKDOWN
                    )
            except Exception as e:
                logging.error(f"Failed to send to payment channel: {e}")
        
        await message.reply_text(
            f"✅ **Payment Submitted!**\n\n"
            f"Your payment proof has been submitted for verification.\n"
            f"Deal: {deal_id}\n"
            f"Amount: {format_currency(deal['amount'], deal['currency'])}\n\n"
            f"⚠️ **Your payment will NOT be marked as received until an Owner verifies it.**\n\n"
            f"An Owner will verify your payment shortly.\n"
            f"You will be notified once verified.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])
        )
        
        context.user_data['deal_state'] = None
        return
    
    # Handle review
    if context.user_data.get('deal_state') == 'awaiting_review':
        deal_id = context.user_data.get('review_deal_id')
        rating = context.user_data.get('review_rating')
        
        if not deal_id or not rating:
            await message.reply_text("❌ Review session expired. Please start over.")
            context.user_data['deal_state'] = None
            return
        
        review_text = text.strip()
        deal = get_deal(deal_id)
        
        if not deal:
            await message.reply_text("❌ Deal not found.")
            context.user_data['deal_state'] = None
            return
        
        # Save vouch
        with db_transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO vouches (deal_id, user_id, rating, review) VALUES (?, ?, ?, ?)",
                (deal['id'], get_user(telegram_id)['id'], rating, review_text)
            )
        
        await message.reply_text(
            f"⭐ **Vouch Submitted!**\n\n"
            f"👤 User: @{user.username or 'N/A'}\n"
            f"⭐ Rating: {rating}/5\n"
            f"💬 Review: {review_text}\n"
            f"🆔 Deal: {deal_id}\n\n"
            f"Thank you for your review!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])
        )
        
        context.user_data['deal_state'] = None
        return
    
    # Seller payout details: UPI ID, then QR, after both parties release.
    if context.user_data.get('deal_state') == 'awaiting_release_upi':
        deal_id = context.user_data.get('release_deal_id')
        deal = get_deal(deal_id) if deal_id else None
        if not deal or deal['status'] != 'release_requested' or not text:
            await message.reply_text("❌ Send a valid UPI ID, or type /cancel.")
            return
        seller = get_user(update.effective_user.id)
        if not seller or seller['id'] != deal['seller_id']:
            await message.reply_text("❌ Only the seller can submit payout details.")
            return
        save_payout_detail(deal, upi_id=text.strip())
        context.user_data['deal_state'] = 'awaiting_release_qr'
        await message.reply_text(
            "✅ Step 1/2 complete — UPI ID saved.\n\n"
            "Step 2/2 — now send a clear QR image for the same account.\n"
            "Hint: send it as a photo/document, not as a screenshot in text."
        )
        return

    if context.user_data.get('deal_state') == 'awaiting_release_qr':
        deal_id = context.user_data.get('release_deal_id')
        deal = get_deal(deal_id) if deal_id else None
        seller = get_user(update.effective_user.id)
        if not deal or not seller or seller['id'] != deal['seller_id']:
            await message.reply_text("❌ Payout session expired or unauthorized.")
            context.user_data['deal_state'] = None
            return
        qr_file_id = None
        qr_is_photo = bool(message.photo)
        if message.photo:
            qr_file_id = message.photo[-1].file_id
        elif message.document:
            qr_file_id = message.document.file_id
        if not qr_file_id:
            await message.reply_text("📸 Step 2/2: send the QR as a photo or document.")
            return
        save_payout_detail(deal, qr_file_id=qr_file_id)
        context.user_data['deal_state'] = None
        payout = get_release_actions(deal)
        owner_text = (
            f"💸 **PAYOUT READY — {deal_id}**\n\n"
            f"Amount held: {format_currency(deal['amount'], deal['currency'])}\n"
            f"MM fee: {format_currency(deal['mm_fee'], deal['currency'])}\n"
            f"Seller net: {format_currency(deal['seller_net_amount'], deal['currency'])}\n"
            f"Seller UPI: `{payout['seller_upi_id']}`\n\n"
            "Verify the UPI/QR, pay the seller after deducting the MM fee, then upload the payout screenshot."
        )
        for owner_id in Config.OWNER_IDS + Config.SUPER_OWNER_IDS:
            try:
                await context.bot.send_message(
                    owner_id, owner_text, parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ I paid — upload proof", callback_data=f"approve_payout_{deal_id}")],
                        [InlineKeyboardButton("❌ Pause / dispute", callback_data=f"reject_payout_{deal_id}")],
                    ])
                )
                if qr_is_photo:
                    await context.bot.send_photo(owner_id, qr_file_id, caption=f"Seller payout QR — {deal_id}")
                else:
                    await context.bot.send_document(owner_id, qr_file_id, caption=f"Seller payout QR — {deal_id}")
            except Exception as error:
                logging.warning("Could not send payout request to owner %s: %s", owner_id, error)
        await message.reply_text(
            "✅ Payout details submitted.\n\n"
            "Hint: the MM will verify the QR, deduct the fee, complete the payout, and send the payment screenshot to the seller."
        )
        return

    if context.user_data.get('deal_state') == 'awaiting_payout_proof':
        deal_id = context.user_data.get('release_deal_id')
        deal = get_deal(deal_id) if deal_id else None
        if not deal or not is_authorized(telegram_id, 'owner'):
            await message.reply_text("❌ Payout proof session expired or unauthorized.")
            context.user_data['deal_state'] = None
            return
        proof_file_id = message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else None)
        if not proof_file_id:
            await message.reply_text("📸 Upload the payout screenshot as a photo/document.")
            return
        if deal['status'] != 'release_requested' or not update_deal_status(deal_id, 'completed', telegram_id):
            await message.reply_text("❌ This deal is no longer ready for payout completion.")
            context.user_data['deal_state'] = None
            return
        save_payout_proof(deal, proof_file_id)
        context.user_data['deal_state'] = None
        seller = get_user_by_id(deal['seller_id'])
        completion = (
            f"✅ **ORDER COMPLETED — {deal_id}**\n\n"
            f"Seller net payout: {format_currency(deal['seller_net_amount'], deal['currency'])}\n"
            "The MM fee was deducted before payout. The payment proof is attached."
        )
        # Buyer has already completed their side after selecting Release
        # Payment. Only the seller receives payout proof from this point.
        for recipient in (seller,):
            if recipient:
                try:
                    await context.bot.send_message(
                        recipient['telegram_id'], completion, parse_mode=ParseMode.MARKDOWN,
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("⭐ Leave Vouch / Review", callback_data=f"review_{deal_id}")],
                            [InlineKeyboardButton("🆘 Report an Issue", callback_data=f"create_ticket_{deal_id}")],
                            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")],
                        ])
                    )
                    await context.bot.copy_message(recipient['telegram_id'], message.chat_id, message.message_id)
                except Exception as error:
                    logging.warning("Could not send payout proof to %s: %s", recipient['telegram_id'], error)
        await message.reply_text("✅ Payout proof saved and sent. Deal marked completed.")
        return

    # Only unhandled private messages in an already-confirmed deal are
    # relayed. Setup/support/admin/payout text must never leak to a partner.
    if message.chat and message.chat.type == 'private' and not context.user_data.get('deal_state'):
        active_deal = get_active_deal_for_user(telegram_id)
        if active_deal:
            await relay_deal_message(context, message, active_deal)
            return

    # Default response for unknown messages
    await message.reply_text(
        "I didn't understand that. Please use the buttons or type /start to begin.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current operation"""
    context.user_data['deal_state'] = None
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])
    )

# ============================================
# GROUP CHAT HANDLERS
# ============================================

async def participant_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    """Allow a buyer or seller to hold/request release from private chat."""
    message = update.message
    user = get_user(update.effective_user.id)
    deal_id = context.args[0].upper() if context.args else None
    if not deal_id:
        await message.reply_text(f"Usage: /{action} <DEAL_ID>")
        return
    deal = get_deal(deal_id)
    if not deal or not user or user['id'] not in [deal['buyer_id'], deal['seller_id']]:
        await message.reply_text("❌ Deal not found or you are not a participant.")
        return
    if deal['status'] == 'completed':
        await message.reply_text("❌ This deal is completed; its payment status cannot be changed.")
        return
    if deal['status'] == 'cancelled':
        await message.reply_text("❌ This deal is cancelled.")
        return

    role = 'buyer' if user['id'] == deal['buyer_id'] else 'seller'
    actions = get_release_actions(deal)
    if action == 'hold':
        if role == 'seller' and actions.get('buyer_action') != 'release':
            await message.reply_text("ℹ️ Seller hold is available only after the buyer selects Release Payment.")
            return
        set_release_action(deal, role, 'hold')
        if role == 'buyer' and deal['status'] == 'payment_held':
            update_deal_status(deal_id, 'disputed', update.effective_user.id)
        result = (
            "🔒 Buyer placed the payment on hold. The order is pending and the seller cannot process payout."
            if role == 'buyer' else
            "🔒 Seller chose to keep the payment held with the MM. Use /release " + deal_id + " when ready."
        )
    else:
        if role == 'seller' and actions.get('buyer_action') != 'release':
            await message.reply_text("⏳ Buyer must select Release Payment before the seller can release.")
            return
        set_release_action(deal, role, 'release')
        actions = get_release_actions(deal)
        if actions.get('buyer_action') == 'release' and actions.get('seller_action') == 'release':
            if deal['status'] != 'release_requested':
                update_deal_status(deal_id, 'release_requested', update.effective_user.id)
            context.user_data['deal_state'] = 'awaiting_release_upi'
            context.user_data['release_deal_id'] = deal_id
            await message.reply_text(
                "✅ Both releases received.\n\n"
                "Step 1/2 — send the seller's UPI ID.\n"
                "Hint: send only the UPI ID, then send a clear QR image."
            )
            return
        result = "✅ Your release choice was recorded. Waiting for the other participant."

    updated_deal = get_deal(deal_id) or deal
    # Once buyer release is recorded, buyer is out of the payout conversation.
    recipient_id = updated_deal['seller_id'] if role == 'buyer' else updated_deal['seller_id']
    if role == 'buyer':
        await notify_deal_user(context, updated_deal, recipient_id, f"📌 **Deal {deal_id} update**\n\n{result}")
    if Config.DEAL_LOG_CHANNEL_ID:
        try:
            await context.bot.send_message(Config.DEAL_LOG_CHANNEL_ID, f"📌 Deal {deal_id}: {result}")
        except Exception as error:
            logging.warning("Could not log payment command: %s", error)
    await message.reply_text(result)

async def private_hold_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await participant_payment_command(update, context, 'hold')

async def private_release_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await participant_payment_command(update, context, 'release')

async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to an explicit MM trigger in a group, with a short cooldown."""
    message = update.message
    if not message:
        return

    text = message.text or message.caption or ""
    if not text:
        return

    # Match complete words anywhere in the sentence, case-insensitively:
    # "need a MiddleMan!!!", "trusted escrow?", and "mm please" all match.
    # The explicit non-alphanumeric boundaries are more reliable than \b for
    # punctuation and mixed Unicode text.
    trigger_words = sorted(
        (word.strip() for word in Config.TRIGGER_WORDS if word.strip()),
        key=len,
        reverse=True,
    )
    trigger_pattern = r"(?<![A-Za-z0-9])(?:" + "|".join(
        re.escape(word) for word in trigger_words
    ) + r")(?![A-Za-z0-9])"
    triggered = bool(re.search(trigger_pattern, text, re.IGNORECASE))
    
    if not triggered:
        return
    
    # Rate limiting - check last response time
    chat_id = message.chat_id
    last_response = context.chat_data.get('last_promo_response')
    if last_response:
        elapsed = (datetime.now() - last_response).total_seconds()
        if elapsed < Config.GROUP_TRIGGER_COOLDOWN_SECONDS:
            return
    
    # Send promotion message
    promo_text = """
🤝 **NAOYA'S MIDDLEMAN SERVICE**

Looking for a trusted Middleman for your deal?

🔐 Professional deal handling
💬 Buyer & Seller support
📋 Transparent deal workflow
⭐ Reputation & vouch system

Ready to do the deal?

Start by opening the bot below and pressing Start. Your deal will continue privately in the bot.
"""
    
    try:
        await message.reply_text(
            promo_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🤝 LET'S DO THE DEAL", url=f"https://t.me/{context.bot.username}?start=group")]
            ])
        )
        # First matching message replies immediately; subsequent matches in
        # this group are suppressed for exactly five minutes.
        context.chat_data['last_promo_response'] = datetime.now()
    except Exception:
        pass

# ============================================
# GROUP COMMAND HANDLERS
# ============================================

async def group_lock_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /lock command in groups"""
    message = update.message
    user = update.effective_user
    
    # /lock is a group moderation command: the caller must be an actual admin
    # of this group, not merely an admin in the bot's database.
    caller_member = await context.bot.get_chat_member(message.chat_id, user.id)
    if caller_member.status not in ('administrator', 'creator'):
        await message.reply_text("❌ Only a Telegram group admin can use /lock.")
        return
    
    # Check bot permissions
    chat_member = await context.bot.get_chat_member(message.chat_id, context.bot.id)
    if chat_member.status not in ['administrator', 'creator']:
        await message.reply_text("❌ I am not an administrator in this group.")
        return
    
    if not chat_member.can_restrict_members:
        await message.reply_text("❌ I don't have permission to restrict members.")
        return
    
    try:
        # Restrict all members
        await context.bot.set_chat_permissions(
            message.chat_id,
            ChatPermissions(can_send_messages=False)
        )
        
        await message.reply_text(
            "🔒 **GROUP LOCKED**\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "This deal group has been locked by the Middleman.\n\n"
            "📌 No further messages are allowed from regular members.\n\n"
            f"🔰 Middleman: {Config.MM_USERNAME}",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Try to update group name
        try:
            deal_id = context.chat_data.get('deal_id')
            if deal_id:
                new_name = Config.GROUP_NAME_LOCKED_TEMPLATE.format(deal_id=deal_id)
                await context.bot.set_chat_title(message.chat_id, new_name[:255])
        except Exception:
            pass
        
    except Exception as e:
        await message.reply_text(f"❌ Failed to lock group: {str(e)}")

async def group_hold_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /hold command in groups"""
    if update.effective_chat and update.effective_chat.type == 'private':
        await private_hold_command(update, context)
        return
    # Payment helpers are private-only. Keep group chats undisturbed.
    return
    
    # Get deal_id from context or message
    deal_id = context.chat_data.get('deal_id') or (context.args[0].upper() if context.args else None)
    if not deal_id:
        await message.reply_text("❌ Provide a Deal ID, for example: /hold NM123456")
        return
    
    deal = get_deal(deal_id)
    if not deal:
        await message.reply_text("❌ Deal not found.")
        return
    
    if deal['status'] != 'payment_held':
        await message.reply_text(f"❌ Deal is not in payment held state. Current: {get_status_display(deal['status'])}")
        return
    
    # Update group name
    try:
        new_name = Config.GROUP_NAME_HOLD_TEMPLATE.format(deal_id=deal_id)
        await context.bot.set_chat_title(message.chat_id, new_name[:255])
    except Exception:
        pass
    
    await message.reply_text(
        f"🔒 **PAYMENT HELD**\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🆔 Deal: {deal_id}\n\n"
        f"💰 Amount: {format_currency(deal['amount'], deal['currency'])}\n"
        f"💵 MM Fee: {format_currency(deal['mm_fee'], deal['currency'])}\n\n"
        f"🔐 STATUS: PAYMENT HELD\n\n"
        "Seller may proceed according to the agreed terms.",
        parse_mode=ParseMode.MARKDOWN
    )

async def group_release_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /release command in groups"""
    if update.effective_chat and update.effective_chat.type == 'private':
        await private_release_command(update, context)
        return
    # Release is a private deal action; no group response.
    if update.effective_chat and update.effective_chat.type != 'private':
        return
    message = update.message
    user = update.effective_user
    
    # Check authorization
    if not is_authorized(user.id, 'owner'):
        await message.reply_text("❌ You are not authorized to use this command.")
        return
    
    # Get deal_id from context or message
    deal_id = context.chat_data.get('deal_id') or (context.args[0].upper() if context.args else None)
    if not deal_id:
        await message.reply_text("❌ Provide a Deal ID, for example: /release NM123456")
        return
    
    deal = get_deal(deal_id)
    if not deal:
        await message.reply_text("❌ Deal not found.")
        return
    
    if deal['status'] != 'release_requested':
        await message.reply_text(f"❌ Deal is not ready for release. Current: {get_status_display(deal['status'])}")
        return
    
    # Approve release
    update_deal_status(deal_id, 'completed', user.id)
    
    # Update group name
    try:
        new_name = Config.GROUP_NAME_COMPLETED_TEMPLATE.format(deal_id=deal_id)
        await context.bot.set_chat_title(message.chat_id, new_name[:255])
    except Exception:
        pass
    
    # Get participants
    buyer = get_user_by_id(deal['buyer_id'])
    seller = get_user_by_id(deal['seller_id'])
    
    release_text = f"""
💸 **PAYMENT RELEASED**

━━━━━━━━━━━━━━━━━━━━

🆔 Deal: {deal_id}

👤 Buyer: @{buyer['username'] if buyer else 'N/A'}
👤 Seller: @{seller['username'] if seller else 'N/A'}

💰 Deal Amount: {format_currency(deal['amount'], deal['currency'])}
💵 MM Fee: {format_currency(deal['mm_fee'], deal['currency'])}
💰 Seller Net Amount: {format_currency(deal['seller_net_amount'], deal['currency'])}

━━━━━━━━━━━━━━━━━━━━

✅ Deal completed successfully!

⭐ Please leave your honest review for Naoya's MM Service.
"""
    
    await message.reply_text(
        release_text,
        parse_mode=ParseMode.MARKDOWN
    )
    
    # Notify participants
    for participant_id in [deal['buyer_id'], deal['seller_id']]:
        participant = get_user_by_id(participant_id)
        if participant:
            try:
                await context.bot.send_message(
                    participant['telegram_id'],
                    release_text,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⭐ Leave Review", callback_data=f"review_{deal_id}")],
                        [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
                    ])
                )
            except Exception:
                pass

# ============================================
# REQUIREMENTS.TXT CONTENT
# ============================================

REQUIREMENTS = """
python-telegram-bot==20.7
qrcode==7.4.2
Pillow==12.3.0
"""

# ============================================
# BOT INITIALIZATION
# ============================================

async def on_bot_startup(application: Application) -> None:
    """Run after Telegram initializes the bot and confirm that it is online."""
    try:
        bot_info = await application.bot.get_me()
        print(f"✅ Bot connected: @{bot_info.username}")
        await application.bot.set_my_commands([
            BotCommand("start", "Open dashboard"),
            BotCommand("s", "Open dashboard (same as /start)"),
            BotCommand("help", "Show workflow help"),
            BotCommand("myid", "Show your MM ID"),
            BotCommand("status", "Show deal status"),
            BotCommand("deals", "List active deals"),
            BotCommand("hold", "Hold payment privately"),
            BotCommand("release", "Release payment privately"),
            BotCommand("cancel", "Cancel current step"),
        ])

        startup_message = (
            "✅ *Naoya's Middleman Bot is online!*\n\n"
            "The bot started successfully and is ready to receive commands."
        )
        for owner_id in Config.SUPER_OWNER_IDS:
            try:
                await application.bot.send_message(
                    chat_id=owner_id,
                    text=startup_message,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except TelegramError as error:
                logging.warning("Could not send startup notification to %s: %s", owner_id, error)
    except TelegramError as error:
        logging.error("Bot startup check failed: %s", error)

async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the stable partner ID without exposing a user's Telegram ID."""
    user = get_or_create_user(update.effective_user.id, update.effective_user.username,
                              update.effective_user.full_name)
    await update.message.reply_text(
        f"🆔 Your Naoya MM ID is: `{encode_user_id(user['telegram_id'])}`\n\n"
        "Share this ID with the other deal participant.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show one deal, or the user's latest active deal."""
    deal_id = context.args[0].upper() if context.args else None
    deal = get_deal(deal_id) if deal_id else get_active_deal_for_user(update.effective_user.id)
    if not deal:
        await update.message.reply_text("ℹ️ No matching active deal found.")
        return
    user = get_user(update.effective_user.id)
    if not user or user['id'] not in (deal['buyer_id'], deal['seller_id']):
        await update.message.reply_text("❌ You are not a participant in this deal.")
        return
    await update.message.reply_text(
        f"🆔 Deal: {deal['deal_id']}\n"
        f"📊 Status: {get_status_display(deal['status'])}\n"
        f"💰 Amount: {format_currency(deal['amount'], deal['currency'])}\n\n"
        "Use /help for the available private deal commands."
    )


async def deals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List the caller's active deals."""
    user = get_user(update.effective_user.id)
    deals = get_deals_by_user(update.effective_user.id) if user else []
    active = [d for d in deals if d['status'] not in ('completed', 'cancelled')]
    if not active:
        await update.message.reply_text("ℹ️ You have no active deals.")
        return
    text = "📋 **Your active deals**\n\n" + "\n".join(
        f"• `{d['deal_id']}` — {get_status_display(d['status'])} — "
        f"{format_currency(d['amount'], d['currency'])}" for d in active[:20]
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


def build_application() -> Application:
    """Build and fully configure the application for hosting platforms."""
    app = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .post_init(on_bot_startup)
        .build()
    )
    app.add_handler(TypeHandler(Update, service_gate), group=-1)
    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r"(?i)^/\w+(?:@\w+)?(?:\s|$)"),
        case_insensitive_command_router,
    ))
    for command, callback in [
        ('start', start), ('s', start), ('resume', resume_service_command),
        ('startservice', resume_service_command), ('help', help_command),
        ('cancel', cancel), ('myid', myid_command), ('status', status_command),
        ('deals', deals_command), ('message', message_user_command),
        ('reply', dispute_reply_command), ('addadmin', add_admin_command),
        ('removeadmin', remove_admin_command), ('lock', group_lock_handler),
        ('hold', group_hold_handler), ('held', group_hold_handler),
        ('release', group_release_handler),
    ]:
        app.add_handler(CommandHandler(command, callback))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.GROUPS, group_message_handler))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    return app


# Hosting platforms discover this module-level variable.
Config.validate()
init_database()
application = build_application()


# Vercel WSGI entrypoint. Telegram sends webhook POST requests here.
app = Flask(__name__)


@app.get('/')
def health_check():
    return 'Naoya Middleman Bot is running.', 200


async def _process_webhook_update(payload: dict) -> None:
    """Process one Telegram update in a short-lived serverless invocation."""
    if not isinstance(payload.get('update_id'), int):
        raise ValueError('Telegram update_id is missing or invalid')
    update = Update.de_json(payload, application.bot)
    if update is None:
        return
    await application.initialize()
    try:
        await application.process_update(update)
    finally:
        await application.shutdown()


@app.post('/webhook')
def telegram_webhook():
    webhook_secret = os.getenv('TELEGRAM_WEBHOOK_SECRET')
    if webhook_secret and request.headers.get('X-Telegram-Bot-Api-Secret-Token') != webhook_secret:
        return 'Unauthorized.', 401
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get('update_id'), int):
        return 'Invalid Telegram update.', 400
    try:
        asyncio.run(_process_webhook_update(payload))
    except Exception:
        logging.exception('Failed to process Telegram webhook update')
        return 'Webhook processing failed.', 500
    return 'OK', 200


def main():
    """Main entry point"""
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    print("""
    ╔══════════════════════════════════════════╗
    ║   NAOYA'S MIDDLEMAN SERVICE              ║
    ║   Dynamic UPI QR Generation Enabled      ║
    ╚══════════════════════════════════════════╝
    """)
    # Start the bot
    print("🤝 Naoya's Middleman Service Bot Starting...")
    print(f"Super Owners: {Config.SUPER_OWNER_IDS}")
    print("✅ QR Generation: ENABLED")
    print("📱 UPI QR: Dynamic amount QR codes will be generated")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

# ============================================
# RUN THE BOT
# ============================================

if __name__ == "__main__":
    main()
