# Stock Market Simulation and Trading Database System

**Group B14 | 23CSE202 Database Management Systems**
**S3 B.Tech CSE | Amrita School of Computing, Amritapuri**

**Team Members:**
- AM.SC.U4CSE25124 — Jagannatha Bhanu Vengelote
- AM.SC.U4CSE25160 — Navdeep S
- AM.SC.U4CSE25162 — M Maheswar
- AM.SC.U4CSE25163 — Pranav Praveen

---

## Project Overview

A full-stack stock trading simulation that demonstrates real database concepts: ACID transactions, row-level locking, triggers, window functions, and indexes — all backed by a real PostgreSQL database.

---

## Setup Instructions

### Prerequisites

- Python 3.11+
- PostgreSQL 15 or 16 (download from https://www.postgresql.org/download/windows/)
- A browser (Chrome / Firefox)

### Step 1: Install PostgreSQL

Download and install from https://www.postgresql.org/download/windows/

During installation, set a password for the `postgres` user. Remember it.

### Step 2: Install Python dependencies

```bash
pip install psycopg2-binary fastapi uvicorn python-dotenv
```

### Step 3: Configure database credentials

Edit the `.env` file in this directory:

```
DB_HOST=localhost
DB_PORT=5432
DB_NAME=stockmarket
DB_USER=postgres
DB_PASSWORD=your_actual_password
```

### Step 4: Create the database and tables

```bash
psql -U postgres -f schema.sql
```

This will:
- Create the `stockmarket` database
- Create all 6 tables with constraints
- Create 2 AFTER INSERT triggers
- Create 5 indexes for performance
- Seed 4 users, 6 stocks, and 30 price history points per stock

### Step 5: Start the backend API

```bash
python backend.py
```

The API runs on http://localhost:8000
Interactive docs available at http://localhost:8000/docs

### Step 6: Open the frontend

Open `index.html` in a browser, or run:

```bash
python -m http.server 5500
```

Then navigate to http://localhost:5500

> **Note:** The frontend works in demo mode even without PostgreSQL running.
> When the backend is available, it shows "✓ Connected to PostgreSQL" in the transaction log.

---

## Database Design

### Tables (6)

| Table | Purpose |
|---|---|
| `users` | Trader and admin accounts with cash balances |
| `stocks` | Listed stocks with prices, high/low, available shares |
| `orders` | Every buy/sell order placed by users |
| `trades` | Completed transactions (triggers fire here) |
| `portfolio_holdings` | Auto-updated by trigger after each trade |
| `price_history` | Auto-updated by trigger; used for rolling averages |

### Triggers (2)

**`trg_update_portfolio_on_trade`** — AFTER INSERT ON trades  
- Deducts/credits user cash balance  
- Upserts portfolio holdings with weighted average buy price  
- Updates available shares on the stock

**`trg_record_price_history`** — AFTER INSERT ON trades  
- Inserts new row into `price_history`  
- Updates `stocks.current_price`, `day_high`, `day_low`

### Indexes (5)

| Index | Column | Reason |
|---|---|---|
| `idx_orders_stock_id` | orders.stock_id | Order matching queries |
| `idx_orders_placed_at` | orders.placed_at | Recent order history |
| `idx_trades_stock_id` | trades.stock_id | Trade history per stock |
| `idx_price_history_recorded_at` | price_history(stock_id, recorded_at) | Rolling avg window functions |
| `idx_portfolio_user_id` | portfolio_holdings.user_id | Portfolio page queries |

---

## Key SQL Features Demonstrated

### 1. Transaction with Row-Level Lock (Race Condition Prevention)
```sql
BEGIN;
SELECT stock_id, available_shares FROM stocks
WHERE stock_id = 'TSLA' FOR UPDATE;  -- Blocks concurrent transactions
INSERT INTO trades (...) VALUES (...);
COMMIT;
```

### 2. RANK() Window Function (Top Gainers/Losers)
```sql
SELECT stock_id,
  RANK() OVER (ORDER BY ((current_price - prev_close)/prev_close) DESC) AS gainer_rank
FROM stocks;
```

### 3. Rolling Average with LAG()
```sql
SELECT price,
  LAG(price) OVER (PARTITION BY stock_id ORDER BY recorded_at) AS prev_price,
  AVG(price) OVER (
    PARTITION BY stock_id ORDER BY recorded_at
    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
  ) AS ma_5
FROM price_history;
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | /stocks | All stocks with rankings |
| GET | /stocks/{id}/history | Price history + window functions |
| GET | /users/{id}/portfolio | Holdings with P&L |
| POST | /orders | Place order (transactional) |
| POST | /stocks | Admin: list new stock |
| POST | /simulate/race | Race condition demo |
| POST | /simulate/shock | Market shock |
| GET | /analytics/gainers | RANK() window function |
| GET | /logs | Transaction log |