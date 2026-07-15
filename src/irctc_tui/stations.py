"""A curated list of Indian Railways stations for the From/To dropdowns.

IRCTC has thousands of stations; a TUI dropdown can't hold them all usefully. This
is a broad, practical set — the Hyderabad↔Tirupati route stations first, then major
junctions across every zone. The **station code** (e.g. ``SC``) is what gets typed
into IRCTC's autocomplete, so codes are what we store.

Need one that isn't here? Add a ``(code, name)`` tuple below, or just type the code
into ``config.json`` — an unknown code is added to the dropdown automatically.
"""

from __future__ import annotations

# (code, name). Route-relevant stations first so they're easy to reach.
STATIONS: list[tuple[str, str]] = [
    # --- Hyderabad ⇄ Tirupati corridor (the default trip) ---
    ("SC", "Secunderabad Jn"),
    ("HYB", "Hyderabad Deccan (Nampally)"),
    ("KCG", "Kacheguda"),
    ("LPI", "Lingampalli"),
    ("CHZ", "Cherlapalli"),
    ("TPTY", "Tirupati"),
    ("RU", "Renigunta Jn"),
    ("GDR", "Gudur Jn"),
    # --- Telangana / Andhra Pradesh majors ---
    ("BZA", "Vijayawada Jn"),
    ("GNT", "Guntur Jn"),
    ("WL", "Warangal"),
    ("KZJ", "Kazipet Jn"),
    ("NLR", "Nellore"),
    ("OGL", "Ongole"),
    ("KRNT", "Kurnool City"),
    ("GTL", "Guntakal Jn"),
    ("HX", "Kadapa"),
    ("ATP", "Anantapur"),
    ("VSKP", "Visakhapatnam"),
    ("RJY", "Rajahmundry"),
    ("NDL", "Nandyal"),
    ("MBNR", "Mahbubnagar"),
    ("NZB", "Nizamabad Jn"),
    # --- South ---
    ("MAS", "MGR Chennai Central"),
    ("MS", "Chennai Egmore"),
    ("SBC", "KSR Bengaluru (Bangalore City)"),
    ("YPR", "Yesvantpur Jn"),
    ("KPD", "Katpadi Jn"),
    ("SA", "Salem Jn"),
    ("ED", "Erode Jn"),
    ("CBE", "Coimbatore Jn"),
    ("TPJ", "Tiruchchirappalli"),
    ("MDU", "Madurai Jn"),
    ("ERS", "Ernakulam Jn (Kochi)"),
    ("TVC", "Thiruvananthapuram Central"),
    ("MAQ", "Mangaluru Central"),
    ("UBL", "Hubballi Jn"),
    ("MAO", "Madgaon (Goa)"),
    # --- West ---
    ("CSMT", "Mumbai CSMT"),
    ("LTT", "Lokmanya Tilak Terminus"),
    ("MMCT", "Mumbai Central"),
    ("DR", "Dadar"),
    ("BDTS", "Bandra Terminus"),
    ("PUNE", "Pune Jn"),
    ("SUR", "Solapur Jn"),
    ("NGP", "Nagpur Jn"),
    ("NK", "Nashik Road"),
    ("ADI", "Ahmedabad Jn"),
    ("ST", "Surat"),
    ("BRC", "Vadodara Jn"),
    ("RJT", "Rajkot Jn"),
    # --- North ---
    ("NDLS", "New Delhi"),
    ("DLI", "Delhi Jn"),
    ("NZM", "Hazrat Nizamuddin"),
    ("JP", "Jaipur Jn"),
    ("JU", "Jodhpur Jn"),
    ("CDG", "Chandigarh"),
    ("ASR", "Amritsar Jn"),
    ("JAT", "Jammu Tawi"),
    ("LKO", "Lucknow (Charbagh)"),
    ("CNB", "Kanpur Central"),
    ("AGC", "Agra Cantt"),
    ("GWL", "Gwalior Jn"),
    ("BSB", "Varanasi Jn"),
    ("PRYJ", "Prayagraj Jn"),
    ("GKP", "Gorakhpur Jn"),
    # --- Central / East ---
    ("BPL", "Bhopal Jn"),
    ("INDB", "Indore Jn"),
    ("JBP", "Jabalpur"),
    ("R", "Raipur Jn"),
    ("HWH", "Howrah Jn"),
    ("SDAH", "Sealdah"),
    ("KOAA", "Kolkata"),
    ("ASN", "Asansol Jn"),
    ("DHN", "Dhanbad Jn"),
    ("PNBE", "Patna Jn"),
    ("RNC", "Ranchi"),
    ("BBS", "Bhubaneswar"),
    ("PURI", "Puri"),
    ("GHY", "Guwahati"),
    ("NJP", "New Jalpaiguri"),
]

_CODES = {code for code, _ in STATIONS}


def options(current: str = "") -> list[tuple[str, str]]:
    """Select ``(label, value)`` options. Includes ``current`` if it's unknown."""
    opts = [(f"{code} · {name}", code) for code, name in STATIONS]
    cur = (current or "").strip().upper()
    if cur and cur not in _CODES:
        opts.insert(0, (f"{cur} · (custom)", cur))
    return opts


def is_known(code: str) -> bool:
    return (code or "").strip().upper() in _CODES
