-- ============================================================
-- schema.sql — Stock Market Simulation Database
-- Group B14 | 23CSE202 | Amrita School of Computing
-- Run: psql -U postgres -f schema.sql
-- ============================================================

\c postgres
DROP DATABASE IF EXISTS stockmarket;
CREATE DATABASE stockmarket;
\c stockmarket

-- ============================================================
-- TABLE 1: users
-- Stores both traders and admin accounts.
-- auth_token is issued on login; NULL means logged out.
-- ============================================================
CREATE TABLE users (
    user_id       SERIAL PRIMARY KEY,
    username      VARCHAR(50)  UNIQUE NOT NULL,
    email         VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL DEFAULT 'demo_hash',
    auth_token    VARCHAR(64)  UNIQUE,
    cash_balance  NUMERIC(15, 2) NOT NULL DEFAULT 50000.00
                  CHECK (cash_balance >= 0),
    role          VARCHAR(10) NOT NULL DEFAULT 'trader'
                  CHECK (role IN ('trader', 'admin')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- TABLE 2: stocks
-- Represents each listed stock on the exchange.
-- available_shares = shares not yet held by any user (pool).
-- ============================================================
CREATE TABLE stocks (
    stock_id         VARCHAR(10) PRIMARY KEY,
    company_name     VARCHAR(100) NOT NULL,
    sector           VARCHAR(100) NOT NULL,
    current_price    NUMERIC(12, 2) NOT NULL CHECK (current_price > 0),
    prev_close       NUMERIC(12, 2) NOT NULL,
    day_high         NUMERIC(12, 2) NOT NULL,
    day_low          NUMERIC(12, 2) NOT NULL,
    volume           BIGINT NOT NULL DEFAULT 0,
    available_shares INT NOT NULL DEFAULT 0 CHECK (available_shares >= 0),
    total_supply     INT NOT NULL DEFAULT 10000,
    market_cap       VARCHAR(20) DEFAULT 'N/A',
    listed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- TABLE 3: orders
-- Each order placed by a user. Matched to create trades.
-- filled_qty tracks partial fills; PARTIAL status is valid.
-- ============================================================
CREATE TABLE orders (
    order_id   SERIAL PRIMARY KEY,
    user_id    INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    stock_id   VARCHAR(10) NOT NULL REFERENCES stocks(stock_id),
    order_type VARCHAR(4)  NOT NULL CHECK (order_type IN ('BUY', 'SELL')),
    price_type VARCHAR(6)  NOT NULL CHECK (price_type IN ('MARKET', 'LIMIT')),
    price      NUMERIC(12, 2) NOT NULL,
    quantity   INT NOT NULL CHECK (quantity > 0),
    filled_qty INT NOT NULL DEFAULT 0 CHECK (filled_qty >= 0),
    status     VARCHAR(10) NOT NULL DEFAULT 'PENDING'
               CHECK (status IN ('PENDING', 'PARTIAL', 'FILLED', 'CANCELLED')),
    placed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- TABLE 4: trades
-- One row per matched fill; always has both a buyer and seller.
-- The trigger on this table does all the heavy lifting.
-- ============================================================
CREATE TABLE trades (
    trade_id       SERIAL PRIMARY KEY,
    buy_order_id   INT NOT NULL REFERENCES orders(order_id),
    sell_order_id  INT NOT NULL REFERENCES orders(order_id),
    stock_id       VARCHAR(10) NOT NULL REFERENCES stocks(stock_id),
    buyer_id       INT NOT NULL REFERENCES users(user_id),
    seller_id      INT NOT NULL REFERENCES users(user_id),
    executed_price NUMERIC(12, 2) NOT NULL,
    quantity       INT NOT NULL CHECK (quantity > 0),
    total_value    NUMERIC(15, 2) NOT NULL,
    executed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- TABLE 5: portfolio_holdings
-- Tracks how many shares each user holds per stock.
-- Automatically updated by trigger on trades.
-- ============================================================
CREATE TABLE portfolio_holdings (
    holding_id    SERIAL PRIMARY KEY,
    user_id       INT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    stock_id      VARCHAR(10) NOT NULL REFERENCES stocks(stock_id),
    shares_held   INT NOT NULL DEFAULT 0 CHECK (shares_held >= 0),
    avg_buy_price NUMERIC(12, 2) NOT NULL DEFAULT 0,
    last_updated  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, stock_id)
);

-- ============================================================
-- TABLE 6: price_history
-- Every price change is recorded here (via trigger on trades
-- and directly by the market bot / market shock).
-- ============================================================
CREATE TABLE price_history (
    history_id  SERIAL PRIMARY KEY,
    stock_id    VARCHAR(10) NOT NULL REFERENCES stocks(stock_id),
    price       NUMERIC(12, 2) NOT NULL,
    volume      INT NOT NULL DEFAULT 0,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- INDEXES
-- Added on columns used in frequent WHERE / JOIN / ORDER BY.
-- ============================================================
CREATE INDEX idx_orders_stock_status ON orders(stock_id, status);
CREATE INDEX idx_orders_placed_at    ON orders(placed_at DESC);
CREATE INDEX idx_trades_stock_id     ON trades(stock_id);
CREATE INDEX idx_trades_buyer        ON trades(buyer_id);
CREATE INDEX idx_trades_seller       ON trades(seller_id);
CREATE INDEX idx_price_history_stock ON price_history(stock_id, recorded_at DESC);
CREATE INDEX idx_portfolio_user_id   ON portfolio_holdings(user_id);

-- ============================================================
-- TRIGGER FUNCTION: fn_on_trade
-- Fires AFTER INSERT ON trades FOR EACH ROW.
--
-- Handles everything that must happen atomically on every fill:
--   1. Deduct cash from buyer, credit cash to seller
--   2. Upsert buyer's portfolio (weighted-average buy price)
--   3. Reduce seller's portfolio; delete row if empty
--   4. Update stock's current price, day high/low, volume
--   5. Append a row to price_history
--
-- available_shares is NOT touched here — the order-placement
-- code manages it only for the unmatched (residual) portion of
-- BUY orders, keeping the conservation law:
--   available_shares + SUM(portfolio shares) = total_supply
-- ============================================================
CREATE OR REPLACE FUNCTION fn_on_trade()
RETURNS TRIGGER AS $$
BEGIN
    -- 1a. Deduct cash from buyer
    UPDATE users
    SET cash_balance = cash_balance - NEW.total_value
    WHERE user_id = NEW.buyer_id;

    -- 1b. Credit cash to seller
    UPDATE users
    SET cash_balance = cash_balance + NEW.total_value
    WHERE user_id = NEW.seller_id;

    -- 2. Upsert buyer portfolio with weighted-average buy price
    INSERT INTO portfolio_holdings (user_id, stock_id, shares_held, avg_buy_price, last_updated)
    VALUES (NEW.buyer_id, NEW.stock_id, NEW.quantity, NEW.executed_price, NOW())
    ON CONFLICT (user_id, stock_id) DO UPDATE SET
        avg_buy_price = ROUND(
            (portfolio_holdings.shares_held * portfolio_holdings.avg_buy_price
             + NEW.quantity * NEW.executed_price)
            / (portfolio_holdings.shares_held + NEW.quantity),
            2
        ),
        shares_held  = portfolio_holdings.shares_held + NEW.quantity,
        last_updated = NOW();

    -- 3a. Reduce seller portfolio
    UPDATE portfolio_holdings
    SET shares_held  = shares_held - NEW.quantity,
        last_updated = NOW()
    WHERE user_id = NEW.seller_id AND stock_id = NEW.stock_id;

    -- 3b. Remove zero-share rows to keep portfolio clean
    DELETE FROM portfolio_holdings
    WHERE user_id  = NEW.seller_id
      AND stock_id = NEW.stock_id
      AND shares_held <= 0;

    -- 4. Update stock's current price, day high/low, and volume
    UPDATE stocks SET
        current_price = NEW.executed_price,
        day_high      = GREATEST(day_high, NEW.executed_price),
        day_low       = LEAST   (day_low,  NEW.executed_price),
        volume        = volume + NEW.quantity
    WHERE stock_id = NEW.stock_id;

    -- 5. Append to price history
    INSERT INTO price_history (stock_id, price, volume)
    VALUES (NEW.stock_id, NEW.executed_price, NEW.quantity);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_on_trade
    AFTER INSERT ON trades
    FOR EACH ROW
    EXECUTE FUNCTION fn_on_trade();

-- ============================================================
-- SEED DATA — Users
-- password_hash = SHA-256 of the password shown below
--   pranav_trader / bhanu_trader / navdeep_trader : password123
--   admin_exchange                                : admin123
-- ============================================================
INSERT INTO users (username, email, password_hash, cash_balance, role) VALUES
    ('pranav_trader',  'pranav@stocksim.com',  'ef92b778bafe771e89245b89ecbc08a44a4e166c06659911881f383d4473e94f', 50000.00, 'trader'),
    ('admin_exchange', 'admin@stocksim.com',   '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9', 999999.00, 'admin'),
    ('bhanu_trader',   'bhanu@stocksim.com',   'ef92b778bafe771e89245b89ecbc08a44a4e166c06659911881f383d4473e94f', 75000.00, 'trader'),
    ('navdeep_trader', 'navdeep@stocksim.com', 'ef92b778bafe771e89245b89ecbc08a44a4e166c06659911881f383d4473e94f', 60000.00, 'trader');

-- ============================================================
-- SEED DATA — Stocks
-- ============================================================
INSERT INTO stocks (stock_id, company_name, sector, current_price, prev_close, day_high, day_low, available_shares, total_supply, market_cap) VALUES
    ('NVDA',  'NVIDIA Corporation', 'Technology / AI',      128.50, 124.10, 130.20, 123.80, 450,  10000, '$3.16T'),
    ('AAPL',  'Apple Inc.',         'Consumer Electronics', 224.30, 221.50, 225.80, 220.10, 600,  10000, '$3.44T'),
    ('TSLA',  'Tesla, Inc.',        'Automotive / EV',      210.80, 218.40, 220.00, 208.50, 15,   5000,  '$670B'),
    ('MSFT',  'Microsoft Corp',     'Cloud & Software',     448.20, 445.00, 450.50, 443.20, 300,  10000, '$3.33T'),
    ('AMZN',  'Amazon.com Inc.',    'E-Commerce / Cloud',   186.40, 184.20, 187.90, 183.50, 400,  10000, '$1.94T'),
    ('GOOGL', 'Alphabet Inc.',      'Internet & AI',        174.90, 176.50, 177.20, 173.80, 500,  10000, '$2.18T');

-- ============================================================
-- SEED DATA — Initial price history (30 data points per stock)
-- ============================================================
DO $$
DECLARE
    s RECORD;
    i INT;
    p NUMERIC(12,2);
BEGIN
    FOR s IN SELECT stock_id, current_price FROM stocks LOOP
        p := s.current_price;
        FOR i IN REVERSE 30..1 LOOP
            p := GREATEST(1, p + (RANDOM() - 0.49) * p * 0.007);
            INSERT INTO price_history (stock_id, price, volume, recorded_at)
            VALUES (
                s.stock_id,
                ROUND(p, 2),
                (RANDOM() * 1000 + 100)::INT,
                NOW() - (i * INTERVAL '30 seconds')
            );
        END LOOP;
    END LOOP;
END $$;

-- ============================================================
-- SEED DATA — Initial portfolios for demo traders
-- (inserted directly; trigger is not involved here)
-- ============================================================
INSERT INTO portfolio_holdings (user_id, stock_id, shares_held, avg_buy_price) VALUES
    (1, 'NVDA', 20, 120.00),
    (1, 'AAPL', 15, 215.00),
    (1, 'MSFT',  5, 430.00),
    (3, 'TSLA', 10, 200.00),
    (4, 'GOOGL', 8, 170.00);

-- Deduct portfolio cost from pranav_trader's cash
UPDATE users
SET cash_balance = 50000.00 - (20*120.00) - (15*215.00) - (5*430.00)
WHERE user_id = 1;

-- Reduce available_shares to reflect pre-seeded user holdings
-- (maintains: available_shares + SUM(portfolio) = total_supply)
UPDATE stocks SET available_shares = available_shares - 20 WHERE stock_id = 'NVDA';
UPDATE stocks SET available_shares = available_shares - 15 WHERE stock_id = 'AAPL';
UPDATE stocks SET available_shares = available_shares -  5 WHERE stock_id = 'MSFT';
UPDATE stocks SET available_shares = available_shares - 10 WHERE stock_id = 'TSLA';
UPDATE stocks SET available_shares = available_shares -  8 WHERE stock_id = 'GOOGL';

-- ============================================================
-- Verify setup
-- ============================================================
SELECT 'Setup complete!'  AS status;
SELECT 'Users:'           AS info, COUNT(*) AS count FROM users;
SELECT 'Stocks:'          AS info, COUNT(*) AS count FROM stocks;
SELECT 'Price history:'   AS info, COUNT(*) AS count FROM price_history;
SELECT 'Portfolio rows:'  AS info, COUNT(*) AS count FROM portfolio_holdings;
