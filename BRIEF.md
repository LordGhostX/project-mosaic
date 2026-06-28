# Mosaic Strategy

**Copy the signal, not the personality.**

**Collective trader intelligence, executed with risk control.**

## Overview

Mosaic Strategy is a Hyperliquid-native strategy platform that turns the behavior of proven traders into risk-managed trading strategies.

Users do not copy individual traders. They select a Mosaic strategy with a defined mandate, risk profile, execution mode, and rebalance cadence.

Mosaic discovers high-signal traders, filters out noise, aggregates qualified behavior, and converts that intelligence into executable market exposure.

Users can participate through:

1. **Mosaic Vaults**  
   Users deposit into a Mosaic-managed strategy vault.

2. **Mosaic Connected Wallet**  
   Users connect their wallet or Hyperliquid account and allow Mosaic to execute strategy-driven trades directly in their account, subject to permissions and risk limits.

Mosaic is not “copy this trader.”

It is a risk-managed strategy layer powered by selected trader intelligence.

## Problem

Copy trading forces users to pick traders from noisy leaderboards.

Most users cannot tell whether a trader’s performance is repeatable, leverage-driven, lucky, manipulated, or already decaying. They chase recent winners, overallocate to risky traders, and lose confidence when performance reverses.

Existing copy-trading products focus on personalities, rankings, and recent PnL. They rarely answer the questions that matter:

- Is the trader’s edge repeatable?
- Is performance real after fees, funding, and slippage?
- Is leverage being used safely?
- Can the strategy scale?
- Is the trader taking hidden tail risk?
- Is the signal improving or decaying?

There is valuable trader intelligence onchain and on Hyperliquid, but it is fragmented, noisy, and difficult to use.

## Solution

Replace trader selection with strategy selection.

Mosaic Strategy:

- Discovers strong Hyperliquid traders
- Scores them by performance, risk, consistency, and capacity
- Filters out excessive leverage, suspicious behavior, and one-off winners
- Aggregates qualified behavior into target exposures
- Converts trader behavior into strategy-level signals
- Executes through managed vaults or connected-wallet automation
- Applies risk controls before execution
- Removes or reduces trader influence when behavior deteriorates

The goal is not to blindly copy trades.

The goal is to extract useful signal from trader behavior and convert it into controlled strategy exposure.

## Why Mosaic

The name **Mosaic** works because the strategy is built from many carefully selected pieces.

One trader may be noisy, risky, lucky, or hard to evaluate. A filtered group of traders can produce a stronger, more stable signal.

Mosaic does not sell trader personalities.

It builds investable strategies from fragmented trader intelligence.

## Why Hyperliquid

Hyperliquid is the right first venue because it has:

- Active perp markets
- Public trader behavior
- Wallet-level trading data
- Native vault mechanics
- Leaderboards and vault pages
- API and WebSocket support
- A strong perp-native user base

This makes it well suited for trader discovery, paper strategy research, live monitoring, vault execution, and connected-wallet automation.

## Core Product

Mosaic should launch Hyperliquid-only with one or two strategies.

The first version should focus on research and paper trading before live execution. Over time, Mosaic can support both pooled strategy vaults and connected-wallet execution.

The initial strategies should be simple, understandable, and differentiated by time horizon and risk level.

## Product Modes

### 1. Mosaic Vaults

Users deposit into a Mosaic-managed strategy vault.

The vault handles:

- Capital pooling
- Strategy execution
- Position sizing
- Rebalancing
- Risk management
- Reporting
- Capacity limits
- Withdrawals

This mode is best for users who want simple, passive exposure to a Mosaic strategy.

### 2. Mosaic Connected Wallet

Users connect their wallet or Hyperliquid account and allow Mosaic to execute strategy trades directly in their account.

The user chooses:

- Strategy
- Allocation size
- Max leverage
- Max drawdown
- Max position size
- Asset restrictions
- Emergency stop settings
- Automatic or approval-based execution

Mosaic sends trades to the user’s account based on strategy-level target exposures.

This mode should use minimum required permissions and should never require withdrawal permissions.

## Initial Strategies

### Mosaic Weekly

Mosaic Weekly captures repeatable swing-trading behavior with lower churn and better capacity than short-term copy strategies.

Focus areas:

- 4-week and 12-week returns
- Drawdown and recovery
- Funding impact
- Sizing discipline
- Asset concentration
- Fee-adjusted turnover
- Trader behavior decay
- Capacity-adjusted performance

Mosaic Weekly should rebalance weekly, with emergency de-risking when risk thresholds are breached.

**Positioning:**  
A higher-activity strategy built from repeatable short-to-medium-term trader behavior.

### Mosaic Monthly

Mosaic Monthly focuses on durable trader quality, lower turnover, stable leverage, and survival across market regimes.

Focus areas:

- 3-month to 12-month performance
- Liquidation avoidance
- Low drawdown
- Stable margin usage
- Lower beta dependency
- Tail-risk control
- Lower turnover
- Capacity preservation

**Positioning:**  
A lower-turnover strategy built from durable trader quality and risk discipline.

## How It Works

### 1. Discover Traders

Mosaic monitors Hyperliquid leaderboards, vault managers, public wallets, fills, positions, funding, margin usage, liquidations, and account equity.

The system looks for repeatable edge, not just recent high returns.

### 2. Filter Traders

Mosaic excludes weak or unsafe profiles, including:

- One-trade winners
- Extreme leverage users
- Recent liquidations
- Fresh-wallet reset patterns
- Illiquid market wins
- Suspicious transfer behavior
- Excessively concentrated bets
- Strategies that cannot scale
- Traders whose performance deteriorates after selection

### 3. Score Trader Quality

Mosaic scores traders across:

- Performance
- Drawdown
- Leverage discipline
- Consistency
- Funding impact
- Slippage sensitivity
- Market capacity
- Correlation to other traders
- Performance after fees and funding
- Behavior stability
- Recovery after losses
- Exposure quality

The goal is not to find the highest-return trader.  
The goal is to find traders whose behavior can improve a strategy after real execution costs.

### 4. Aggregate Signals

Mosaic uses signal replication, not trade-by-trade copying.

It reads selected trader behavior, estimates aggregate target exposures, nets conflicts, applies risk limits, and produces a clean strategy portfolio.

Mosaic asks:

- What assets are qualified traders accumulating?
- Which direction is the high-quality cohort leaning?
- How much conviction is visible?
- Which signals are crowded, stale, or too risky?
- Which exposures survive fees, funding, and slippage?
- Which trades fit the selected risk profile?

This captures trader intelligence while reducing noise, leverage risk, and unnecessary churn.

### 5. Execute With Risk Controls

Mosaic executes only after risk checks.

Execution can happen through:

- Mosaic-managed vaults
- Connected-wallet automation
- Approval-based trade suggestions in a later version

Risk controls include:

- Max leverage
- Max drawdown
- Max liquidation proximity
- Max exposure per asset
- Max directional beta
- Max trader contribution
- Max trade size versus market depth
- Capacity caps
- Emergency de-risking
- Trade pause authority
- User-level stop settings
- Strategy-level kill switches

The execution layer should prioritize controlled exposure over perfect replication.

## Connected Wallet Experience

The connected-wallet experience should be simple and safe.

A user should be able to:

1. Visit **Mosaic Strategy**
2. Connect their wallet or Hyperliquid account
3. Choose a Mosaic strategy
4. Select allocation amount
5. Set risk limits
6. Review permissions
7. Activate strategy execution
8. Monitor trades, exposure, PnL, drawdown, and risk status

The interface should show:

- Strategy mandate
- Current exposure
- Open positions
- Recent trades
- Historical performance
- Max drawdown
- User-defined risk limits
- Current leverage
- Liquidation proximity
- Capacity status
- Fees, funding, and execution costs
- Strategy status
- Pause or disconnect controls

Users should always understand what Mosaic can and cannot do with their account.

**Trust principle:** Mosaic should never ask for withdrawal permissions.

## User Experience

The UX should stay simple.

Users choose:

- Strategy
- Time horizon
- Risk level
- Allocation amount
- Execution mode

Execution modes:

- Deposit into Mosaic Vaults
- Connect wallet for automated execution
- Connect wallet for approval-based execution, if supported later

Each strategy should show:

- Mandate
- Current exposure
- Historical performance
- Max drawdown
- Capacity status
- Last rebalance summary
- Risk status
- Fees, funding, and withdrawal terms
- Connected-wallet permissions
- Emergency stop controls

Avoid raw trader leaderboards, entertainment-style trader profiles, hidden leverage, hidden fees, guaranteed-yield language, and personality-led marketing.

## Key Differentiator

Traditional copy trading copies traders.

Mosaic extracts intelligence from trader behavior.

The product does not sell personalities. It builds filtered, risk-managed strategies from the collective behavior of proven traders.

Mosaic’s moat is the trader intelligence engine:

- Discovery
- Filtering
- Scoring
- Signal aggregation
- Execution
- Risk control
- Performance history
- Wallet-level monitoring
- Strategy-level reporting
- User-specific risk settings

Mosaic is not a leaderboard.

It is a strategy execution layer for collective trader intelligence.

## Business Model

Mosaic should keep monetization simple and transparent.

Revenue can come from:

- **Performance fee** on strategy gains
- **Execution fee** for connected-wallet automation
- **Subscription fees** for Hyperliquid data access

Fees should be easy to understand and clearly disclosed before users allocate capital or enable execution.

Avoid hidden costs, complicated fee stacks, and pricing that makes users feel performance is being quietly eroded.

## Validation Roadmap

### Phase 1: Paper Strategies

Build Hyperliquid-only paper strategies for Mosaic Weekly and Mosaic Monthly.

Success means:

- Strategies beat simple baselines after fees, funding, and slippage
- Drawdowns are explainable
- Trader turnover is manageable
- Capacity can support the business case
- Signal decay can be measured
- Strategy behavior is understandable

### Phase 2: Internal Capital

Trade small internal capital.

Success means:

- Live execution matches paper assumptions
- Slippage is controlled
- Risk systems work
- PnL reconciliation is reliable
- Emergency de-risking works
- Funding and fees are accurately modeled

### Phase 3: Gated Mosaic Vault

Launch one capped Mosaic Vault to a limited user group.

Success means:

- Users understand the mandate
- Deposits stay within capacity
- Reporting builds trust
- Risk remains within stated limits
- Withdrawals do not create excessive slippage

### Phase 4: Connected Wallet Beta

Launch connected-wallet execution for a small group of experienced users.

Success means:

- Users understand permissions
- Trades execute correctly in user accounts
- Risk limits are respected
- Users can pause or disconnect easily
- Strategy-level execution remains consistent across accounts
- Support burden remains manageable

### Phase 5: Public Launch

Launch publicly only after both vault and connected-wallet execution have been tested with real capital, real costs, real slippage, and real user behavior.

The public product should start capped.

Capacity discipline should be part of the brand.

## Major Risks

- Trader edge disappears after being replicated
- Selected traders were lucky, not skilled
- Strategy flow crowds trades
- Leverage creates severe drawdowns
- Wallets are fragmented or misleading
- Hyperliquid outages or API issues affect execution
- Withdrawals create slippage
- Connected-wallet permissions create security concerns
- Users misunderstand automated execution
- Strategy execution differs across user accounts
- Users override risk settings and blame the product
- The product may require investment-management, advisory, or copy-trading legal review

## Risk and Trust Principles

Mosaic should be built around trust.

Core principles:

- No guaranteed returns
- No hidden leverage
- No withdrawal permissions
- No personality-led trader promotion
- Clear risk limits
- Clear fee disclosure
- Clear execution permissions
- Clear drawdown reporting
- Clear pause and exit controls
- Real performance after fees, funding, and slippage

The product should feel like controlled strategy infrastructure, not a gambling interface.

## Final Recommendation

Proceed with **Mosaic Strategy** as the product name and **mosaicstrategy.xyz** as the primary domain.

Start with a Hyperliquid-only strategy platform. Validate Mosaic Weekly and Mosaic Monthly through paper trading, then trade small internal capital, then launch one capped Mosaic Vault, and only then expand into connected-wallet execution for a limited beta.

Strongest positioning:

**Mosaic Strategy turns collective Hyperliquid trader intelligence into risk-managed strategy execution.**

Simpler version:

**Copy the signal, not the personality.**

Product structure:

- **Mosaic Weekly** — higher-activity strategy based on repeatable swing-trading behavior
- **Mosaic Monthly** — more conservative strategy based on durable trader quality
- **Mosaic Vaults** — pooled strategy products
- **Mosaic Connected Wallet** — user-account strategy automation

Mosaic should become the trusted strategy layer between noisy trader behavior and controlled user execution.
