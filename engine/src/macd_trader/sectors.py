"""Sector classification for the F&O spot universe.

Mapping follows NSE/AMFI-style industry groups, hand-maintained for the ~210
F&O names the terminal watches. Unmapped symbols fall into "Other" and are
surfaced by the RRG endpoint so a drifting universe is visible, not silent.
"""
from __future__ import annotations

SECTOR_MAP: dict[str, str] = {}

_GROUPS: dict[str, list[str]] = {
    "Banks": [
        "AUBANK", "AXISBANK", "BANDHANBNK", "BANKBARODA", "BANKINDIA", "CANBK",
        "FEDERALBNK", "HDFCBANK", "ICICIBANK", "IDFCFIRSTB", "INDIANB",
        "INDUSINDBK", "KOTAKBANK", "PNB", "RBLBANK", "SBIN", "UNIONBANK", "YESBANK",
    ],
    "Financial Services": [
        "360ONE", "ABCAPITAL", "ANGELONE", "BAJAJFINSV", "BAJFINANCE",
        "BAJAJHLDNG", "BSE", "CAMS", "CDSL", "CHOLAFIN", "HDFCAMC", "HDFCLIFE",
        "ICICIGI", "ICICIPRULI", "IEX", "IREDA", "IRFC", "JIOFIN", "KFINTECH",
        "LICHSGFIN", "LICI", "LTF", "MANAPPURAM", "MCX", "MFSL", "MOTILALOFS",
        "MUTHOOTFIN", "NAM-INDIA", "PAYTM", "PFC", "PNBHOUSING", "POLICYBZR",
        "RECLTD", "SBICARD", "SBILIFE", "SHRIRAMFIN",
    ],
    "IT": [
        "COFORGE", "HCLTECH", "INFY", "KPITTECH", "LTM", "MPHASIS", "OFSS",
        "PERSISTENT", "TATAELXSI", "TCS", "TECHM", "WIPRO",
    ],
    "Auto & Components": [
        "ASHOKLEY", "BAJAJ-AUTO", "BHARATFORG", "BOSCHLTD", "EICHERMOT",
        "FORCEMOT", "HEROMOTOCO", "HYUNDAI", "M&M", "MARUTI", "MOTHERSON",
        "SONACOMS", "TIINDIA", "TMPV", "TVSMOTOR", "UNOMINDA",
    ],
    "Pharma & Healthcare": [
        "ALKEM", "APOLLOHOSP", "AUROPHARMA", "BIOCON", "CIPLA", "DIVISLAB",
        "DRREDDY", "FORTIS", "GLENMARK", "LAURUSLABS", "LUPIN", "MANKIND",
        "MAXHEALTH", "SUNPHARMA", "TORNTPHARM", "ZYDUSLIFE",
    ],
    "Metals & Mining": [
        "COALINDIA", "HINDALCO", "HINDZINC", "JINDALSTEL", "JSWSTEEL",
        "NATIONALUM", "NMDC", "SAIL", "TATASTEEL", "VEDL",
    ],
    "Oil & Gas": [
        "BPCL", "GAIL", "HINDPETRO", "IOC", "OIL", "ONGC", "PETRONET", "RELIANCE",
    ],
    "Power & Utilities": [
        "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "JSWENERGY", "NHPC", "NTPC",
        "POWERGRID", "PREMIERENE", "SUZLON", "TATAPOWER", "WAAREEENER",
    ],
    "Capital Goods & Defence": [
        "ABB", "BDL", "BEL", "BHEL", "CGPOWER", "COCHINSHIP", "CUMMINSIND",
        "GVT&D", "HAL", "INOXWIND", "KAYNES", "KEI", "MAZDOCK", "POLYCAB",
        "POWERINDIA", "SIEMENS", "SOLARINDS",
    ],
    "Consumer Durables": [
        "AMBER", "BLUESTARCO", "CROMPTON", "DIXON", "HAVELLS", "PGEL", "VOLTAS",
    ],
    "FMCG": [
        "ASIANPAINT", "BRITANNIA", "COLPAL", "DABUR", "GODREJCP", "GODFRYPHLP",
        "HINDUNILVR", "ITC", "MARICO", "NESTLEIND", "PATANJALI", "RADICO",
        "TATACONSUM", "UNITDSPR", "VBL",
    ],
    "Retail & Internet": [
        "DMART", "ETERNAL", "INDHOTEL", "JUBLFOOD", "KALYANKJIL", "NYKAA",
        "NAUKRI", "PAGEIND", "SWIGGY", "TITAN", "TRENT", "VMM",
    ],
    "Transport & Logistics": [
        "ADANIPORTS", "CONCOR", "DELHIVERY", "GMRAIRPORT", "INDIGO",
    ],
    "Realty & Infra": [
        "DLF", "GODREJPROP", "LODHA", "LT", "NBCC", "OBEROIRLTY", "PHOENIXLTD",
        "PRESTIGE", "RVNL", "ADANIENT",
    ],
    "Cement & Building Materials": [
        "AMBUJACEM", "APLAPOLLO", "ASTRAL", "DALBHARAT", "GRASIM", "SHREECEM",
        "SUPREMEIND", "ULTRACEMCO",
    ],
    "Chemicals": [
        "PIDILITIND", "PIIND", "SRF", "UPL",
    ],
    "Telecom": [
        "BHARTIARTL", "IDEA", "INDUSTOWER",
    ],
}

for _sector, _names in _GROUPS.items():
    for _name in _names:
        SECTOR_MAP[_name] = _sector


def sector_of(symbol: str) -> str:
    """Sector for a Fyers spot symbol like ``NSE:HDFCBANK-EQ``."""
    root = symbol.split(":")[-1].removesuffix("-EQ").removesuffix("-INDEX")
    if symbol.endswith("-INDEX"):
        return "Indices"
    return SECTOR_MAP.get(root, "Other")
