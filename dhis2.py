"""DHIS2 API client — all HTTP calls to DHIS2 are isolated here."""
import time
import requests


DHIS2_BASE = 'https://asc.education.gov.ng/dhis/api'
TIMEOUT_SHORT = 30   # seconds — login, datasets, logs
TIMEOUT_LONG  = 90   # seconds — compare (may fetch large datasets)

_JSON_HEADERS = {'Accept': 'application/json'}

# Lightweight in-memory metadata cache (per-process).
_CACHE_TTL_SECONDS = 600
_DATASET_ELEMENTS_CACHE = {}
_NAME_CACHE = {
    'dataElements': {},
    'categoryOptionCombos': {},
}


def _cache_get(cache: dict, key):
    entry = cache.get(key)
    if not entry:
        return None
    if entry['expires_at'] < time.time():
        cache.pop(key, None)
        return None
    return entry['value']


def _cache_set(cache: dict, key, value, ttl_seconds=_CACHE_TTL_SECONDS):
    cache[key] = {
        'value': value,
        'expires_at': time.time() + ttl_seconds,
    }


def _get(path: str, username: str, password: str, params: dict = None) -> dict:
    resp = requests.get(
        f'{DHIS2_BASE}{path}',
        auth=(username, password),
        params=params,
        headers=_JSON_HEADERS,
        timeout=TIMEOUT_SHORT,
    )
    resp.raise_for_status()
    return resp.json()


def _post(path: str, username: str, password: str, payload: dict) -> dict:
    resp = requests.post(
        f'{DHIS2_BASE}{path}',
        auth=(username, password),
        json=payload,
        headers={**_JSON_HEADERS, 'Content-Type': 'application/json'},
        timeout=TIMEOUT_LONG,
    )
    resp.raise_for_status()
    return resp.json()


def _chunked(values, chunk_size=50):
    """Yield fixed-size chunks from a sequence."""
    for i in range(0, len(values), chunk_size):
        yield values[i:i + chunk_size]


def _get_name_map(resource_path: str, ids: list, username: str, password: str) -> dict:
    """Resolve a list of UIDs to names via metadata endpoint.

    Args:
        resource_path: e.g. '/dataElements' or '/categoryOptionCombos'
    """
    clean_ids = [x for x in ids if x]
    if not clean_ids:
        return {}

    collection_key = resource_path.lstrip('/')
    resource_cache = _NAME_CACHE.get(collection_key, {})

    result = {}
    missing_ids = []
    for uid in clean_ids:
        cached_name = _cache_get(resource_cache, (username, uid))
        if cached_name is None:
            missing_ids.append(uid)
        else:
            result[uid] = cached_name

    for batch in _chunked(missing_ids, chunk_size=50):
        in_clause = '[' + ','.join(batch) + ']'
        data = _get(
            resource_path,
            username,
            password,
            params={
                'fields': 'id,name',
                'paging': 'false',
                'filter': f'id:in:{in_clause}',
            },
        )
        for item in data.get(collection_key, []):
            item_id = item.get('id', '')
            item_name = item.get('name', '')
            result[item_id] = item_name
            _cache_set(resource_cache, (username, item_id), item_name)

    _NAME_CACHE[collection_key] = resource_cache
    return result


def get_data_element_name_map(ids: list, username: str, password: str) -> dict:
    """Return {deUID: deName} for the given data element IDs."""
    return _get_name_map('/dataElements', ids, username, password)


def get_coc_name_map(ids: list, username: str, password: str) -> dict:
    """Return {cocUID: cocName} for the given category option combo IDs."""
    return _get_name_map('/categoryOptionCombos', ids, username, password)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def login(username: str, password: str) -> dict:
    """Authenticate against DHIS2 and return school/user metadata."""
    data = _get(
        '/me',
        username, password,
        params={
            'fields': (
                'id,username,name,'
                'organisationUnits['
                'id,name,code,'
                'parent[id,name,level,parent[id,name,level]]'
                ']'
            )
        },
    )
    org_units = data.get('organisationUnits', [])
    if not org_units:
        raise ValueError('No organisation unit is assigned to this DHIS2 user.')
    ou = org_units[0]
    ward_node = ou.get('parent', {}) or {}
    lga_node = ward_node.get('parent', {}) or {}

    ward_name = ward_node.get('name', '')
    lga_name = ''

    # Prefer explicit level-3 org unit as LGA; fall back to grandparent name.
    if int(ward_node.get('level', 0) or 0) == 3:
        lga_name = ward_node.get('name', '')
    elif int(lga_node.get('level', 0) or 0) == 3:
        lga_name = lga_node.get('name', '')
    else:
        lga_name = lga_node.get('name', '')

    return {
        'orgUnitUID': ou['id'],
        'schoolName': ou.get('name', ''),
        'schoolCode': ou.get('code', ''),
        'parentName': ward_name,
        'wardName': ward_name,
        'lgaName': lga_name,
        'userName':   data.get('name', username),
    }


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

def get_datasets(username: str, password: str) -> list:
    """Return all datasets visible to the authenticated user."""
    data = _get(
        '/dataSets',
        username, password,
        params={'fields': 'id,name', 'paging': 'false'},
    )
    return data.get('dataSets', [])


# ---------------------------------------------------------------------------
# Dataset structure — data elements + category option combos
# ---------------------------------------------------------------------------

def get_dataset_elements(dataset_uid: str, username: str, password: str) -> list:
    """Return [{deUID, deName, cocUID, cocName, sectionName, sectionOrder}] per dataset cell."""
    cache_key = (username, dataset_uid)
    cached = _cache_get(_DATASET_ELEMENTS_CACHE, cache_key)
    if cached is not None:
        # Return a shallow copy to prevent accidental caller mutation of cache.
        return list(cached)

    data = _get(
        f'/dataSets/{dataset_uid}',
        username, password,
        params={
            'fields': (
                'dataSetElements['
                '  categoryCombo[categoryOptionCombos[id,name]],'
                '  dataElement['
                '    id,name,'
                '    categoryCombo[categoryOptionCombos[id,name]]'
                '  ]'
                '],'
                'sections[id,name,dataElements[id]]'
            )
        },
    )
    section_by_de_uid = {}
    section_order_by_de_uid = {}

    # Resolve section membership via /sections endpoint when possible.
    # This is more reliable for some DHIS2 instances than relying only on
    # embedded dataSet.sections[].dataElements from /dataSets/{id}.
    section_refs = data.get('sections', [])
    section_ids = [s.get('id', '') for s in section_refs if s.get('id', '')]
    section_order_index = {sid: idx for idx, sid in enumerate(section_ids)}

    resolved_sections = []
    if section_ids:
        for batch in _chunked(section_ids, chunk_size=50):
            in_clause = '[' + ','.join(batch) + ']'
            section_data = _get(
                '/sections',
                username,
                password,
                params={
                    'filter': f'id:in:{in_clause}',
                    'fields': 'id,name,dataElements[id,name]',
                    'paging': 'false',
                },
            )
            resolved_sections.extend(section_data.get('sections', []))

    # Keep dataset form order (section sequence) for consistent UI grouping.
    if resolved_sections:
        resolved_sections.sort(key=lambda s: section_order_index.get(s.get('id', ''), 9999))
    else:
        # Fallback to embedded section metadata.
        resolved_sections = section_refs

    for idx, section in enumerate(resolved_sections):
        section_name = section.get('name', '') or 'Unsectioned'
        for de_ref in section.get('dataElements', []):
            de_uid = de_ref.get('id', '')
            if de_uid and de_uid not in section_by_de_uid:
                section_by_de_uid[de_uid] = section_name
                section_order_by_de_uid[de_uid] = idx

    elements = []
    seen = set()
    for dse in data.get('dataSetElements', []):
        de = dse.get('dataElement', {})
        de_uid  = de.get('id', '')
        de_name = de.get('name', '')
        section_name = section_by_de_uid.get(de_uid, 'Unsectioned')
        section_order = section_order_by_de_uid.get(de_uid, 9999)

        # In DHIS2, a DataSetElement can override the data element's category combo.
        # Prefer the DSE-level combo, then fall back to the DE default combo.
        dse_combo = dse.get('categoryCombo', {})
        de_combo = de.get('categoryCombo', {})
        cocs = dse_combo.get('categoryOptionCombos') or de_combo.get('categoryOptionCombos') or []

        if not cocs:
            key = (de_uid, '')
            if key not in seen:
                seen.add(key)
                elements.append({
                    'deUID': de_uid,
                    'deName': de_name,
                    'cocUID': '',
                    'cocName': 'default',
                    'sectionName': section_name,
                    'sectionOrder': section_order,
                })
        else:
            for coc in cocs:
                coc_uid = coc.get('id', '')
                key = (de_uid, coc_uid)
                if key in seen:
                    continue
                seen.add(key)
                elements.append({
                    'deUID':   de_uid,
                    'deName':  de_name,
                    'cocUID':  coc_uid,
                    'cocName': coc.get('name', 'default'),
                    'sectionName': section_name,
                    'sectionOrder': section_order,
                })
    _cache_set(_DATASET_ELEMENTS_CACHE, cache_key, list(elements))
    return elements


# ---------------------------------------------------------------------------
# Data values
# ---------------------------------------------------------------------------

def get_data_values(org_unit_uid: str, period: str, dataset_uid: str,
                    username: str, password: str, id_scheme: str = 'uid') -> dict:
    """Return {dataElement|categoryOptionCombo: value} for DHIS2 values.

    Args:
        id_scheme: 'uid' (default) or 'name'.
            Uses DHIS2 dataElementIdScheme/categoryOptionComboIdScheme for response
            to support robust matching when UID mappings drift across instances.
    """
    scheme = (id_scheme or 'uid').lower()
    if scheme not in ('uid', 'name'):
        raise ValueError("id_scheme must be 'uid' or 'name'.")

    params = {
        'dataSet': dataset_uid,
        'orgUnit': org_unit_uid,
        'period': period,
        'fields': 'dataElement,categoryOptionCombo,value',
        'dataElementIdScheme': scheme,
        'categoryOptionComboIdScheme': scheme,
        'orgUnitIdScheme': 'uid',
    }

    # Use TIMEOUT_LONG for potentially large value sets
    resp = requests.get(
        f'{DHIS2_BASE}/dataValueSets',
        auth=(username, password),
        params=params,
        headers=_JSON_HEADERS,
        timeout=TIMEOUT_LONG,
    )
    resp.raise_for_status()
    data = resp.json()
    result = {}
    for dv in data.get('dataValues', []):
        de_id = dv.get('dataElement', '')
        coc_id = dv.get('categoryOptionCombo', '')
        value = dv.get('value', '')
        # Use empty string if cocUID is missing, to match elements without combos
        key = f"{de_id}|{coc_id}" if coc_id else f"{de_id}|"
        result[key] = value
    return result


def push_data_values(org_unit_uid: str, period: str, dataset_uid: str,
                     entries: list, username: str, password: str) -> dict:
    """POST data values to DHIS2. entries: [{deUID, cocUID, value}].
    
    Note: if cocUID is empty string, we omit categoryOptionCombo from the request.
    """
    data_values = []
    for e in entries:
        dv = {
            'dataElement': e['deUID'],
            'orgUnit':     org_unit_uid,
            'period':      period,
            'value':       str(e['value']),
        }
        # Only include categoryOptionCombo if it's non-empty
        if e.get('cocUID'):
            dv['categoryOptionCombo'] = e['cocUID']
        data_values.append(dv)
    
    payload = {'dataValues': data_values}
    return _post('/dataValueSets', username, password, payload)
