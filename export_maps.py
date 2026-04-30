"""Load optional DHIS2 CSV export maps by dataset UID.

Drop files named dataset_exports_<dataset_uid>.csv in the project root.
These maps are used as supplemental matching during compare.
"""
import csv
import os
from typing import Dict

BASE_DIR = os.path.dirname(__file__)


def _norm(text: str) -> str:
    return ' '.join((text or '').strip().lower().split())


def _get_col(row: dict, *names: str) -> str:
    for name in names:
        if name in row:
            return str(row.get(name) or '').strip()
    return ''


def load_dataset_export_map(dataset_uid: str) -> dict:
    """Load mapping data from dataset_exports/<dataset_uid>.csv if present.

    Returns structure:
    {
      'uid_map': {deUID|cocUID: value},
      'name_cocuid_map': {deNameNorm|cocUID: value},
      'name_cocname_map': {deNameNorm|cocNameNorm: value},
      'row_count': int,
      'exists': bool,
    }
    """
    path = os.path.join(BASE_DIR, f'dataset_exports_{dataset_uid}.csv')
    result = {
        'uid_map': {},
        'name_cocuid_map': {},
        'name_cocname_map': {},
        'row_count': 0,
        'exists': os.path.exists(path),
    }

    if not result['exists']:
        return result

    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Common DHIS2 CSV export header variants.
            de = _get_col(row, 'dataelement', 'dataElement', 'Data element', 'deUID', 'DE UID')
            coc = _get_col(row, 'categoryoptioncombo', 'catoptcombo', 'categoryOptionCombo', 'Category option combo', 'cocUID', 'COC UID')
            value = _get_col(row, 'value', 'Value', 'localValue')
            if value == '':
                continue

            result['row_count'] += 1

            # UID style key if CSV provides IDs.
            if de:
                result['uid_map'][f"{de}|{coc}"] = value

            # Name + COC UID key when dataelement is a name and COC is a UID.
            de_norm = _norm(de)
            if de_norm and coc:
                result['name_cocuid_map'][f"{de_norm}|{coc}"] = value

            # Name + COC name key (if both are textual names in export).
            coc_norm = _norm(coc)
            if de_norm and coc_norm:
                result['name_cocname_map'][f"{de_norm}|{coc_norm}"] = value

    return result
