"""Verifica que list_unnotified() funciona após migração."""
from bugfinder.storage import Storage
from bugfinder.config import CONFIG

s = Storage(CONFIG.db_full_path)
rows = s.list_unnotified(min_roi_pct=15.0, min_match_confidence=0.5, limit=5)
print(f"unnotified candidates: {len(rows)}")
for r in rows:
    roi = r["via_roi_pct"]
    conf = r["ml_match_confidence"]
    title = r["title"][:60]
    print(f"  #{r['id']} ROI={roi:.0f}% match={conf*100:.0f}% — {title}")
