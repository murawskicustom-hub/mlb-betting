"""
nfl_reference.py — static NFL reference data shared across bots (division
alignments today; anything else league-structural belongs here too).
"""

NFL_DIVISIONS = {
    'BUF': 'AFC East',  'MIA': 'AFC East',  'NE': 'AFC East',  'NYJ': 'AFC East',
    'BAL': 'AFC North', 'CIN': 'AFC North', 'CLE': 'AFC North', 'PIT': 'AFC North',
    'HOU': 'AFC South', 'IND': 'AFC South', 'JAX': 'AFC South', 'TEN': 'AFC South',
    'DEN': 'AFC West',  'KC': 'AFC West',   'LAC': 'AFC West',  'LV': 'AFC West',
    'DAL': 'NFC East',  'NYG': 'NFC East',  'PHI': 'NFC East',  'WSH': 'NFC East',
    'CHI': 'NFC North', 'DET': 'NFC North', 'GB': 'NFC North',  'MIN': 'NFC North',
    'ATL': 'NFC South', 'CAR': 'NFC South', 'NO': 'NFC South',  'TB': 'NFC South',
    'ARI': 'NFC West',  'LAR': 'NFC West',  'SF': 'NFC West',   'SEA': 'NFC West',
}


def is_divisional(home_team: str, away_team: str) -> bool:
    return NFL_DIVISIONS.get(home_team) is not None and NFL_DIVISIONS.get(home_team) == NFL_DIVISIONS.get(away_team)
