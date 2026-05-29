"""
Maps The Odds API team name strings to the MLB Stats API team name strings
stored in our games table.

Both APIs use nearly identical names for all 30 teams as of 2026. The only
historically tricky team is the Athletics (no city prefix in either API).
This module is the single place to fix any future mismatches.
"""

# Keys   = team name as returned by The Odds API
# Values = team name as stored in our games table (from MLB Stats API)
ODDS_TO_MLB: dict[str, str] = {
    "Arizona Diamondbacks":  "Arizona Diamondbacks",
    "Athletics":             "Athletics",
    "Atlanta Braves":        "Atlanta Braves",
    "Baltimore Orioles":     "Baltimore Orioles",
    "Boston Red Sox":        "Boston Red Sox",
    "Chicago Cubs":          "Chicago Cubs",
    "Chicago White Sox":     "Chicago White Sox",
    "Cincinnati Reds":       "Cincinnati Reds",
    "Cleveland Guardians":   "Cleveland Guardians",
    "Colorado Rockies":      "Colorado Rockies",
    "Detroit Tigers":        "Detroit Tigers",
    "Houston Astros":        "Houston Astros",
    "Kansas City Royals":    "Kansas City Royals",
    "Los Angeles Angels":    "Los Angeles Angels",
    "Los Angeles Dodgers":   "Los Angeles Dodgers",
    "Miami Marlins":         "Miami Marlins",
    "Milwaukee Brewers":     "Milwaukee Brewers",
    "Minnesota Twins":       "Minnesota Twins",
    "New York Mets":         "New York Mets",
    "New York Yankees":      "New York Yankees",
    "Philadelphia Phillies": "Philadelphia Phillies",
    "Pittsburgh Pirates":    "Pittsburgh Pirates",
    "San Diego Padres":      "San Diego Padres",
    "San Francisco Giants":  "San Francisco Giants",
    "Seattle Mariners":      "Seattle Mariners",
    "St. Louis Cardinals":   "St. Louis Cardinals",
    "Tampa Bay Rays":        "Tampa Bay Rays",
    "Texas Rangers":         "Texas Rangers",
    "Toronto Blue Jays":     "Toronto Blue Jays",
    "Washington Nationals":  "Washington Nationals",
}


def translate(odds_api_name: str) -> str | None:
    """
    Return the MLB Stats API team name for a given Odds API team name.
    Returns None if the name is not in the mapping — caller should log and skip.
    """
    return ODDS_TO_MLB.get(odds_api_name)
