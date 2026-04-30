"""Debug DHIS2 API — check what's actually coming back."""
import json
import sys

import requests

if len(sys.argv) < 4:
    print("Usage: py -3 debug_dhis2.py <username> <password> <dataset_uid>")
    sys.exit(1)

username = sys.argv[1]
password = sys.argv[2]
dataset_uid = sys.argv[3]

DHIS2_BASE = 'https://asc.education.gov.ng/dhis/api'
org_uid = 'XW44AR7IIP5'  # From the Excel file
period = '2024'

print("=" * 70)
print("1. Fetching dataset structure...")
print("=" * 70)

resp = requests.get(
    f'{DHIS2_BASE}/dataSets/{dataset_uid}',
    auth=(username, password),
    params={
        'fields': (
            'dataSetElements['
            '  dataElement['
            '    id,name,'
            '    categoryCombo[id,name,categoryOptionCombos[id,name]]'
            '  ]'
            ']'
        )
    },
    timeout=30,
)
resp.raise_for_status()
data = resp.json()

elements = data.get('dataSetElements', [])
print(f"Found {len(elements)} dataset elements")
print("\nFirst 3 elements with their category combos:")
for i, dse in enumerate(elements[:3]):
    de = dse.get('dataElement', {})
    print(f"\n  Element {i+1}: {de.get('name', 'N/A')}")
    print(f"    ID: {de.get('id', 'N/A')}")
    cocs = de.get('categoryCombo', {}).get('categoryOptionCombos', [])
    if cocs:
        print(f"    Category Combos ({len(cocs)}):")
        for coc in cocs[:3]:
            print(f"      - {coc.get('name', 'N/A')} (ID: {coc.get('id', 'N/A')})")
        if len(cocs) > 3:
            print(f"      ... and {len(cocs) - 3} more")
    else:
        print(f"    Category Combos: (none)")

print("\n" + "=" * 70)
print("2. Fetching data values...")
print("=" * 70)

resp = requests.get(
    f'{DHIS2_BASE}/dataValueSets',
    auth=(username, password),
    params={'dataSet': dataset_uid, 'orgUnit': org_uid, 'period': period},
    timeout=90,
)
resp.raise_for_status()
dv_data = resp.json()

dvs = dv_data.get('dataValues', [])
print(f"Found {len(dvs)} data values")
print("\nFirst 5 data values:")
for i, dv in enumerate(dvs[:5]):
    print(f"  {i+1}. DE: {dv.get('dataElement', 'N/A')}")
    print(f"     COC: {dv.get('categoryOptionCombo', '(none)')}")
    print(f"     Value: {dv.get('value', 'N/A')}")

# Count by COC
coc_counts = {}
for dv in dvs:
    coc = dv.get('categoryOptionCombo', '(none)')
    coc_counts[coc] = coc_counts.get(coc, 0) + 1

print(f"\nUnique category combos in data values: {len(coc_counts)}")
if coc_counts:
    print("Top 5 COCs by frequency:")
    for coc, count in sorted(coc_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"  {coc}: {count} values")
