"""
backend.py — Stock Market Simulation API
Group B14 | 23CSE202 | Amrita School of Computing
Run:  python backend.py   →  http://localhost:8000
Deps: pip install -r requirements.txt
"""

import os
import time
import random
import hashlib
import secrets
import threading
from datetime import datetime
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ─── DB CONNECTION POOL ─────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", "5432")),
    "dbname":   os.getenv("DB_NAME", "stockmarket"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}

try:
    pool = psycopg2.pool.ThreadedConnectionPool(1, 15, **DB_CONFIG)
    DB_AVAILABLE = True
    print("Connected to PostgreSQL:", DB_CONFIG["dbname"])
except Exception as e:
    pool = None
    DB_AVAILABLE = False
    print("PostgreSQL unavailable:", e)


@contextmanager
def get_conn():
    if not DB_AVAILABLE:
        raise HTTPException(503, "PostgreSQL not available. Check .env and run schema.sql.")
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)


# ─── IN-MEMORY TRANSACTION LOG ──────────────────────────────
tx_logs = []

def add_log(log_type: str, message: str):
    tx_logs.insert(0, {
        "id":        f"l{int(time.time()*1000)}",
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "type":      log_type,
        "message":   message,
    })
    if len(tx_logs) > 200:
        tx_logs.pop()


# ─── APP ────────────────────────────────────────────────────
app = FastAPI(title="StockSim API", description="Group B14 — 23CSE202")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

if not tx_logs:
    add_log("SYSTEM",  "Engine initialized — isolation level READ COMMITTED")
    add_log("TRIGGER", "trg_on_trade registered (portfolio + price history)")


# ─── REQUEST MODELS ─────────────────────────────────────────
class RegisterRequest(BaseModel):
    username:     str
    email:        str
    password:     str
    initial_cash: float = 50000.00

class LoginRequest(BaseModel):
    username: str
    password: str

class OrderRequest(BaseModel):
    stock_id:   str
    order_type: str          # BUY | SELL
    price_type: str          # MARKET | LIMIT
    price:      float = 0   # only used for LIMIT orders
    quantity:   int

class NewStockRequest(BaseModel):
    stock_id:         str
    company_name:     str
    sector:           str   = "Technology"
    current_price:    float
    available_shares: int   = 300
    total_supply:     int   = 10000
    market_cap:       str   = "N/A"

class MarketShockRequest(BaseModel):
    shock_type: str          # BULL | CRASH | SPIKE

class CashAdjustmentRequest(BaseModel):
    action: str              # "DEPOSIT" | "WITHDRAW"
    amount: float


# ─── AUTH HELPERS ───────────────────────────────────────────
def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

def issue_token() -> str:
    return secrets.token_hex(32)

def current_user(authorization: str) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1]
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT user_id, username, email, cash_balance, role "
            "FROM users WHERE auth_token = %s", (token,))
        user = cur.fetchone()
    if not user:
        raise HTTPException(401, "Invalid or expired token")
    return user

def require_admin(user: dict):
    if user["role"] != "admin":
        raise HTTPException(403, "Admin privileges required")


# ─── HEALTH & FRONTEND ──────────────────────────────────────
@app.get("/")
def serve_index():
    index_file = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"status": "ok", "message": "StockSim API running"}


@app.get("/health")
def health():
    return {"status": "ok", "db": DB_AVAILABLE}


# ─── AUTH ───────────────────────────────────────────────────
@app.post("/register")
def register(req: RegisterRequest):
    if len(req.username) < 3 or len(req.password) < 6:
        raise HTTPException(400, "Username >= 3 chars, password >= 6 chars")
    init_cash = max(0.0, round(float(req.initial_cash), 2)) if req.initial_cash is not None else 50000.00
    token = issue_token()
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        try:
            cur.execute("""
                INSERT INTO users (username, email, password_hash, auth_token, cash_balance, role)
                VALUES (%s, %s, %s, %s, %s, 'trader')
                RETURNING user_id, username, role, cash_balance;
            """, (req.username, req.email, hash_password(req.password), token, init_cash))
            user = cur.fetchone()
            conn.commit()
            add_log("SYSTEM", f"User registered: {req.username} (Starting cash: ${init_cash:,.2f})")
            return {"success": True, "token": token, **user}
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(400, "Username or email already taken")


@app.post("/login")
def login(req: LoginRequest):
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT user_id, username, role, cash_balance, password_hash "
            "FROM users WHERE username = %s", (req.username,))
        user = cur.fetchone()
        if not user or user["password_hash"] != hash_password(req.password):
            raise HTTPException(401, "Invalid username or password")
        token = issue_token()
        cur.execute("UPDATE users SET auth_token = %s WHERE user_id = %s",
                    (token, user["user_id"]))
        conn.commit()
        add_log("SYSTEM", f"User logged in: {req.username}")
        return {
            "success": True, "token": token,
            "user_id": user["user_id"], "username": user["username"],
            "role": user["role"], "cash_balance": float(user["cash_balance"]),
        }


@app.post("/logout")
def logout(authorization: str = Header(None)):
    user = current_user(authorization)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE users SET auth_token = NULL WHERE user_id = %s",
                    (user["user_id"],))
        conn.commit()
    return {"success": True}


# ─── STOCKS ─────────────────────────────────────────────────
@app.get("/stocks")
def get_stocks():
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT stock_id, company_name, sector,
                   current_price, prev_close, day_high, day_low, volume,
                   available_shares, total_supply, market_cap,
                   ROUND(((current_price - prev_close) / prev_close) * 100, 2) AS pct_change,
                   RANK() OVER (ORDER BY (current_price - prev_close) / prev_close DESC) AS gainer_rank,
                   RANK() OVER (ORDER BY (current_price - prev_close) / prev_close ASC)  AS loser_rank
            FROM stocks
            ORDER BY stock_id;
        """)
        return cur.fetchall()


@app.post("/stocks")
def add_stock(req: NewStockRequest, authorization: str = Header(None)):
    user = current_user(authorization)
    require_admin(user)
    sid = req.stock_id.upper().strip()
    with get_conn() as conn, conn.cursor() as cur:
        try:
            cur.execute("""
                INSERT INTO stocks (stock_id, company_name, sector, current_price,
                                    prev_close, day_high, day_low,
                                    available_shares, total_supply, market_cap)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (sid, req.company_name, req.sector, req.current_price,
                  req.current_price, req.current_price, req.current_price,
                  req.available_shares, req.total_supply, req.market_cap))
            cur.execute("INSERT INTO price_history (stock_id, price, volume) VALUES (%s, %s, 0);",
                        (sid, req.current_price))
            conn.commit()
            add_log("TRANSACTION", f"COMMIT — '{sid}' listed at ${req.current_price}")
            return {"success": True, "stock_id": sid}
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(400, f"Ticker '{sid}' already exists")


@app.delete("/stocks/{stock_id}")
def delist_stock(stock_id: str, authorization: str = Header(None)):
    """Admin: delist a stock (only if it has no pending orders)."""
    user = current_user(authorization)
    require_admin(user)
    sid = stock_id.upper()
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM orders "
            "WHERE stock_id = %s AND status IN ('PENDING','PARTIAL')", (sid,))
        if cur.fetchone()["cnt"] > 0:
            raise HTTPException(400, f"Cancel all open orders for '{sid}' before delisting")
        try:
            cur.execute("DELETE FROM stocks WHERE stock_id = %s RETURNING stock_id", (sid,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, f"Stock '{sid}' not found")
            conn.commit()
            add_log("SYSTEM", f"Stock '{sid}' delisted by {user['username']}")
            return {"success": True, "stock_id": sid}
        except psycopg2.errors.ForeignKeyViolation:
            conn.rollback()
            raise HTTPException(400, f"Cannot delist '{sid}': it has trade or portfolio history")


# ─── CURRENT USER ───────────────────────────────────────────
@app.get("/me")
def get_me(authorization: str = Header(None)):
    user = current_user(authorization)
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT cash_balance FROM users WHERE user_id = %s", (user["user_id"],))
        row = cur.fetchone()
    return {**user, "cash_balance": float(row["cash_balance"])}


@app.post("/me/cash")
def adjust_cash(req: CashAdjustmentRequest, authorization: str = Header(None)):
    user = current_user(authorization)
    uid = user["user_id"]
    amt = round(float(req.amount), 2)
    action = req.action.upper().strip()

    if amt <= 0:
        raise HTTPException(400, "Amount must be greater than zero")
    if action not in ("DEPOSIT", "WITHDRAW"):
        raise HTTPException(400, "Action must be DEPOSIT or WITHDRAW")

    with get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT cash_balance FROM users WHERE user_id = %s FOR UPDATE;", (uid,))
                row = cur.fetchone()
                current_cash = float(row["cash_balance"])

                if action == "WITHDRAW":
                    if current_cash < amt:
                        conn.rollback()
                        raise HTTPException(400, f"Insufficient funds: available ${current_cash:,.2f}, requested ${amt:,.2f}")
                    new_balance = round(current_cash - amt, 2)
                    cur.execute("UPDATE users SET cash_balance = cash_balance - %s WHERE user_id = %s;", (amt, uid))
                    msg = f"Withdrew ${amt:,.2f}"
                else:
                    new_balance = round(current_cash + amt, 2)
                    cur.execute("UPDATE users SET cash_balance = cash_balance + %s WHERE user_id = %s;", (amt, uid))
                    msg = f"Deposited ${amt:,.2f}"

                conn.commit()
                add_log("TRANSACTION", f"{user['username']}: {msg} (New balance: ${new_balance:,.2f})")
                return {"success": True, "action": action, "amount": amt, "cash_balance": new_balance}
        except HTTPException:
            raise
        except Exception as e:
            conn.rollback()
            raise HTTPException(500, str(e))


@app.get("/me/portfolio")
def get_my_portfolio(authorization: str = Header(None)):
    user = current_user(authorization)
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT ph.stock_id, s.company_name, s.sector,
                   ph.shares_held, ph.avg_buy_price, s.current_price,
                   ROUND(ph.shares_held * s.current_price, 2)                                   AS current_value,
                   ROUND(ph.shares_held * (s.current_price - ph.avg_buy_price), 2)              AS unrealized_pnl,
                   ROUND(((s.current_price - ph.avg_buy_price) / ph.avg_buy_price) * 100, 2)   AS pnl_pct
            FROM portfolio_holdings ph
            JOIN stocks s ON ph.stock_id = s.stock_id
            WHERE ph.user_id = %s AND ph.shares_held > 0
            ORDER BY current_value DESC;
        """, (user["user_id"],))
        return cur.fetchall()


@app.get("/me/trades")
def get_my_trades(limit: int = 50, authorization: str = Header(None)):
    user = current_user(authorization)
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT t.trade_id, t.stock_id, s.company_name,
                   CASE WHEN t.buyer_id = %s THEN 'BUY' ELSE 'SELL' END AS trade_type,
                   t.executed_price, t.quantity, t.total_value, t.executed_at
            FROM trades t
            JOIN stocks s ON t.stock_id = s.stock_id
            WHERE t.buyer_id = %s OR t.seller_id = %s
            ORDER BY t.executed_at DESC LIMIT %s;
        """, (user["user_id"], user["user_id"], user["user_id"], limit))
        return cur.fetchall()


@app.get("/me/orders")
def get_my_orders(authorization: str = Header(None)):
    user = current_user(authorization)
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT order_id, stock_id, order_type, price_type, price,
                   quantity, filled_qty, status, placed_at
            FROM orders
            WHERE user_id = %s AND status IN ('PENDING','PARTIAL')
            ORDER BY placed_at DESC;
        """, (user["user_id"],))
        return cur.fetchall()


# ─── MATCHING ENGINE HELPERS ────────────────────────────────
def _insert_trade(cur, buy_order, sell_order, qty):
    """Insert one trade record and return its trade_id.
    The trg_on_trade trigger fires on this INSERT and atomically:
      - updates buyer & seller cash
      - updates buyer & seller portfolios
      - updates stock price, high/low, volume
      - appends to price_history
    """
    exec_price = sell_order["price"]
    total      = round(exec_price * qty, 2)
    cur.execute("""
        INSERT INTO trades
            (buy_order_id, sell_order_id, stock_id,
             buyer_id, seller_id, executed_price, quantity, total_value)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING trade_id;
    """, (buy_order["order_id"], sell_order["order_id"], buy_order["stock_id"],
          buy_order["user_id"],  sell_order["user_id"],
          exec_price, qty, total))
    trade_id = cur.fetchone()["trade_id"]
    add_log("SQL", f"INSERT INTO trades (buy={buy_order['order_id']}, "
                   f"sell={sell_order['order_id']}, qty={qty}, px={exec_price}) "
                   f"→ trade_id={trade_id}")
    return trade_id


def _fill_order(cur, order_id, fill_qty):
    """Increment filled_qty and set status to PARTIAL or FILLED."""
    cur.execute("""
        UPDATE orders
        SET filled_qty = filled_qty + %s,
            status = CASE
                WHEN filled_qty + %s >= quantity THEN 'FILLED'
                ELSE 'PARTIAL'
            END
        WHERE order_id = %s
        RETURNING order_id, user_id, stock_id, order_type,
                  price, quantity, filled_qty, status;
    """, (fill_qty, fill_qty, order_id))
    return cur.fetchone()


# ─── ORDER PLACEMENT ────────────────────────────────────────
@app.post("/orders")
def place_order(req: OrderRequest, authorization: str = Header(None)):
    user = current_user(authorization)
    return _place_order_for_user(user["user_id"], req)


def _place_order_for_user(uid: int, req) -> dict:
    sid = req.stock_id.upper()
    add_log("TRANSACTION", f"BEGIN — user={uid} {req.order_type} {req.quantity}x {sid}")

    with get_conn() as conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:

                # Lock the stock row to prevent concurrent over-sells
                add_log("SQL", f"SELECT * FROM stocks WHERE stock_id='{sid}' FOR UPDATE")
                cur.execute(
                    "SELECT stock_id, current_price, available_shares "
                    "FROM stocks WHERE stock_id = %s FOR UPDATE;", (sid,))
                stock = cur.fetchone()
                if not stock:
                    conn.rollback()
                    raise HTTPException(404, f"Stock '{sid}' not found")

                # Resolve price
                order_price = (float(stock["current_price"]) if req.price_type == "MARKET"
                               else float(req.price))
                if req.price_type == "LIMIT" and order_price <= 0:
                    conn.rollback()
                    raise HTTPException(400, "Limit price must be positive")

                # Pre-trade validation (lock user/holding row too)
                if req.order_type == "BUY":
                    cur.execute(
                        "SELECT cash_balance FROM users WHERE user_id = %s FOR UPDATE;", (uid,))
                    u = cur.fetchone()
                    need = order_price * req.quantity
                    if not u or u["cash_balance"] < need:
                        conn.rollback()
                        have = float(u["cash_balance"]) if u else 0
                        add_log("SQL", f"ROLLBACK — insufficient cash (${have:.2f} < ${need:.2f})")
                        raise HTTPException(400, f"Insufficient cash. Need ${need:.2f}, have ${have:.2f}")
                else:
                    cur.execute(
                        "SELECT shares_held FROM portfolio_holdings "
                        "WHERE user_id = %s AND stock_id = %s FOR UPDATE;", (uid, sid))
                    h = cur.fetchone()
                    held = h["shares_held"] if h else 0
                    if held < req.quantity:
                        conn.rollback()
                        add_log("SQL", f"ROLLBACK — only {held} shares held")
                        raise HTTPException(400, f"You only hold {held} shares of {sid}")

                # Insert the new order
                cur.execute("""
                    INSERT INTO orders
                        (user_id, stock_id, order_type, price_type, price, quantity, filled_qty, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 0, 'PENDING')
                    RETURNING order_id;
                """, (uid, sid, req.order_type, req.price_type, order_price, req.quantity))
                new_order_id = cur.fetchone()["order_id"]
                add_log("SQL", f"INSERT INTO orders → order_id={new_order_id}")

                # Find matching orders (price-time priority, lock them)
                opposite    = "SELL" if req.order_type == "BUY" else "BUY"
                price_cond  = "price <= %s" if req.order_type == "BUY" else "price >= %s"
                price_order = "ASC"         if req.order_type == "BUY" else "DESC"

                cur.execute(f"""
                    SELECT order_id, user_id, stock_id, order_type,
                           price, quantity, filled_qty, status
                    FROM orders
                    WHERE stock_id  = %s
                      AND order_type = %s
                      AND status IN ('PENDING','PARTIAL')
                      AND order_id  <> %s
                      AND {price_cond}
                    ORDER BY price {price_order}, placed_at ASC
                    FOR UPDATE;
                """, (sid, opposite, new_order_id, order_price))
                matches = cur.fetchall()

                # Re-read our order to track fill state
                cur.execute(
                    "SELECT order_id, user_id, stock_id, order_type, price, quantity, filled_qty, status "
                    "FROM orders WHERE order_id = %s;", (new_order_id,))
                new_order   = cur.fetchone()
                total_filled = 0
                trade_ids    = []

                # Match loop
                for match in matches:
                    remaining_ours   = new_order["quantity"] - new_order["filled_qty"]
                    remaining_theirs = match["quantity"]    - match["filled_qty"]
                    if remaining_ours <= 0 or remaining_theirs <= 0:
                        continue

                    fill = min(remaining_ours, remaining_theirs)
                    buy_ord, sell_ord = (
                        (new_order, match) if req.order_type == "BUY" else (match, new_order)
                    )

                    trade_ids.append(_insert_trade(cur, buy_ord, sell_ord, fill))
                    new_order = _fill_order(cur, new_order["order_id"], fill)
                    _fill_order(cur, match["order_id"], fill)
                    total_filled += fill

                    if new_order["status"] == "FILLED":
                        break

                # Handle unmatched residual for BUY orders only:
                # Decrement available_shares pool for shares bought from the market.
                # SELL residuals do NOT adjust available_shares — the seller still holds
                # those shares in their portfolio until a buyer matches them.
                residual = req.quantity - total_filled
                if residual > 0 and req.order_type == "BUY":
                    if stock["available_shares"] < residual:
                        conn.rollback()
                        add_log("SQL", f"ROLLBACK — only {stock['available_shares']} shares in pool")
                        raise HTTPException(400,
                            f"Only {stock['available_shares']} shares available in market pool")
                    cur.execute(
                        "UPDATE stocks SET available_shares = available_shares - %s "
                        "WHERE stock_id = %s", (residual, sid))

                conn.commit()
                add_log("TRANSACTION",
                    f"COMMIT — {total_filled} filled, {residual} resting")
                return {
                    "success":     True,
                    "order_id":    new_order_id,
                    "status":      new_order["status"],
                    "filled_qty":  total_filled,
                    "residual_qty": residual,
                    "trade_ids":   trade_ids,
                    "price":       order_price,
                }

        except HTTPException:
            raise
        except Exception as e:
            conn.rollback()
            add_log("SQL", f"ROLLBACK — {e}")
            raise HTTPException(500, str(e))


@app.delete("/orders/{order_id}")
def cancel_order(order_id: int, authorization: str = Header(None)):
    user = current_user(authorization)
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT order_id, user_id, stock_id, order_type, quantity, filled_qty, status
            FROM orders WHERE order_id = %s FOR UPDATE;
        """, (order_id,))
        o = cur.fetchone()
        if not o:
            raise HTTPException(404, "Order not found")
        if o["user_id"] != user["user_id"]:
            raise HTTPException(403, "Not your order")
        if o["status"] not in ("PENDING", "PARTIAL"):
            raise HTTPException(400, f"Cannot cancel a {o['status']} order")

        residual = o["quantity"] - o["filled_qty"]

        # Only BUY orders reserved pool shares — return them on cancel.
        # SELL orders never touched available_shares, so nothing to restore.
        if residual > 0 and o["order_type"] == "BUY":
            cur.execute(
                "UPDATE stocks SET available_shares = available_shares + %s WHERE stock_id = %s",
                (residual, o["stock_id"]))

        cur.execute("UPDATE orders SET status = 'CANCELLED' WHERE order_id = %s", (order_id,))
        conn.commit()
        add_log("SYSTEM", f"Order #{order_id} cancelled ({residual} shares released)")
        return {"success": True, "order_id": order_id}


# ─── ANALYTICS ──────────────────────────────────────────────
@app.get("/analytics/gainers")
def get_gainers():
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        add_log("SQL", "SELECT RANK() OVER (ORDER BY pct_change DESC/ASC) FROM stocks")
        cur.execute("""
            SELECT stock_id, company_name, sector,
                   current_price, prev_close,
                   ROUND(((current_price - prev_close) / prev_close) * 100, 2) AS pct_change,
                   RANK() OVER (ORDER BY (current_price - prev_close) / prev_close DESC) AS gainer_rank,
                   RANK() OVER (ORDER BY (current_price - prev_close) / prev_close ASC)  AS loser_rank
            FROM stocks
            ORDER BY gainer_rank;
        """)
        return cur.fetchall()


@app.get("/analytics/rolling/{stock_id}")
def get_rolling(stock_id: str, limit: int = 40):
    sid = stock_id.upper()
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        add_log("SQL", f"SELECT AVG() OVER(), LAG() OVER() FROM price_history WHERE stock_id='{sid}'")
        cur.execute("""
            SELECT stock_id, price, recorded_at,
                   ROUND(price - COALESCE(
                       LAG(price) OVER (PARTITION BY stock_id ORDER BY recorded_at),
                       price), 2)                                                    AS lag_diff,
                   ROUND(AVG(price) OVER (
                       PARTITION BY stock_id ORDER BY recorded_at
                       ROWS BETWEEN 4 PRECEDING AND CURRENT ROW), 2)               AS ma_5,
                   ROUND(AVG(price) OVER (
                       PARTITION BY stock_id ORDER BY recorded_at
                       ROWS BETWEEN 19 PRECEDING AND CURRENT ROW), 2)              AS ma_20
            FROM price_history
            WHERE stock_id = %s
            ORDER BY recorded_at DESC
            LIMIT %s;
        """, (sid, limit))
        return list(reversed(cur.fetchall()))


@app.get("/analytics/orderbook/{stock_id}")
def get_order_book(stock_id: str):
    sid = stock_id.upper()
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT order_type, price, SUM(quantity - filled_qty) AS open_qty
            FROM orders
            WHERE stock_id = %s AND status IN ('PENDING','PARTIAL')
            GROUP BY order_type, price
            ORDER BY price DESC;
        """, (sid,))
        rows = cur.fetchall()
    bids = [r for r in rows if r["order_type"] == "BUY"][:10]
    asks = sorted([r for r in rows if r["order_type"] == "SELL"], key=lambda x: x["price"])[:10]
    return {"bids": bids, "asks": asks}


# ─── ADMIN ──────────────────────────────────────────────────
@app.get("/admin/users")
def get_all_users(authorization: str = Header(None)):
    """Admin: list all users with order and trade counts."""
    user = current_user(authorization)
    require_admin(user)
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT u.user_id, u.username, u.email, u.role, u.cash_balance, u.created_at,
                   COUNT(DISTINCT o.order_id)  AS total_orders,
                   COUNT(DISTINCT t.trade_id)  AS total_trades,
                   COALESCE(SUM(ph.shares_held * s.current_price), 0) AS portfolio_value
            FROM users u
            LEFT JOIN orders o
                ON o.user_id = u.user_id
            LEFT JOIN trades t
                ON t.buyer_id = u.user_id OR t.seller_id = u.user_id
            LEFT JOIN portfolio_holdings ph ON ph.user_id = u.user_id
            LEFT JOIN stocks s              ON s.stock_id = ph.stock_id
            WHERE u.username != 'race_bot'
            GROUP BY u.user_id, u.username, u.email, u.role, u.cash_balance, u.created_at
            ORDER BY u.user_id;
        """)
        return cur.fetchall()


# ─── VERIFICATION ───────────────────────────────────────────
@app.get("/verify/consistency")
def verify_consistency():
    """Check that available_shares + portfolio holdings = total_supply for every stock."""
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT s.stock_id, s.total_supply,
                   s.available_shares,
                   COALESCE(SUM(ph.shares_held), 0) AS held_by_users,
                   s.available_shares + COALESCE(SUM(ph.shares_held), 0) AS accounted_for,
                   (s.available_shares + COALESCE(SUM(ph.shares_held), 0) = s.total_supply)
                       AS consistent
            FROM stocks s
            LEFT JOIN portfolio_holdings ph ON ph.stock_id = s.stock_id
            GROUP BY s.stock_id, s.total_supply, s.available_shares
            ORDER BY s.stock_id;
        """)
        return cur.fetchall()


# ─── LOGS ───────────────────────────────────────────────────
@app.get("/logs")
def get_logs(limit: int = 100):
    return tx_logs[:limit]


# ─── RACE CONDITION DEMO ────────────────────────────────────
@app.post("/simulate/race")
def simulate_race(stock_id: str = "TSLA", authorization: str = Header(None)):
    """
    Demonstrates concurrency control: two threads race to buy more than
    half the available shares simultaneously. SELECT ... FOR UPDATE ensures
    only one succeeds; the other rolls back with an error.
    """
    user = current_user(authorization)
    sid  = stock_id.upper()
    add_log("SYSTEM", f"Race test on {sid}: two barrier-synced buyers")

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT available_shares FROM stocks WHERE stock_id = %s;", (sid,))
        row = cur.fetchone()
        avail = row["available_shares"] if row else 0

    if avail < 2:
        raise HTTPException(400, f"Not enough shares of {sid} to run the race test")

    qty     = avail // 2 + 1   # Each thread asks for more than half → only one can win
    barrier = threading.Barrier(2)
    results = {}

    # Ensure a persistent bot account exists
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        bot_token = secrets.token_hex(32)
        cur.execute("""
            INSERT INTO users (username, email, password_hash, auth_token, cash_balance, role)
            VALUES ('race_bot', 'racebot@stocksim.com',
                    encode(sha256('racebot'::bytea), 'hex'), %s, 500000.00, 'trader')
            ON CONFLICT (username) DO UPDATE SET
                auth_token = EXCLUDED.auth_token,
                cash_balance = 500000.00
            RETURNING auth_token;
        """, (bot_token,))
        bot_token = cur.fetchone()["auth_token"]
        conn.commit()

    caller_token = authorization.split(" ", 1)[1] if authorization else None

    def attempt(thread_num, token):
        barrier.wait()   # Both threads start at the same instant
        try:
            with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT user_id FROM users WHERE auth_token = %s", (token,))
                u = cur.fetchone()
            if not u:
                results[f"thread{thread_num}"] = {"success": False, "error": "bad token"}
                return

            class _Req:
                stock_id   = sid
                order_type = "BUY"
                price_type = "MARKET"
                price      = 0
                quantity   = qty

            r = _place_order_for_user(u["user_id"], _Req())
            results[f"thread{thread_num}"] = r
            add_log("SYSTEM", f"Thread {thread_num} committed — order #{r.get('order_id')}")
        except HTTPException as e:
            results[f"thread{thread_num}"] = {"success": False, "error": str(e.detail)}
            add_log("SYSTEM", f"Thread {thread_num} rolled back: {e.detail}")

    t1 = threading.Thread(target=attempt, args=(1, caller_token))
    t2 = threading.Thread(target=attempt, args=(2, bot_token))
    t1.start(); t2.start()
    t1.join();  t2.join()

    winner = next((n for n in (1, 2) if results.get(f"thread{n}", {}).get("success")), None)
    loser  = 3 - winner if winner else None
    if winner:
        add_log("SYSTEM", f"Thread {winner} acquired row lock first — no overselling")
    if loser:
        add_log("SYSTEM", f"Thread {loser} rolled back — race condition prevented")

    return results


@app.post("/simulate/shock")
def market_shock(req: MarketShockRequest, authorization: str = Header(None)):
    """Admin: apply a market-wide price shock (BULL / CRASH / SPIKE)."""
    user = current_user(authorization)
    require_admin(user)

    mult = {"BULL": 1.08, "CRASH": 0.90}.get(req.shock_type)
    if req.shock_type == "SPIKE":
        mult = 1.12 if random.random() > 0.5 else 0.85
    if mult is None:
        raise HTTPException(400, "shock_type must be BULL, CRASH, or SPIKE")

    add_log("SYSTEM", f"Market shock: {req.shock_type} (×{mult:.2f})")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE stocks SET
                current_price = ROUND(current_price * %s, 2),
                day_high = GREATEST(day_high, ROUND(current_price * %s, 2)),
                day_low  = LEAST   (day_low,  ROUND(current_price * %s, 2));
        """, (mult, mult, mult))
        cur.execute("""
            INSERT INTO price_history (stock_id, price, volume, recorded_at)
            SELECT stock_id, current_price, 0, NOW() FROM stocks;
        """)
        conn.commit()
    add_log("TRANSACTION", f"COMMIT — shock applied to all stocks")
    return {"success": True, "multiplier": mult}


# ─── MARKET BOT ─────────────────────────────────────────────
def market_bot():
    """Background thread: nudge a random stock's price every 2 seconds
    to simulate live market movement and populate price_history."""
    while True:
        time.sleep(2)
        if not DB_AVAILABLE:
            continue
        try:
            with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT stock_id, current_price FROM stocks ORDER BY RANDOM() LIMIT 1;")
                row = cur.fetchone()
                if not row:
                    continue
                sid       = row["stock_id"]
                price     = float(row["current_price"])
                pct       = (random.random() - 0.48) * 0.01
                new_price = max(1.0, round(price * (1 + pct), 2))
                cur.execute("""
                    UPDATE stocks SET
                        current_price = %s,
                        day_high = GREATEST(day_high, %s),
                        day_low  = LEAST   (day_low,  %s)
                    WHERE stock_id = %s;
                """, (new_price, new_price, new_price, sid))
                cur.execute(
                    "INSERT INTO price_history (stock_id, price, volume) VALUES (%s, %s, %s);",
                    (sid, new_price, random.randint(10, 80)))
                conn.commit()
        except Exception:
            pass


threading.Thread(target=market_bot, daemon=True).start()


# ─── ENTRY POINT ────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("\nStockSim API — Group B14 | 23CSE202")
    print("http://localhost:8000       docs: /docs\n")
    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=True)