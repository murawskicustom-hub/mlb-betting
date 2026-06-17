"""
analyzer.py -- turns per-book edges into recommendations written to the DB.

Entry points:
    generate_recommendations_for_game(conn, game_pk) -> list[int]   [Algo 1: devig]
    generate_all_recommendations(conn) -> dict                       [Algo 1: devig, all games]
    generate_model_recommendations(conn) -> dict                     [Algo 2: model_v1]
"""

import math
from datetime import datetime, timezone
from logger import get_logger
from devig import edge_percent, american_to_decimal, american_to_probability, probability_to_american
from consensus import find_edges_for_game, get_latest_snapshots, compute_consensus_fair_price

log = get_logger('analyzer')

REFERENCE_BANKROLL = 2500.0
MARKETS_TO_ANALYZE = ('moneyline', 'total', 'f5_moneyline', 'f5_total')  # spreads excluded

EDGE_MOVE_THRESHOLD = 0.5   # percentage points, dedup threshold

# F5 model parameters (used in Algo 2 totals probability)
F5_MAX_LINE    = 9.0   # filter implausible alternate/F5 total lines
F5_SIGMA_TOTAL = 2.5   # empirical std dev of 5-inning run totals (full-game ~3.5; scaled sqrt(5/9)~2.6)


# ============================================================================ #
# Algo 1 (devig) helpers
# ============================================================================ #

def classify_color(edge_pct: float, market: str = 'moneyline') -> tuple[str, int]:
    """
    Returns (confidence_color, is_shadow).
    'none' = don't write a recommendation at all.
    Thresholds: >= 4.0% green, >= 2.0% yellow, >= 0.5% red.

    F5 HIGH-BAR RULE (original design decision): F5 markets (f5_moneyline, f5_total)
    require a green threshold to surface as bettable. Yellow and red F5 edges are
    shadow-only — thinner book coverage and fewer innings of luck regression mean
    the signal is not strong enough to stake below the green bar.
    Only F5 green (edge >= 4%) writes is_shadow=0.
    """
    is_f5 = market in ('f5_moneyline', 'f5_total')
    if edge_pct >= 4.0:
        return ('green', 0)
    elif edge_pct >= 2.0:
        return ('yellow', 1 if is_f5 else 0)
    elif edge_pct >= 0.5:
        return ('red', 1)
    else:
        return ('none', 1)


def recommended_stake_pct(color: str) -> float:
    return {'green': 0.02, 'yellow': 0.01, 'red': 0.0}.get(color, 0.0)


def _best_price_for_side(edges: list[dict], side: str) -> dict | None:
    candidates = [e for e in edges if e['side'] == side]
    if not candidates:
        return None
    return max(candidates, key=lambda e: american_to_decimal(e['offered_price_american']))


def _existing_ungraded_rec(conn, game_pk: int, market: str,
                            side: str, line, algo: str = 'devig') -> dict | None:
    """Return the most-recently generated ungraded rec for this combination."""
    row = conn.execute("""
        SELECT id, edge_percent
        FROM recommendations
        WHERE game_pk = ? AND market = ? AND side = ?
          AND (line IS NULL AND ? IS NULL OR line = ?)
          AND result IS NULL
          AND algo = ?
        ORDER BY generated_at_utc DESC
        LIMIT 1
    """, (game_pk, market, side, line, line, algo)).fetchone()
    return dict(row) if row else None


def generate_recommendations_for_game(conn, game_pk: int) -> tuple[list[int], int]:
    """
    Analyze one game's odds. Write devig recommendation rows for any +EV sides.
    Returns (inserted_ids, f5_thin_skips).
    """
    inserted_ids = []
    f5_thin_skips = 0
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    for market in MARKETS_TO_ANALYZE:
        edges = find_edges_for_game(conn, game_pk, market)
        if not edges:
            # For F5, log whether there were snapshots but consensus was too thin
            if market in ('f5_moneyline', 'f5_total'):
                snaps = get_latest_snapshots(conn, game_pk, market)
                if snaps:
                    f5_thin_skips += 1
                    log.info(
                        f'game_pk={game_pk} {market}: {len(snaps)} snapshots, '
                        f'thin consensus (<4 books) — skipped'
                    )
            continue

        sides = list({e['side'] for e in edges})

        for side in sides:
            best = _best_price_for_side(edges, side)
            if best is None:
                continue

            best_ep = edge_percent(
                best['offered_price_american'],
                best['fair_price_american']
            )

            color, is_shadow = classify_color(best_ep, market=market)
            if color == 'none':
                continue

            # DUAL-AXIS GREEN FLOOR (v2 recalibration, 2026-06-17).
            # Same edge-only mislabeling as Algo 2: greens on sub-50% fair
            # probability (longshots) lost — 4 graded greens at fair_prob < 0.50
            # won 25% (-3.56u), vs 9 greens at 50%+ winning 77.8% (+6.56u).
            # A green must clear an edge bar AND a confidence bar: edge >= 4%
            # (enforced above) AND consensus fair_probability >= 0.50. High-edge
            # longshots below the floor demote to RED shadow (kept for the
            # control-group ledger, not bettable).
            if color == 'green' and best.get('fair_probability', 1.0) < 0.50:
                color, is_shadow = 'red', 1

            existing = _existing_ungraded_rec(conn, game_pk, market, side,
                                              best.get('line'), algo='devig')
            if existing is not None:
                if abs(best_ep - existing['edge_percent']) <= EDGE_MOVE_THRESHOLD:
                    log.debug(
                        f'game_pk={game_pk} {market}/{side}: '
                        f'edge unchanged ({best_ep:.2f}% vs {existing["edge_percent"]:.2f}%), '
                        f'skipping duplicate'
                    )
                    continue

            stake_pct    = recommended_stake_pct(color)
            stake_dollars = round(stake_pct * REFERENCE_BANKROLL, 2)

            conn.execute("""
                INSERT INTO recommendations (
                    game_pk, generated_at_utc, market, side, line,
                    target_price_american, fair_price_american,
                    edge_percent, confidence_color,
                    recommended_stake_pct, recommended_stake_dollars_at_2500,
                    is_shadow, num_books_in_consensus, algo
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game_pk, now_utc, market, side, best.get('line'),
                best['offered_price_american'], best['fair_price_american'],
                round(best_ep, 4), color,
                stake_pct, stake_dollars,
                is_shadow, best['num_books_in_consensus'], 'devig',
            ))

            rec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            inserted_ids.append(rec_id)

            shadow_note = ' [SHADOW]' if is_shadow else ''
            log.info(
                f'Rec #{rec_id} [devig]: game_pk={game_pk} {market}/{side}'
                + (f' line={best["line"]}' if best.get('line') else '')
                + f' | best={best["offered_price_american"]:+d} '
                f'fair={best["fair_price_american"]:+d} '
                f'edge={best_ep:.2f}% [{color.upper()}]{shadow_note}'
            )

            if is_shadow == 0:
                try:
                    from notify import send_algo1_alert
                    game_row = conn.execute(
                        'SELECT away_team, home_team, game_date, game_datetime_utc '
                        'FROM games WHERE game_pk=?', (game_pk,)
                    ).fetchone()
                    send_algo1_alert(
                        {
                            'market': market, 'side': side,
                            'line': best.get('line'),
                            'target_price_american': best['offered_price_american'],
                            'fair_price_american':   best['fair_price_american'],
                            'edge_percent':          round(best_ep, 4),
                            'confidence_color':      color,
                            'recommended_stake_dollars_at_2500': stake_dollars,
                            'recommended_stake_pct':             stake_pct,
                            'num_books_in_consensus': best['num_books_in_consensus'],
                        },
                        dict(game_row) if game_row else {},
                    )
                except Exception as _notify_exc:
                    log.warning(f'Discord notify failed: {_notify_exc}')

    return inserted_ids, f5_thin_skips


def generate_all_recommendations(conn) -> dict:
    """Analyze every upcoming game that has odds. Algo 1 (devig) only."""
    upcoming_games = conn.execute("""
        SELECT DISTINCT g.game_pk
        FROM games g
        JOIN odds_snapshots o ON o.game_pk = g.game_pk
        WHERE g.status IN ('Scheduled', 'Pre-Game', 'Warmup')
        ORDER BY g.game_datetime_utc
    """).fetchall()

    game_pks = [r['game_pk'] for r in upcoming_games]
    log.info(f'Analyzer [devig]: {len(game_pks)} upcoming game(s) with odds')

    total_written = 0
    total_f5_thin = 0
    color_market_counts: dict[str, int] = {}
    games_no_rec = []

    for game_pk in game_pks:
        ids, f5_thin = generate_recommendations_for_game(conn, game_pk)
        total_f5_thin += f5_thin
        if ids:
            total_written += len(ids)
            recs = conn.execute("""
                SELECT market, confidence_color FROM recommendations
                WHERE id IN ({})
            """.format(','.join('?' * len(ids))), ids).fetchall()
            for r in recs:
                key = f'{r["confidence_color"]}_{r["market"]}'
                color_market_counts[key] = color_market_counts.get(key, 0) + 1
        else:
            games_no_rec.append(game_pk)

    log.info(
        f'Analyzer [devig] done: {len(game_pks)} games analyzed, '
        f'{total_written} recommendations written, '
        f'{len(games_no_rec)} games produced no recommendations, '
        f'{total_f5_thin} F5 markets skipped (thin consensus)'
    )

    return {
        'games_analyzed':   len(game_pks),
        'total_written':    total_written,
        'by_color_market':  color_market_counts,
        'games_no_rec':     games_no_rec,
        'f5_thin_skipped':  total_f5_thin,
    }


# ============================================================================ #
# Algo 2 (model_v1) color classification
# ============================================================================ #

def classify_model_color(edge_pct: float) -> tuple[str, int]:
    """
    Model color thresholds (wider bands: model is unproven, calibration unknown).
    All model recs are is_shadow=1, stake=0.

    >= 8.0% -> green, >= 5.0% -> yellow, >= 3.0% -> red, < 3.0% -> none
    """
    if edge_pct >= 8.0:
        return ('green', 1)
    elif edge_pct >= 5.0:
        return ('yellow', 1)
    elif edge_pct >= 3.0:
        return ('red', 1)
    else:
        return ('none', 1)


# ============================================================================ #
# Algo 2 (model_v1) recommendation generation
# ============================================================================ #

def generate_model_recommendations(conn) -> dict:
    """
    Generate Algo 2 (model_v1) recommendations for all upcoming games.

    Rules:
    - Requires both probable pitchers with stats >= MIN_IP_FOR_SIGNAL
    - Requires consensus market odds (min 4 books, same as devig)
    - Edge threshold: >= 3.0% (model edges run hotter due to unproven calibration)
    - ALL recs: algo='model_v1', is_shadow=1, stake=0
    - Deduplication: same logic as devig (skip if edge moved <= 0.5pp)
    """
    import pytz
    from model_v1 import model_game, build_model_notes, yrfi_probability, LEAGUE_AVG_OPS
    from devig import american_to_decimal

    EASTERN = pytz.timezone('US/Eastern')
    today = datetime.now(EASTERN).strftime('%Y-%m-%d')
    now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    upcoming_games = conn.execute("""
        SELECT DISTINCT g.game_pk
        FROM games g
        JOIN odds_snapshots o ON o.game_pk = g.game_pk
        WHERE g.status IN ('Scheduled', 'Pre-Game', 'Warmup')
        ORDER BY g.game_datetime_utc
    """).fetchall()

    game_pks = [r['game_pk'] for r in upcoming_games]
    log.info(f'Analyzer [model_v1]: {len(game_pks)} upcoming game(s) with odds')

    total_written = abstained = 0
    color_market_counts: dict[str, int] = {}
    abstain_reasons: list[str] = []
    new_recs: list[dict] = []

    for game_pk in game_pks:
        result = model_game(conn, game_pk, today)

        if result is None or not result.get('data_ok'):
            reason = (result or {}).get('abstain_reason', 'no game data')
            log.info(f'  game_pk={game_pk}: model abstains -- {reason}')
            abstained += 1
            abstain_reasons.append(f'game_pk={game_pk}: {reason}')
            continue

        home_win  = result['home_win_prob']
        away_win  = result['away_win_prob']
        total_est = result['game_total_estimate']

        # Pull consensus market for comparison (need best offered price)
        game_written = 0
        f5_thin_model = 0

        for market in MARKETS_TO_ANALYZE:
            # For F5 markets, guard against model data being absent (old result format)
            if market in ('f5_moneyline', 'f5_total') and 'f5_home_win_prob' not in result:
                log.debug(f'  game_pk={game_pk} {market}: F5 model data missing, skip')
                continue

            snapshots = get_latest_snapshots(conn, game_pk, market)
            if not snapshots:
                continue
            consensus = compute_consensus_fair_price(snapshots, market)
            if consensus is None or consensus['num_books'] < 4:
                if market in ('f5_moneyline', 'f5_total'):
                    books = consensus['num_books'] if consensus else 0
                    log.info(
                        f'  game_pk={game_pk} {market}: thin F5 consensus '
                        f'({books} books), skip'
                    )
                    f5_thin_model += 1
                continue

            if market == 'moneyline':
                sides_to_check = [('home', home_win), ('away', away_win)]
            elif market == 'f5_moneyline':
                sides_to_check = [
                    ('home', result['f5_home_win_prob']),
                    ('away', result['f5_away_win_prob']),
                ]
            else:
                # total or f5_total
                is_f5_total    = (market == 'f5_total')
                sigma          = F5_SIGMA_TOTAL if is_f5_total else 3.5
                max_line_val   = F5_MAX_LINE    if is_f5_total else 13.5
                total_estimate = result.get('f5_total_estimate') if is_f5_total else total_est

                consensus_line = consensus['consensus_line']
                if consensus_line is not None and consensus_line > max_line_val:
                    continue
                if consensus_line is None or total_estimate is None:
                    continue
                over_prob  = 1 - _normal_cdf(consensus_line, total_estimate, sigma=sigma)
                under_prob = _normal_cdf(consensus_line, total_estimate, sigma=sigma)
                sides_to_check = [('over', over_prob), ('under', under_prob)]

            for side, model_prob in sides_to_check:
                # Find best offered price for this side from any book
                best_snap = None
                best_dec  = 0.0
                for snap in snapshots:
                    ot = snap['outcome_type']
                    if ot != side:
                        continue
                    if market in ('total', 'f5_total') and snap.get('line') != consensus.get('consensus_line'):
                        continue
                    dec = american_to_decimal(snap['price_american'])
                    if dec > best_dec:
                        best_dec  = dec
                        best_snap = snap

                if best_snap is None:
                    continue

                offered_american = best_snap['price_american']

                # Totals calibration gate: applies to both full-game and F5 totals.
                # Clamp model probability to market ± 8pp before computing edge.
                # ML (moneyline, f5_moneyline) is untouched. Raw value logged for audit.
                totals_clamp_note = ''
                if market in ('total', 'f5_total'):
                    mkt_prob = 1.0 / best_dec
                    raw_prob = model_prob
                    MAX_TOTALS_DEVIATION = 0.08
                    if abs(raw_prob - mkt_prob) > MAX_TOTALS_DEVIATION:
                        clamped_prob = mkt_prob + math.copysign(MAX_TOTALS_DEVIATION, raw_prob - mkt_prob)
                        totals_clamp_note = (
                            f'; totals clamp: raw {raw_prob:.0%} -> {clamped_prob:.0%}'
                            f' (mkt {mkt_prob:.0%})'
                        )
                        model_prob = clamped_prob
                        log.debug(
                            f'  game_pk={game_pk} {market}/{side}: '
                            f'clamped {raw_prob:.3f} -> {clamped_prob:.3f} '
                            f'(mkt {mkt_prob:.3f})'
                        )

                model_ep = round((model_prob * best_dec - 1) * 100, 4)

                color, is_shadow = classify_model_color(model_ep)
                if color == 'none':
                    continue

                # DUAL-AXIS GREEN FLOOR (v2 recalibration, 2026-06-17).
                # Green was classified on edge alone, which mislabeled high-edge
                # low-confidence longshots as high-conviction. Calibration on
                # graded greens showed: model_probability < 0.60 won only 44.4%
                # over 75 picks (-7.60u), while 60%+ greens won 69-72% and were
                # profitable. A "green" must clear BOTH an edge bar AND a
                # confidence bar: edge >= 8.0% (already enforced above) AND
                # model_probability >= 0.60. Picks with the edge but sub-0.60
                # probability demote to RED shadow — kept in the ledger as
                # control-group data, never bettable.
                if color == 'green' and model_prob < 0.60:
                    color, is_shadow = 'red', 1

                # Dedup
                existing = _existing_ungraded_rec(conn, game_pk, market, side,
                                                  consensus.get('consensus_line'),
                                                  algo='model_v1')
                if existing is not None:
                    if abs(model_ep - existing['edge_percent']) <= EDGE_MOVE_THRESHOLD:
                        log.debug(
                            f'  game_pk={game_pk} model {market}/{side}: '
                            f'edge unchanged ({model_ep:.2f}%), skipping'
                        )
                        continue

                # Fair price from model probability
                model_fair_american = probability_to_american(model_prob)
                notes = build_model_notes(result, market, side) + totals_clamp_note


                conn.execute("""
                    INSERT INTO recommendations (
                        game_pk, generated_at_utc, market, side, line,
                        target_price_american, fair_price_american,
                        edge_percent, confidence_color,
                        recommended_stake_pct, recommended_stake_dollars_at_2500,
                        is_shadow, num_books_in_consensus,
                        algo, model_probability, model_notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    game_pk, now_utc, market, side,
                    consensus.get('consensus_line'),
                    offered_american, model_fair_american,
                    model_ep, color,
                    0.01, 25.0,
                    1, consensus['num_books'],
                    'model_v1', round(model_prob, 4), notes,
                ))

                rec_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                game_written += 1
                total_written += 1
                new_recs.append({
                    'game_pk': game_pk, 'market': market, 'side': side,
                    'line': consensus.get('consensus_line'),
                    'target_price_american': offered_american,
                    'fair_price_american': model_fair_american,
                    'edge_percent': model_ep,
                    'confidence_color': color,
                    'model_probability': round(model_prob, 4),
                })

                key = f'{color}_{market}'
                color_market_counts[key] = color_market_counts.get(key, 0) + 1

                log.info(
                    f'Rec #{rec_id} [model_v1]: game_pk={game_pk} {market}/{side}'
                    + (f' line={consensus.get("consensus_line")}' if consensus.get('consensus_line') else '')
                    + f' | offered={offered_american:+d} '
                    f'model_prob={model_prob:.3f} edge={model_ep:.2f}% [{color.upper()}]'
                    + f' | {notes}'
                )

    # ── Second pass: F5 paper picks + YRFI/NRFI (no market odds needed) ─────────
    _PAPER_INSERT = """
        INSERT INTO recommendations (
            game_pk, generated_at_utc, market, side, line,
            target_price_american, fair_price_american,
            edge_percent, confidence_color,
            recommended_stake_pct, recommended_stake_dollars_at_2500,
            is_shadow, num_books_in_consensus,
            algo, model_probability, model_notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    all_games_for_yrfi = conn.execute("""
        SELECT DISTINCT g.game_pk
        FROM games g
        WHERE g.status IN ('Scheduled', 'Pre-Game', 'Warmup')
        ORDER BY g.game_datetime_utc
    """).fetchall()
    all_game_pks = [r['game_pk'] for r in all_games_for_yrfi]

    for gp in all_game_pks:
        result = model_game(conn, gp, today)
        if result is None or not result.get('data_ok'):
            continue

        hp    = result['home_pitcher_q']
        ap    = result['away_pitcher_q']
        pf    = result['park_factor']
        h_off = result['home_offense']
        a_off = result['away_offense']

        # ── F5 ML paper picks ─────────────────────────────────────────────────
        f5_home = result.get('f5_home_win_prob', 0.0)
        f5_away = result.get('f5_away_win_prob', 0.0)

        for f5_side, f5_prob in (('home', f5_home), ('away', f5_away)):
            if f5_prob >= 0.70:
                f5_color = 'green'
            elif f5_prob >= 0.60:
                f5_color = 'yellow'
            else:
                continue

            if _existing_ungraded_rec(conn, gp, 'f5_moneyline', f5_side, None, algo='model_v1') is not None:
                continue

            fair_am  = probability_to_american(f5_prob)
            f5_notes = build_model_notes(result, 'f5_moneyline', f5_side)

            conn.execute(_PAPER_INSERT, (
                gp, now_utc, 'f5_moneyline', f5_side, None,
                fair_am, fair_am, 0.0, f5_color,
                0.01, 25.0, 1, 0,
                'model_v1', round(f5_prob, 4), f5_notes,
            ))
            total_written += 1
            new_recs.append({
                'game_pk': gp, 'market': 'f5_moneyline', 'side': f5_side,
                'line': None, 'target_price_american': fair_am,
                'fair_price_american': fair_am, 'edge_percent': 0.0,
                'confidence_color': f5_color, 'model_probability': round(f5_prob, 4),
            })
            k = f'{f5_color}_f5_moneyline_paper'
            color_market_counts[k] = color_market_counts.get(k, 0) + 1
            log.info(
                f'Paper F5 rec: game_pk={gp} f5_moneyline/{f5_side} '
                f'prob={f5_prob:.3f} [{f5_color.upper()}]'
            )

        # ── YRFI / NRFI picks ─────────────────────────────────────────────────
        yrfi_prob = yrfi_probability(hp, ap, h_off, a_off, pf)

        h_fip_str = f'{hp["regressed_fip"]:.2f}'
        a_fip_str = f'{ap["regressed_fip"]:.2f}'
        yrfi_notes = (
            f'YRFI est {yrfi_prob:.0%} · away_sp {a_fip_str} FIP · '
            f'home_sp {h_fip_str} FIP · park {pf:.2f}'
        )

        if yrfi_prob >= 0.60:
            color = 'green' if yrfi_prob >= 0.68 else 'yellow'
            if _existing_ungraded_rec(conn, gp, 'yrfi', 'yrfi', None, algo='model_v1') is None:
                fair_am = probability_to_american(yrfi_prob)
                conn.execute(_PAPER_INSERT, (
                    gp, now_utc, 'yrfi', 'yrfi', None,
                    fair_am, fair_am, 0.0, color,
                    0.01, 25.0, 1, 0,
                    'model_v1', round(yrfi_prob, 4), yrfi_notes,
                ))
                total_written += 1
                new_recs.append({
                    'game_pk': gp, 'market': 'yrfi', 'side': 'yrfi',
                    'line': None, 'target_price_american': fair_am,
                    'fair_price_american': fair_am, 'edge_percent': 0.0,
                    'confidence_color': color, 'model_probability': round(yrfi_prob, 4),
                })
                k = f'{color}_yrfi'
                color_market_counts[k] = color_market_counts.get(k, 0) + 1
                log.info(f'YRFI rec: game_pk={gp} prob={yrfi_prob:.3f} [{color.upper()}]')

        elif yrfi_prob <= 0.40:
            nrfi_prob = round(1.0 - yrfi_prob, 4)
            color = 'green' if yrfi_prob <= 0.32 else 'yellow'
            if _existing_ungraded_rec(conn, gp, 'nrfi', 'nrfi', None, algo='model_v1') is None:
                fair_am = probability_to_american(nrfi_prob)
                conn.execute(_PAPER_INSERT, (
                    gp, now_utc, 'nrfi', 'nrfi', None,
                    fair_am, fair_am, 0.0, color,
                    0.01, 25.0, 1, 0,
                    'model_v1', round(nrfi_prob, 4), yrfi_notes,
                ))
                total_written += 1
                new_recs.append({
                    'game_pk': gp, 'market': 'nrfi', 'side': 'nrfi',
                    'line': None, 'target_price_american': fair_am,
                    'fair_price_american': fair_am, 'edge_percent': 0.0,
                    'confidence_color': color, 'model_probability': round(nrfi_prob, 4),
                })
                k = f'{color}_nrfi'
                color_market_counts[k] = color_market_counts.get(k, 0) + 1
                log.info(f'NRFI rec: game_pk={gp} nrfi_prob={nrfi_prob:.3f} [{color.upper()}]')

    log.info(
        f'Analyzer [model_v1] done: {len(game_pks)} games (market), '
        f'{len(all_game_pks)} games (paper), '
        f'{total_written} recs written, {abstained} abstained'
    )

    return {
        'games_analyzed':   len(game_pks),
        'total_written':    total_written,
        'abstained':        abstained,
        'abstain_reasons':  abstain_reasons,
        'by_color_market':  color_market_counts,
        'new_recs':         new_recs,
    }


# ============================================================================ #
# Helper: normal CDF approximation for totals probability
# ============================================================================ #

def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    """Approximation of P(X <= x) for Normal(mu, sigma)."""
    z = (x - mu) / (sigma * math.sqrt(2))
    return 0.5 * (1 + _erf(z))


def _erf(x: float) -> float:
    """Abramowitz & Stegun approximation for erf, error < 1.5e-7."""
    t  = 1.0 / (1.0 + 0.3275911 * abs(x))
    y  = (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
           - 0.284496736) * t + 0.254829592) * t
    result = 1.0 - y * math.exp(-x * x)
    return result if x >= 0 else -result
